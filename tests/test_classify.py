"""Unit tests for the article section classifier."""
from __future__ import annotations

from app.services import classify
from app.services.rss import normalize_entry


def test_classify_files_a_transfer_story():
    assert classify.classify("United agree fee for Bundesliga midfielder") == "Transfers"


def test_classify_files_an_injury_story():
    assert classify.classify("Mainoo ruled out with hamstring injury") == "Team News"


def test_classify_files_a_matchday_story():
    assert classify.classify("Predicted XI: how United line up at Anfield") == "Matchday"


def test_classify_files_an_opinion_piece():
    assert classify.classify("Ranking every United signing under INEOS") == "Opinion"


def test_classify_files_a_club_story():
    assert classify.classify("Old Trafford redevelopment: the £2bn stadium plan") == "Club"


def test_classify_falls_back_to_news_for_an_unrelated_headline():
    assert classify.classify("Sir Alex Ferguson opens new school in Govan") == "News"


def test_classify_ignores_a_lone_summary_hit():
    """One passing mention in the summary is not enough to file an article."""
    assert classify.classify("Fans gather for the parade", "A quiet day at the club.") == "News"


def test_classify_prefers_the_stronger_section():
    """A transfer headline mentioning a knock still reads as a transfer story."""
    assert classify.classify(
        "United step up talks for striker despite injury worry",
        "The club's transfer plans continue.",
    ) == "Transfers"


def test_classify_weighs_tags_heavily():
    assert classify.classify("The numbers behind the weekend", tags=["Transfers"]) == "Transfers"


def test_normalize_entry_derives_a_category():
    entry = normalize_entry(
        {
            "title": "United close in on £60m signing",
            "link": "https://example.com/a",
            "summary": "The transfer is expected to be completed this week.",
        }
    )
    assert entry is not None
    assert entry.category == "Transfers"


def test_normalize_entry_keeps_an_explicit_category():
    entry = normalize_entry(
        {"title": "United close in on £60m signing", "link": "https://example.com/a"},
        category="Matchday",
    )
    assert entry is not None
    assert entry.category == "Matchday"


def test_backfill_files_previously_generic_articles(seeded):
    result = seeded["news"].insert_one(
        {
            "external": True,
            "title": "United agree deal for new striker",
            "summary": "",
            "category": classify.DEFAULT_CATEGORY,
        }
    )

    changed = classify.backfill(seeded)

    assert changed >= 1
    assert seeded["news"].find_one({"_id": result.inserted_id})["category"] == "Transfers"


def test_backfill_leaves_editorial_categories_alone(seeded):
    result = seeded["news"].insert_one(
        {
            "external": True,
            "title": "United agree deal for new striker",
            "summary": "",
            "category": "Analysis",
        }
    )

    assert classify.backfill(seeded) == 0
    assert seeded["news"].find_one({"_id": result.inserted_id})["category"] == "Analysis"
