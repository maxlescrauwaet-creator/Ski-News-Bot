"""Headline, summary, facts, captions and hashtags.

Everything is derived from the source text. The optional free LLM (GitHub Models) may only
rephrase the supplied text, and its output is rejected unless every number and proper noun
also appears in the source.
"""
from __future__ import annotations

import json
import logging
import os
import re

import requests

from .news import Item, clean_text, norm_title

log = logging.getLogger("skibot")
CAPTION_LIMIT = 2200


def shorten(text: str, n: int) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0].rstrip(" ,;:-–")
    return cut + "…"


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean_text(text)) if len(s.strip()) > 25]


def make_headline(title: str, max_len: int = 110) -> str:
    t = clean_text(title)
    if t.isupper():
        t = t.title()
    return shorten(t, max_len)


def extract_facts(item: Item, limit: int = 3) -> list[str]:
    """Pick sentences from the source that carry concrete figures (numbers, money, dates)."""
    num = re.compile(r"(\d[\d,.]*\s?(?:cm|mm|in|inches|m|km|ft|feet|metres|meters|%|million|billion|lifts?|runs?|pistes?|trails?|days?)\b|[$€£]\s?\d)", re.I)
    out = []
    head = norm_title(item.title)
    for s in sentences(item.summary):
        if num.search(s) and norm_title(s) != head:
            out.append(shorten(s, 160))
        if len(out) >= limit:
            break
    return out


def make_summary(item: Item, max_len: int = 300) -> str:
    out = ""
    head = norm_title(item.title)
    for s in sentences(item.summary):
        n = norm_title(s)
        if n == head or head in n and len(n) < len(head) + 15:
            continue
        if len(out) + len(s) > max_len:
            break
        out = f"{out} {s}".strip()
    return out


# ---------------------------------------------------------------- optional free LLM
def _numbers(s: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", s))


def _caps(s: str) -> set[str]:
    return {w.lower() for w in re.findall(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-zA-Z]{3,}\b", s)}


def grounded(output: str, source: str) -> bool:
    """True only if every figure and mid-sentence proper noun in `output` occurs in `source`."""
    src = source.lower()
    if not _numbers(output) <= _numbers(source):
        return False
    return all(w in src for w in _caps(output))


def llm_rewrite(item: Item, settings: dict) -> dict | None:
    cfg = settings.get("llm", {})
    token = os.getenv("GITHUB_TOKEN")
    if not cfg.get("enabled") or not token or not item.summary:
        return None
    source = f"{item.title}. {item.summary}"
    prompt = (
        "You write for an Instagram ski-industry news account. Using ONLY the source text below, return JSON "
        '{"headline": "<max 90 chars>", "summary": "<max 280 chars, 1-2 sentences, plain English>"}. '
        "Do not add any fact, number, name, date or quote that is not in the source. "
        "If the source is too thin, return {\"headline\": \"\", \"summary\": \"\"}.\n\nSOURCE:\n" + source
    )
    try:
        r = requests.post(
            "https://models.github.ai/inference/chat/completions",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "Accept": "application/vnd.github+json"},
            json={"model": cfg.get("model", "openai/gpt-4.1-mini"), "temperature": 0.2, "max_tokens": 300,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=45,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        head, summ = clean_text(data.get("headline")), clean_text(data.get("summary"))
        if not head or not summ or len(head) > 100 or len(summ) > 320:
            return None
        if not (grounded(head, source) and grounded(summ, source)):
            log.warning("LLM output rejected: not grounded in the source text.")
            return None
        return {"headline": head, "summary": summ}
    except Exception as exc:  # noqa: BLE001 - LLM is optional; fall back silently
        log.warning("LLM step skipped (%s: %s)", type(exc).__name__, str(exc)[:120])
        return None


# ---------------------------------------------------------------- hashtags & captions
def tag(s: str) -> str:
    return "#" + re.sub(r"[^a-z0-9]", "", s.lower())


def hashtags(topic_tags: list[str], extra: list[str], limit: int) -> str:
    base = ["skiing", "skiresort", "snow", "wintersports", "mountains", "skinews"]
    seen, out = set(), []
    for t in [*extra, *topic_tags, *base]:
        h = tag(t)
        if len(h) > 1 and h not in seen:
            seen.add(h)
            out.append(h)
    return " ".join(out[:limit])


def photo_line(photo: dict | None) -> str:
    if not photo:
        return ""
    return f"Photo: {photo['credit']} ({photo['license']}), Wikimedia Commons. Illustrative image."


def caption_news(post: dict, settings: dict) -> str:
    parts = [post["headline"]]
    if post.get("summary"):
        parts.append(post["summary"])
    if post.get("facts"):
        parts.append("\n".join(f"▪ {f}" for f in post["facts"]))
    parts.append(f"Source: {post['source']}")
    if post.get("photo"):
        parts.append(photo_line(post["photo"]))
    parts.append(post["hashtags"])
    return _fit("\n\n".join(parts))


def caption_profile(post: dict, settings: dict) -> str:
    parts = [f"Resort spotlight: {post['headline']}", post["summary"], post["hashtags"]]
    if post.get("photo"):
        parts.insert(2, photo_line(post["photo"]))
    return _fit("\n\n".join(parts))


def caption_fact(post: dict, settings: dict) -> str:
    parts = [f"Did you know? {post['headline']}", post["summary"]]
    if post.get("photo"):
        parts.append(photo_line(post["photo"]))
    parts.append(post["hashtags"])
    return _fit("\n\n".join(parts))


def _fit(caption: str) -> str:
    return caption if len(caption) <= CAPTION_LIMIT else caption[: CAPTION_LIMIT - 1].rsplit(" ", 1)[0] + "…"
