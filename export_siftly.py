#!/usr/bin/env python3
"""Export reddit_monitor.db posts to Siftly-compatible JSON."""
import sqlite3
import json
import sys
from datetime import datetime

def export_to_siftly(db_path, output_path, hours=None, min_score=None):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = "SELECT * FROM posts WHERE 1=1"
    params = []
    
    if hours:
        query += " AND discovered_at >= datetime('now', '-{} hours')".format(hours)
    if min_score:
        query += " AND score >= ?"
        params.append(min_score)
    
    query += " ORDER BY discovered_at DESC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    out = []
    for row in rows:
        # Parse matched keywords
        try:
            keywords = json.loads(row["matched_keywords"]) if row["matched_keywords"] else []
        except:
            keywords = []
        
        # Build full_text
        full_text = row["title"]
        if row["url"]:
            full_text += f"\n\n[link] {row['url']}"
        if keywords:
            full_text += f"\n\n[keywords] {', '.join(keywords)}"
        
        # Convert created_utc to Twitter-style date
        created_at = datetime.utcfromtimestamp(row["created_utc"]).strftime("%a %b %d %H:%M:%S +0000 %Y")
        
        out.append({
            "id_str": f"reddit_monitor_{row['subreddit']}_{row['reddit_id']}",
            "full_text": full_text,
            "created_at": created_at,
            "user": {
                "screen_name": row["author"] or "unknown",
                "name": row["author"] or "Unknown",
            },
            "entities": {
                "hashtags": [],
                "urls": [{"expanded_url": row["url"], "url": row["url"]}] if row["url"] else [],
                "media": [],
            },
        })
    
    with open(output_path, "w") as f:
        json.dump(out, f, indent=2)
    
    print(f"Exported {len(out)} posts to {output_path}")
    return len(out)

if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "reddit_monitor.db"
    out = sys.argv[2] if len(sys.argv) > 2 else "reddit_monitor_siftly.json"
    hours = int(sys.argv[3]) if len(sys.argv) > 3 else None
    
    export_to_siftly(db, out, hours=hours)
