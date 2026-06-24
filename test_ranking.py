#!/usr/bin/env python3
"""Unit tests for the highlights ranking heuristic (ranking.signal_score).

These pin the behavior of the promo/curated regexes and the score arithmetic so a
future wording tweak can't silently re-admit the keyword-stuffed self-promo the
showcase is meant to suppress (or demote the genuine releases it's meant to surface).
"""
import ranking
from ranking import CURATED_BOOST, PROMO_PENALTY, signal_score


def _post(title, rel=50):
    return {"title": title, "rel": rel}


# --- self-promo is penalized -------------------------------------------------
def test_begging_self_promo_penalized():
    assert signal_score(_post("hi i'm 15, check out my first EP")) == 50 - PROMO_PENALTY


def test_my_noun_variants_penalized():
    for t in ["my new album out now", "my mixtape dropped", "my latest single",
              "please check out my band"]:
        assert signal_score(_post(t)) == 50 - PROMO_PENALTY, t


def test_redeem_codes_plural_penalized():
    # The plural "codes" previously leaked past the regex; it must be caught now.
    assert signal_score(_post("[Free redeem codes] Reverberant Evenings")) == 50 - PROMO_PENALTY


# --- curated posts are boosted ----------------------------------------------
def test_curated_markers_boosted():
    for t in ["[FRESH] Great Band - Album", "New Release: Some Artist",
              "Pitchfork review of the year", "FORM Arcosanti reveals 2026 lineup"]:
        assert signal_score(_post(t)) == 50 + CURATED_BOOST, t


# --- neutral / false-positive guards ----------------------------------------
def test_neutral_title_unchanged():
    assert signal_score(_post("Some Band - Some Song")) == 50


def test_band_name_with_i_am_not_penalized():
    # "I Am the River" is a band name; dropping bare "i am" from _PROMO prevents
    # this false positive. ("new single" is not a curated marker, so it stays neutral.)
    assert signal_score(_post("I Am the River - new single")) == 50


# --- promo takes precedence over curated (closes the rescue/both-match dodge) -
def test_promo_dominates_curated():
    # Tacking "[FRESH]"/"New release" onto begging self-promo must NOT rescue it.
    assert signal_score(_post("New release: my debut album")) == 50 - PROMO_PENALTY
    assert signal_score(_post("[FRESH] check out my new track")) == 50 - PROMO_PENALTY


# --- arithmetic / edge cases -------------------------------------------------
def test_none_rel_and_title_safe():
    assert signal_score({"rel": None, "title": None}) == 0


def test_min_relevance_default():
    assert ranking.MIN_RELEVANCE == 0


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
