"""Broadcast the daily post to every server that ran /ski setup (subscriber list lives in the Cloudflare Worker)."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

from .discord_hook import build_payload

log = logging.getLogger("skibot")
API = "https://discord.com/api/v10"
GONE_CODES = {10003, 10004}  # Unknown Channel / Unknown Guild -> unsubscribe automatically


def fetch_subscribers(worker_url: str, secret: str) -> list[dict]:
    r = requests.get(f"{worker_url.rstrip('/')}/subscribers", headers={"Authorization": f"Bearer {secret}"}, timeout=30)
    r.raise_for_status()
    return r.json().get("subscribers", [])


def _unsubscribe(worker_url: str, secret: str, guild_id: str) -> None:
    try:
        requests.post(f"{worker_url.rstrip('/')}/unsubscribe", headers={"Authorization": f"Bearer {secret}"},
                      json={"guild_id": guild_id}, timeout=20)
    except requests.RequestException:
        pass


def _send_one(token: str, channel_id: str, payload: dict, name: str, data: bytes) -> tuple[bool, int | None, str]:
    for attempt in range(1, 4):
        try:
            r = requests.post(f"{API}/channels/{channel_id}/messages", headers={"Authorization": f"Bot {token}"},
                              data={"payload_json": json.dumps(payload)}, files={"files[0]": (name, data, "image/jpeg")}, timeout=60)
        except requests.RequestException as exc:
            return False, None, f"network: {exc}"
        if r.status_code < 300:
            return True, None, ""
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 429:  # rate limited: message was not created, safe to retry
            time.sleep(min(float(body.get("retry_after", 2)), 30))
            continue
        return False, body.get("code"), f"HTTP {r.status_code} code {body.get('code')}: {body.get('message', '')[:100]}"
    return False, None, "rate limited repeatedly"


def broadcast(post: dict, image_path: Path, brand: str, worker_url: str, secret: str, token: str) -> dict:
    subs = fetch_subscribers(worker_url, secret)
    data = image_path.read_bytes()
    delivered, failed, removed = 0, 0, 0
    for s in subs:
        payload = build_payload(post, image_path.name, brand, bool(s.get("mention_everyone")))
        ok, code, err = _send_one(token, s["channel_id"], payload, image_path.name, data)
        if ok:
            delivered += 1
        else:
            failed += 1
            log.warning("Server %s: %s", s["guild_id"], err)
            if code in GONE_CODES:
                _unsubscribe(worker_url, secret, s["guild_id"])
                removed += 1
        time.sleep(0.4)  # stay far below Discord's global rate limit
    return {"subscribers": len(subs), "delivered": delivered, "failed": failed, "removed": removed}
