"""Paths, JSON helpers, settings and run log."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "docs" / "data"
POSTS_DIR = ROOT / "docs" / "posts"
OUT_DIR = ROOT / "out"
CACHE_DIR = ROOT / ".cache"

log = logging.getLogger("skibot")

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "post_time_local": "18:00",
    "timezone": "Europe/Brussels",
    "brand_name": "Snowline Daily",
    "handle": "@your_instagram_handle",
    "max_age_hours": 96,
    "min_score": 5,
    "dedupe_days": 90,
    "profile_cooldown_days": 120,
    "fact_cooldown_days": 300,
    "allow_share_alike_photos": True,
    "hashtag_limit": 12,
    "graph": {"host": "graph.facebook.com", "version": "v23.0"},
    "llm": {"enabled": False, "model": "openai/gpt-4.1-mini"},
    "publishers": {"instagram": {"enabled": False}, "discord": {"enabled": True}},
}


def load_json(path: Path | str, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        log.error("Invalid JSON in %s: %s", path, exc)
        return default


def save_json(path: Path | str, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    return deep_merge(DEFAULTS, load_json(CONFIG_DIR / "settings.json", {}) or {})


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


class RunLog:
    """Collects human-readable log lines for the dashboard (docs/data/runs.json)."""

    def __init__(self) -> None:
        self.started = utcnow()
        self.lines: list[dict] = []
        self.status = "running"
        self.summary = ""

    def add(self, level: str, msg: str) -> None:
        getattr(log, level.lower() if level.lower() != "warn" else "warning")(msg)
        self.lines.append({"t": iso(utcnow()), "level": level.upper(), "msg": msg[:500]})

    def info(self, msg: str) -> None:
        self.add("INFO", msg)

    def warn(self, msg: str) -> None:
        self.add("WARN", msg)

    def error(self, msg: str) -> None:
        self.add("ERROR", msg)

    def save(self, keep: int = 150) -> None:
        runs = load_json(DATA_DIR / "runs.json", []) or []
        runs.append({
            "started": iso(self.started),
            "finished": iso(utcnow()),
            "status": self.status,
            "summary": self.summary,
            "lines": self.lines[-60:],
        })
        save_json(DATA_DIR / "runs.json", runs[-keep:])
