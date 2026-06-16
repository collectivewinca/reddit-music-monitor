# Reddit Music Monitor

A Python-based Reddit monitoring system that tracks indie artists and music-related posts across 175 subreddits. Designed to discover emerging musicians from Sweden, Copenhagen, Morocco, Mexico, India, Hungary, Austria, Norway, South America, Japan, and Southeast Asia.

**Live Dashboard:** https://olive-monsoon-n9ct.here.now/  
**GitHub Repo:** https://github.com/collectivewinca/reddit-music-monitor

---

## What This Project Does

This tool continuously monitors Reddit for music-related posts that match specific criteria:

- **Indie artists** and **underground bands** from target regions
- **New releases** (singles, albums, EPs, mixtapes)
- **Genre-specific** content (shoegaze, dream pop, post-rock, ambient, etc.)
- **Platform links** (Bandcamp, Spotify, SoundCloud)
- **DIY/music collective** activity

### Key Features

- **Keyless Reddit Retrieval**: Uses last30days' RSS layer (`lib.reddit_rss.search_rss`) — no residential proxies, no API key needed for retrieval
- **Residential IP Required**: Reddit 403-blocks datacenter IPs, so the monitor runs on a Mac
- **Multi-Region Focus**: Monitors 175 subreddits across 20+ countries/cities
- **Smart Keyword Gate**: 214-keyword matcher (`check_keywords`) gates inclusion — no false positives
- **LLM-Enhanced Ranking**: `compute_relevance` combines keyword density with an optional deepseek-v4-flash LLM relevance score (via Ollama Cloud through last30days)
- **SQLite Storage**: Stores posts with metadata, matched keywords, raw JSON, and a `relevance_score` column
- **Web Dashboard**: `gen_dashboard.py` renders `index.html` from the database
- **Scheduled Runs**: Cron job every 6 hours on a Mac

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  last30days (keyless RSS layer)                                  │
│  lib.reddit_rss.search_rss — multi-tier, rate-limit-aware fetch  │
│  (no proxies, no API key)                                        │
└──────────────────────────┬───────────────────────────────────────┘
                           │  broad per-subreddit set
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  l30d_monitor.py — keyword gate + relevance ranking              │
│                                                                  │
│  1. check_keywords — 214-keyword substring match (inclusion)     │
│  2. compute_relevance — keyword density + optional LLM score     │
│     (deepseek-v4-flash via Ollama Cloud through last30days)      │
└──────────────────────────┬───────────────────────────────────────┘
                           │  gated, ranked posts
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  SQLite (reddit_monitor.db)                                      │
│  posts table with relevance_score REAL column                    │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  gen_dashboard.py → index.html → here.now (live dashboard)       │
└──────────────────────────────────────────────────────────────────┘
```

### Components

| File | Purpose |
|------|---------|
| `l30d_monitor.py` | **Active monitor** — last30days retrieval, keyword gate, relevance ranking, SQLite storage |
| `reddit_monitor.py` | **RETIRED** — old Webshare residential-proxy approach (dead, unused) |
| `config.json` | 175 subreddits and 214 keywords configuration |
| `reddit_monitor.db` | SQLite database with posts table (includes `relevance_score` column) |
| `gen_dashboard.py` | Renders `index.html` dashboard from the database |
| `requirements.txt` | Python dependencies |

---

## Installation

### Prerequisites

- Python 3.8+
- A Mac (or any machine with a residential IP — Reddit blocks datacenter IPs)
- last30days skill installed in `~/.claude/skills/last30days/` (provides `lib.reddit_rss`)

### Setup

```bash
# Clone the repo
git clone https://github.com/collectivewinca/reddit-music-monitor.git
cd reddit-music-monitor

# Install dependencies
pip install -r requirements.txt

