#!/usr/bin/env python3
"""
Reddit Monitor with Webshare.io Residential Proxies
Monitors subreddits for keywords using rotating residential proxies
"""

import os
import sys
import json
import time
import random
import logging
import sqlite3
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('reddit_monitor.log')
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Constants
WEBSHARE_API_URL = "https://proxy.webshare.io/api/v2/proxy/list/"
REDDIT_JSON_URL = "https://www.reddit.com/r/{subreddit}/new.json"
DB_PATH = "reddit_monitor.db"
CONFIG_PATH = "config.json"

# Rotating User Agents
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0"
]


class WebshareProxyManager:
    """Manages Webshare.io proxy rotation"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.proxies: List[Dict] = []
        self.working_proxies: List[Dict] = []
        self.last_fetch = 0
        self.cache_duration = 300  # Refresh proxy list every 5 minutes
        
    def fetch_proxies(self) -> List[Dict]:
        """Fetch proxy list from Webshare API"""
        if time.time() - self.last_fetch < self.cache_duration and self.proxies:
            return self.proxies
            
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json"
        }
        
        params = {
            "mode": "direct",
            "page": 1,
            "page_size": 100
        }
        
        try:
            response = requests.get(
                WEBSHARE_API_URL,
                headers=headers,
                params=params,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            
            self.proxies = data.get("results", [])
            self.last_fetch = time.time()
            
            logger.info(f"Fetched {len(self.proxies)} proxies from Webshare")
            return self.proxies
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch proxies: {e}")
            return self.proxies  # Return cached if available
    
    def get_random_proxy(self) -> Optional[Dict]:
        """Get a random working proxy from the pool"""
        if not self.working_proxies:
            self.test_proxies()
        if self.working_proxies:
            return random.choice(self.working_proxies)
        # Fallback to any proxy if none tested working
        proxies = self.fetch_proxies()
        if proxies:
            return random.choice(proxies)
        return None
    
    def test_proxies(self):
        """Test all proxies and keep only working ones"""
        all_proxies = self.fetch_proxies()
        if not all_proxies:
            return
        
        test_url = "https://www.reddit.com/r/indieheads/hot.json?limit=1"
        headers = {"User-Agent": "reddit-music-monitor/1.0"}
        working = []
        
        logger.info(f"Testing {len(all_proxies)} proxies...")
        for proxy in all_proxies[:30]:  # Test first 30 to save time
            proxy_dict = self.get_proxy_dict(proxy)
            try:
                resp = requests.get(test_url, headers=headers, proxies=proxy_dict, timeout=8)
                if resp.status_code == 200:
                    working.append(proxy)
            except:
                pass
        
        self.working_proxies = working
        logger.info(f"Found {len(working)} working proxies")
    
    def mark_failed(self, proxy: Dict):
        """Remove proxy from working list"""
        addr = proxy.get('proxy_address')
        port = proxy.get('port')
        self.working_proxies = [p for p in self.working_proxies 
                                if not (p.get('proxy_address') == addr and p.get('port') == port)]
    
    def get_proxy_dict(self, proxy: Dict) -> Dict:
        """Convert proxy to requests-compatible dict"""
        proxy_url = f"http://{proxy['username']}:{proxy['password']}@{proxy['proxy_address']}:{proxy['port']}"
        return {
            "http": proxy_url,
            "https": proxy_url
        }


class RedditMonitor:
    """Reddit monitoring with proxy rotation"""
    
    def __init__(self):
        self.config = self.load_config()
        self.db_path = Path(DB_PATH)
        self.init_database()
        
        # Initialize proxy manager
        api_key = os.getenv("WEBSHARE_API_KEY")
        if not api_key:
            raise ValueError("WEBSHARE_API_KEY not found in environment")
        
        self.proxy_manager = WebshareProxyManager(api_key)
        
    def load_config(self) -> Dict:
        """Load configuration from JSON file"""
        config_path = Path(CONFIG_PATH)
        if not config_path.exists():
            default_config = {
                "subreddits": [],
                "keywords": [],
                "check_interval_minutes": 5,
                "min_score_threshold": 5,
                "max_posts_per_check": 25
            }
            config_path.write_text(json.dumps(default_config, indent=2))
            logger.info(f"Created default config at {CONFIG_PATH}")
            return default_config
        
        return json.loads(config_path.read_text())
    
    def save_config(self):
        """Save configuration to JSON file"""
        Path(CONFIG_PATH).write_text(json.dumps(self.config, indent=2))
    
    def init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subreddit TEXT NOT NULL,
                reddit_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                author TEXT NOT NULL,
                score INTEGER NOT NULL,
                created_utc REAL NOT NULL,
                matched_keywords TEXT,
                raw_json TEXT,
                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_reddit_id ON posts(reddit_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_subreddit ON posts(subreddit)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_discovered ON posts(discovered_at)
        """)
        
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    
    def make_request(self, url: str, max_retries: int = 5) -> Optional[Dict]:
        """Make HTTP request with proxy rotation and retry logic"""
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        for attempt in range(max_retries):
            proxy = self.proxy_manager.get_random_proxy()
            if not proxy:
                logger.error("No proxies available")
                return None
            
            proxy_dict = self.proxy_manager.get_proxy_dict(proxy)
            
            try:
                logger.debug(f"Using proxy: {proxy['proxy_address']}:{proxy['port']}")
                
                response = requests.get(
                    url,
                    headers=headers,
                    proxies=proxy_dict,
                    timeout=30,
                    allow_redirects=True
                )
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    logger.warning(f"Rate limited (429), retrying... (attempt {attempt + 1})")
                    time.sleep(random.uniform(2, 5))
                elif response.status_code == 403:
                    logger.warning(f"Blocked (403), trying different proxy... (attempt {attempt + 1})"); self.proxy_manager.mark_failed(proxy)
                    self.proxy_manager.mark_proxy_failed(proxy)
                    time.sleep(random.uniform(1, 3))
                else:
                    logger.warning(f"HTTP {response.status_code}, retrying...")
                    time.sleep(random.uniform(1, 2))
                    
            except requests.exceptions.ProxyError as e:
                logger.warning(f"Proxy error: {e}, retrying...")
                time.sleep(random.uniform(1, 2))
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout, retrying...")
                time.sleep(random.uniform(2, 4))
            except Exception as e:
                logger.error(f"Request error: {e}")
                time.sleep(random.uniform(1, 2))
        
        logger.error(f"Failed after {max_retries} attempts")
        return None
    
    def fetch_subreddit(self, subreddit: str) -> List[Dict]:
        """Fetch new posts from a subreddit"""
        url = REDDIT_JSON_URL.format(subreddit=subreddit)
        url += f"?limit={self.config.get('max_posts_per_check', 25)}"
        
        data = self.make_request(url)
        if not data or "data" not in data:
            logger.warning(f"No data returned for r/{subreddit}")
            return []
        
        posts = data["data"].get("children", [])
        return [post["data"] for post in posts]
    
    def check_keywords(self, title: str, selftext: str) -> List[str]:
        """Check if post matches any keywords"""
        text = f"{title} {selftext}".lower()
        matched = []
        
        for keyword in self.config.get("keywords", []):
            if keyword.lower() in text:
                matched.append(keyword)
        
        return matched
    
    def save_post(self, post: Dict, matched_keywords: List[str]):
        """Save post to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO posts 
                (subreddit, reddit_id, title, url, author, score, created_utc, matched_keywords, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                post.get("subreddit"),
                post.get("id"),
                post.get("title"),
                post.get("url"),
                post.get("author"),
                post.get("score", 0),
                post.get("created_utc", 0),
                json.dumps(matched_keywords),
                json.dumps(post)
            ))
            
            conn.commit()
            
            if cursor.rowcount > 0:
                logger.info(f"New post saved: r/{post.get('subreddit')} - {post.get('title')[:60]}...")
                logger.info(f"  URL: {post.get('url')}")
                logger.info(f"  Keywords matched: {matched_keywords}")
                return True
            
        except sqlite3.Error as e:
            logger.error(f"Database error: {e}")
        finally:
            conn.close()
        
        return False
    
    def check_subreddit(self, subreddit: str) -> Tuple[int, int]:
        """Check a subreddit for new matching posts"""
        posts = self.fetch_subreddit(subreddit)
        new_count = 0
        min_score = self.config.get("min_score_threshold", 5)
        
        for post in posts:
            if post.get("score", 0) < min_score:
                continue
            
            matched = self.check_keywords(
                post.get("title", ""),
                post.get("selftext", "")
            )
            
            if matched:
                if self.save_post(post, matched):
                    new_count += 1
        
        return len(posts), new_count
    
    def run_monitor_loop(self):
        """Main monitoring loop"""
        logger.info("Starting Reddit monitor...")
        logger.info(f"Tracking subreddits: {self.config.get('subreddits', [])}")
        logger.info(f"Keywords: {self.config.get('keywords', [])}")
        
        interval = self.config.get("check_interval_minutes", 5) * 60
        
        try:
            while True:
                total_checked = 0
                total_new = 0
                
                for subreddit in self.config.get("subreddits", []):
                    logger.info(f"Checking r/{subreddit}...")
                    checked, new = self.check_subreddit(subreddit)
                    total_checked += checked
                    total_new += new
                    
                    # Random delay between subreddits
                    time.sleep(random.uniform(2, 5))
                
                logger.info(f"Check complete: {total_checked} posts checked, {total_new} new matches")
                logger.info(f"Sleeping for {interval} seconds...")
                
                time.sleep(interval)
                
        except KeyboardInterrupt:
            logger.info("Monitor stopped by user")
    
    def add_subreddit(self, name: str):
        """Add a subreddit to monitor"""
        name = name.lower().strip().replace("r/", "")
        
        if name not in self.config["subreddits"]:
            self.config["subreddits"].append(name)
            self.save_config()
            logger.info(f"Added r/{name} to monitoring list")
        else:
            logger.info(f"r/{name} is already being monitored")
    
    def add_keyword(self, keyword: str):
        """Add a keyword to track"""
        keyword = keyword.lower().strip()
        
        if keyword not in self.config["keywords"]:
            self.config["keywords"].append(keyword)
            self.save_config()
            logger.info(f"Added keyword: '{keyword}'")
        else:
            logger.info(f"Keyword '{keyword}' is already being tracked")
    
    def list_config(self):
        """Display current configuration"""
        print("\n=== Reddit Monitor Configuration ===\n")
        print(f"Subreddits ({len(self.config.get('subreddits', []))}):")
        for sub in self.config.get("subreddits", []):
            print(f"  - r/{sub}")
        
        print(f"\nKeywords ({len(self.config.get('keywords', []))}):")
        for kw in self.config.get("keywords", []):
            print(f"  - {kw}")
        
        print(f"\nSettings:")
        print(f"  Check interval: {self.config.get('check_interval_minutes', 5)} minutes")
        print(f"  Min score threshold: {self.config.get('min_score_threshold', 5)}")
        print(f"  Max posts per check: {self.config.get('max_posts_per_check', 25)}")
        
        # Show recent posts count
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM posts")
        total_posts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM posts WHERE discovered_at > datetime('now', '-24 hours')")
        posts_24h = cursor.fetchone()[0]
        conn.close()
        
        print(f"\nDatabase:")
        print(f"  Total posts stored: {total_posts}")
        print(f"  Posts in last 24h: {posts_24h}")
    
    def export_recent(self, hours: int = 24, output: str = None):
        """Export recent posts to JSON"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT subreddit, title, url, author, score, created_utc, 
                   matched_keywords, discovered_at
            FROM posts 
            WHERE discovered_at > datetime('now', '-? hours')
            ORDER BY discovered_at DESC
        """, (hours,))
        
        posts = []
        for row in cursor.fetchall():
            posts.append({
                "subreddit": row[0],
                "title": row[1],
                "url": row[2],
                "author": row[3],
                "score": row[4],
                "created_utc": row[5],
                "matched_keywords": json.loads(row[6]),
                "discovered_at": row[7]
            })
        
        conn.close()
        
        output_file = output or f"posts_last_{hours}h.json"
        Path(output_file).write_text(json.dumps(posts, indent=2))
        logger.info(f"Exported {len(posts)} posts to {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Reddit Monitor with Webshare.io Residential Proxies"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Add subreddit command
    add_sub = subparsers.add_parser("add-subreddit", help="Add a subreddit to monitor")
    add_sub.add_argument("name", help="Subreddit name (without r/)")
    
    # Add keyword command
    add_kw = subparsers.add_parser("add-keyword", help="Add a keyword to track")
    add_kw.add_argument("keyword", help="Keyword to track")
    
    # List command
    subparsers.add_parser("list", help="Show current configuration and stats")
    
    # Run command
    subparsers.add_parser("run", help="Start monitoring loop")
    
    # Export command
    export_cmd = subparsers.add_parser("export", help="Export recent posts to JSON")
    export_cmd.add_argument("--hours", type=int, default=24, help="Hours to look back")
    export_cmd.add_argument("--output", help="Output file path")
    
    # Test proxies command
    subparsers.add_parser("test-proxies", help="Test Webshare proxy connection")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Initialize monitor
    if args.command != "test-proxies":
        try:
            monitor = RedditMonitor()
        except ValueError as e:
            logger.error(e)
            logger.error("Make sure WEBSHARE_API_KEY is set in your .env file")
            sys.exit(1)
    
    # Execute command
    if args.command == "add-subreddit":
        monitor.add_subreddit(args.name)
    
    elif args.command == "add-keyword":
        monitor.add_keyword(args.keyword)
    
    elif args.command == "list":
        monitor.list_config()
    
    elif args.command == "run":
        monitor.run_monitor_loop()
    
    elif args.command == "export":
        monitor.export_recent(args.hours, args.output)
    
    elif args.command == "test-proxies":
        api_key = os.getenv("WEBSHARE_API_KEY")
        if not api_key:
            logger.error("WEBSHARE_API_KEY not found in environment")
            sys.exit(1)
        
        manager = WebshareProxyManager(api_key)
        proxies = manager.fetch_proxies()
        
        if proxies:
            print(f"\nSuccessfully fetched {len(proxies)} proxies from Webshare")
            print("\nSample proxy:")
            sample = proxies[0]
            print(f"  Address: {sample['proxy_address']}:{sample['port']}")
            print(f"  Country: {sample.get('country_code', 'N/A')}")
            print(f"  City: {sample.get('city_name', 'N/A')}")
            
            # Test a request
            print("\nTesting proxy with httpbin.org...")
            proxy_dict = manager.get_proxy_dict(sample)
            try:
                response = requests.get(
                    "https://httpbin.org/ip",
                    proxies=proxy_dict,
                    timeout=30
                )
                print(f"Success! Response: {response.json()}")
            except Exception as e:
                print(f"Test failed: {e}")
        else:
            print("Failed to fetch proxies. Check your API key.")


if __name__ == "__main__":
    main()
