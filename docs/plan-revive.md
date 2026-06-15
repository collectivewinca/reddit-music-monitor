# Implementation Plan: Revive Reddit Music Monitor on last30days

**Date:** 2026-06-15
**Status:** Ready for implementation
**Source:** `docs/requirements-revive.md` + updated LLM-layer status

---

## 1. Architecture Decision: Scoring Strategy

### Decision: HYBRID — keyword matcher for gate + last30days LLM scoring for ranking

**Options considered:**

| Option | Pros | Cons |
|--------|------|------|
| A) last30days LLM end-to-end | Best quality, semantic understanding, can catch posts keywords miss | Cost per run (Ollama Cloud via OpenRouter), non-deterministic, opaque scoring |
| B) Keyword matcher only | Zero cost, deterministic, proven on this exact corpus | Misses semantically relevant posts that don't contain exact keyword phrases |
| C) HYBRID: keyword gate + LLM rank | Deterministic base coverage, LLM adds quality ranking on already-matched posts | Slightly more complex pipeline |

**Choice: C (HYBRID)** with keyword matching as the **required gate** and LLM scoring as an **optional ranking pass**.

**Justification:**
- **Cost:** The keyword gate reduces the candidate set from thousands of raw posts to tens of matches. LLM scoring only runs on matched posts (~20-50 per cycle), not the full firehose. At Ollama Cloud/OpenRouter rates this is pennies per run.
- **Determinism:** Every post that matched before still matches. The keyword gate is the source of truth for inclusion. LLM scoring only adds a `relevance_score` column for sorting — it never excludes a keyword-matched post.
- **Quality:** LLM scoring surfaces the most interesting keyword-matched posts at the top of the dashboard, rather than showing them in chronological order. This is a strict upgrade over Option B with minimal downside.
- **Graceful degradation:** If the LLM layer fails (API down, bad key, timeout), the pipeline still completes — posts are stored with `relevance_score = NULL` and displayed chronologically. No data loss.

**Implementation:** The wrapper calls last30days with `source=llm` scoring on the batch of keyword-matched posts. If it errors, log a warning and continue without scores.

---

## 2. Host & Deployment Decision

### Decision: Runs on the Mac

**Rationale:**
- last30days is installed on the Mac and its keyless Reddit fetch (RSS + listing + keyless JSON) is verified working from the Mac's IP.
- disco-cli (the datacenter VM) gets 403-blocked by Reddit. last30days RSS retrieval is unverified from there and likely blocked.
- The Mac has `here-now` CLI access for dashboard publishing.

**Repo access:** The `reddit-music-monitor` repo is currently on disco-cli. Clone it to the Mac (or keep it on both — the Mac is the runtime host, disco-cli is optional for dev/CI).

**File layout on Mac:**

```
~/projects/reddit-music-monitor/   # git clone
├── config.json                    # existing, unchanged
├── reddit_monitor.py              # retired, kept for reference
├── l30d_monitor.py                # NEW wrapper script
├── gen_dashboard.py               # existing, minor path fix
├── export_siftly.py               # existing, unchanged
├── reddit_monitor.db              # SQLite DB (existing or fresh)
├── index.html                     # generated dashboard
└── docs/
```

---

## 3. Implementation Steps

### Step 1: Create `l30d_monitor.py` (the new wrapper)

**Purpose:** Single-file orchestrator (~150-200 lines) that replaces `reddit_monitor.py`.

**Structure:**

```python
#!/usr/bin/env python3
"""Reddit Music Monitor — last30days retrieval + keyword matching + optional LLM scoring."""

# Functions:
# 1. load_config() — read config.json
# 2. fetch_posts(subreddits) — invoke last30days CLI, parse JSON output
# 3. check_keywords(title, selftext, keywords) — reuse logic from reddit_monitor.py
# 4. score_with_llm(posts) — optional: call last30days with source=llm for relevance scoring
# 5. store_posts(posts, db_path) — INSERT OR IGNORE into existing schema
# 6. run_dashboard(db_path) — subprocess call to gen_dashboard.py
# 7. publish_dashboard() — subprocess call to here-now CLI
# 8. main() — orchestrate all steps, log results
```

