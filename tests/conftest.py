"""
Test setup: an in-memory Chroma that enforces Chroma Cloud's limits, no real AI calls.

Run with:  python -m pytest -q tests
"""
import os
import sys
import time
import uuid

os.environ["SARVAM_API_KEY"] = ""  # never call Sarvam from tests (local OCR fallbacks are used)
os.environ["STARTER_NOTES"] = "false"  # tests that need them add them explicitly
os.environ.pop("ADMIN_EMAILS", None)
os.environ.pop("ALERT_WEBHOOK_URL", None)
os.environ.setdefault("MAX_UPLOAD_MB", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chromadb  # noqa: E402
import pytest  # noqa: E402
from chromadb.config import Settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import backend.chroma_store as cs  # noqa: E402

# Chroma Cloud quotas (https://docs.trychroma.com/cloud/quotas-limits)
MAX_RESULTS = 300
MAX_RECORDS_PER_WRITE = 300
MAX_DOCUMENT_BYTES = 16384
MAX_ID_BYTES = 128
MAX_WHERE_PREDICATES = 8


class CloudQuotaError(AssertionError):
    pass


def _count_predicates(where) -> int:
    if not where:
        return 0
    total = 0
    for key, val in where.items():
        if key in ("$and", "$or"):
            total += sum(_count_predicates(v) for v in val)
        else:
            total += 1
    return total


class LimitedCollection:
    """Wraps a Chroma collection and fails like Chroma Cloud would when a quota is exceeded."""

    def __init__(self, inner):
        self._c = inner

    def __getattr__(self, name):
        return getattr(self._c, name)

    def _check_write(self, kw):
        ids = kw.get("ids") or []
        if len(ids) > MAX_RECORDS_PER_WRITE:
            raise CloudQuotaError(f"{len(ids)} records in one write")
        for i in ids:
            if len(i.encode("utf-8")) > MAX_ID_BYTES:
                raise CloudQuotaError(f"id too long ({len(i.encode())} bytes)")
        for d in kw.get("documents") or []:
            if d is not None and len(d.encode("utf-8")) > MAX_DOCUMENT_BYTES:
                raise CloudQuotaError(f"document too large ({len(d.encode())} bytes)")

    def get(self, **kw):
        if _count_predicates(kw.get("where")) > MAX_WHERE_PREDICATES:
            raise CloudQuotaError("too many where predicates")
        if kw.get("limit") is not None and kw["limit"] > MAX_RESULTS:
            raise CloudQuotaError("limit over 300")
        res = self._c.get(**kw)
        if len(res["ids"]) > MAX_RESULTS:
            raise CloudQuotaError(f"get returned {len(res['ids'])} records")
        return res

    def query(self, **kw):
        if kw.get("n_results", 10) > MAX_RESULTS:
            raise CloudQuotaError("n_results over 300")
        if _count_predicates(kw.get("where")) > MAX_WHERE_PREDICATES:
            raise CloudQuotaError("too many where predicates")
        return self._c.query(**kw)

    def add(self, **kw):
        self._check_write(kw)
        return self._c.add(**kw)

    def upsert(self, **kw):
        self._check_write(kw)
        return self._c.upsert(**kw)

    def update(self, **kw):
        self._check_write(kw)
        return self._c.update(**kw)

    def delete(self, **kw):
        if len(kw.get("ids") or []) > MAX_RECORDS_PER_WRITE:
            raise CloudQuotaError("delete of more than 300 ids")
        return self._c.delete(**kw)


class LimitedClient:
    def __init__(self, inner):
        self._client = inner

    def get_collection(self, name, **kw):
        return LimitedCollection(self._client.get_collection(name=name, **kw))

    def create_collection(self, name, **kw):
        return LimitedCollection(self._client.create_collection(name=name, **kw))

    def delete_collection(self, name):
        return self._client.delete_collection(name=name)

    def list_collections(self):
        return self._client.list_collections()


_raw_client = chromadb.EphemeralClient(settings=Settings(allow_reset=True, anonymized_telemetry=False))
cs._global_client = LimitedClient(_raw_client)

from backend import api, legacy_migration, rate_limit  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_state():
    """Empty database and request counters for every test."""
    _raw_client.reset()
    rate_limit.reset()
    legacy_migration._checked_users.clear()
    yield


@pytest.fixture
def client():
    return TestClient(api.app, raise_server_exceptions=False)


@pytest.fixture
def signup(client):
    def _signup(email: str | None = None, password: str = "goodpass123") -> dict:
        email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
        r = client.post("/register", data={"email": email, "password": password, "name": "Test", "accepted_privacy": "true"})
        assert r.status_code == 200, r.text
        body = r.json()
        return {"headers": {"X-Session-Id": body["session_id"]}, "user_id": body["user_id"], "email": email}

    return _signup


@pytest.fixture
def user(signup):
    return signup()


def wait_for_job(client, headers, job_id, timeout=60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}", headers=headers).json()
        if job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"upload job {job_id} did not finish")


@pytest.fixture
def upload(client):
    """Upload a file through /upload_note and wait for the background job to finish."""

    def _upload(headers, name: str, data: bytes, folder: str = "", expect: str = "done") -> dict:
        r = client.post("/upload_note", data={"path": folder}, files={"file": (name, data)}, headers=headers)
        assert r.status_code == 202, r.text
        job = wait_for_job(client, headers, r.json()["job_id"])
        assert job["status"] == expect, job
        return job

    return _upload


def tree_paths(client, headers) -> set[str]:
    from backend.vfs_tree import flatten_file_paths

    return set(flatten_file_paths(client.get("/list_tree", headers=headers).json()["tree"]))
