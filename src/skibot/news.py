"""News discovery: RSS/Atom collection, relevance scoring, verification and de-duplication."""
from __future__ import annotations

import datetime as dt
import difflib
import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .config import CONFIG_DIR, load_json, parse_iso, utcnow

log = logging.getLogger("skibot")
UA = "Mozilla/5.0 (compatible; SkiNewsBot/1.0; +https://github.com)"

STOP = set(
    "the and for with from that this have has had will would could should about after over into more than "
    "says said their they them been being were what when where which while also just only your you our new".split()
)


@dataclass
class Item:
    title: str
    link: str
    summary: str
    source: str
    published: dt.datetime | None
    trust: int
    domain: str
    feed: str
    # filled by scoring
    topic: str = "general"
    score: float = 0.0
    corroboration: int = 0
    resort: str | None = None
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- parsing helpers
def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_date(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        d = None
    if d is None:
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def real_link(link: str) -> str:
    """Bing News RSS wraps publisher URLs in a redirect: extract the original."""
    if "bing.com/news/apiclick" in link:
        qs = parse_qs(urlparse(link).query)
        if qs.get("url"):
            return unquote(qs["url"][0])
    return link


def parse_feed(content: bytes) -> list[dict]:
    root = ET.fromstring(content)
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        row: dict = {}
        for ch in el:
            name = _local(ch.tag)
            if name == "link":
                href = ch.attrib.get("href")
                if href and ch.attrib.get("rel", "alternate") == "alternate":
                    row["link"] = href
                elif ch.text and "link" not in row:
                    row["link"] = ch.text.strip()
            elif name in ("title", "description", "summary", "source"):
                row.setdefault(name, "".join(ch.itertext()))
            elif name in ("encoded", "content"):
                row.setdefault("content", "".join(ch.itertext()))
            elif name in ("pubdate", "published", "updated", "date"):
                row.setdefault("date", ch.text)
        if row.get("title") and row.get("link"):
            out.append(row)
    return out


def fetch_feed(feed: dict, trusted: set[str]) -> list[Item]:
    url = feed["url"]
    if url.startswith("file:"):
        content = Path(url[5:]).read_bytes()
    else:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
        r.raise_for_status()
        content = r.content
    items = []
    for row in parse_feed(content):
        link = real_link(row["link"].strip())
        dom = domain_of(link)
        title = clean_text(row["title"])
        if feed.get("bing"):
            title = re.sub(r"\s[-–|]\s[^-–|]{2,40}$", "", title)
        summary = clean_text(row.get("description") or row.get("summary") or row.get("content"))
        if len(summary) > 1200:
            summary = summary[:1200]
        trust = int(feed.get("trust", 1))
        if feed.get("bing") and dom in trusted:
            trust = 2
        source = clean_text(row.get("source")) if feed.get("bing") and row.get("source") else feed["name"]
        items.append(Item(title=title, link=link, summary=summary, source=source or dom,
                          published=parse_date(row.get("date")), trust=trust, domain=dom, feed=feed["name"]))
    return items


def fetch_all(cfg: dict) -> tuple[list[Item], list[str]]:
    trusted = set(cfg.get("trusted_domains", []))
    items: list[Item] = []
    problems: list[str] = []
    for feed in cfg.get("feeds", []):
        if not feed.get("enabled", True):
            continue
        try:
            got = fetch_feed(feed, trusted)
            log.info("Feed %-28s %3d items", feed["name"], len(got))
            items.extend(got)
        except Exception as exc:  # noqa: BLE001 - one bad feed must never stop the run
            problems.append(f"{feed['name']}: {type(exc).__name__}: {str(exc)[:120]}")
    return items, problems


# ---------------------------------------------------------------- scoring
def tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 3 and w not in STOP}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def norm_title(t: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", t.lower()))


def similar(a: str, b: str) -> bool:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return False
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.72 or jaccard(tokens(a), tokens(b)) >= 0.6


def _count(patterns_or_words, text: str, regex: bool = False) -> int:
    n = 0
    for p in patterns_or_words:
        pat = p if regex else r"\b" + re.escape(p) + r"\b"
        if re.search(pat, text, re.I):
            n += 1
    return n


def find_resort(text: str, resorts: list[dict]) -> str | None:
    for r in resorts:
        for alias in r.get("aliases", [r["name"]]):
            if re.search(r"\b" + re.escape(alias) + r"\b", text):
                return r["name"]
    return None


def score_item(item: Item, topics_cfg: dict, resorts: list[dict]) -> bool:
    """Fill topic/score. Returns False when the item is off-topic or blocked."""
    text = f"{item.title}. {item.summary}"
    if _count(topics_cfg["blocklist"], text, regex=True):
        return False
    if not _count(topics_cfg["core_patterns"], text, regex=True):
        return False
    best, best_score = "general", 0.0
    for name, t in topics_cfg["topics"].items():
        s = 3 * _count(t["keywords"], item.title) + 1 * _count(t["keywords"], item.summary)
        if name == "general":
            s *= 0.5
        if s > best_score:
            best, best_score = name, s
    item.topic = best
    item.resort = find_resort(text, resorts)
    core_hits = _count(topics_cfg["core_patterns"], item.title, regex=True)
    item.score = best_score + 1.5 * core_hits + (2 if item.resort else 0)
    return True


def select_candidates(items: list[Item], settings: dict, history: list[dict], topics_cfg: dict,
                      resorts: list[dict]) -> list[Item]:
    now = utcnow()
    max_age = dt.timedelta(hours=settings["max_age_hours"])
    horizon = now - dt.timedelta(days=settings["dedupe_days"])
    seen_urls = {h.get("url") for h in history if h.get("url")}
    recent_titles = [h.get("title", "") for h in history
                     if h.get("posted_at") and parse_iso(h["posted_at"]) >= horizon and h.get("title")]

    pool: list[Item] = []
    for it in items:
        if not it.published or it.published > now + dt.timedelta(hours=12) or now - it.published > max_age:
            continue  # undated, future-dated or stale items are never published
        if it.link in seen_urls or any(similar(it.title, t) for t in recent_titles):
            continue
        if not score_item(it, topics_cfg, resorts):
            continue
        pool.append(it)

    # corroboration: the same story reported by other domains
    for it in pool:
        tk = tokens(it.title)
        doms = {o.domain for o in pool if o is not it and o.domain != it.domain and jaccard(tk, tokens(o.title)) >= 0.45}
        it.corroboration = len(doms)

    eligible = []
    for it in pool:
        verified = it.trust >= 2 or it.corroboration >= 1
        if not verified:
            continue
        age_h = (now - it.published).total_seconds() / 3600
        it.score += it.trust + 2 * min(it.corroboration, 3) + max(0.0, 3 - age_h / 24)
        if it.score >= settings["min_score"]:
            eligible.append(it)

    # drop near-duplicates inside this batch, keeping the highest score
    eligible.sort(key=lambda i: i.score, reverse=True)
    unique: list[Item] = []
    for it in eligible:
        if not any(similar(it.title, u.title) for u in unique):
            unique.append(it)
    return unique


# ---------------------------------------------------------------- page enrichment
def _meta(page: str) -> dict[str, str]:
    out = {}
    for tag in re.findall(r"<meta\s+[^>]*>", page, re.I):
        attrs = {k.lower(): v.strip("\"'") for k, v in re.findall(r"([a-zA-Z:_-]+)\s*=\s*(\"[^\"]*\"|'[^']*')", tag)}
        key = attrs.get("property") or attrs.get("name")
        if key and attrs.get("content"):
            out.setdefault(key.lower(), html.unescape(attrs["content"]))
    return out


def enrich(item: Item) -> bool:
    """Confirm the article page is reachable and enrich a thin summary with the page's own description.

    Only text (meta description) is used. The page's image is never used.
    """
    if item.link.startswith("file:"):
        return True
    try:
        r = requests.get(item.link, headers={"User-Agent": UA}, timeout=15, allow_redirects=True)
    except requests.RequestException as exc:
        log.warning("Cannot open %s: %s", item.link, exc)
        return False
    if r.status_code >= 400:
        log.warning("Article returned HTTP %s: %s", r.status_code, item.link)
        return False
    meta = _meta(r.text[:400_000])
    desc = clean_text(meta.get("og:description") or meta.get("description") or meta.get("twitter:description"))
    if len(item.summary) < 140 and len(desc) > len(item.summary):
        item.summary = desc[:900]
    item.link = r.url
    item.domain = domain_of(r.url)
    return True
