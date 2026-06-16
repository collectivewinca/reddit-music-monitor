# Automations Plan — reddit-music-monitor

Spec for the operational automations around the monitor (ce-brainstorm → ce-plan).
Written 2026-06-16, after the "0-match" diagnosis. Context: the monitor runs on a
Mac (residential IP — datacenter IPs are 403-blocked by Reddit), retrieves via the
keyless last30days RSS layer, gates on 214 keywords + structural signals, stores to
SQLite, renders `index.html`, and publishes to a here.now URL.

## Root constraint that shapes everything

Retrieval is **multi-tier and rate-limit sensitive**. Under throttling it degrades
to a **title-only** tier (no `selftext`), which halves the keyword gate's recall
(~43% → ~22%). So the automations are not just "run it on a timer" — they must
*detect thin runs* and *avoid self-inflicted throttling* (no competing jobs on the
same IP; pace the calls). See memory `keyless-rss-title-only-tier-degrades-gate`.

---

## Brainstorm — candidate automations

| # | Automation | Problem it solves | Effort | Value |
|---|------------|-------------------|--------|-------|
| 1 | **6h scheduled run w/ body-tier guard** | README wants a 6h cron, but a naive timer re-collects thin title-only data when throttled | M | High |
| 2 | **Process/log watchdog** | A retired-space zombie ran 8 days, 210 MB log, unnoticed | S | High |
| 3 | **Capture-rate canary** | A 0-match run silently overwrites the dashboard with stale data | S | High |
| 4 | **Auto-publish to here.now** | `gen_dashboard.py` writes the file; publishing is manual | S | Med |
| 5 | **Schema/retrieval drift test** | README claimed a `relevance_score` column the DB lacked | S | Med |

---

## Plan — sequenced, with the constraint front and center

### 1. Scheduled run with body-tier guard  *(do first; everything else hangs off a healthy run)*
- **Guard probe:** before the full sweep, one cheap `search_rss` call on a known-busy
  sub (e.g. `indieheads`). If the returned items have empty `selftext`, we're on the
  thin tier → **skip this run** (log `tier=thin, skipped`) rather than collect junk.
- **Schedule:** local `crontab` every 6h (NOT a cloud /schedule agent — the job must
  egress from the Mac's residential IP). Stagger off the hour to avoid collisions.
- **Pacing:** keep existing `INTER_BATCH_SLEEP`/`SUB_WINDOW` rotation; never run two
  monitors at once (watchdog enforces — see #2).
- **Cron line (ready, install after one clean manual run):**
  `0 */6 * * * cd ~/projects/reddit-music-monitor && /usr/bin/python3 l30d_monitor.py >> ~/.reddit-music-monitor/run.log 2>&1`
  (log to a dot-dir, not Documents — avoids the macOS LaunchAgent/TCC trap.)

### 2. Process/log watchdog
- Pre-run: `pgrep -f 'reddit_monitor.*run'` — if another instance (esp. from the
  retired `~/reddit-webshare-monitor` space) is alive, refuse to start and alert.
- Log hygiene: rotate/truncate any monitor log > 50 MB. Today's was 210 MB.
- Cheap to bolt onto the cron wrapper as a preamble.

### 3. Capture-rate canary
- After each run, compare `new_inserted` and `keyword_matched` vs `fetched`.
- If `fetched > 0` but `matched == 0` (today's signature) → **do not regenerate the
  dashboard from an empty delta**; emit an alert instead. Prevents a thin run from
  presenting as "all good, 0 new."
- Threshold starting point: warn if match-rate < 5% of fetched on a non-thin tier.

### 4. Auto-publish to here.now
- On a *successful, non-thin* run that inserted rows, publish `index.html` to the
  live slug (`hearty-garnet-eq9j.here.now`). Use the `here-now` skill / publish flow.
- Gate publish on #3 passing so we never push a stale/empty dashboard.

### 5. Schema/retrieval drift test
- Tiny check (CI or pre-run): assert the live DB has the columns the code writes
  (`relevance_score` was the gap), and that `search_rss` still returns the expected
  item keys (`title,url,author,score,created_utc,selftext,subreddit,id`).
- Fails loud on a library/schema change rather than silently inserting nulls.

---

## Sequencing
1 (guard + cron) → 2 (watchdog, folds into 1's wrapper) → 3 (canary, folds into the
run) → 4 (publish, gated on 3) → 5 (drift test, independent, do anytime).

## Open dependency
None of 1–4 can be **live-verified** until the current Reddit throttle clears (we
triggered it with back-to-back test runs on 2026-06-16). Build against the 570-post
DB corpus meanwhile; switch on the cron after one clean manual run shows a non-thin
tier + non-zero inserts.
