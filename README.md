# Reddit Monitor with Webshare.io Residential Proxies

A Python tool for monitoring Reddit subreddits using rotating residential proxies from Webshare.io. Avoids IP blocks by rotating proxies per request.

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env and add your Webshare API key:
   # WEBSHARE_API_KEY=iknqkujezim0p0bkjcnjrcrbfax6522aakuzi17t
   ```

3. **Edit config.json** to customize subreddits and keywords.

## Usage

```bash
# Test proxy connection
python reddit_monitor.py test-proxies

# Add subreddits to monitor
python reddit_monitor.py add-subreddit art
python reddit_monitor.py add-subreddit contemporaryart
python reddit_monitor.py add-subreddit artmarket

# Add keywords to track
python reddit_monitor.py add-keyword exhibition
python reddit_monitor.py add-keyword gallery
python reddit_monitor.py add-keyword "artist opportunity"

# View current configuration
python reddit_monitor.py list

# Start monitoring
python reddit_monitor.py run

# Export recent posts (last 24 hours)
python reddit_monitor.py export
python reddit_monitor.py export --hours 48 --output my_posts.json
```

## Features

- **Rotating Residential Proxies**: Uses Webshare.io API to fetch and rotate proxies
- **User-Agent Rotation**: Cycles through different browser User-Agent strings
- **Retry Logic**: Handles 429/403 errors with exponential backoff
- **SQLite Storage**: Stores matched posts with full metadata
- **Keyword Matching**: Tracks multiple keywords per post
- **Score Filtering**: Configurable minimum score threshold
- **JSON Export**: Export matched posts for further processing

## Configuration (config.json)

```json
{
  "subreddits": ["art", "contemporaryart"],
  "keywords": ["exhibition", "gallery", "opening"],
  "check_interval_minutes": 5,
  "min_score_threshold": 5,
  "max_posts_per_check": 25
}
```

## Database Schema

Posts are stored in SQLite with:
- `subreddit` - Subreddit name
- `reddit_id` - Reddit post ID (unique)
- `title` - Post title
- `url` - Post URL
- `author` - Reddit username
- `score` - Upvote score
- `created_utc` - Unix timestamp
- `matched_keywords` - JSON array of matched keywords
- `raw_json` - Full Reddit API response
- `discovered_at` - When post was first seen

## Proxy Rotation Details

The tool:
1. Fetches proxy list from Webshare API every 5 minutes (cached)
2. Randomly selects a proxy for each request
3. Falls back to cached proxies if API fetch fails
4. Retries with different proxies on 403/429 errors

## Rate Limiting

- Random delays (2-5s) between subreddit checks
- Configurable check interval (default: 5 minutes)
- Exponential backoff on errors
- Score filtering to skip low-engagement posts

## Troubleshooting

**No proxies fetched:**
- Check WEBSHARE_API_KEY in .env
- Verify API key has active proxy plan

**All requests fail:**
- Run `test-proxies` to check connectivity
- Webshare proxies may need time to activate
- Try increasing retry delays

**Rate limited by Reddit:**
- Increase check_interval_minutes
- Reduce max_posts_per_check
- Add more proxies to pool
