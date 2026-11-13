from __future__ import annotations

import argparse
import logging
import os
import sys

from . import images
from .config import OUT_DIR
from .pipeline import run


def selftest() -> int:
    """Render sample graphics offline so layouts can be checked without any network or credentials."""
    s = {"brand_name": "Snowline Daily", "handle": "@your_handle"}
    base = {"kicker": "Snowmaking", "headline": "Resort adds 40 new snow guns ahead of the winter season",
            "blurb": "The upgrade covers the main beginner slopes and the lower mountain.", "footer_left": "Source: Example",
            "source_label": "Example", "credit_line": "Photo: Example Author · CC BY 4.0 · Wikimedia Commons"}
    from PIL import Image
    ph = images.mountain_scene("photo-stand-in")
    OUT_DIR.mkdir(exist_ok=True)
    for name, kind, key, photo in [("news_photo", "news", "n2", ph), ("news_split", "news", "n1", ph),
                                   ("typographic", "fact", "fact:demo", None)]:
        images.save_jpeg(images.render({**base, "kind": kind, "key": key}, photo, s), OUT_DIR / f"selftest_{name}.jpg")
    print("Wrote 3 sample images to out/")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="skibot")
    ap.add_argument("--dry-run", action="store_true", help="build a post and write it to out/ without publishing")
    ap.add_argument("--force", action="store_true", help="ignore the posting-time and already-posted-today checks")
    ap.add_argument("--only", choices=["news", "profile", "fact", "recycle"], help="test a single content stage")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.selftest:
        return selftest()
    return run(dry_run=a.dry_run or os.getenv("DRY_RUN") == "1", force=a.force or os.getenv("FORCE") == "1", only=a.only)


if __name__ == "__main__":
    sys.exit(main())
