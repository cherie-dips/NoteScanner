"""
Back up the whole NoteScanner Chroma database to one compressed file.

    python scripts/backup_chroma.py                       # notescanner-backup-<UTC time>.jsonl.gz
    python scripts/backup_chroma.py --out my-backup.jsonl.gz

Connection settings come from the environment / .env (CHROMA_API_KEY, CHROMA_TENANT,
CHROMA_DATABASE for Chroma Cloud, or CHROMA_HOST / CHROMA_PORT for a self-hosted server).

The file is gzip-compressed JSON lines:
  {"type": "header", ...}
  {"type": "collection", "name": ..., "metadata": ..., "count": N}
  {"type": "record", "collection": ..., "id": ..., "document": ..., "metadata": ..., "embedding": [...]}
  ...
  {"type": "footer", "collections": N, "records": N}

Short-lived data (login sessions, password-reset tokens, OneNote sign-in state) and OneNote tokens
(secrets; students can reconnect) are left out. The file contains password hashes and students'
notes: keep it private, and encrypt it before storing it anywhere shared.
"""
import argparse
import gzip
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FORMAT = "notescanner-chroma-backup"
FORMAT_VERSION = 1
SKIPPED_COLLECTIONS = frozenset({"sessions", "password_resets", "onenote_oauth_states", "onenote_tokens"})
LIST_PAGE = 100


def _list_collection_names(client) -> list[str]:
    """Every collection name, paging where the client supports it."""
    names: list[str] = []
    try:
        offset = 0
        while True:
            page = list(client.list_collections(limit=LIST_PAGE, offset=offset))
            names.extend(c if isinstance(c, str) else c.name for c in page)
            if len(page) < LIST_PAGE:
                break
            offset += LIST_PAGE
    except TypeError:  # a client without paging arguments
        names = [c if isinstance(c, str) else c.name for c in client.list_collections()]
    return sorted(set(names))


def _plain(value):
    """numpy arrays / numbers → plain JSON types."""
    if value is None:
        return None
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def backup(out_path: str | Path, client=None, log=print) -> dict:
    """Write the backup file and return {"collections": {name: count}, "records": total, "path": ...}."""
    from backend.chroma_store import get_all, get_global_client

    client = client or get_global_client()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".partial")
    counts: dict[str, int] = {}
    started = time.time()

    with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "header",
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }) + "\n")
        for name in _list_collection_names(client):
            if name in SKIPPED_COLLECTIONS:
                continue
            col = client.get_collection(name=name)
            res = get_all(col, include=["documents", "metadatas", "embeddings"])
            ids = res["ids"]
            docs = res.get("documents") or [None] * len(ids)
            metas = res.get("metadatas") or [None] * len(ids)
            embs = res.get("embeddings")
            embs = [None] * len(ids) if embs is None else embs
            f.write(json.dumps({
                "type": "collection",
                "name": name,
                "metadata": getattr(col, "metadata", None) or None,
                "count": len(ids),
            }, ensure_ascii=False) + "\n")
            for i, rid in enumerate(ids):
                f.write(json.dumps({
                    "type": "record",
                    "collection": name,
                    "id": rid,
                    "document": docs[i],
                    "metadata": metas[i],
                    "embedding": _plain(embs[i]),
                }, ensure_ascii=False) + "\n")
            counts[name] = len(ids)
            log(f"  {name}: {len(ids)} records")
        f.write(json.dumps({"type": "footer", "collections": len(counts), "records": sum(counts.values())}) + "\n")

    # Only a complete file gets the final name, so a crash never leaves a half backup that looks whole.
    tmp_path.replace(out_path)
    total = sum(counts.values())
    log(f"Backed up {total} records from {len(counts)} collections to {out_path} in {time.time() - started:.1f}s")
    return {"collections": counts, "records": total, "path": str(out_path)}


def default_out_name() -> str:
    return f"notescanner-backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl.gz"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the NoteScanner Chroma database to one file.")
    parser.add_argument("--out", help="output file (default: notescanner-backup-<UTC time>.jsonl.gz)")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    try:
        backup(args.out or default_out_name())
    except Exception as e:
        print(f"Backup failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