**Detailed design for each function:**

#### `fetch_posts(subreddits: list[str]) -> list[dict]`

- Invoke: `last30days --search reddit --subreddits <batch> --emit json`
- Batch subreddits in groups of ~20 to avoid CLI argument limits. 177 subs / 20 = 9 invocations.
- Parse JSON stdout. Each post object should have at minimum: `subreddit`, `id` (or `post_id`), `title`, `url`, `author`, `score`, `created_utc`, `selftext`.
- Handle: subprocess timeout (120s per batch), non-zero exit codes, malformed JSON.
- Map last30days field names to the DB schema field names (field mapping TBD based on actual last30days JSON output — inspect once on Mac and hardcode the mapping).

#### `check_keywords(title: str, selftext: str, keywords: list[str]) -> list[str]`

- Extracted verbatim from `reddit_monitor.py:289-298` (the `check_keywords` method).
- Case-insensitive substring match on `f"{title} {selftext}".lower()`.
- Returns list of matched keyword strings.

#### `score_with_llm(posts: list[dict]) -> list[dict]`

- Takes posts that already passed keyword matching.
- Calls `last30days --score --source llm --emit json` (or equivalent CLI invocation — verify exact flags on Mac).
- Parses response, extracts per-post relevance score (0.0-1.0).
- Attaches `relevance_score` to each post dict.
- **On any failure:** logs warning, returns posts unchanged (score=NULL). Pipeline continues.

#### `store_posts(posts: list[dict], db_path: str) -> int`

- Opens SQLite connection.
- Ensures table exists (same CREATE TABLE IF NOT EXISTS as `reddit_monitor.py:191-206`).
- `INSERT OR IGNORE` on `reddit_id` for dedup.
- `matched_keywords` stored as JSON array string.
- `raw_json` stores the full last30days output dict for the post.
- Returns count of newly inserted rows.

#### `run_dashboard()` and `publish_dashboard()`

- `subprocess.run(["python3", "gen_dashboard.py"], check=True)`
- `subprocess.run(["here-now", "publish", "--site", "hearty-garnet-eq9j", "index.html"], check=True)`
- Both log errors but don't crash the pipeline.

#### `main()`

```
1. Load config.json
2. Fetch posts from all subreddits via last30days (batched)
3. Filter: score >= min_score_threshold (3)
4. Filter: check_keywords() — discard zero-match posts
5. Optional: score_with_llm() on matched posts
6. Store matched posts to DB
7. Regenerate dashboard
8. Publish to here.now
9. Log summary: fetched N, matched M, stored K new, LLM scored Y/N
```

### Step 2: Modify `gen_dashboard.py`

**Changes needed:**
- The hardcoded paths (`DB_PATH`, `OUT_PATH`) use `/root/projects/reddit-music-monitor/...` which is the disco-cli path. Make them relative to `__file__` or accept CLI args so they work on the Mac too.
- Add `relevance_score` display: if the column exists in the DB, sort by it (descending) instead of `discovered_at`. Show score badge on posts that have one.
- Add the `relevance_score` column to the DB schema (ALTER TABLE ADD COLUMN, idempotent).

**Specific edits:**

```python
# Line 6-7: Make paths relative to script location
SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / "reddit_monitor.db"
OUT_PATH = SCRIPT_DIR / "index.html"
```

```python
# Line 12-15: Sort by relevance_score when available
cursor.execute(
    "SELECT title, subreddit, author, score, url, matched_keywords, discovered_at, "
    "COALESCE(relevance_score, 0) as rel_score "
    "FROM posts ORDER BY rel_score DESC, discovered_at DESC LIMIT 50"
)
```

### Step 3: Add `relevance_score` column to DB schema

