"""Request limits: overall caps, per-route limits, daily AI caps, and what the client is told."""
import time

import pytest

from backend import rate_limit, settings
from backend.routes import chat, files


def test_429_says_when_to_retry(client, signup):
    signup("limits@example.com")
    for _ in range(10):
        client.post("/login", data={"email": "limits@example.com", "password": "wrongpass1"})
    r = client.post("/login", data={"email": "limits@example.com", "password": "wrongpass1"})
    assert r.status_code == 429
    assert 0 < int(r.headers["retry-after"]) <= 900
    assert "minutes" in r.json()["detail"]  # human-friendly wait, not "843 seconds"


def test_refused_request_does_not_use_up_other_limits():
    rules = [("a", "k", 5, 60, None), ("b", "k", 1, 60, None)]
    rate_limit.check_all(rules)
    for _ in range(3):
        with pytest.raises(Exception):
            rate_limit.check_all(rules)  # refused by "b"
    # "a" only counted the one request that was let through.
    for _ in range(4):
        rate_limit.check("a", "k", 5, 60)
    with pytest.raises(Exception):
        rate_limit.check("a", "k", 5, 60)


def test_overall_cap_per_signed_in_browser(client, user, monkeypatch):
    monkeypatch.setattr(settings, "LIMIT_ALL_PER_SESSION", (5, 60))
    codes = [client.get("/list_tree", headers=user["headers"]).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
    r = client.get("/jobs", headers={**user["headers"], "Origin": "https://cherie-dips.github.io"})
    assert r.status_code == 429 and r.headers.get("access-control-allow-origin")  # browser can read it
    assert client.get("/health").status_code == 200  # monitoring is never limited


def test_overall_cap_per_network_address(client, monkeypatch):
    monkeypatch.setattr(settings, "LIMIT_ALL_PER_IP", (3, 60))
    codes = [client.get("/config").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    # Another address has its own count. (Addresses come from X-Forwarded-For, which a client can
    # fake; that's why the AI-cost limits are per account, not per address.)
    assert client.get("/config", headers={"X-Forwarded-For": "10.0.0.9"}).status_code == 200


def test_tree_changes_and_text_edits_limited(client, user, monkeypatch):
    monkeypatch.setattr(files, "LIMIT_TREE_CHANGES_PER_USER", (2, 60))
    monkeypatch.setattr(files, "LIMIT_TEXT_EDITS_PER_USER", (1, 600))
    h = user["headers"]
    codes = [client.post("/create_folder", data={"name": f"F{i}"}, headers=h).status_code for i in range(3)]
    assert codes == [200, 200, 429]
    rate_limit.reset()
    client.post("/create_file", data={"name": "n"}, headers=h)
    assert client.post("/file_text", data={"path": "n.txt", "text": "one"}, headers=h).status_code == 200
    assert client.post("/file_text", data={"path": "n.txt", "text": "two"}, headers=h).status_code == 429


def test_daily_ai_cap_message(client, user, upload, monkeypatch):
    monkeypatch.setattr(chat, "LIMIT_QUESTIONS_PER_DAY", (2, 86400))
    monkeypatch.setattr(chat.llm_pipeline, "sarvam_rag_answer", lambda q, ctx: ("ok", None))
    h = user["headers"]
    upload(h, "a.txt", b"Photosynthesis turns light into chemical energy. " * 5)
    for _ in range(2):
        assert client.post("/query_folder", data={"query": "What is photosynthesis?"}, headers=h).status_code == 200
    r = client.post("/query_folder/stream", data={"query": "What is photosynthesis?"}, headers=h)
    assert r.status_code == 429
    assert r.json()["detail"].startswith("You've reached the daily limit of 2 questions")
    assert int(r.headers["retry-after"]) > 80000


def test_daily_counters_survive_memory_cleanup(monkeypatch):
    rate_limit.check("q-day", "u1", 2, 86400)
    rate_limit.check("q-day", "u1", 2, 86400)
    monkeypatch.setattr(rate_limit, "_MAX_KEYS", 0)
    real_monotonic = time.monotonic
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: real_monotonic() + 7200)  # 2 hours later
    with pytest.raises(Exception):
        rate_limit.check("q-day", "u1", 2, 86400)  # still counted: cleanup only drops expired windows


def test_multiplier_scales_limits(monkeypatch):
    monkeypatch.setattr(rate_limit, "MULTIPLIER", 2.0)
    for _ in range(4):
        rate_limit.check("m", "k", 2, 60)
    with pytest.raises(Exception):
        rate_limit.check("m", "k", 2, 60)
