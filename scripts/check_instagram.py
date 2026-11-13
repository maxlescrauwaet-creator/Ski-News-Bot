"""Verify the Meta credentials WITHOUT publishing anything.

Usage:  IG_USER_ID=... IG_ACCESS_TOKEN=... PYTHONPATH=src python scripts/check_instagram.py
"""
import os, sys
from skibot.config import load_settings
from skibot.instagram import Instagram

s = load_settings()
ig = Instagram(os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"], s["graph"]["host"], s["graph"]["version"])
try:
    print("Connected as @" + ig.check_credentials())
    print("Recent posts visible:", len(ig.recent_posts(5)))
except Exception as exc:  # noqa: BLE001
    print("FAILED:", exc); sys.exit(1)
