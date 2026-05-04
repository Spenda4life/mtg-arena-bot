"""
Downloads Scryfall's bulk card data (oracle-cards) and saves it to
data/scryfall_cards.json for use by GrpDatabase when Arena's own
data files aren't present.

Usage:
    python tools/download_card_data.py

Scryfall bulk data is released under CC BY 4.0.
Only downloads if the file is missing or older than 7 days.
"""
import json
import sys
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path(__file__).parent.parent / "data"
OUT_FILE = DATA_DIR / "scryfall_cards.json"
BULK_INDEX_URL = "https://api.scryfall.com/bulk-data"
MAX_AGE_DAYS = 7


def _needs_update() -> bool:
    if not OUT_FILE.exists():
        return True
    age = datetime.now() - datetime.fromtimestamp(OUT_FILE.stat().st_mtime)
    return age > timedelta(days=MAX_AGE_DAYS)


def _fetch_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "mtg-arena-bot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _download_with_progress(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "mtg-arena-bot/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk = 64 * 1024
        with open(dest, "wb") as f:
            while True:
                data = resp.read(chunk)
                if not data:
                    break
                f.write(data)
                downloaded += len(data)
                if total:
                    pct = downloaded / total * 100
                    print(f"\r  {pct:5.1f}%  {downloaded // 1024 // 1024} MB", end="", flush=True)
    print()


def main() -> None:
    if not _needs_update():
        age = datetime.now() - datetime.fromtimestamp(OUT_FILE.stat().st_mtime)
        print(f"Card data is fresh ({age.days}d old). Delete {OUT_FILE} to force re-download.")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("Fetching Scryfall bulk-data index...")
    index = _fetch_json(BULK_INDEX_URL)
    entries = index.get("data", [])

    # Prefer 'default_cards' which includes Arena IDs
    entry = next(
        (e for e in entries if e.get("type") == "default_cards"),
        next((e for e in entries if e.get("type") == "oracle_cards"), None),
    )
    if not entry:
        print("ERROR: Could not find a suitable bulk data entry.")
        sys.exit(1)

    download_url = entry["download_uri"]
    size_mb = entry.get("size", 0) // 1024 // 1024
    print(f"Downloading {entry['type']} ({size_mb} MB)...")
    print(f"  URL: {download_url}")

    _download_with_progress(download_url, OUT_FILE)
    print(f"Saved to {OUT_FILE}")

    # Quick sanity check
    with open(OUT_FILE) as f:
        cards = json.load(f)
    arena_cards = [c for c in cards if c.get("arena_id")]
    print(f"  Total cards: {len(cards)}, Arena-playable: {len(arena_cards)}")


if __name__ == "__main__":
    main()