# Verify last30days RSS layer is available
python3 -c "import sys; sys.path.insert(0, '$HOME/.claude/skills/last30days/skills/last30days/scripts'); from lib import reddit_rss; print('OK')"
```

---

## Usage

```bash
# Run the monitor (fetches, filters, ranks, stores, generates dashboard)
python3 l30d_monitor.py
```

### Scheduled Execution (cron)

```bash
# Run every 6 hours
0 */6 * * * cd /path/to/reddit-music-monitor && python3 l30d_monitor.py >> l30d_monitor.log 2>&1
```

---

## Configuration

### Subreddits (config.json — 175 total)

**Music-focused:**
- `indieheads`, `WeAreTheMusicMakers`, `listentothis`
- Genre subs: `shoegaze`, `dreampop`, `postrock`, `ambientmusic`, `experimentalmusic`
- Production: `musicproduction`, `edmproduction`, `synthesizers`, `modular`

**Regional:**
- **Sweden:** `sweden`, `Stockholm`, `Gothenburg`, `Malmo`
- **Denmark:** `Denmark`, `copenhagen`
- **Morocco:** `Morocco`, `Casablanca`, `marrakech`
- **Mexico:** `Mexico`, `MexicoCity`, `Guadalajara`, `Monterrey`
- **India:** `india`, `bangalore`, `mumbai`, `delhi`
- **Hungary:** `hungary`, `Budapest`
- **Austria:** `Austria`, `vienna`
- **Norway:** `norway`, `oslo`, `bergen`, `trondheim`
- **South America:** `argentina`, `brazil`, `chile`, `colombia`, `peru`
- **Asia:** `Japan`, `Thailand`, `Philippines`, `Indonesia`, `VietNam`, `Malaysia`, `Singapore`

### Keywords (config.json — 214 total)

**Artist descriptors:**
- `indie artist`, `indie band`, `unsigned artist`, `diy artist`, `emerging artist`
- `local band`, `local artist`, `underground artist`

**Releases:**
- `new release`, `debut single`, `debut album`, `ep release`
- `bandcamp album`, `bandcamp single`, `spotify link`

**Genres:**
- `shoegaze`, `dream pop`, `post-rock`, `ambient music`
- `bedroom pop`, `lo-fi hip hop`, `experimental music`

**Regional:**
- `swedish band`, `swedish artist`, `swedish music`
- `mexican music`, `mexico city band`, `latin music`
- `japanese band`, `tokyo music`, `indian music`

---

## Database Schema

```sql
CREATE TABLE posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subreddit TEXT NOT NULL,
    reddit_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    author TEXT NOT NULL,
    score INTEGER NOT NULL,
    created_utc REAL NOT NULL,
    matched_keywords TEXT,       -- JSON array of matched keywords
    raw_json TEXT,               -- Full post data from last30days
    relevance_score REAL,        -- Combined keyword-density + LLM score (for ranking)
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Query Examples

```bash
# Recent posts (ranked by relevance)
sqlite3 reddit_monitor.db "SELECT title, relevance_score FROM posts ORDER BY relevance_score DESC LIMIT 10;"

# Posts by region
sqlite3 reddit_monitor.db "SELECT * FROM posts WHERE matched_keywords LIKE '%swedish%';"

# Bandcamp links
sqlite3 reddit_monitor.db "SELECT title, url FROM posts WHERE url LIKE '%bandcamp%';"

# Stats
sqlite3 reddit_monitor.db "SELECT COUNT(*), COUNT(DISTINCT subreddit) FROM posts;"
```

---

## Pipeline Details

### 1. Retrieval — last30days RSS Layer

`l30d_monitor.py` calls `lib.reddit_rss.search_rss` directly (the same keyless, multi-tier RSS/listing fetch that last30days uses). It processes subreddits in batches of 20 (175 subs → 9 calls). No residential proxies, no API key — the Mac's residential IP is sufficient because Reddit only blocks datacenter ranges.

### 2. Keyword Gate — Inclusion

Every fetched post runs through `check_keywords(title, body, keywords)`, a case-insensitive substring match against the 214-keyword list. Posts that match zero keywords are discarded. This is the **source of truth for inclusion** — no post enters the database without at least one keyword hit.

### 3. Relevance Ranking

`compute_relevance` produces the `relevance_score` used for dashboard ordering:

