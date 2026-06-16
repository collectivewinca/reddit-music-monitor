#!/usr/bin/env python3
"""Reddit Music Monitor — last30days (keyless) retrieval + keyword gate + LLM relevance.

Replaces the retired Webshare-proxy reddit_monitor.py. Runs on the Mac, where
last30days' keyless Reddit retrieval works (datacenter IPs are 403-blocked).

Pipeline: last30days fetch -> keyword gate (inclusion) -> compute_relevance
(ranking) -> SQLite -> dashboard.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "reddit_monitor.db"
CONFIG_PATH = SCRIPT_DIR / "config.json"
CURSOR_PATH = SCRIPT_DIR / ".sub_cursor"
LOCK_PATH = SCRIPT_DIR / ".monitor.lock"
TOPIC = "new indie and underground music releases and emerging artists"
# Reddit throttles ~175 rapid feed pulls from one IP, so each run covers a
# ROTATING window of the sub list (full sweep ~daily at a 6h cron) and paces
# the RSS calls: small batches, light depth, and a sleep between batches.
SUB_WINDOW = 30          # subreddits processed per run (rotates across runs)
BATCH_SIZE = 6           # subreddits per last30days RSS call
RSS_DEPTH = "default"    # 'deep' fires too many feed URLs per sub at scale
INTER_BATCH_SLEEP = 5    # seconds between RSS batches
PROBE_TIMEOUT = 45       # seconds; the probe must stay cheap (see probe_tier)
# Body-tier guard: the keyless RSS layer is multi-tier; under Reddit rate-limiting
# it degrades to a TITLE-ONLY tier (empty selftext), which halves the keyword
# gate's recall. We cheaply probe ONE busy text-heavy sub first; if the tier looks
# thin/throttled we skip the whole run rather than collect junk and overwrite the
# dashboard. PROBE_SUB is text-heavy so the rich tier reliably yields selftext.
PROBE_SUB = "WeAreTheMusicMakers"

# Call last30days' keyless Reddit retrieval layer DIRECTLY (it handles Reddit
# rate-limiting via multi-tier RSS/listing fetch). We use its broad discovery
# output rather than the full CLI, whose topic-ranking emits only the top ~2.
_L30_SCRIPTS = Path.home() / ".claude/skills/last30days/skills/last30days/scripts"
if str(_L30_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_L30_SCRIPTS))
from lib import reddit_rss  # noqa: E402
from lib import env as l30_env, providers as l30_prov  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("l30d_monitor")


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def get_conn() -> sqlite3.Connection:
    """Open the DB, ensuring the original schema plus the new relevance_score column."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subreddit TEXT NOT NULL, reddit_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL, url TEXT NOT NULL, author TEXT NOT NULL,
            score INTEGER NOT NULL, created_utc REAL NOT NULL,
            matched_keywords TEXT, raw_json TEXT,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    try:
        cur.execute("ALTER TABLE posts ADD COLUMN relevance_score REAL")
    except sqlite3.OperationalError:
        pass  # column already exists — idempotent
    conn.commit()
    return conn


_ID_RE = re.compile(r"/comments/([a-z0-9]+)", re.I)


