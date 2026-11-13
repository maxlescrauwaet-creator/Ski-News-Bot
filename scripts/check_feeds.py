"""Check which configured feeds respond and how many relevant items they currently yield.

Usage:  PYTHONPATH=src python scripts/check_feeds.py
"""
from skibot import news
from skibot.config import CONFIG_DIR, DATA_DIR, load_json

cfg = load_json(CONFIG_DIR / "feeds.json", {})
topics = load_json(CONFIG_DIR / "topics.json", {})
resorts = (load_json(DATA_DIR / "resorts.json", {}) or {}).get("resorts", [])
trusted = set(cfg.get("trusted_domains", []))
for feed in cfg["feeds"]:
    try:
        items = news.fetch_feed(feed, trusted)
        rel = sum(1 for i in items if news.score_item(i, topics, resorts))
        dated = sum(1 for i in items if i.published)
        print(f"OK    {feed['name']:<30} {len(items):3d} items, {dated:3d} dated, {rel:3d} relevant")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  {feed['name']:<30} {type(exc).__name__}: {str(exc)[:80]}")
