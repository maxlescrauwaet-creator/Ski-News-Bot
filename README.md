# Ski News Bot – automated Instagram ski-news account

Runs entirely on **GitHub Actions** (free for public repositories), publishes to **Discord** (webhook, default) and/or **Instagram** (official Graph API, optional),
generates graphics with **Pillow**, and needs **no paid AI API** and **no computer that stays on**.

## Publishing targets

| Target | Status | Needs |
|---|---|---|
| **Discord** (default, on) | works with no review | a webhook URL (Channel settings -> Integrations -> Webhooks) |
| **Instagram** (off by default) | needs a Meta developer account | `IG_USER_ID`, `IG_ACCESS_TOKEN`, see below |
| TikTok / YouTube | **not included** | both force API posts/uploads to *private* until the app passes the platform's audit |

Switch targets in `config/settings.json` -> `publishers`. Several can be on at once.

## How it works

```
GitHub Actions (hourly cron)
  └─ python -m skibot
       1. due? (enabled, local posting time reached, nothing posted today – checked in state.json AND on Instagram itself)
       2. build post, in priority order:
            news    RSS/Atom feeds -> relevance + safety filters -> freshness -> trust/corroboration -> de-duplication
            profile educational resort spotlight (docs/data/resorts.json)
            fact    ski/snowmaking fact (docs/data/facts.json)
            recycle oldest evergreen item (so a day is never empty)
       3. photo: Wikimedia Commons only, licence-checked (CC0 / PD / CC BY / CC BY-SA), credited on image + caption.
          No licensed photo found -> procedurally drawn mountain graphic (no photo needed).
       4. render 1080x1350 JPEG (3 layouts), commit to docs/posts/, wait until the public URL works
       5. Publish: Discord embed with the image attached (no public URL needed), and/or Graph API: create container -> poll -> publish. Log + history committed back to the repo.
  └─ GitHub Pages serves docs/  ->  dashboard (docs/index.html)
```

Failures: any stage that throws falls through to the next one; Graph API calls retry with backoff (transient errors only);
a failed publish keeps a "pending" post that the next hourly run re-uses (no new article picked, no double posting –
publishing itself is never blindly retried). Failed workflow runs make GitHub e-mail you.

## What is real vs. what you must know (honest limitations)

* **No generative AI is required.** Headline/summary/facts are extracted from the publisher's RSS text + page meta
  description, with credit to the source. Summaries are therefore short snippets, not original prose.
* **Optional free LLM:** if `llm.enabled` is true, GitHub Models (free tier, rate-limited, availability can change) may
  *rephrase* that text. Output is discarded unless every number and mid-sentence proper noun also occurs in the source.
  If the call fails, the extractive text is used. Set `"llm": {"enabled": false}` to turn it off.
* **"Verified" means:** item is fresh (default ≤ 96 h), dated, relevant, free of tragedy/deal keywords, and either from a
  trusted outlet (trust ≥ 2) or reported by a second domain; the article page must open. It does **not** mean fact-checked
  by a human. Review the first weeks of posts.
* **Feeds:** the URLs in `config/feeds.json` are best-effort and were **not verifiable from the build environment**.
  Run `scripts/check_feeds.py` (or the "Check feeds" step below) and edit the list. Dead feeds are skipped automatically.
  Bing News RSS is an unofficial convenience feed and may stop working.
* **Photos may not match the story exactly** (they are topic/resort illustrations; captions say "Illustrative image").
  CC BY-SA images: attribution is given, but share-alike on a composite graphic is legally debatable – set
  `allow_share_alike_photos` to `false` for CC0/PD/CC BY only. Recognisable people are not filtered automatically.
* **Evergreen content** (`resorts.json`, `facts.json`) was written conservatively from general knowledge. **Verify each
  line before enabling**, and extend the lists – with only 9 profiles + 12 facts the fallback pool is small (recycling
  kicks in after the cooldowns).
* **Timing:** GitHub cron is best-effort and can be delayed (sometimes 15–60 min). The job runs hourly and posts at the
  first run after your chosen time. Scheduled workflows in a public repo are auto-disabled after 60 days without repository
  activity; the bot commits on every run, which normally counts as activity – if GitHub ever sends a "disabled" notice, re-enable it in the Actions tab.
* **Free-tier facts to re-check yourself:** GitHub Actions is free and unlimited for public repos (private repos: 2,000
  min/month – hourly runs use roughly 500); Instagram content publishing is limited per 24 h (far above 1/day); Meta
  API versions/permissions change – the version is set in `config/settings.json` (`graph.version`).
* **Repo must be public** for free Pages (and, for Instagram only, because the Graph API needs a public image URL served from raw.githubusercontent.com). Discord does not need that.
* **Duplicate risk:** the Discord-only path has no external 'already posted today' check (Instagram does). If the final commit of `state.json` ever fails, a later run could post again; the log shows it.
  Never commit secrets – use GitHub Secrets only.
