"""Backups made by scripts/backup_chroma.py can be restored by scripts/restore_chroma.py."""
import gzip
import json

import dotenv
import pytest

from backend import llm_pipeline
from conftest import _raw_client, tree_paths
from scripts import backup_chroma, restore_chroma

NOTE = b"Newton's second law: force equals mass times acceleration, F = ma. " * 10


@pytest.fixture
def library(client, signup, upload):
    """A student with a folder, a note and a flashcard deck, all created through the API."""
    u = signup("backup@example.com")
    h = u["headers"]
    assert client.post("/create_folder", data={"name": "Physics"}, headers=h).status_code == 200
    upload(h, "newton.md", NOTE, folder="Physics")
    cards = [{"front": "F = ?", "back": "ma", "source": "notes"}, {"front": "Unit of force?", "back": "newton", "source": "notes"}]
    r = client.post("/study/decks", data={"name": "Physics deck", "source_path": "Physics/newton.md", "cards": json.dumps(cards)}, headers=h)
    assert r.status_code == 201, r.text
    return u


def quiet(*_args, **_kwargs):
    pass


def test_backup_then_restore_into_empty_database(client, library, tmp_path, monkeypatch):
    path = tmp_path / "backup.jsonl.gz"
    made = backup_chroma.backup(path, log=quiet)
    assert made["records"] > 0
    assert "sessions" not in made["collections"]  # short-lived data is left out
    assert "users" in made["collections"] and "user_documents" in made["collections"]

    _raw_client.reset()  # lose everything
    assert client.post("/login", data={"email": "backup@example.com", "password": "goodpass123"}).status_code == 401

    restored = restore_chroma.restore(path, log=quiet)
    assert restored["records"] == made["records"]
    assert restored["collections"] == made["collections"]

    # Old logins aren't in the backup; signing in again works and everything is back.
    assert client.get("/me", headers=library["headers"]).status_code == 401
    login = client.post("/login", data={"email": "backup@example.com", "password": "goodpass123"})
    assert login.status_code == 200, login.text
    h = {"X-Session-Id": login.json()["session_id"]}
    assert tree_paths(client, h) == {"Physics/newton.md"}
    text = client.get("/file_text", params={"path": "Physics/newton.md"}, headers=h).json()["text"]
    assert text.startswith("Newton's second law")

    monkeypatch.setattr(llm_pipeline, "sarvam_rag_answer", lambda q, ctx: (f"ANSWER\n{ctx}", None))
    r = client.post("/query_folder", data={"query": "What is Newton's second law?"}, headers=h)
    assert r.status_code == 200 and "force equals mass" in r.json()["answer"]

    decks = client.get("/study/decks", headers=h).json()["decks"]
    assert [(d["name"], d["card_count"]) for d in decks] == [("Physics deck", 2)]
    cards = client.get(f"/study/decks/{decks[0]['deck_id']}/cards", headers=h).json()["cards"]
    assert {c["back"] for c in cards} == {"ma", "newton"}


def test_restore_refuses_a_database_that_has_data(library, tmp_path):
    path = tmp_path / "backup.jsonl.gz"
    backup_chroma.backup(path, log=quiet)
    with pytest.raises(restore_chroma.RestoreRefused) as err:
        restore_chroma.restore(path, log=quiet)
    assert "users" in err.value.collections

    dry = restore_chroma.restore(path, force=True, dry_run=True, log=quiet)
    assert dry["dry_run"] and dry["records"] > 0
    forced = restore_chroma.restore(path, force=True, log=quiet)  # same ids: replaced, nothing duplicated
    assert not forced["dry_run"]
    again = backup_chroma.backup(tmp_path / "after.jsonl.gz", log=quiet)
    assert again["collections"] == backup_chroma.backup(tmp_path / "check.jsonl.gz", log=quiet)["collections"]


def test_cut_off_backup_is_rejected(library, tmp_path):
    path = tmp_path / "backup.jsonl.gz"
    backup_chroma.backup(path, log=quiet)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        lines = f.readlines()
    cut = tmp_path / "cut.jsonl.gz"
    with gzip.open(cut, "wt", encoding="utf-8") as f:
        f.writelines(lines[: len(lines) // 2])
    _raw_client.reset()
    with pytest.raises(ValueError, match="incomplete"):
        restore_chroma.restore(cut, log=quiet)
    assert not _raw_client.list_collections()  # nothing was written


def test_command_line(library, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)  # never read the real .env in tests
    path = tmp_path / "cli.jsonl.gz"
    assert backup_chroma.main(["--out", str(path)]) == 0
    assert path.exists() and not (tmp_path / "cli.jsonl.gz.partial").exists()
    assert restore_chroma.main([str(path)]) == 1  # data already there, no --force
    assert "already have records" in capsys.readouterr().err
    assert restore_chroma.main([str(path), "--dry-run", "--force"]) == 0
