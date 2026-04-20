# Reddit Music Monitor

A Python-based Reddit monitoring system that tracks indie artists and music-related posts across 100+ subreddits using rotating residential proxies from Webshare.io. Designed to discover emerging musicians from Sweden, Copenhagen, Morocco, Mexico, India, Hungary, Austria, Norway, South America, Japan, and Southeast Asia.

**Live Dashboard:** https://hearty-garnet-eq9j.here.now/  
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

- **Rotating Residential Proxies**: Uses Webshare.io API to fetch and rotate proxies, avoiding Reddit IP blocks
- **Multi-Region Focus**: Monitors subreddits for 20+ countries/cities
- **Smart Keyword Matching**: 150+ music-specific keywords (no false positives)
- **SQLite Storage**: Stores posts with metadata, matched keywords, and raw JSON
- **Auto-Restart**: Cron job ensures the monitor stays running
- **Web Dashboard**: Auto-published to here.now for easy browsing
- **Email Reports**: Daily summaries sent via Himalaya/Proton Mail

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Webshare.io   │────▶│  Reddit Monitor    │────▶│   SQLite DB     │
│  (Proxies API)  │     │  (Python + requests)│     │  (reddit_monitor.db)
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │   here.now       │
                        │  (Web Dashboard) │
                        └──────────────────┘
```

### Components

| File | Purpose |
|------|---------|
| `reddit_monitor.py` | Main application (600+ lines) with proxy rotation, Reddit API scraping, SQLite storage |
| `config.json` | Subreddits (100+) and keywords (150+) configuration |
| `.env` | Webshare API key and optional Reddit credentials |
| `reddit_monitor.db` | SQLite database with posts table |
| `requirements.txt` | Python dependencies |

---

## Installation

### Prerequisites

- Python 3.8+
- Webshare.io account with residential proxy plan
- (Optional) Reddit API credentials for higher rate limits

### Setup

```bash
# Clone the repo
git clone https://github.com/collectivewinca/reddit-music-monitor.git
cd reddit-music-monitor

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your Webshare API key:
# WEBSHARE_API_KEY=your_key_here

# Test proxy connection
python3 reddit_monitor.py test-proxies
```

---

## Usage

### CLI Commands

```bash
# Add subreddits to monitor
python3 reddit_monitor.py add-subreddit indieheads
python3 reddit_monitor.py add-subreddit WeAreTheMusicMakers

# Add keywords to track
python3 reddit_monitor.py add-keyword "indie artist"
python3 reddit_monitor.py add-keyword "bandcamp"

# View current configuration
python3 reddit_monitor.py list

# Start monitoring (foreground)
python3 reddit_monitor.py run

# Test proxy connection
python3 reddit_monitor.py test-proxies

# Export recent posts to JSON
python3 reddit_monitor.py export --hours 24 --output posts.json
```

### Running as Background Service

```bash
# Start in background
nohup python3 reddit_monitor.py run > monitor.log 2>&1 &

# Or use the included cron job for auto-restart
crontab -l | grep reddit-music-monitor
```

---

## Configuration

### Subreddits (config.json)

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

### Keywords (config.json)

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
    matched_keywords TEXT,  -- JSON array
    raw_json TEXT,            -- Full Reddit API response
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Query Examples

```bash
# Recent posts
sqlite3 reddit_monitor.db "SELECT * FROM posts ORDER BY discovered_at DESC LIMIT 10;"

# Posts by region
sqlite3 reddit_monitor.db "SELECT * FROM posts WHERE matched_keywords LIKE '%swedish%';"

# Bandcamp links
sqlite3 reddit_monitor.db "SELECT title, url FROM posts WHERE url LIKE '%bandcamp%';"

# Stats
sqlite3 reddit_monitor.db "SELECT COUNT(*), COUNT(DISTINCT subreddit) FROM posts;"
```

---

## Proxy Rotation

The tool uses Webshare.io's API to fetch residential proxies:

1. **Fetch proxy list** from `https://proxy.webshare.io/api/v2/proxy/list/`
2. **Cache for 5 minutes** to avoid API rate limits
3. **Random selection** per request for distribution
4. **Retry logic** with different proxies on 403/429 errors
5. **User-Agent rotation** to avoid fingerprinting

### Proxy Configuration

Each request uses:
```python
proxy_url = f"http://{username}:{password}@{proxy_address}:{port}"
proxies = {
    "http": proxy_url,
    "https": proxy_url
}
```

---

## Deployment

### Web Dashboard (here.now)

The dashboard auto-publishes to here.now:

```bash
# Build HTML from database
python3 -c "
import sqlite3
# ... (see repo for full script)
"

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

Dashboard: https://hearty-garnet-eq9j.here.now/
Stats: 574 posts from 73 subreddits
...
EOF
```

### Cron Jobs

```bash
# Auto-restart monitor every 10 minutes
*/10 * * * * cd ~/reddit-webshare-monitor && (pgrep -f "reddit_monitor.py run" || nohup python3 reddit_monitor.py run > monitor.log 2>&1 &)

# Daily email report (optional)
0 9 * * * cd ~/reddit-webshare-monitor && python3 email_report.py
```

---

## Project History

This project was built iteratively with Claude Code:

1. **Initial Setup**: Created Python scraper with Webshare proxy integration
2. **Keyword Tuning**: Expanded from basic keywords to 150+ music-specific terms
3. **False Positive Removal**: Cleaned database of non-music matches (116 posts removed)
4. **Web Dashboard**: Built HTML dashboard and published to here.now
5. **Email Integration**: Added daily reports via Himalaya/Proton Mail
6. **GitHub Repo**: Published to collectivewinca/reddit-music-monitor

### Key Decisions

- **SQLite over Postgres**: Single-file, zero-config, sufficient for this scale
- **JSON keywords**: Flexible matching, easy to inspect
- **Proxy rotation**: Avoids Reddit blocks that affected VM IPs
- **Regional focus**: Prioritized underrepresented music scenes

---

## Troubleshooting

**No proxies fetched:**
- Check `WEBSHARE_API_KEY` in `.env`
- Verify active proxy plan at webshare.io

**All requests fail:**
- Run `python3 reddit_monitor.py test-proxies`
- Check proxy list: `curl -H "Authorization: Token $WEBSHARE_API_KEY" https://proxy.webshare.io/api/v2/proxy/list/`

**Rate limited by Reddit:**
- Increase `check_interval_minutes` in config.json
- Reduce `max_posts_per_check`
- Verify proxy rotation is working

**Database locked:**
- Stop the monitor: `pkill -f reddit_monitor.py`
- Check for zombie processes: `ps aux | grep reddit_monitor`

---

## License

MIT - Feel free to fork and adapt for your own music discovery needs.

---

## Credits

- **Webshare.io** - Residential proxy infrastructure
- **here.now** - Free static hosting for the dashboard
- **Himalaya** - CLI email client for reports
- **Claude Code** - Assisted development
