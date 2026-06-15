#!/usr/bin/env python3
import sqlite3
import json

conn = sqlite3.connect("/root/projects/reddit-music-monitor/reddit_monitor.db")
cursor = conn.cursor()

removed_keywords = [
    "remix", "featuring", "feat.", "album review", "first listen",
    "name your price", "free download", "pay what you want",
    "hand numbered", "screen printed", "creative commons music",
    "cc0 music", "live show", "festival lineup", "exclusive premiere",
    "cover song", "sample flip", "live session", "studio session",
    "recording session"
]
removed_lower = [k.lower() for k in removed_keywords]

cursor.execute("SELECT id, matched_keywords FROM posts")
posts = cursor.fetchall()

to_remove = []
for post_id, matched_json in posts:
    matched = json.loads(matched_json) if matched_json else []
    if matched and all(m.lower() in removed_lower for m in matched):
        to_remove.append(post_id)

print(f"Found {len(to_remove)} posts to remove (false positives)")

if to_remove:
    for pid in to_remove:
        cursor.execute("DELETE FROM posts WHERE id = ?", (pid,))
    conn.commit()
    print(f"Removed {len(to_remove)} posts")

cursor.execute("SELECT COUNT(*), COUNT(DISTINCT subreddit) FROM posts")
total, subs = cursor.fetchone()
print(f"\n=== AFTER CLEANUP ===")
print(f"Total posts: {total}")
print(f"Subreddits: {subs}")

cursor.execute("SELECT subreddit, COUNT(*) as cnt FROM posts GROUP BY subreddit ORDER BY cnt DESC LIMIT 10")
print("\nTop subreddits:")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]}")

cursor.execute("SELECT matched_keywords, COUNT(*) as cnt FROM posts WHERE matched_keywords != '[]' GROUP BY matched_keywords ORDER BY cnt DESC LIMIT 10")
print("\nTop keyword matches:")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]}")

conn.close()
