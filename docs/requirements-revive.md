---
date: 2026-06-15
topic: revive-monitor-last30days
---

# Revive Reddit Music Monitor on last30days

## Summary

Replace the dead Webshare-proxy fetch layer with `last30days --search reddit` as the retrieval engine, keep the proven 150+ keyword matcher for relevance scoring, and schedule the pipeline as a Mac cron job that writes to the existing SQLite DB and here.now dashboard.

---

## Problem Frame

The monitor died 2026-04-20 when the Webshare residential-proxy subscription was dropped. Datacenter IPs get 403-blocked by Reddit, so the old `reddit_monitor.py` cannot fetch at all. The system was never load-bearing — `export_siftly.py` never injected a row into Siftly, and the monitor was run manually despite README claims of cron. Webshare is gone for good; no proxy budget exists.

`last30days` is already installed on the Mac and has a keyless multi-tier Reddit fetch (RSS + listing + keyless JSON) that pulled 48 real posts from datacenter IPs with no proxy and no API key.

---

## Key Decisions

**Option B for the design fork: last30days retrieves, keyword matcher scores.** last30days's LLM ranking is broken (invalid API keys collapse 48 cards to 1). Option A (fix the keys) would create a runtime dependency on a paid LLM API for a batch job that runs unattended. Option B uses last30days purely for keyless retrieval (`--emit json`, no LLM pass) and reuses the original keyword matcher for relevance. This keeps the pipeline zero-cost, deterministic, and independent of external API keys. LLM re-ranking can be layered on later as optional polish.

**Runs on the Mac, not disco-cli.** last30days and Reddit RSS both work from the Mac's IP. The pipeline script and cron live there. The SQLite DB and dashboard HTML can be synced or served from the Mac.

**Keep the existing DB schema unchanged.** The `posts` table schema works. New posts from last30days get mapped into the same columns (`subreddit`, `reddit_id`, `title`, `url`, `author`, `score`, `created_utc`, `matched_keywords`, `raw_json`, `discovered_at`).

---

## Requirements

**Retrieval**

R1. A wrapper script invokes `last30days --search reddit --subreddits <list> --emit json` to fetch recent posts from the configured subreddits without proxies or API keys.

R2. The subreddit list is read from the existing `config.json` (177 subreddits). Batching across multiple `last30days` invocations is acceptable if the CLI doesn't support 177 subreddits in one call.

R3. The wrapper parses last30days JSON output and extracts per-post fields: subreddit, post ID, title, URL, author, score, created timestamp, and selftext (or body) when available.

**Scoring**

R4. Each post is matched against the existing 214 keywords from `config.json` using case-insensitive substring matching on title + selftext, reusing the logic from `reddit_monitor.py:check_keywords()`.

R5. Posts with zero keyword matches are discarded. Posts with matches are stored with `matched_keywords` as a JSON array.

R6. The `min_score_threshold` from `config.json` (currently 3) is applied to Reddit score before keyword matching.

**Storage**

R7. Matched posts are inserted into the existing `reddit_monitor.db` using `INSERT OR IGNORE` on `reddit_id` to deduplicate against historical data.

R8. The DB schema is unchanged. The `raw_json` column stores the last30days output for the post.

**Dashboard**

R9. After ingestion, the wrapper runs `gen_dashboard.py` to regenerate `index.html` from the DB.

R10. The regenerated dashboard is published to the existing here.now site (`hearty-garnet-eq9j`). The publish mechanism (here-now CLI or API) is a planning detail.

**Scheduling**

R11. The wrapper is scheduled as a Mac cron job. Frequency: every 4-6 hours (planning decides exact interval).

R12. The cron job runs headlessly with no user interaction, no GUI, and no LLM API key requirement.

**Siftly bridge (optional)**

R13. `export_siftly.py` remains available for manual use. No automated Siftly injection is in scope.

---

## Scope Boundaries

- **In scope:** retrieval via last30days, keyword scoring, SQLite storage, dashboard generation, here.now publish, Mac cron scheduling.
- **Out of scope:** LLM-based ranking or summarization, Reddit API credentials or OAuth, proxy infrastructure, automated Siftly injection, email reports, mobile notifications, new dashboard UI beyond what `gen_dashboard.py` already produces.
- **Deferred:** LLM re-ranking as optional polish if valid API keys become available later. Expanding to non-Reddit sources.

---

## Dependencies / Assumptions

- `last30days` CLI is installed and functional on the Mac and its keyless Reddit fetch continues to work without proxies.
- The Mac has network access to Reddit RSS/listing endpoints (not 403-blocked like datacenter IPs).
- `here-now` CLI or API is available on the Mac for dashboard publishing.
- The existing `reddit_monitor.db` is accessible on the Mac (or a fresh DB is acceptable for a clean start).

---

## What Changes in the Codebase

| File | Action |
|------|--------|
| `reddit_monitor.py` | Retired. Webshare code is dead weight. Keyword matching and DB logic are extracted into the new wrapper. |
| `config.json` | Stays as-is. Read by the new wrapper. |
| `gen_dashboard.py` | Stays as-is. Called by the wrapper after ingestion. |
| `export_siftly.py` | Stays as-is. Optional manual use. |
| New: wrapper script | Orchestrates last30days retrieval, keyword scoring, DB insert, dashboard regen, and here.now publish. Single file, ~100-200 lines. |
| `README.md` | Updated to reflect the new architecture (no Webshare, last30days engine, Mac cron). |