In `l30d_monitor.py`'s `store_posts()` or init, run:

```sql
ALTER TABLE posts ADD COLUMN relevance_score REAL;
```

Wrapped in try/except (column already exists = no-op). This is backwards-compatible — existing rows get NULL.

### Step 4: Retire `reddit_monitor.py`

- **Do NOT delete it** — keep for reference and git history.
- Add a comment at the top: `# RETIRED: Webshare proxy layer is dead. See l30d_monitor.py`
- Remove from any cron jobs or scripts that reference it.

### Step 5: Update `README.md`

Rewrite to reflect new architecture:
- Remove all Webshare references.
- Document `l30d_monitor.py` as the active script.
- Update architecture diagram: `last30days CLI -> keyword matcher -> SQLite -> gen_dashboard -> here.now`
- Document the hybrid scoring approach.
- Document Mac cron setup.
- Keep the DB schema section (add `relevance_score` column).

### Step 6: Set up Mac cron job

```cron
# Every 6 hours: fetch, score, store, dashboard, publish
0 */6 * * * cd ~/projects/reddit-music-monitor && /usr/bin/python3 l30d_monitor.py >> cron.log 2>&1
```

**Cadence rationale:** 6 hours balances freshness against Reddit RSS staleness (RSS only shows recent posts anyway, and last30days deduplicates). 4 invocations/day * 9 batches * ~20 subs = 36 last30days calls/day — well within any rate limits.

### Step 7: Clone repo to Mac and verify

```bash
# On Mac:
cd ~/projects
git clone <repo-url> reddit-music-monitor
cd reddit-music-monitor

# Verify last30days works:
last30days --search reddit --subreddits indieheads,listentothis --emit json

# Inspect the JSON output to confirm field names for the mapping in Step 1.

# Copy existing DB from disco-cli if historical data is wanted:
scp disco-cli:/root/projects/reddit-music-monitor/reddit_monitor.db .

# Or start fresh — the wrapper creates the table if missing.
```

---

## 4. What Gets Extracted / Kept from `reddit_monitor.py`

| Component | Action |
|-----------|--------|
| `check_keywords()` (lines 289-298) | **Extract** verbatim into `l30d_monitor.py` |
| `init_database()` / CREATE TABLE (lines 186-221) | **Extract** into `l30d_monitor.py` (add `relevance_score` column) |
| `save_post()` (lines 300-335) | **Extract** INSERT OR IGNORE logic into `store_posts()` |
| `load_config()` (lines 165-180) | **Extract** simplified version (just `json.loads`) |
| `min_score_threshold` filtering (line 344) | **Extract** into main pipeline |
| `WebshareProxyManager` class (lines 53-147) | **Delete** (dead code) |
| `make_request()` with proxy rotation (lines 223-274) | **Delete** (replaced by last30days) |
| `fetch_subreddit()` (lines 276-287) | **Delete** (replaced by last30days) |
| `run_monitor_loop()` (lines 358-383) | **Delete** (replaced by cron + single-shot main) |
| CLI arg parsing (lines 473-562) | **Delete** (wrapper is single-purpose) |

---

## 5. File-Level Change Summary

| File | Status | Changes |
|------|--------|---------|
| `l30d_monitor.py` | **NEW** | ~150-200 line wrapper: fetch via last30days, keyword match, optional LLM score, store, dashboard, publish |
| `gen_dashboard.py` | **EDIT** | Relative paths, optional relevance_score sort, score badge display |
| `reddit_monitor.py` | **RETIRE** | Add retired comment at top, no functional changes |
| `config.json` | **KEEP** | Unchanged — 177 subs, 214 keywords, min_score=3 |
| `export_siftly.py` | **KEEP** | Unchanged |
| `clean_db.py` | **KEEP** | Unchanged |
| `README.md` | **REWRITE** | New architecture, remove Webshare, document hybrid scoring, Mac cron |
| `requirements.txt` | **EDIT** | Remove `requests` dependency if no longer needed; no new deps (last30days is a CLI tool called via subprocess) |
| `.env` | **DELETE/IGNORE** | `WEBSHARE_API_KEY` no longer needed |