- **Keyword density**: `min(len(matched_keywords), 5) × 2.0` (0–10 range, capped)
- **LLM score**: When available, last30days provides a `final_score` (~0–100) from deepseek-v4-flash via Ollama Cloud. The final score is `llm_score + kw_bonus`.
- **Fallback**: When the LLM layer is unavailable, posts rank by keyword density alone (still meaningful for ordering).

The relevance score **never gates inclusion** — that's already decided by `check_keywords`.

### 4. Storage

Posts are inserted into the SQLite `posts` table with `INSERT OR IGNORE` (deduplication by `reddit_id`). The `relevance_score` column is added via `ALTER TABLE` if missing (idempotent).

### 5. Dashboard

After storage, `l30d_monitor.py` calls `gen_dashboard.py`, which queries the database and renders `index.html` for the here.now live dashboard.

---

## Deployment

### Web Dashboard (here.now)

The dashboard auto-publishes to here.now:

```bash
# Build HTML from database
python3 gen_dashboard.py

# Publish via here.now API
curl -sS https://here.now/api/v1/publish \
  -H "Authorization: Bearer $HERENOW_API_KEY" \
  -d '{"files": [{"path": "index.html", "size": 38000, "contentType": "text/html"}]}'
```

### Email Reports

Daily summaries via Himalaya (Proton Mail):

```bash
himalaya template send << 'EOF'
From: alet@velab.org
To: alet@velab.org
Subject: Reddit Music Monitor - Daily Summary

Dashboard: https://olive-monsoon-n9ct.here.now/
...
EOF
```

### Cron Jobs

```bash
# Run monitor every 6 hours
0 */6 * * * cd /path/to/reddit-music-monitor && python3 l30d_monitor.py >> l30d_monitor.log 2>&1

# Daily email report (optional)
0 9 * * * cd /path/to/reddit-music-monitor && python3 email_report.py
```

---

## Project History

This project was built iteratively with Claude Code:

1. **Initial Setup**: Created Python scraper with Webshare proxy integration (now retired)
2. **Switch to last30days**: Replaced Webshare proxies with last30days' keyless RSS retrieval — simpler, no API key, no proxy costs
3. **Keyword Tuning**: Expanded from basic keywords to 214 music-specific terms
4. **LLM Relevance**: Added deepseek-v4-flash scoring via Ollama Cloud for smarter ranking
5. **Web Dashboard**: Built HTML dashboard and published to here.now
6. **Email Integration**: Added daily reports via Himalaya/Proton Mail
7. **GitHub Repo**: Published to collectivewinca/reddit-music-monitor

### Key Decisions

- **SQLite over Postgres**: Single-file, zero-config, sufficient for this scale
- **last30days over proxies**: Keyless RSS retrieval avoids proxy costs and API key management; residential IP is the only requirement
- **Keyword gate + LLM ranking**: Decoupled inclusion (cheap substring match) from ranking (optional LLM) — fast, flexible, no lock-in
- **Regional focus**: Prioritized underrepresented music scenes

---

## Troubleshooting

**No posts found:**
- Check that last30days is installed: `ls ~/.claude/skills/last30days/skills/last30days/scripts/lib/reddit_rss.py`
- Verify config.json has subreddits and keywords
- Run `python3 l30d_monitor.py` and check logs

**Reddit blocks requests:**
- Ensure you're running on a residential IP (Mac at home), not a datacenter VM
- last30days' RSS layer handles rate-limiting internally

**Database locked:**
- Stop any running monitor: `pkill -f l30d_monitor.py`
- Check for zombie processes: `ps aux | grep l30d_monitor`

**Dashboard not updating:**
- Run `python3 gen_dashboard.py` manually to check for errors
- Verify the database has new posts

---

## License

MIT - Feel free to fork and adapt for your own music discovery needs.

---

## Credits

- **last30days** — Keyless Reddit RSS retrieval layer
- **here.now** — Free static hosting for the dashboard
- **Himalaya** — CLI email client for reports
- **Claude Code** — Assisted development
