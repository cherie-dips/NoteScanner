import json

import numpy as np
import pytest

import backend.chroma_store as cs
from backend import chat_cache, llm_pipeline
from backend.ingest_api import embed_texts
from backend.settings import CHAT_CACHE_TTL_SECONDS, MAX_CHAT_FILES_PER_SESSION

REAL_ANSWER_STREAM = llm_pipeline.sarvam_rag_answer_stream  # before the fixture below replaces it


@pytest.fixture(autouse=True)
def fake_answers(monkeypatch):
    """Answer with the context we were given, so tests can see which notes were used."""
    monkeypatch.setattr(llm_pipeline, "sarvam_rag_answer", lambda q, ctx: (f"ANSWER\n{ctx[:400]}", None))
    monkeypatch.setattr(llm_pipeline, "sarvam_rag_answer_stream", lambda q, ctx: iter(["**Force** ", "= mass × acceleration"]))


@pytest.fixture
def library(client, user, upload):
    h = user["headers"]
    for folder in ("Physics", "Bio"):
        client.post("/create_folder", data={"name": folder}, headers=h)
    upload(h, "newton.md", b"Newton's second law: force equals mass times acceleration, F = ma. " * 10, folder="Physics")
    upload(h, "cells.txt", b"Mitochondria is the powerhouse of the cell and makes ATP. " * 10, folder="Bio")
    return h


def test_question_with_no_file_open_searches_all_notes(client, library):
    r = client.post("/query_folder", data={"query": "What is Newton's second law?"}, headers=library)
    j = r.json()
    assert r.status_code == 200 and j["selection_stage"] == "all_notes"
    assert "Physics/newton.md" in j["answer"]


def test_scores_are_true_cosine_similarity(client, user, library):
    q = "What is Newton's second law?"
    qv = embed_texts([q])[0]
    got = cs.get_user_collection(user["user_id"]).get(where={"path": "Physics/newton.md"}, include=["embeddings"])
    direct = max(float(np.dot(qv, np.asarray(e))) for e in got["embeddings"])
    j = client.post("/query_folder", data={"query": q, "opened_file_path": "Physics/newton.md"}, headers=library).json()
    assert j["selection_stage"] == "opened"
    assert abs(j["score_opened_best"] - direct) < 1e-3


def test_off_topic_open_file_falls_through(client, library):
    j = client.post(
        "/query_folder",
        data={"query": "How do mitochondria make ATP?", "opened_file_path": "Physics/newton.md"},
        headers=library,
    ).json()
    assert j["selection_stage"] == "all_notes" and "Bio/cells.txt" in j["answer"]


def test_main_source_is_labelled_for_the_ai(client, library):
    client.post("/file_meta", data={"path": "Bio/cells.txt", "is_primary_authority": "true"}, headers=library)
    j = client.post("/query_folder", data={"query": "What makes ATP?"}, headers=library).json()
    assert "Bio/cells.txt (main source)" in j["answer"]
    assert j["source_documents"][0]["metadata"]["is_primary_authority"] == 1


def test_streaming_answer(client, library):
    r = client.post("/query_folder/stream", data={"query": "What is force?"}, headers=library)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in r.text.splitlines()]
    assert lines[0]["type"] == "meta" and lines[0]["source_documents"]
    assert "".join(x["text"] for x in lines if x["type"] == "delta") == "**Force** = mass × acceleration"
    assert lines[-1] == {"type": "done"}


def test_streaming_reports_ai_failure(client, library, monkeypatch):
    def broken(q, ctx):
        raise llm_pipeline.LLMError("upstream down")
        yield  # pragma: no cover

    monkeypatch.setattr(llm_pipeline, "sarvam_rag_answer_stream", broken)
    lines = [json.loads(x) for x in client.post("/query_folder/stream", data={"query": "force?"}, headers=library).text.splitlines()]
    assert lines[-1]["type"] == "error" and "try again" in lines[-1]["error"]


def test_no_notes_gives_clear_error(client, user):
    r = client.post("/query_folder/stream", data={"query": "anything"}, headers=user["headers"])
    assert r.status_code == 400 and "No notes found" in r.json()["detail"]


def test_long_chat_upload_fully_searchable(client, user):
    h = user["headers"]
    text = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 800
    text += " The secret exam topic is the Krebs citric acid cycle in mitochondria."
    r = client.post("/chat/upload_ephemeral", data={"chat_session_id": "c1"}, files={"file": ("long.txt", text.encode())}, headers=h)
    assert r.status_code == 200
    j = client.post("/query_folder", data={"query": "What is the secret exam topic?", "chat_session_id": "c1"}, headers=h).json()
    assert j["selection_stage"] == "ephemeral" and "Krebs" in j["answer"]


def test_chat_cache_expiry_and_cap(client, user):
    h, uid = user["headers"], user["user_id"]
    client.post("/chat/upload_ephemeral", data={"chat_session_id": "c1"}, files={"file": ("a.txt", b"hello there")}, headers=h)
    chat_cache._cache[chat_cache._key(uid, "c1")]["last_used"] -= CHAT_CACHE_TTL_SECONDS + 5
    assert chat_cache.items(uid, "c1") == []
    for i in range(MAX_CHAT_FILES_PER_SESSION):
        client.post("/chat/upload_ephemeral", data={"chat_session_id": "c2"}, files={"file": (f"f{i}.txt", b"some text")}, headers=h)
    r = client.post("/chat/upload_ephemeral", data={"chat_session_id": "c2"}, files={"file": ("x.txt", b"more")}, headers=h)
    assert r.status_code == 400 and "Start a new chat" in r.text


def test_sse_parsing_of_sarvam_stream(monkeypatch):
    """The real streaming client reads OpenAI-style server-sent events."""
    events = [
        'data: {"choices":[{"delta":{"reasoning_content":"thinking..."}}]}',
        "",
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"content":" world"}}]}',
        "data: [DONE]",
    ]

    class FakeResponse:
        status_code = 200

        def iter_lines(self):
            return iter(events)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setenv("SARVAM_API_KEY", "test-key")
    monkeypatch.setattr(llm_pipeline.httpx, "stream", lambda *a, **k: FakeResponse())
    assert "".join(REAL_ANSWER_STREAM("q", "ctx")) == "Hello world"


def test_loosely_related_files_are_not_listed_as_sources(client, library, upload, user):
    upload(user["headers"], "kinematics.md", b"Kinematics describes motion with velocity and displacement. " * 10, folder="Physics")
    j = client.post("/query_folder", data={"query": "What is Newton's second law?", "course_path": "Physics"}, headers=library).json()
    assert {d["metadata"]["path"] for d in j["source_documents"]} == {"Physics/newton.md"}