def extract_reddit_id(url: str) -> str | None:
    m = _ID_RE.search(url or "")
    return m.group(1) if m else None


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_epoch(published_at: str) -> float:
    """last30days gives a 'YYYY-MM-DD' date; store as epoch seconds (schema wants REAL)."""
    try:
        return datetime.strptime(published_at, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _int(v) -> int:
    f = _safe_float(v)
    return int(f) if f is not None else 0


def fetch_posts(subreddits: list[str]) -> list[dict]:
    """Fetch the broad per-subreddit set via last30days' keyless RSS layer.

    Calls reddit_rss.search_rss directly (multi-tier, rate-limit-aware) and
    normalizes to post dicts. This is the WIDE net the keyword gate needs —
    not the full CLI's topic-ranked top-N. LLM relevance is decoupled
    (compute_relevance handles llm_score=None as keyword-density ranking).
    """
    posts: list[dict] = []
    for i in range(0, len(subreddits), BATCH_SIZE):
        if i > 0:
            time.sleep(INTER_BATCH_SLEEP)  # pace RSS calls to avoid Reddit throttling
        batch = subreddits[i : i + BATCH_SIZE]
        try:
            raw = reddit_rss.search_rss(TOPIC, depth=RSS_DEPTH, subreddits=batch) or []
        except Exception as e:  # keyless layer is best-effort; never abort the run
            logger.warning(f"batch {i // BATCH_SIZE} ({batch[0]}…) failed: {e}")
            continue
        for it in raw:
            url = it.get("url", "")
            rid = extract_reddit_id(url) or str(it.get("id", "")).replace("t3_", "")
            if not rid:
                continue
            posts.append({
                "subreddit": it.get("subreddit", "unknown"),
                "reddit_id": rid,
                "title": it.get("title", ""),
                "url": url,
                "author": it.get("author") or "unknown",
                "score": _int(it.get("score")),
                "created_utc": _safe_float(it.get("created_utc")) or 0.0,
                "body": it.get("selftext") or "",
                "llm_score": None,  # retrieval decoupled from LLM scoring (future phase)
            })
    logger.info(f"fetched {len(posts)} posts across {len(subreddits)} subreddits")
    return posts


# Reddit's standard "new music" post tags and the streaming/DIY platforms a real
# release links to. These are high-precision inclusion signals the keyword gate
# missed: the keyless RSS layer yields title-only items (no body), so phrase
# keywords that relied on body text rarely fire — but a [FRESH] tag or a
# bandcamp/soundcloud link in the URL is near-certain new-music evidence.
_FRESH_RE = re.compile(r"\[\s*(?:fresh[a-z ]*|fm|premiere)\s*\]", re.I)
_PLATFORM_RE = re.compile(
    r"(bandcamp\.com|soundcloud\.com|(?:open\.)?spotify\.com|music\.apple\.com)", re.I
)


def _phrase_in(text: str, kw: str) -> bool:
    """Whole-word match on the keyword as a unit (boundaries on both ends), e.g.
    'indie band' must appear adjacent — NOT 'indie' and 'band' scattered apart.
    Tested the loose all-words-present variant; on the keyless layer's short
    titles it gave ~zero recall lift (both words are adjacent anyway when present)
    but added false positives ('New phone release' -> 'new release'). Boundaries
    still fix the substring noise (so 'new' won't fire inside 'newest')."""
    return re.search(rf"\b{re.escape(kw.lower())}\b", text) is not None


def check_keywords(title: str, body: str, keywords: list[str], url: str = "") -> list[str]:
    """Inclusion gate: whole-word keyword match (phrases = all-words-present)
    plus structural signals (FRESH/premiere tags, platform links)."""
    text = f"{title} {body}".lower()
    matched = [kw for kw in keywords if _phrase_in(text, kw)]
    if _FRESH_RE.search(title or ""):
        matched.append("[fresh]")
    plat = _PLATFORM_RE.search(url or "")
    if plat:
        matched.append(f"link:{plat.group(1).split('.')[0]}")
    return matched


def compute_relevance(matched_keywords: list[str], llm_score: float | None) -> float | None:
    """Combine the deterministic keyword signal with last30days' LLM score into the
    relevance_score used to RANK the dashboard (it never gates inclusion — that's
    already decided by check_keywords).

    Inputs:
      - matched_keywords: the keywords this post hit (>=1, since it passed the gate).
        len() is a cheap proxy for keyword-strength.
      - llm_score: last30days final_score, roughly 0-100, or None if the LLM layer
        was unavailable for this post.

    Returns a float for ordering (higher = surface first), or None.

    Policy: keyword density is a cheap on-topic signal, capped so it tunes
    rather than dominates. The LLM score is authoritative when present.
    """
    kw_bonus = min(len(matched_keywords), 5) * 2.0  # 0..10, capped
    if llm_score is None:
        # LLM unavailable for this post: rank by keyword density alone. Still a
        # real (low) score so denser-on-topic posts surface above sparse ones;
        # the dashboard's discovered_at tiebreak orders within a tier.
        return round(kw_bonus, 2)
    # LLM understood the semantics — treat it as the base, nudged by density.
    return round(llm_score + kw_bonus, 2)


def score_with_llm(posts: list[dict]) -> None:
    """Fill each post's llm_score (0-100) via last30days' deepseek client. Best-effort."""
    if not posts:
        return
    try:
        cfg = l30_env.get_config()
        runtime, client = l30_prov.resolve_runtime(cfg, "quick")
        if client is None:
            logger.warning("no LLM client available; keeping keyword-only ranking")
            return
        lines = [f"{i}. r/{p['subreddit']}: {p['title']}" for i, p in enumerate(posts)]
        prompt = (
            "Rate each Reddit post's relevance to discovering NEW or emerging indie "
            "and underground MUSIC (new releases, emerging artists, bandcamp/soundcloud "
            "finds) on a 0-100 scale. Return ONLY a JSON object mapping the index (as a "
            "string) to an integer 0-100. Posts:\n" + "\n".join(lines)
        )
        data = client.generate_json(runtime.rerank_model, prompt)
        if not isinstance(data, dict):
            return
        for i, p in enumerate(posts):
            v = data.get(str(i))
            try:
                p["llm_score"] = float(v) if v is not None else None
            except (TypeError, ValueError):
                p["llm_score"] = None
    except Exception as e:
        logger.warning(f"LLM scoring failed ({e}); continuing with keyword-only ranking")


def store_posts(conn: sqlite3.Connection, records: list[dict]) -> int:
    cur = conn.cursor()
    new = 0
    for r in records:
        cur.execute(
            """INSERT OR IGNORE INTO posts
               (subreddit, reddit_id, title, url, author, score, created_utc,
                matched_keywords, raw_json, relevance_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r["subreddit"], r["reddit_id"], r["title"], r["url"], r["author"],
                r["score"], r["created_utc"], json.dumps(r["matched_keywords"]),
                json.dumps(r), r.get("relevance_score"),
            ),
        )
        if cur.rowcount:
            new += 1
            logger.info(f"new: r/{r['subreddit']} - {r['title'][:60]}")
    conn.commit()
    return new


def run_dashboard() -> None:
    try:
        subprocess.run([sys.executable, str(SCRIPT_DIR / "gen_dashboard.py")], check=True)
    except subprocess.CalledProcessError as e:
        logger.warning(f"dashboard generation failed (Step 2 path fix pending): {e}")


def _read_cursor() -> int:
    try:
        return int(CURSOR_PATH.read_text().strip())
    except (OSError, ValueError):
        return 0


def _select_window(all_subs: list[str]) -> list[str]:
    """Rotate through the full sub list across runs so no single run hammers Reddit."""
    if not all_subs:
        return []
    n = len(all_subs)
    off = _read_cursor() % n
    window = (all_subs + all_subs)[off : off + SUB_WINDOW]  # wrap-around
    CURSOR_PATH.write_text(str((off + SUB_WINDOW) % n))
    return window


def acquire_lock():
    """Single-instance guard: take an exclusive, non-blocking lock so a 6h cron
    fire never overlaps a manual run (or a previous run that ran long) — two
    monitors on one residential IP just deepen Reddit throttling. Returns the
    held file handle (keep it referenced for the process lifetime) or None if
    another instance holds it."""
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def probe_tier() -> str:
    """Classify which retrieval tier Reddit is currently serving, cheaply.

    Returns:
      'throttled' — zero items came back (rate-limited or down) -> skip the run.
      'thin'      — items came back but ALL lack selftext AND have score 0,
                    the signature of the title-only RSS fallback -> skip.
      'ok'        — at least one item has real body text or a real score -> run.

    One probe call on a text-heavy sub. Link-only subs would false-flag 'thin'
    (legit link posts have no selftext), so PROBE_SUB is deliberately text-heavy.

    Bounded by PROBE_TIMEOUT: when throttled, reddit_rss retries across tiers with
    backoff and can hang for minutes — a guard must stay cheap, so a timeout is
    itself a 'throttled' signal (the IP is clearly not healthy). SIGALRM is fine
    here: main() runs on the main thread.
    """
    def _timeout(signum, frame):
        raise TimeoutError("probe exceeded PROBE_TIMEOUT")
    old = signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(PROBE_TIMEOUT)
    try:
        items = reddit_rss.search_rss(TOPIC, depth=RSS_DEPTH, subreddits=[PROBE_SUB]) or []
    except TimeoutError:
        logger.warning(f"tier probe timed out after {PROBE_TIMEOUT}s; treating as throttled")
        return "throttled"
    except Exception as e:
        logger.warning(f"tier probe failed ({e}); treating as throttled")
        return "throttled"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    if not items:
        return "throttled"
    rich = any((it.get("selftext") or "").strip() or _int(it.get("score")) > 0
               for it in items)
    return "ok" if rich else "thin"


def main() -> int:
    lock = acquire_lock()
    if lock is None:
        logger.warning("another monitor instance is running; skipping this run")
        return 0
    tier = probe_tier()
    if tier != "ok":
        # Skip WITHOUT regenerating the dashboard — a thin/throttled run would
        # only insert junk (or nothing) and a 0-delta rebuild can present stale
        # data as fresh. Better to no-op and let the next 6h fire try a cool IP.
        logger.warning(f"retrieval tier '{tier}' (not 'ok'); skipping run to avoid thin data")
        return 0
    logger.info("tier probe ok (rich tier available); proceeding")

    cfg = load_config()
    all_subs = cfg.get("subreddits", [])
    keywords = cfg.get("keywords", [])

    subreddits = _select_window(all_subs)
    logger.info(f"covering {len(subreddits)} of {len(all_subs)} subreddits (rotating window)")
    raw = fetch_posts(subreddits)
    # Phase 1 — keyword gate (inclusion only, no ranking)
    gated: list[dict] = []
    for p in raw:
        matched = check_keywords(p["title"], p["body"], keywords, p.get("url", ""))
        if not matched:
            continue  # keyword gate = source of truth for inclusion
        p["matched_keywords"] = matched
        gated.append(p)

    # Phase 2 — LLM scoring (fills llm_score on each post)
    score_with_llm(gated)

    # Phase 3 — hybrid ranking (keyword density + LLM score)
    for p in gated:
        p["relevance_score"] = compute_relevance(p["matched_keywords"], p.get("llm_score"))

    conn = get_conn()
    new = store_posts(conn, gated)
    conn.close()
    logger.info(f"{len(gated)} keyword-matched, {new} new inserted")
    run_dashboard()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
