"""Publish to a Discord channel through a webhook (no developer account, no app review, free)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path

import requests

from .content import shorten

log = logging.getLogger("skibot")
ALLOWED = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")


class DiscordError(RuntimeError):
    pass


def build_payload(post: dict, filename: str, brand: str) -> dict:
    parts = [post.get("summary", "")]
    if post.get("facts"):
        parts.append("\n".join(f"▪ {f}" for f in post["facts"]))
    footer = [f"Source: {post['source']}" if post["kind"] == "news" else post["kicker"]]
    if post.get("photo"):
        footer.append(f"Photo: {post['photo']['credit']} ({post['photo']['license']}), Wikimedia Commons")
    embed = {
        "title": shorten(post["headline"], 250),
        "description": "\n\n".join(p for p in parts if p)[:4000],
        "color": 0xFF5A36,
        "image": {"url": f"attachment://{filename}"},
        "footer": {"text": " · ".join(footer)[:2000]},
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if post.get("url"):
        embed["url"] = post["url"]  # title links to the original article
    return {"username": brand[:80], "embeds": [embed]}


def send(webhook_url: str, post: dict, image_path: Path, brand: str) -> dict:
    if not webhook_url.startswith(ALLOWED):
        raise DiscordError("DISCORD_WEBHOOK_URL does not look like a Discord webhook URL")
    payload = build_payload(post, image_path.name, brand)
    data = image_path.read_bytes()
    last = ""
    for attempt in range(1, 4):
        try:
            r = requests.post(webhook_url, params={"wait": "true"}, timeout=60,
                              data={"payload_json": json.dumps(payload)},
                              files={"files[0]": (image_path.name, data, "image/jpeg")})
        except requests.RequestException as exc:
            last = f"network error: {exc}"
        else:
            if r.status_code < 300:
                j = r.json()
                return {"message_id": j.get("id"), "channel_id": j.get("channel_id")}
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code == 429:  # rate limited: nothing was posted, safe to retry
                time.sleep(min(float(r.json().get("retry_after", 5)), 30))
                continue
            raise DiscordError(last)  # other HTTP errors: do not retry blindly (could double-post)
        log.warning("Discord attempt %s/3 failed: %s", attempt, last)
        time.sleep(5 * attempt)
    raise DiscordError(last)
