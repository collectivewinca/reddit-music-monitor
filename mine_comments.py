#!/usr/bin/env python3
"""Comment-intelligence layer for reddit-music-monitor.

The post-title pipeline (l30d_monitor.py) finds the THREADS; this harvests the
discovery signal INSIDE them. For recent music/discussion threads it reads the
comments via opencli `reddit read` (logged-in Chrome CDP), extracts the
artist/band names commenters actually recommend (deepseek-v4-flash via
last30days), and aggregates them into a `comment_artists` table ranked by how
many distinct threads mention each.

Run after l30d_monitor.py. Requires Chrome running with CDP (same as reddit-dive)
for opencli's cookie-based `reddit read`.
"""
from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "reddit_monitor.db"
OPENCLI = Path.home() / ".npm-global/bin/opencli"
MAX_THREADS = 12          # bound opencli + LLM calls per run
COMMENTS_CHAR_CAP = 7000  # truncate long threads before the LLM

# Subreddits where comments carry recommendations (not pure production-advice).
DISCOVERY_SUBS = (
    "indieheads", "listentothis", "Music", "LetsTalkMusic", "musicsuggestions",
    "ifyoulikeblank", "postrock", "shoegaze", "dreampop", "ambientmusic",
    "jazz", "metal", "vaporwave", "hiphopheads", "electronicmusic", "indie_rock",
)

# last30days reasoning client (deepseek-v4-flash via Ollama Cloud).
_L30 = Path.home() / ".claude/skills/last30days/skills/last30days/scripts"
if str(_L30) not in sys.path:
    sys.path.insert(0, str(_L30))
from lib import env as l30_env, providers as l30_prov  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("mine_comments")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS comment_artists (
            name_key TEXT PRIMARY KEY,
            name TEXT,
            mentions INTEGER DEFAULT 0,
            sources TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.commit()
    return conn


def select_threads(conn: sqlite3.Connection) -> list[dict]:
    # SQLite TEXT IN(...) is case-sensitive and posts.subreddit is stored verbatim
    # from RSS (e.g. "music"), so match case-insensitively — otherwise mixed-case
    # entries like "Music"/"LetsTalkMusic" here would silently never match.
    subs_lc = tuple(s.lower() for s in DISCOVERY_SUBS)
    qmarks = ",".join("?" * len(subs_lc))
    rows = conn.execute(
        f"""SELECT reddit_id, subreddit, title, url FROM posts
            WHERE url LIKE '%reddit.com/r/%/comments/%'
              AND LOWER(subreddit) IN ({qmarks})
            ORDER BY discovered_at DESC LIMIT ?""",
        (*subs_lc, MAX_THREADS),
    ).fetchall()
    return [{"id": r[0], "subreddit": r[1], "title": r[2], "url": r[3]} for r in rows]


def fetch_comments(url: str) -> str:
    """Read a thread + comments via opencli (best-effort; needs Chrome CDP)."""
    try:
        out = subprocess.run(
            [str(OPENCLI), "reddit", "read", url],
            capture_output=True, text=True, timeout=90,
        )
        return (out.stdout or "")[:COMMENTS_CHAR_CAP]
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f"opencli read failed for {url}: {e}")
        return ""


def extract_artists(client, model: str, title: str, comments: str) -> list[str]:
    prompt = (
        f'These are Reddit comments on a music thread titled "{title}". '
        "Extract every distinct ARTIST or BAND name that commenters recommend or "
        "mention as music worth listening to. Ignore producers, plugins, gear, and "
        'non-music chatter. Return ONLY a JSON object: {"artists": ["Name1", "Name2"]}. '
        "Empty list if none.\n\nComments:\n" + comments
    )
    try:
        data = client.generate_json(model, prompt)
        arts = data.get("artists") if isinstance(data, dict) else None
        if not isinstance(arts, list):
            return []
        return [a.strip() for a in arts if isinstance(a, str) and a.strip()]
    except Exception as e:
        logger.warning(f"extract failed: {e}")
        return []


def upsert(conn: sqlite3.Connection, name: str, source_id: str) -> None:
    key = name.lower()
    row = conn.execute(
        "SELECT mentions, sources FROM comment_artists WHERE name_key=?", (key,)
    ).fetchone()
    if row:
        srcs = set((row[1] or "").split(",")) | {source_id}
        conn.execute(
            "UPDATE comment_artists SET mentions=?, sources=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE name_key=?",
            (row[0] + 1, ",".join(sorted(s for s in srcs if s)), key),
        )
    else:
        conn.execute(
            "INSERT INTO comment_artists (name_key, name, mentions, sources) VALUES (?,?,1,?)",
            (key, name, source_id),
        )


def main() -> int:
    conn = get_conn()
    threads = select_threads(conn)
    if not threads:
        logger.info("no discovery threads to mine")
        return 0

    cfg = l30_env.get_config()
    runtime, client = l30_prov.resolve_runtime(cfg, "quick")
    if client is None:
        logger.error("no LLM client available; cannot extract")
        return 1

    total = 0
    for t in threads:
        comments = fetch_comments(t["url"])
        if not comments:
            continue
        artists = extract_artists(client, runtime.rerank_model, t["title"], comments)
        for a in artists:
            upsert(conn, a, t["id"])
            total += 1
        conn.commit()  # persist per-thread so a mid-run kill keeps prior progress
        logger.info(f'r/{t["subreddit"]}: {len(artists)} artists from "{t["title"][:45]}"')

    top = conn.execute(
        "SELECT name, mentions FROM comment_artists ORDER BY mentions DESC, updated_at DESC LIMIT 25"
    ).fetchall()
    logger.info(f"{total} artist mentions extracted this run; top community recommendations:")
    for name, m in top:
        print(f"  {m:3d}  {name}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
