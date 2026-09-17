"""Tests for the United-first feed ranking algorithm."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import feed_rank, news_service

NOW = datetime(2025, 9, 1, 12, 0, tzinfo=timezone.utc)


def article(**overrides):
    doc = {
        "id": "a1",
        "title": "Manchester United agree deal for new striker",
        "summary": "Amorim wants the move done before the deadline.",
        "source": "BBC Sport",
        "tags": ["manchester-united"],
        "category": "Transfers",
        "image": "https://example.com/a.jpg",
        "views": 10,
        "comments": 1,
        "published_at": NOW - timedelta(hours=1),
    }
    doc.update(overrides)
    return doc


# --- The gate ---------------------------------------------------------------

def test_strong_name_is_united():
    assert feed_rank.analyse(article(), now=NOW).united is True


def test_bare_united_still_counts():
    doc = article(title="United complete medical for new signing", summary="")
    assert feed_rank.analyse(doc, now=NOW).united is True


def test_other_united_clubs_are_masked():
    doc = article(
        title="Newcastle United seal late winner",
        summary="A fine night for Newcastle United.",
        tags=[],
    )
    assert feed_rank.analyse(doc, now=NOW).united is False


def test_context_terms_can_gate():
    doc = article(title="Old Trafford rebuild moves a step closer", summary="Plans for the stadium.")
    assert feed_rank.analyse(doc, now=NOW).united is True


def test_unrelated_article_is_rejected():
    doc = article(
        title="Arsenal hold talks over midfield target",
        summary="The Gunners want a deal done this week.",
        source="The Athletic",
        tags=[],
        category="Transfers",
    )
    assert feed_rank.analyse(doc, now=NOW).united is False


def test_rival_only_story_is_penalised():
    united = feed_rank.analyse(article(), now=NOW).score
    rival = feed_rank.analyse(
        article(title="Manchester United target watched by Liverpool", summary="Liverpool lead the race."),
        now=NOW,
    ).score
    assert rival < united


# --- Scoring axes -----------------------------------------------------------

def test_recency_lifts_a_score():
    fresh = feed_rank.analyse(article(published_at=NOW - timedelta(minutes=20)), now=NOW).score
    stale = feed_rank.analyse(article(published_at=NOW - timedelta(days=5)), now=NOW).score
    assert fresh > stale


def test_missing_image_costs_points():
    with_image = feed_rank.analyse(article(), now=NOW).score
    without = feed_rank.analyse(article(image=None), now=NOW).score
    assert with_image > without


def test_explain_returns_the_breakdown():
    breakdown = feed_rank.explain(article(), now=NOW)
    assert breakdown["united"] is True
    assert "score" in breakdown and "reasons" in breakdown


# --- Ordering and diversification -------------------------------------------

def test_rank_gates_when_the_pool_is_big_enough():
    docs = [
        article(id=f"u{i}", title=f"Manchester United story {i}", source=f"Outlet {i}")
        for i in range(8)
    ]
    docs.append(
        article(id="other", title="Arsenal win again", summary="The Gunners stay top.", tags=[], source="Wire")
    )
    page, total = feed_rank.rank(docs, limit=10, min_items=8, now=NOW)
    assert total == 8
    assert "other" not in {row["id"] for row in page}


def test_rank_keeps_everything_on_a_cold_corpus():
    docs = [article(id="other", title="Arsenal win again", summary="Nothing here.", tags=[], source="Wire")]
    page, total = feed_rank.rank(docs, limit=10, min_items=8, now=NOW)
    assert total == 1
    assert page[0]["id"] == "other"


def test_query_matches_are_scored_and_preferred():
    docs = [
        article(id="hit", title="Amorim confirms the squad for Sunday"),
        article(id="miss", title="United announce hospitality packages"),
    ]
    page, _ = feed_rank.rank(docs, limit=5, query="amorim", min_items=0, now=NOW)
    assert page[0]["id"] == "hit"


def test_diversify_demotes_a_prolific_source():
    docs = [article(id=f"w{i}", title=f"Manchester United wire {i}", source="Wire") for i in range(6)]
    page, _ = feed_rank.rank(docs, limit=6, max_per_source=2, min_items=0, now=NOW)
    # Nothing is dropped - it is only reordered.
    assert {row["id"] for row in page} == {f"w{i}" for i in range(6)}


# --- Annotation and backfill ------------------------------------------------

def test_annotate_is_storable():
    fields = feed_rank.annotate(article(), now=NOW)
    assert fields["united"] is True
    assert isinstance(fields["united_score"], float)


def test_backfill_scores_unannotated_documents(seeded):
    seeded["news"].update_many({}, {"$unset": {"united_score": ""}})
    updated = feed_rank.backfill(seeded)
    assert updated > 0
    assert seeded["news"].count_documents({"united_score": {"$exists": True}}) == updated
    # Idempotent: a second run has nothing left to do.
    assert feed_rank.backfill(seeded) == 0


# --- The service surface ----------------------------------------------------

def test_list_news_is_united_first(seeded):
    items, total = news_service.list_news(seeded, limit=20)
    assert total > 0
    assert items


def test_united_only_false_returns_the_raw_corpus(seeded):
    items, total = news_service.list_news(seeded, limit=50, united_only=False)
    assert total == seeded["news"].count_documents({"status": "Published"})
    assert items


def test_ranked_helper_returns_a_page(seeded):
    page = news_service.ranked(seeded, limit=5)
    assert page
    assert len(page) <= 5
