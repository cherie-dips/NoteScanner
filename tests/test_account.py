import time

import backend.chroma_store as cs
from backend import mailer
from conftest import tree_paths


def test_register_validation(client):
    r = client.post("/register", data={"email": "a@b.co", "password": "short", "accepted_privacy": "true"})
    assert r.status_code == 400 and "8" in r.json()["error"]
    r = client.post("/register", data={"email": "not-an-email", "password": "goodpass123", "accepted_privacy": "true"})
    assert r.status_code == 400
    r = client.post("/register", data={"email": "a@b.co", "password": "x" * 80, "accepted_privacy": "true"})
    assert r.status_code == 400 and "too long" in r.json()["error"]


def test_duplicate_email_rejected(client, signup):
    signup("same@example.com")
    r = client.post("/register", data={"email": "SAME@example.com", "password": "goodpass123", "accepted_privacy": "true"})
    assert r.status_code == 400


def test_login_attempts_limited(client, signup):
    signup("victim@example.com")
    codes = [client.post("/login", data={"email": "victim@example.com", "password": "wrongpass1"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_logout_ends_session_everywhere(client, user):
    h = user["headers"]
    assert client.post("/logout", headers=h).status_code == 200
    assert client.get("/me", headers=h).status_code == 401
    assert client.get("/list_tree", headers=h).status_code == 401  # guest-allowed routes too


def test_expired_and_old_sessions_rejected(client, user):
    sid = user["headers"]["X-Session-Id"]
    col = cs.get_sessions_collection()
    meta = col.get(ids=[sid])["metadatas"][0]
    col.update(ids=[sid], metadatas=[{**meta, "expires_at": int(time.time()) - 1}])
    assert client.get("/me", headers=user["headers"]).status_code == 401
    col.add(ids=["old-style"], documents=[""], metadatas=[{"user_id": "u"}], embeddings=[cs._dummy_embedding()])
    assert cs.session_get_user_id("old-style") is None


def test_change_password_signs_out_other_devices(client, signup):
    u = signup("change@example.com")
    other = client.post("/login", data={"email": "change@example.com", "password": "goodpass123"}).json()
    other_h = {"X-Session-Id": other["session_id"]}
    r = client.post("/account/change_password", data={"current_password": "wrong", "new_password": "newpass1234"}, headers=u["headers"])
    assert r.status_code == 400
    r = client.post("/account/change_password", data={"current_password": "goodpass123", "new_password": "newpass1234"}, headers=u["headers"])
    assert r.status_code == 200, r.text
    assert client.get("/me", headers=u["headers"]).status_code == 200
    assert client.get("/me", headers=other_h).status_code == 401
    assert client.post("/login", data={"email": "change@example.com", "password": "newpass1234"}).status_code == 200


def test_delete_account_removes_everything(client, user, upload):
    h, uid = user["headers"], user["user_id"]
    upload(h, "notes.txt", b"Some notes about cells and mitochondria. " * 10)
    r = client.post("/account/delete", data={"password": "wrong"}, headers=h)
    assert r.status_code == 400
    r = client.post("/account/delete", data={"password": "goodpass123"}, headers=h)
    assert r.status_code == 200, r.text
    assert client.get("/me", headers=h).status_code == 401
    assert cs.user_get_by_id(uid) is None
    names = {c.name for c in cs._global_client.list_collections()}
    assert not any(uid in n for n in names), names
    assert cs.user_document_get(uid, "notes.txt") is None
    r = client.post("/login", data={"email": user["email"], "password": "goodpass123"})
    assert r.status_code == 401


def test_forgot_password_not_configured(client):
    assert client.get("/config").json()["password_reset_enabled"] is False
    assert client.post("/password/forgot", data={"email": "x@example.com"}).status_code == 503


def test_forgot_and_reset_password(client, signup, monkeypatch):
    signup("forgot@example.com")
    sent = []
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_password_reset", lambda to, link: sent.append((to, link)))
    assert client.get("/config").json()["password_reset_enabled"] is True

    unknown = client.post("/password/forgot", data={"email": "nobody@example.com"})
    known = client.post("/password/forgot", data={"email": "forgot@example.com"})
    assert unknown.status_code == known.status_code == 200
    assert unknown.json() == known.json()  # can't be used to discover accounts
    assert len(sent) == 1 and sent[0][0] == "forgot@example.com"
    token = sent[0][1].split("reset_token=")[1]

    assert client.post("/password/reset", data={"token": token, "new_password": "short"}).status_code == 400
    r = client.post("/password/reset", data={"token": token, "new_password": "brandnewpass1"})
    assert r.status_code == 200, r.text
    assert client.post("/password/reset", data={"token": token, "new_password": "anotherpass1"}).status_code == 400  # single use
    assert client.post("/login", data={"email": "forgot@example.com", "password": "brandnewpass1"}).status_code == 200


def test_guests_cannot_store_or_spend(client):
    guest = {"X-Guest-Id": client.get("/guest_id").json()["guest_id"]}
    r = client.post("/upload_note", data={"path": ""}, files={"file": ("a.txt", b"hello")}, headers=guest)
    assert r.status_code == 401
    r = client.post("/query_folder", data={"query": "hi"}, headers=guest)
    assert r.status_code == 401
    assert client.get("/list_tree", headers=guest).json()["tree"] == []
    assert not any("guest_" in c.name for c in cs._global_client.list_collections())
    assert client.get("/list_tree", headers={"X-Guest-Id": "../../weird"}).status_code == 400


def test_tree_survives_signout(client, signup):
    u = signup("persist@example.com")
    client.post("/create_folder", data={"name": "Math"}, headers=u["headers"])
    client.post("/logout", headers=u["headers"])
    again = client.post("/login", data={"email": "persist@example.com", "password": "goodpass123"}).json()
    h = {"X-Session-Id": again["session_id"]}
    assert client.get("/list_tree", headers=h).json()["tree"][0]["name"] == "Math"
    assert tree_paths(client, h) == set()