* Dashboard "change time / enable" edits `config/settings.json` through the GitHub API using a token you paste into
  your browser (kept in localStorage). Without a token the dashboard is read-only. You can also edit the file on github.com.

## Discord setup (2 minutes)

1. In your server: channel -> Edit Channel -> Integrations -> Webhooks -> New Webhook -> **Copy Webhook URL**.
2. GitHub repo -> Settings -> Secrets and variables -> Actions -> new secret `DISCORD_WEBHOOK_URL`.
3. Anyone with the webhook URL can post to that channel: never commit it.
Discord posts are an embed (headline, summary, key facts, source link in the title, photo credit in the footer). Hashtags are omitted there.

## Instagram: accounts you need (all free, optional)

1. **GitHub** account.
2. **Instagram Professional account** (Business or Creator) connected to a **Facebook Page**.
3. **Meta developer account** + an app (type *Business*) with the *Instagram Graph API* product.

## Installation

1. **Create the repo.** New **public** repository, upload everything in this folder (keep the structure), default branch `main`.
2. **Settings → Pages:** source = *Deploy from a branch*, branch `main`, folder `/docs`. Dashboard: `https://<user>.github.io/<repo>/`.
3. **Settings → Actions → General → Workflow permissions:** *Read and write permissions*.
4. **Edit `config/settings.json`:** `handle`, `brand_name`, `timezone`, `post_time_local`.
5. **Meta credentials** (only if you enable Instagram: set `publishers.instagram.enabled` to `true`)
   * developers.facebook.com → create app → add *Instagram Graph API*; in *Graph API Explorer* select your app and request
     `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, `business_management`.
   * Generate a user token, then exchange it for a **long-lived** token
     (`GET /oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=SHORT_TOKEN`).
   * With the long-lived user token call `GET /me/accounts` – the Page `access_token` returned there is a **Page token that
     does not expire** (when derived from a long-lived user token). Use this one as `IG_ACCESS_TOKEN`.
   * `GET /{page-id}?fields=instagram_business_account` -> the `id` is your `IG_USER_ID`.
   * For your own account the app can stay in development mode (no app review needed).
   * If you use *Instagram Login* tokens instead, set `graph.host` to `graph.instagram.com`; those tokens last 60 days and must be refreshed.
6. **GitHub Secrets** (Settings → Secrets and variables → Actions → *New repository secret*): `IG_USER_ID`, `IG_ACCESS_TOKEN` (Discord: `DISCORD_WEBHOOK_URL`).
   Secrets are never printed in logs and are not visible to forks.

## Test before enabling (nothing is published in steps 1–4)

1. Locally or in a Codespace: `pip install -r requirements.txt && PYTHONPATH=src python -m skibot --selftest` -> sample graphics in `out/`.
2. `PYTHONPATH=src python scripts/check_feeds.py` -> which feeds work. Fix `config/feeds.json`.
3. **Actions → Publish daily Instagram post → Run workflow** with *dry_run* ticked (default). Download the `preview` artifact:
   image + `caption.txt`. Try `--only profile` / `--only fact` locally to test the fallbacks.
4. (Instagram only) `IG_USER_ID=… IG_ACCESS_TOKEN=… PYTHONPATH=src python scripts/check_instagram.py` -> must print `Connected as @yourname`.
   (Discord) create a *separate test channel/webhook* first and run step 5 against it.
5. Set `"enabled": false` in settings, run the workflow with *dry_run* **off** and *force* **on** once to make exactly one real test post, check Instagram, delete it if you like.
6. Set `"enabled": true`. From now on the hourly runs publish one post per day.

## Files

```
.github/workflows/post.yml   hourly schedule + manual dry-run/force
config/settings.json         time, timezone, brand, LLM, thresholds
config/feeds.json            feeds, trusted domains, trust levels
config/topics.json           relevance keywords, block-list, hashtags, photo queries
src/skibot/news.py           feed parsing, scoring, verification, de-duplication
src/skibot/content.py        headline/summary/facts, grounded optional LLM, captions, hashtags
src/skibot/images.py         Commons search + licence filter, fonts, 3 Pillow layouts
src/skibot/instagram.py      Graph API publishing, retries, "posted today" check (optional)
src/skibot/discord_hook.py   Discord webhook publishing
src/skibot/pipeline.py       scheduling logic, fallback chain, state/history/log
src/skibot/gitops.py         commit & push results (Actions only)
docs/index.html              dashboard (GitHub Pages)
docs/data/*.json             state, history, run logs, resorts, facts
scripts/, tests/             feed + credential checks, unit tests, offline fixture
```

## Tuning

* More/less strict news: `min_score`, `max_age_hours` in settings; keywords/blocklist in `config/topics.json`.
* Fonts (Playfair Display + Inter, OFL) are downloaded on first run and cached; DejaVu is the fallback.
* Images older than the last 60 posts are deleted automatically to keep the repo small.
