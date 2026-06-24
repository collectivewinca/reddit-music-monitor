#!/usr/bin/env python3
"""Pure highlights-ranking logic for the MINY A&R Radar dashboard.

Extracted from gen_dashboard.py so it can be unit-tested WITHOUT triggering the
dashboard's module-level DB read / index.html write. The only side effect here is
reading config.json for the tunable weights (cheap, no DB).

Why this exists: the "Signal from the threads" showcase must NOT rank by upvotes
(in this dataset upvotes and relevance are disjoint — fresh on-topic posts have
~0 upvotes; the only high-upvote posts are off-topic false-positives). So we rank
on-topic posts by relevance, demote keyword-stuffed self-promo, and boost curated
release/discussion posts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_CFG_PATH = Path(__file__).resolve().parent / "config.json"

_DEFAULTS = {"promo_penalty": 60, "curated_boost": 25, "min_relevance": 0}


def _load_weights() -> dict:
    """Read the highlights weights from config.json, falling back to defaults for
    any missing/unreadable key (so a stripped config never crashes the render)."""
    try:
        hl = json.loads(_CFG_PATH.read_text()).get("highlights", {}) or {}
    except (OSError, ValueError):
        hl = {}
    return {k: hl.get(k, _DEFAULTS[k]) for k in _DEFAULTS}


_W = _load_weights()
PROMO_PENALTY = _W["promo_penalty"]
CURATED_BOOST = _W["curated_boost"]
MIN_RELEVANCE = _W["min_relevance"]

# Begging self-promo ("hi i'm 15, check out my first EP") crams genre keywords,
# so it scores high on raw relevance; curated posts ("[FRESH]", "New Release",
# reviews, interviews, lineups) are the genuine signal.
# Dropped bare "i am" (matched band names like "I Am the River"); widened
# "my <noun>" to catch mixtape/latest; "redeem codes?" matches the plural spam
# phrasing that previously leaked through.
_PROMO = re.compile(
    r"\b(i'?m|my (?:first|new|debut|band|album|ep|song|track|project|mixtape|latest)|"
    r"check out my|please|feedback|redeem codes?|free download|promote|self.?promo)\b",
    re.I,
)
_CURATED = re.compile(
    r"(\[fresh\]|new release|announce|review|interview|lineup|reveals|debut album)",
    re.I,
)


def signal_score(p) -> float:
    """On-topic signal rank for the highlights showcase. Higher = better.

    Accepts any mapping with "rel" (relevance_score) and "title" keys — an
    sqlite3.Row in production, a plain dict in tests.

    Promo and curated markers are mutually exclusive with promo taking
    precedence: a begging title can't buy its way back into the showcase by
    tacking on "[FRESH]" (closes the both-match -35 quirk and the curated-rescue
    dodge). NOTE: a spammer who avoids first-person words AND adds a curated tag
    can still slip through — an inherent limit of a public, enumerable heuristic.
    """
    s = p["rel"] or 0
    title = p["title"] or ""
    if _PROMO.search(title):
        return s - PROMO_PENALTY
    if _CURATED.search(title):
        return s + CURATED_BOOST
    return s
