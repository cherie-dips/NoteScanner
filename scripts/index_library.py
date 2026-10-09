"""
Index the shared course library: the course PDFs behind SDE-Prep's Notes tab (see backend/library.py).

    python scripts/index_library.py --dry-run     # what would be read, and how many pieces need OCR
    python scripts/index_library.py               # read and index new or changed PDFs
    python scripts/index_library.py --only plaksha-university/discrete-maths --limit 3
    python scripts/index_library.py --force       # re-read every PDF, even unchanged ones

Each PDF is read once and shared by every student. Unchanged PDFs (same eTag) are skipped, so
running it again only reads what's new. Handwritten and scanned pages are read with Sarvam Vision
when SARVAM_API_KEY is set (about ₹0.5 per piece at Sarvam's listed price; --dry-run counts the
pieces first), otherwise with Tesseract. LIBRARY_OCR=tesseract forces the free reader.

Connection settings come from the environment / .env, like the server: CHROMA_API_KEY,
CHROMA_TENANT, CHROMA_DATABASE (Chroma Cloud) or CHROMA_HOST / CHROMA_PORT.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SARVAM_LISTED_PRICE_PER_PAGE = 0.5  # INR, https://docs.sarvam.ai/api-reference-docs/pricing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="count what would be read; change nothing")
    parser.add_argument("--only", action="append", default=[], metavar="PREFIX", help="only paths starting with this (repeatable)")
    parser.add_argument("--limit", type=int, default=None, help="read at most this many PDFs")
    parser.add_argument("--force", action="store_true", help="re-read PDFs even when unchanged")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    from backend import library, settings

    if not settings.LIBRARY_ENABLED:
        print("LIBRARY_ENABLED is off; nothing to do.")
        return 1
    started = time.time()
    summary = library.sync(dry_run=args.dry_run, only=args.only, limit=args.limit, force=args.force, log=print)
    print(json.dumps(summary, indent=2))
    if summary["ocr"] == "sarvam":
        pieces = summary["ocr_pieces"] if args.dry_run else summary["sarvam_pieces"]
        price = settings.AI_PRICE_PER_PAGE or SARVAM_LISTED_PRICE_PER_PAGE
        verb = "would cost" if args.dry_run else "cost"
        print(f"Sarvam Vision: {pieces} piece(s) {verb} about {settings.AI_PRICE_CURRENCY} {pieces * price:.2f}")
    elif args.dry_run and summary["ocr_pieces"]:
        print(f"{summary['ocr_pieces']} piece(s) would be read with Tesseract (free; weaker on handwriting).")
    print(f"Done in {time.time() - started:.0f}s.")
    return 0 if not summary["failed"] else 2


if __name__ == "__main__":
    sys.exit(main())
