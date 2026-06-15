#!/usr/bin/env python3
import html as html_mod
import json
import sqlite3

DB_PATH = "/root/projects/reddit-music-monitor/reddit_monitor.db"
OUT_PATH = "/root/projects/reddit-music-monitor/index.html"

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute(
    "SELECT title, subreddit, author, score, url, matched_keywords, discovered_at "
    "FROM posts ORDER BY discovered_at DESC LIMIT 50"
)
posts = cursor.fetchall()

total_posts = cursor.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
total_subs = cursor.execute("SELECT COUNT(DISTINCT subreddit) FROM posts").fetchone()[0]
last_24h = cursor.execute(
    "SELECT COUNT(*) FROM posts WHERE discovered_at >= datetime('now', '-1 day')"
).fetchone()[0]

head = """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<title>Reddit Music Monitor</title>
<style>
body{font-family:sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#222}
h1{font-size:1.8rem}h2{font-size:1.2rem;margin-top:2rem;border-bottom:1px solid #eee;padding-bottom:.3rem}
.stats{display:flex;gap:40px;margin:20px 0}
.stat{text-align:center}
.stat .num{font-size:2rem;font-weight:bold;color:#0066cc}
.post{margin:12px 0;padding:12px;border:1px solid #eee;border-radius:6px}
.post-title{font-weight:bold;margin-bottom:4px}
.post-meta{color:#666;font-size:.85rem}
.post-kw{color:#0066cc;font-size:.8rem;margin-top:4px}
a{color:#0066cc;text-decoration:none}
</style></head><body>
<h1>Reddit Music Monitor</h1>
<p>Indie artists from Sweden, Copenhagen, Morocco, Mexico &amp; more</p>
"""

html = head + f"""<div class="stats">
<div class="stat"><div class="num">{total_posts}</div><div>Total Posts</div></div>
<div class="stat"><div class="num">{total_subs}</div><div>Subreddits</div></div>
<div class="stat"><div class="num">{last_24h}</div><div>Last 24h</div></div>
</div>
<h2>Recent Posts</h2>
"""

for post in posts:
    title, sub, author, score, url, kw, dt = post
    kw_str = ", ".join(json.loads(kw)) if kw else ""
    safe_title = html_mod.escape(str(title))
    safe_sub = html_mod.escape(str(sub))
    safe_author = html_mod.escape(str(author))
    safe_kw = html_mod.escape(kw_str)
    safe_url = html_mod.escape(str(url)) if str(url).startswith(("http://", "https://")) else "#"
    html += f'<div class="post"><div class="post-title"><a href="{safe_url}">{safe_title}</a></div>'
    html += f'<div class="post-meta">r/{safe_sub} | u/{safe_author} | {score} upvotes | {dt}</div>'
    if safe_kw:
        html += f'<div class="post-kw">{safe_kw}</div>'
    html += "</div>\n"

html += "</body></html>"

with open(OUT_PATH, "w") as f:
    f.write(html)

conn.close()
print(f"Wrote index.html (total={total_posts}, subs={total_subs}, last24h={last_24h})")
