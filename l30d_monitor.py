#!/usr/bin/env python3
"""Reddit Music Monitor — last30days (keyless) retrieval + keyword gate + LLM relevance.

Replaces the retired Webshare-proxy reddit_monitor.py. Runs on the Mac, where
last30days' keyless Reddit retrieval works (datacenter IPs are 403-blocked).

Pipeline: last30days fetch -> keyword gate (inclusion) -> compute_relevance
(ranking) -> SQLite -> dashboard.
"""
from __future__ import annotations

import json
import logging
import re
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
TOPIC = "new indie and underground music releases and emerging artists"
# Reddit throttles ~175 rapid feed pulls from one IP, so each run covers a
# ROTATING window of the sub list (full sweep ~daily at a 6h cron) and paces
# the RSS calls: small batches, light depth, and a sleep between batches.
SUB_WINDOW = 30          # subreddits processed per run (rotates across runs)
BATCH_SIZE = 6           # subreddits per last30days RSS call
RSS_DEPTH = "default"    # 'deep' fires too many feed URLs per sub at scale
INTER_BATCH_SLEEP = 5    # seconds between RSS batches

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


def check_keywords(title: str, body: str, keywords: list[str]) -> list[str]:
    """Reused from reddit_monitor.py: case-insensitive substring match (the inclusion gate)."""
    text = f"{title} {body}".lower()
    return [kw for kw in keywords if kw.lower() in text]


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


def main() -> int:
    cfg = load_config()
    all_subs = cfg.get("subreddits", [])
    keywords = cfg.get("keywords", [])

    subreddits = _select_window(all_subs)
    logger.info(f"covering {len(subreddits)} of {len(all_subs)} subreddits (rotating window)")
    raw = fetch_posts(subreddits)
    # Phase 1 — keyword gate (inclusion only, no ranking)
    gated: list[dict] = []
    for p in raw:
        matched = check_keywords(p["title"], p["body"], keywords)
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
