import json
import time

import pytest

from backend import llm_pipeline, study_store


@pytest.fixture
def opened_file(client, user, upload):
    upload(user["headers"], "newton.md", b"Newton's second law: force equals mass times acceleration. " * 10)
    return "newton.md"


def test_ai_failure_gives_error_not_fake_items(client, user, opened_file, monkeypatch):
    monkeypatch.setattr(llm_pipeline, "_sarvam_chat_complete", lambda *a, **k: ("this is not json", None))
    r = client.post("/study/generate", data={"task": "mcq", "count": "3", "opened_file_path": opened_file}, headers=user["headers"])
    assert r.status_code == 503 and "items" not in r.json() and "try again" in r.json()["error"]
    monkeypatch.setattr(llm_pipeline, "_sarvam_chat_complete", lambda *a, **k: (None, "upstream down"))
    r = client.post("/study/summary", data={"opened_file_path": opened_file}, headers=user["headers"])
    assert r.status_code == 503 and "summary" not in r.json()


def test_mcqs_have_explanations_and_shuffled_answers(client, user, opened_file, monkeypatch):
    item = {"question": "F = ?", "options": ["ma", "mv", "m/a", "a/m"], "answer_index": 0, "explanation": "Newton's second law.", "source": "notes"}
    monkeypatch.setattr(llm_pipeline, "_sarvam_chat_complete", lambda *a, **k: (json.dumps([item] * 20), None))
    r = client.post("/study/generate", data={"task": "mcq", "count": "20", "opened_file_path": opened_file}, headers=user["headers"])
    items = r.json()["items"]
    assert len(items) == 20
    assert all(it["options"][it["answer_index"]] == "ma" and it["explanation"] for it in items)
    assert len({it["answer_index"] for it in items}) > 1  # the right answer isn't always in the same place


def test_summary_route_and_old_name(client, user, opened_file, monkeypatch):
    monkeypatch.setattr(llm_pipeline, "_sarvam_chat_complete", lambda *a, **k: ("A summary of Newton's laws " * 5, None))
    for route in ("/study/summary", "/study/mindmap"):
        r = client.post(route, data={"opened_file_path": opened_file}, headers=user["headers"])
        assert r.status_code == 200 and r.json()["summary"].startswith("A summary")


def test_deck_lifecycle(client, user):
    h = user["headers"]
    cards = [{"front": f"Q{i}", "back": f"A{i}", "source": "notes"} for i in range(3)]
    r = client.post("/study/decks", data={"name": "Physics week 1", "source_path": "newton.md", "cards": json.dumps(cards)}, headers=h)
    assert r.status_code == 201, r.text
    deck_id = r.json()["deck_id"]

    decks = client.get("/study/decks", headers=h).json()["decks"]
    assert decks[0]["card_count"] == 3 and decks[0]["due_count"] == 3

    due = client.get(f"/study/decks/{deck_id}/cards", params={"due_only": "true"}, headers=h).json()["cards"]
    assert len(due) == 3
    r = client.post(f"/study/cards/{due[0]['card_id']}/review", data={"grade": "good"}, headers=h)
    assert r.status_code == 200 and r.json()["interval_days"] == 1.0
    r = client.post(f"/study/cards/{due[1]['card_id']}/review", data={"grade": "again"}, headers=h)
    assert r.json()["due_at"] - time.time() < 700
    assert client.post(f"/study/cards/{due[2]['card_id']}/review", data={"grade": "perfect"}, headers=h).status_code == 400
    assert client.get("/study/decks", headers=h).json()["decks"][0]["due_count"] == 1

    r = client.get(f"/study/decks/{deck_id}/export", headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="Physics week 1.csv"' in r.headers["content-disposition"]
    assert r.text.splitlines()[0] in {"Q0,A0", "Q1,A1", "Q2,A2"}

    assert client.delete(f"/study/decks/{deck_id}", headers=h).status_code == 200
    assert client.get("/study/decks", headers=h).json()["decks"] == []
    assert client.get(f"/study/decks/{deck_id}/cards", headers=h).status_code == 404


def test_decks_are_private(client, signup):
    a, b = signup(), signup()
    deck = client.post("/study/decks", data={"name": "mine", "cards": json.dumps([{"front": "q", "back": "a"}])}, headers=a["headers"]).json()
    assert client.get(f"/study/decks/{deck['deck_id']}/cards", headers=b["headers"]).status_code == 404
    card = client.get(f"/study/decks/{deck['deck_id']}/cards", headers=a["headers"]).json()["cards"][0]
    assert client.post(f"/study/cards/{card['card_id']}/review", data={"grade": "good"}, headers=b["headers"]).status_code == 404


def test_schedule_grows_intervals():
    meta = {"ease": 2.5, "interval_days": 0, "reps": 0, "lapses": 0}
    intervals = []
    for _ in range(4):
        meta = study_store.schedule(meta, "good", 0)
        intervals.append(meta["interval_days"])
    assert intervals[0] == 1.0 and intervals[1] == 3.0 and intervals[2] > 3.0 and intervals[3] > intervals[2]
    lapsed = study_store.schedule(meta, "again", 0)
    assert lapsed["reps"] == 0 and lapsed["lapses"] == 1 and lapsed["ease"] < meta["ease"]


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


def test_study_tools_use_the_current_model_without_long_reasoning(monkeypatch):
    sent = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return FakeResponse(200, {"choices": [{"message": {"content": "A short summary of the notes on Newton's laws."}}]})

    monkeypatch.setenv("SARVAM_API_KEY", "test-key")
    monkeypatch.setenv("SARVAM_MODEL_STUDY", "")  # blank settings fall back to the default
    monkeypatch.setattr(llm_pipeline.httpx, "post", fake_post)
    text, err = llm_pipeline.topic_summary("Newton's laws of motion: inertia, F = ma, action and reaction. " * 5)
    assert err is None and text
    assert sent[0]["model"] == "sarvam-105b"
    assert "reasoning_effort" in sent[0] and sent[0]["reasoning_effort"] is None  # null = reasoning off


def test_retired_model_setting_falls_back_to_the_default(monkeypatch):
    models = []

    def fake_post(url, headers=None, json=None, timeout=None):
        models.append(json["model"])
        if json["model"] == "sarvam-30b":
            return FakeResponse(400, {"error": {"message": "Model 'sarvam-30b' has been deprecated."}})
        return FakeResponse(200, {"choices": [{"message": {"content": "Summary of the notes about forces and motion."}}]})

    monkeypatch.setenv("SARVAM_API_KEY", "test-key")
    monkeypatch.setenv("SARVAM_MODEL_STUDY", "sarvam-30b")
    monkeypatch.setattr(llm_pipeline.httpx, "post", fake_post)
    text, err = llm_pipeline.topic_summary("Newton's laws of motion: inertia, F = ma, action and reaction. " * 5)
    assert err is None and text and models == ["sarvam-30b", "sarvam-105b"]
