"""Orchestration: decide if a post is due, build it (news -> profile -> fact -> recycle), publish, record."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import re
import sys
from zoneinfo import ZoneInfo

from . import content, discord_hook, images, news
from .config import (CONFIG_DIR, DATA_DIR, OUT_DIR, POSTS_DIR, RunLog, iso, load_json, load_settings, parse_iso,
                     save_json, utcnow)
from .gitops import commit_and_push
from .instagram import Instagram, InstagramError, wait_for_url

log = logging.getLogger("skibot")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def _days_since(history: list[dict], key: str) -> float | None:
    times = [parse_iso(h["posted_at"]) for h in history if h.get("key") == key and h.get("posted_at")]
    return (utcnow() - max(times)).days if times else None


def _used_photos(history: list[dict]) -> set[str]:
    return {h["photo_title"] for h in history if h.get("photo_title")}


# ------------------------------------------------------------------ stages
def stage_news(settings, history, rl: RunLog) -> dict | None:
    cfg = load_json(CONFIG_DIR / "feeds.json", {})
    topics = load_json(CONFIG_DIR / "topics.json", {})
    resorts = (load_json(DATA_DIR / "resorts.json", {}) or {}).get("resorts", [])
    items, problems = news.fetch_all(cfg)
    for p in problems:
        rl.warn(f"Feed problem - {p}")
    rl.info(f"Fetched {len(items)} items from {len(cfg.get('feeds', []))} feeds")
    cands = news.select_candidates(items, settings, history, topics, resorts)
    rl.info(f"{len(cands)} verified, relevant, fresh, non-duplicate candidates")
    for it in cands[:6]:
        if not news.enrich(it):
            continue
        if not news.score_item(it, topics, resorts):  # re-check with enriched text
            rl.info(f"Rejected after enrichment: {it.title[:70]}")
            continue
        headline, summary = content.make_headline(it.title), content.make_summary(it)
        llm = content.llm_rewrite(it, settings)
        if llm:
            headline, summary = llm["headline"], llm["summary"]
            rl.info("Caption text rephrased by free LLM (grounding check passed)")
        t = topics["topics"][it.topic]
        key = hashlib.sha1(it.link.encode()).hexdigest()[:12]
        return {
            "kind": "news", "key": key, "topic": it.topic, "kicker": t["label"], "headline": headline,
            "summary": summary, "blurb": summary if len(headline) < 70 else "", "facts": content.extract_facts(it),
            "source": it.source, "source_label": it.source, "url": it.link, "title": it.title,
            "published": iso(it.published), "footer_left": f"Source: {it.source}",
            "hashtags": content.hashtags(t["hashtags"], [it.resort] if it.resort else [], settings["hashtag_limit"]),
            "photo_queries": ([f"{it.resort} ski resort"] if it.resort else []) + t["photo_queries"],
            "corroborated_by": it.corroboration,
        }
    return None


def _pool_pick(pool, key_fn, history, cooldown):
    fresh = [(x, _days_since(history, key_fn(x))) for x in pool]
    never = [x for x, d in fresh if d is None]
    if never:
        return never[0]
    ok = [(x, d) for x, d in fresh if d >= cooldown]
    return max(ok, key=lambda p: p[1])[0] if ok else None


def stage_profile(settings, history, rl) -> dict | None:
    resorts = (load_json(DATA_DIR / "resorts.json", {}) or {}).get("resorts", [])
    r = _pool_pick(resorts, lambda x: f"profile:{x['id']}", history, settings["profile_cooldown_days"])
    return r and _profile_post(r, settings)


def _profile_post(r, settings) -> dict:
    return {
        "kind": "profile", "key": f"profile:{r['id']}", "topic": "profile", "kicker": "Resort Spotlight",
        "headline": f"{r['name']}, {r['country']}", "summary": " ".join(r["notes"]), "blurb": r["notes"][0],
        "facts": [], "source": "Editorial", "source_label": "Editorial", "url": "", "title": r["name"],
        "footer_left": f"{r['region']} · {r['country']}",
        "hashtags": content.hashtags(["skiresortspotlight", "skitravel"], r.get("tags", []), settings["hashtag_limit"]),
        "photo_queries": [r.get("photo_query", f"{r['name']} ski")],
    }


def stage_fact(settings, history, rl) -> dict | None:
    facts = (load_json(DATA_DIR / "facts.json", {}) or {}).get("facts", [])
    f = _pool_pick(facts, lambda x: f"fact:{x['id']}", history, settings["fact_cooldown_days"])
    return f and _fact_post(f, settings)


def _fact_post(f, settings) -> dict:
    topics = load_json(CONFIG_DIR / "topics.json", {})["topics"]
    return {
        "kind": "fact", "key": f"fact:{f['id']}", "topic": f["topic"], "kicker": "Did you know?",
        "headline": f["headline"], "summary": f["detail"], "blurb": f["detail"], "facts": [],
        "source": "Editorial", "source_label": "Editorial", "url": "", "title": f["headline"],
        "footer_left": "Ski & snow facts",
        "hashtags": content.hashtags(topics.get(f["topic"], {}).get("hashtags", []), f.get("tags", []), settings["hashtag_limit"]),
        "photo_queries": [f.get("photo_query", "ski slope")],
    }


def stage_recycle(settings, history, rl) -> dict | None:
    """Last resort so a day is never empty: re-use the least recently posted evergreen item."""
    resorts = (load_json(DATA_DIR / "resorts.json", {}) or {}).get("resorts", [])
    facts = (load_json(DATA_DIR / "facts.json", {}) or {}).get("facts", [])
    pool = [(_days_since(history, f"profile:{r['id']}") or 0, "p", r) for r in resorts] + \
           [(_days_since(history, f"fact:{f['id']}") or 0, "f", f) for f in facts]
    if not pool:
        return None
    _, kind, obj = max(pool, key=lambda p: p[0])
    rl.warn("All evergreen content is within its cooldown - recycling the oldest item.")
    post = _profile_post(obj, settings) if kind == "p" else _fact_post(obj, settings)
    post["recycled"] = True
    return post


def build_post(settings, history, rl, only: str | None = None) -> dict:
    stages = [("news", stage_news), ("profile", stage_profile), ("fact", stage_fact), ("recycle", stage_recycle)]
    for name, fn in stages:
        if only and name != only:
            continue
        try:
            post = fn(settings, history, rl)
        except Exception as exc:  # noqa: BLE001 - a failing stage falls through to the next one
            rl.error(f"Stage '{name}' failed: {type(exc).__name__}: {exc}")
            continue
        if post:
            rl.info(f"Using stage '{name}': {post['headline'][:80]}")
            return post
        rl.info(f"Stage '{name}' had nothing suitable - falling back")
    raise RuntimeError("No content available from any stage")


def render_post(post, settings, history, out_path, rl) -> None:
    photo, meta = None, None
    try:
        found = images.find_photo(post["photo_queries"], settings["allow_share_alike_photos"], _used_photos(history), post["key"])
        if found:
            photo, meta = found
    except Exception as exc:  # noqa: BLE001
        rl.warn(f"Photo lookup failed: {exc}")
    post["photo"] = meta
    post["credit_line"] = f"Photo: {meta['credit']} · {meta['license']} · Wikimedia Commons" if meta else ""
    rl.info(f"Photo: {meta['title']} ({meta['license']})" if meta else "No licensed photo found - using typographic layout")
    img = images.render(post, photo, settings)
    images.save_jpeg(img, out_path)
    cap = {"news": content.caption_news, "profile": content.caption_profile, "fact": content.caption_fact}[post["kind"]]
    post["caption"] = cap(post, settings)
    post["alt"] = f"Editorial graphic: {post['headline']}"[:400]


# ------------------------------------------------------------------ main run
def run(dry_run=False, force=False, only=None) -> int:
    rl, settings = RunLog(), load_settings()
    state = load_json(DATA_DIR / "state.json", {}) or {}
    history = load_json(DATA_DIR / "history.json", []) or []
    tz = ZoneInfo(settings["timezone"])
    now = dt.datetime.now(tz)
    today = now.date().isoformat()
    use_ig = settings["publishers"]["instagram"]["enabled"]
    use_dc = settings["publishers"]["discord"]["enabled"]
    code = 0
    try:
        if not settings["enabled"] and not dry_run:
            rl.status, rl.summary = "skipped", "Publishing disabled in settings"
            rl.info(rl.summary)
            return 0
        if not (dry_run or force):
            if state.get("last_post_date") == today:
                return 0  # quiet exit: already posted today (not logged, runs hourly)
            hh, mm = map(int, settings["post_time_local"].split(":"))
            if now.time() < dt.time(hh, mm):
                return 0
        ig, hook = None, None
        if not dry_run:
            if not (use_ig or use_dc):
                raise RuntimeError("No publisher enabled in config/settings.json")
            if use_ig:
                uid, tok = os.getenv("IG_USER_ID"), os.getenv("IG_ACCESS_TOKEN")
                if not uid or not tok:
                    raise RuntimeError("IG_USER_ID / IG_ACCESS_TOKEN secrets are missing")
                ig = Instagram(uid, tok, settings["graph"]["host"], settings["graph"]["version"])
                start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc)
                if not force and ig.posted_since(start_of_day):
                    state["last_post_date"] = today
                    rl.status, rl.summary = "skipped", "Instagram already shows a post today"
                    rl.info(rl.summary)
                    save_json(DATA_DIR / "state.json", state)
                    return 0
            if use_dc:
                hook = os.getenv("DISCORD_WEBHOOK_URL")
                if not hook:
                    raise RuntimeError("DISCORD_WEBHOOK_URL secret is missing")

        pending = state.get("pending")
        if pending and pending.get("date") == today and not dry_run and (POSTS_DIR / pending["image"]).exists():
            post, image_name = pending["post"], pending["image"]
            rl.info("Re-using today's pending post from an earlier failed attempt")
        else:
            post = build_post(settings, history, rl, only)
            image_name = f"{today}-{_slug(post['key'])}.jpg"
            render_post(post, settings, history, (OUT_DIR if dry_run else POSTS_DIR) / image_name, rl)

        if dry_run:
            rl.status, rl.summary = "dry-run", f"Preview written to out/{image_name}"
            (OUT_DIR / "caption.txt").write_text(post["caption"], encoding="utf-8")
            save_json(OUT_DIR / "post.json", post)
            print(f"\n--- CAPTION ---\n{post['caption']}\n--- IMAGE: out/{image_name} ---")
            return 0

        state["pending"] = {"date": today, "image": image_name, "post": post}
        save_json(DATA_DIR / "state.json", state)
        results: dict = {}
        failures: list[str] = []
        if use_dc:
            try:
                results["discord"] = discord_hook.send(hook, post, POSTS_DIR / image_name, settings["brand_name"])
                rl.info("Published to Discord")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"discord: {exc}")
        if use_ig:
            try:
                commit_and_push(f"Prepare post {image_name}", ["docs"])  # Instagram needs a public image URL
                base = os.getenv("IMAGE_BASE_URL") or (
                    f"https://raw.githubusercontent.com/{os.getenv('GITHUB_REPOSITORY')}/"
                    f"{os.getenv('GITHUB_REF_NAME', 'main')}/docs/posts")
                url = f"{base}/{image_name}"
                if not wait_for_url(url):
                    raise RuntimeError(f"Image URL is not publicly reachable: {url} (is the repository public?)")
                results["instagram"] = ig.publish_image(url, post["caption"], post["alt"])
                rl.info(f"Published to Instagram: {results['instagram'].get('permalink') or results['instagram']['media_id']}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"instagram: {exc}")
        for f in failures:
            rl.error(f)
        if not results:
            raise RuntimeError("; ".join(failures) or "nothing was published")

        history.append({"key": post["key"], "kind": post["kind"], "topic": post["topic"], "title": post.get("title", ""),
                        "headline": post["headline"], "url": post.get("url", ""), "source": post["source"],
                        "posted_at": iso(utcnow()), "image": image_name, "caption": post["caption"],
                        "photo_title": (post.get("photo") or {}).get("title"), "posted_to": sorted(results),
                        "media_id": (results.get("instagram") or {}).get("media_id"),
                        "permalink": (results.get("instagram") or {}).get("permalink", "")})
        state.update({"last_post_date": today, "last_success_at": iso(utcnow()), "pending": None,
                      "consecutive_failures": 1 if failures else 0,
                      "last_error": {"at": iso(utcnow()), "message": "; ".join(failures)[:300]} if failures else None})
        rl.status = "partial" if failures else "success"
        rl.summary = f"Published {post['kind']} post to {', '.join(sorted(results))}: {post['headline'][:70]}"
    except Exception as exc:  # noqa: BLE001
        code = 1
        rl.status, rl.summary = "failed", f"{type(exc).__name__}: {exc}"[:300]
        rl.error(rl.summary)
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        state["last_error"] = {"at": iso(utcnow()), "message": rl.summary}
    finally:
        if not dry_run and rl.status not in ("running",) and not (rl.status == "skipped" and not rl.lines):
            state["last_run"] = {"at": iso(utcnow()), "status": rl.status, "summary": rl.summary}
            keep = {h["image"] for h in history[-60:]} | ({state["pending"]["image"]} if state.get("pending") else set())
            for f in POSTS_DIR.glob("*.jpg"):
                if f.name not in keep:
                    f.unlink(missing_ok=True)
            save_json(DATA_DIR / "history.json", history)
            save_json(DATA_DIR / "state.json", state)
            rl.save()
            try:
                commit_and_push(f"Bot run: {rl.status}", ["docs"])
            except Exception as exc:  # noqa: BLE001
                log.error("Could not push run results: %s", exc)
    return code
