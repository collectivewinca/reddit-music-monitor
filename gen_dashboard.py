#!/usr/bin/env python3
"""Render the MINY A&R Radar dashboard (index.html) from reddit_monitor.db.

Surfaces two things a MINY scout actually wants: the emerging artists the Reddit
community keeps recommending (from comment_artists, mined by mine_comments.py),
and the releases/threads worth a listen (high-relevance posts). Modern minimalist
UI; the signature is a per-artist "signal-bar" glyph scaled to mention count.
"""
import html as H
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ranking import MIN_RELEVANCE, signal_score

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "reddit_monitor.db"
OUT_PATH = SCRIPT_DIR / "index.html"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()


def _table_exists(name: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


# ---- stats ----------------------------------------------------------------
total_posts = cur.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
total_subs = cur.execute("SELECT COUNT(DISTINCT subreddit) FROM posts").fetchone()[0]
artist_count = (
    cur.execute("SELECT COUNT(*) FROM comment_artists").fetchone()[0]
    if _table_exists("comment_artists") else 0
)

# ---- emerging artists (community recommendations) -------------------------
artists = []
if _table_exists("comment_artists"):
    rows = cur.execute(
        "SELECT name, mentions, sources FROM comment_artists "
        "ORDER BY mentions DESC, updated_at DESC LIMIT 60"
    ).fetchall()
    for r in rows:
        ids = [s for s in (r["sources"] or "").split(",") if s]
        subs = []
        if ids:
            q = ",".join("?" * len(ids))
            subs = [
                x[0] for x in cur.execute(
                    f"SELECT DISTINCT subreddit FROM posts WHERE reddit_id IN ({q})", ids
                ).fetchall()
            ]
        artists.append({"name": r["name"], "mentions": r["mentions"], "subs": subs[:3]})

# ---- highlights (releases & threads worth a listen) -----------------------
# Rank by an ON-TOPIC signal score, NOT by upvotes. In this dataset the two
# signals are disjoint: posts are captured seconds after they're posted, so
# on-topic music releases sit at ~0 upvotes forever, while the only high-upvote
# posts are off-topic keyword false-positives (r/bangalore, politics) that the
# LLM relevance scorer correctly zeroed. Ranking by upvotes just imports that
# off-topic noise — so we rank on-topic posts (rel>0) by relevance, then demote
# keyword-stuffed self-promo and boost curated release/discussion posts.
_candidates = cur.execute(
    "SELECT title, subreddit, url, matched_keywords, "
    "COALESCE(relevance_score, 0) AS rel, discovered_at FROM posts"
).fetchall()

conn.close()

# Ranking logic (signal_score, the promo/curated regexes, and the tunable
# weights) lives in ranking.py so it's unit-testable without this script's
# module-level DB read. Only on-topic posts (rel>MIN_RELEVANCE) are eligible;
# off-topic upvote magnets have rel=0.
highlights = sorted(
    (p for p in _candidates if (p["rel"] or 0) > MIN_RELEVANCE),
    key=signal_score,
    reverse=True,
)[:12]
lead = artists[0] if artists else None
updated = datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M UTC")


# ---- render helpers -------------------------------------------------------
def signal_bars(mentions: int, mini: bool = False) -> str:
    """Equalizer glyph: 5 bars, filled in proportion to cross-thread mentions.
    `mini` renders a shorter glyph for the compact artist roster rows."""
    filled = max(1, min(5, mentions))
    base, step = (4, 2) if mini else (6, 4)
    bars = "".join(
        f'<span class="bar{" on" if i < filled else ""}" style="height:{base + i * step}px"></span>'
        for i in range(5)
    )
    cls = "bars mini" if mini else "bars"
    return f'<span class="{cls}" title="{mentions} thread(s)">{bars}</span>'


def artist_row(rank: int, a: dict) -> str:
    """One compact, scannable roster line (vs the old 128px card). Subreddits move
    to the hover tooltip so the row stays single-line and the section stays dense."""
    name = H.escape(a["name"])
    subs = " · ".join("r/" + H.escape(s) for s in a["subs"]) or "music threads"
    q = H.escape(a["name"]).replace(" ", "+")
    return f"""<a class="arow-item" href="https://www.google.com/search?q={q}+band+music" target="_blank" rel="noopener" title="{subs}">
      <span class="arank">{rank:02d}</span>
      <span class="aname2">{name}</span>
      <span class="aspark">{signal_bars(a["mentions"], mini=True)}</span>
      <span class="acount">{a["mentions"]}&times;</span>
    </a>"""


def highlight_card(p) -> str:
    title = H.escape(p["title"] or "")
    sub = H.escape(p["subreddit"] or "")
    url = p["url"] or "#"
    safe_url = H.escape(url) if url.startswith(("http://", "https://")) else "#"
    rel = p["rel"] or 0
    try:
        kws = ", ".join(json.loads(p["matched_keywords"]) or [])
    except Exception:
        kws = ""
    kw_html = f'<div class="tags">{H.escape(kws[:80])}</div>' if kws else ""
    # Upvotes are ~0 for freshly-captured posts (see signal_score note), so we
    # surface the SIGNAL RANK score (rel after promo/curated adjustment) — the
    # actual sort key — not raw relevance, so the badge matches the ordering.
    sig = signal_score(p)
    rel_html = f'<span class="rel" title="signal rank">signal {sig:.0f}</span>' if rel else ""
    return f"""<a class="card hl" href="{safe_url}" target="_blank" rel="noopener">
      <div class="hl-title">{title}</div>
      <div class="hl-meta"><span class="sub">r/{sub}</span><span class="dot">·</span>{rel_html}</div>
      {kw_html}
    </a>"""


hero = ""
if lead:
    subline = f' · {" · ".join("r/" + H.escape(s) for s in lead["subs"])}' if lead["subs"] else ""
    plural = "s" if lead["mentions"] != 1 else ""
    hero = f"""<section class="hero">
      <div class="eyebrow">Most recommended right now</div>
      <h1 class="lead">{H.escape(lead["name"])}</h1>
      <div class="lead-meta">{signal_bars(lead["mentions"])}<span>surfaced across {lead["mentions"]} thread{plural}{subline}</span></div>
    </section>"""

artist_html = "".join(artist_row(i + 1, a) for i, a in enumerate(artists)) or \
    '<div class="empty">No artists mined yet — run mine_comments.py.</div>'
highlight_html = "".join(highlight_card(p) for p in highlights) or \
    '<div class="empty">No highlights yet.</div>'

CSS = """
:root{
  --paper:#F4F4F6; --ink:#16161A; --accent:#5B4BE8; --lilac:#ECEAFC;
  --muted:#8A8792; --line:#E6E6EA; --card:#FFFFFF;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--paper);color:var(--ink);
  font-family:'Inter',system-ui,sans-serif;line-height:1.5;
  -webkit-font-smoothing:antialiased;padding:0 24px}
.wrap{max-width:1080px;margin:0 auto;padding:56px 0 80px}
a{color:inherit;text-decoration:none}

.masthead{display:flex;justify-content:space-between;align-items:baseline;
  flex-wrap:wrap;gap:12px;padding-bottom:20px;border-bottom:1px solid var(--line)}
.mark{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:20px;
  letter-spacing:-.02em}
.mark b{color:var(--accent)}
.tagline{color:var(--muted);font-size:13.5px;max-width:440px;margin-top:6px}
.stamp{font-family:'Space Mono',monospace;font-size:11.5px;color:var(--muted)}

.hero{padding:54px 0 40px;border-bottom:1px solid var(--line)}
.eyebrow{font-family:'Space Mono',monospace;font-size:11px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--accent);margin-bottom:14px}
.lead{font-family:'Space Grotesk',sans-serif;font-weight:700;
  font-size:clamp(40px,8vw,76px);letter-spacing:-.035em;line-height:.98}
.lead-meta{display:flex;align-items:center;gap:12px;margin-top:18px;
  color:var(--muted);font-size:14px}

.section{margin-top:52px}
.shead{display:flex;align-items:baseline;gap:12px;margin-bottom:22px;flex-wrap:wrap}
.shead h2{font-family:'Space Grotesk',sans-serif;font-weight:500;font-size:15px;
  letter-spacing:.02em}
.shead .count{font-family:'Space Mono',monospace;font-size:12px;color:var(--muted)}
.shead .desc{color:var(--muted);font-size:13px;margin-left:auto;text-align:right}

.grid{display:grid;gap:14px}
.grid.hls{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}

/* Compact, scannable artist roster (replaces the old big-card grid) */
.roster{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));
  gap:0 30px}
.arow-item{display:flex;align-items:center;gap:11px;padding:8px 10px;
  border-bottom:1px solid var(--line);border-radius:7px;
  transition:background .12s ease}
.arow-item:hover{background:var(--lilac)}
.arow-item:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.arank{font-family:'Space Mono',monospace;font-size:11px;color:var(--muted);
  width:20px;flex:none}
.aname2{font-family:'Space Grotesk',sans-serif;font-weight:500;font-size:14.5px;
  letter-spacing:-.01em;flex:1 1 auto;min-width:0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.aspark{flex:none;margin-left:auto;display:inline-flex;align-items:flex-end}
.acount{font-family:'Space Mono',monospace;font-size:12px;color:var(--accent);
  flex:none;width:30px;text-align:right}

.card{display:block;background:var(--card);border:1px solid var(--line);
  border-radius:14px;padding:18px;
  transition:transform .16s ease,border-color .16s ease,box-shadow .16s ease}
.card:hover{transform:translateY(-3px);border-color:var(--accent);
  box-shadow:0 10px 30px -18px rgba(91,75,232,.5)}
.card:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

.bars{display:inline-flex;align-items:flex-end;gap:3px;height:22px}
.bars.mini{height:12px;gap:2px}
.bars .bar{width:3px;border-radius:2px;background:var(--line);display:inline-block}
.bars.mini .bar{width:2.5px}
.bars .bar.on{background:var(--accent)}

.hl{display:flex;flex-direction:column;gap:9px}
.hl-title{font-family:'Space Grotesk',sans-serif;font-weight:500;font-size:16px;
  letter-spacing:-.01em;line-height:1.25}
.hl-meta{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--muted)}
.hl-meta .sub{color:var(--ink);font-weight:500}
.hl-meta .rel{margin-left:auto;font-family:'Space Mono',monospace;font-size:11px;
  color:var(--accent);background:var(--lilac);padding:2px 7px;border-radius:20px}
.tags{font-size:11.5px;color:var(--muted);font-family:'Space Mono',monospace}

.empty{color:var(--muted);font-size:14px;padding:20px 0}
footer{margin-top:64px;padding-top:20px;border-top:1px solid var(--line);
  display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;
  font-family:'Space Mono',monospace;font-size:11.5px;color:var(--muted)}

@media (max-width:560px){.wrap{padding:36px 0 60px}body{padding:0 16px}}
@media (prefers-reduced-motion:reduce){.card{transition:none}.card:hover{transform:none}}
"""

html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MINY A&amp;R Radar — Reddit music discovery</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Space+Grotesk:wght@500;700&family=Space+Mono&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head><body><div class="wrap">

  <header class="masthead">
    <div>
      <div class="mark">MINY <b>A&amp;R Radar</b></div>
      <div class="tagline">Emerging artists the Reddit music community keeps recommending — discovery leads for MINY, mined from the comments.</div>
    </div>
    <div class="stamp">updated {updated}</div>
  </header>

  {hero}

  <section class="section">
    <div class="shead">
      <h2>Emerging artists</h2><span class="count">top {len(artists)} of {artist_count}</span>
      <span class="desc">Ranked by how many threads recommend them · hover for subs</span>
    </div>
    <div class="roster">{artist_html}</div>
  </section>

  <section class="section">
    <div class="shead">
      <h2>Signal from the threads</h2><span class="count">{total_posts} posts · {total_subs} subs</span>
      <span class="desc">New releases &amp; reactions worth a listen</span>
    </div>
    <div class="grid hls">{highlight_html}</div>
  </section>

  <footer>
    <span>reddit-music-monitor → MINY · last30days RSS + deepseek</span>
    <span>{updated}</span>
  </footer>

</div></body></html>"""

OUT_PATH.write_text(html)
print(f"Wrote {OUT_PATH.name} (artists={len(artists)}, highlights={len(highlights)}, posts={total_posts})")
