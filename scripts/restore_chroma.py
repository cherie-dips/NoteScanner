"""
Restore a NoteScanner backup (made by scripts/backup_chroma.py) into the Chroma database configured
in the environment / .env.

    python scripts/restore_chroma.py notescanner-backup-20261002T020000Z.jsonl.gz --dry-run
    python scripts/restore_chroma.py notescanner-backup-20261002T020000Z.jsonl.gz

By default it refuses to write into a collection that already has records, so it can't mix a backup
into live data by accident. --force writes anyway: records from the backup replace records with the
same id, and other records already in the collection are kept.

Point it at a separate, empty Chroma database first to check a backup can actually be restored.
"""
import argparse
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backup_chroma import FORMAT  # noqa: E402

FLUSH_EVERY = 250  # records per write (Chroma Cloud allows at most 300)


class RestoreRefused(Exception):
    """The target already has data in some collections and --force wasn't given."""

    def __init__(self, collections: list[str]):
        self.collections = collections
        super().__init__(
            "These collections already have records: " + ", ".join(collections)
            + ". Restore into an empty database, or use --force to write anyway."
        )


def _read_lines(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for n, line in enumerate(f, start=1):
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Line {n} of {path} is not valid JSON ({e}); the file may be damaged.") from e


def read_summary(path: str | Path) -> dict:
    """Check the file is a complete NoteScanner backup and return {"collections": {name: (metadata, count)}}."""
    path = Path(path)
    header = footer = None
    collections: dict[str, tuple] = {}
    records = 0
    for item in _read_lines(path):
        kind = item.get("type")
        if kind == "header":
            header = item
        elif kind == "collection":
            collections[item["name"]] = (item.get("metadata"), int(item.get("count") or 0))
        elif kind == "record":
            records += 1
        elif kind == "footer":
            footer = item
    if not header or header.get("format") != FORMAT:
        raise ValueError(f"{path} is not a NoteScanner backup file.")
    if not footer or footer.get("records") != records:
        raise ValueError(f"{path} is incomplete (the backup was cut off); don't restore it.")
    return {"created_at": header.get("created_at"), "collections": collections, "records": records}


def _flush(col, batch: list[dict]) -> None:
    from backend.chroma_store import write_records

    if not batch:
        return
    ids = [r["id"] for r in batch]
    docs = [r.get("document") for r in batch]
    metas = [r.get("metadata") for r in batch]
    embs = [r.get("embedding") for r in batch]
    write_records(
        col,
        "upsert",
        ids,
        documents=None if all(d is None for d in docs) else [d if d is not None else "" for d in docs],
        # Chroma accepts None for a record without metadata, but rejects an empty dict.
        metadatas=None if all(not m for m in metas) else [m or None for m in metas],
        embeddings=None if all(e is None for e in embs) else embs,
    )


def restore(in_path: str | Path, client=None, force: bool = False, dry_run: bool = False, log=print) -> dict:
    """Restore the backup. Returns {"collections": {name: count}, "records": total, "dry_run": bool}."""
    from backend.chroma_store import get_global_client
    from chromadb.errors import NotFoundError

    in_path = Path(in_path)
    summary = read_summary(in_path)
    client = client or get_global_client()

    def existing(name):
        try:
            return client.get_collection(name=name)
        except NotFoundError:
            return None

    # Check every target collection before writing anything.
    busy = []
    for name in summary["collections"]:
        col = existing(name)
        if col is not None and col.count() > 0:
            busy.append(name)
    if busy and not force:
        raise RestoreRefused(busy)

    planned = {name: count for name, (_meta, count) in summary["collections"].items()}
    if dry_run:
        for name, count in planned.items():
            log(f"  would restore {name}: {count} records{' (collection has data; --force)' if name in busy else ''}")
        log(f"Dry run: {summary['records']} records in {len(planned)} collections from a backup made {summary['created_at']}")
        return {"collections": planned, "records": summary["records"], "dry_run": True}

    cols = {}
    for name, (meta, _count) in summary["collections"].items():
        cols[name] = existing(name) or client.create_collection(name=name, metadata=meta or None)

    written: dict[str, int] = {name: 0 for name in summary["collections"]}
    batch: list[dict] = []
    current = None
    for item in _read_lines(in_path):
        if item.get("type") != "record":
            continue
        name = item["collection"]
        if name != current or len(batch) >= FLUSH_EVERY:
            if current is not None:
                _flush(cols[current], batch)
                written[current] += len(batch)
            batch, current = [], name
        batch.append(item)
    if current is not None:
        _flush(cols[current], batch)
        written[current] += len(batch)

    for name, count in written.items():
        log(f"  {name}: {count} records")
    total = sum(written.values())
    log(f"Restored {total} records into {len(written)} collections from a backup made {summary['created_at']}")
    return {"collections": written, "records": total, "dry_run": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore a NoteScanner backup into the configured Chroma database.")
    parser.add_argument("backup_file", help="file made by scripts/backup_chroma.py (.jsonl.gz, decrypted)")
    parser.add_argument("--force", action="store_true", help="write into collections that already have records")
    parser.add_argument("--dry-run", action="store_true", help="check the file and show what would be restored")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    try:
        restore(args.backup_file, force=args.force, dry_run=args.dry_run)
    except RestoreRefused as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Restore failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