---

## 6. Scheduling Details

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Frequency | Every 6 hours | Balances freshness vs. rate limits; RSS window is ~24h anyway |
| Cron expression | `0 */6 * * *` | 00:00, 06:00, 12:00, 18:00 |
| Execution mode | Single-shot (not a loop) | Cron handles scheduling; script runs once and exits |
| Timeout | 30 min max | 9 batches * 120s timeout + scoring + dashboard = well under 30 min |
| Logging | Append to `cron.log` | Rotated manually or via `logrotate` |
| Error handling | Log and continue | Each batch/step fails independently; partial results are still stored |

---

## 7. Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| last30days CLI interface changes | Pin version; wrapper logs full stderr on non-zero exit |
| Reddit blocks Mac IP | Unlikely (residential IP), but: wrapper logs 0-post batches; alert if total fetched = 0 |
| LLM scoring fails | Graceful degradation: pipeline continues, posts stored without score, dashboard falls back to chronological sort |
| DB locked during concurrent runs | Single cron job + flock: `flock -n /tmp/l30d_monitor.lock l30d_monitor.py` |
| here-now publish fails | Log warning, dashboard is stale but data is preserved in DB |
| Subreddit batch too large for CLI | Start with batches of 20; reduce if errors occur |

---

## 8. Verification Checklist

After implementation, verify each of these on the Mac:

- [ ] `last30days --search reddit --subreddits indieheads --emit json` returns parseable JSON
- [ ] `python3 l30d_monitor.py` completes a full cycle: fetch -> filter -> store -> dashboard -> publish
- [ ] New posts appear in `reddit_monitor.db` with correct schema
- [ ] `matched_keywords` is a valid JSON array for each stored post
- [ ] `relevance_score` is populated for posts when LLM scoring succeeds
- [ ] `gen_dashboard.py` produces valid `index.html` with posts sorted by relevance
- [ ] `here-now publish` updates `hearty-garnet-eq9j.here.now`
- [ ] Cron job fires and logs to `cron.log`
- [ ] Running the cron job twice doesn't duplicate posts (INSERT OR IGNORE works)
- [ ] LLM scoring failure doesn't crash the pipeline

---

## 9. Sequence Diagram

```
Cron (every 6h)
  │
  ▼
l30d_monitor.py
  │
  ├─ load config.json (177 subs, 214 keywords, min_score=3)
  │
  ├─ for each batch of ~20 subreddits:
  │     └─ subprocess: last30days --search reddit --subreddits <batch> --emit json
  │     └─ parse JSON, collect posts
  │
  ├─ filter: score >= 3
  │
  ├─ filter: check_keywords(title + selftext) — discard zero matches
  │
  ├─ optional: score_with_llm(matched_posts)
  │     └─ subprocess: last30days --score --source llm --emit json
  │     └─ on failure: log warning, continue without scores
  │
  ├─ store_posts() — INSERT OR IGNORE into reddit_monitor.db
  │
  ├─ subprocess: python3 gen_dashboard.py
  │
  └─ subprocess: here-now publish index.html
```

---

## 10. Implementation Order

1. **Inspect last30days JSON output** on Mac — confirm field names and CLI flags. This unblocks the field mapping in `fetch_posts()`.
2. **Write `l30d_monitor.py`** — the core wrapper.
3. **Edit `gen_dashboard.py`** — relative paths + relevance_score support.
4. **Test locally** on Mac — single manual run, verify DB and dashboard.
5. **Retire `reddit_monitor.py`** — add comment header.
6. **Rewrite `README.md`** — new architecture docs.
7. **Set up cron** — install crontab entry, verify first automated run.
8. **Monitor** — check cron.log and dashboard after 24h.
