"""Tests for the broad site search engine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import db as db_module
from app.services import feed_rank, search_engine

NOW = datetime(2025, 9, 1, 12, 0, tzinfo=timezone.utc)


def doc(**overrides):
    base = {
        "id": "s1",
        "slug": "rashford-exit",
        "title": "Rashford set for exit as Bruno talks continue",
        "summary": "Old Trafford rebuild plans move on.",
        "source": "Sky Sports",
        "source_url": "https://www.skysports.com/football/rashford",
        "category": "Transfers",
        "tags": ["manchester-united", "transfers"],
        "author": None,
        "status": "Published",
        "published_at": NOW - timedelta(hours=2),
    }
    base.update(overrides)
    return base


CORPUS = [
    doc(),
    doc(
        id="s2",
        slug="united-striker-deal",
        title="United agree 85m deal for new striker",
        summary="The fee is significant for the club.",
        source="BBC Sport",
        source_url="https://www.bbc.co.uk/sport/football/united",
        published_at=NOW - timedelta(hours=5),
    ),
    doc(
        id="s3",
        slug="arsenal-midfield",
        title="Arsenal hold talks over midfield target",
        summary="The Gunners want a deal done this week.",
        source="The Athletic",
        source_url="https://theathletic.com/football/arsenal",
        tags=["arsenal"],
        category="Transfers",
        published_at=NOW - timedelta(days=400),
    ),
]


def load(database, docs=CORPUS):
    database[db_module.NEWS].insert_many([dict(item) for item in docs])
    return database


def ids(outcome):
    return [row[0]["id"] for row in outcome.rows]


# --- Query parsing ----------------------------------------------------------


def test_query_is_split_into_terms_phrases_scopes_and_exclusions():
    parsed = search_engine.parse_query('bruno "old trafford" source:sky -arsenal rash*')
    assert parsed.as_dict() == {
        "terms": ["bruno", "rash"],
        "phrases": ["old trafford"],
        "fields": {"source": ["sky"]},
        "excluded": ["arsenal"],
    }


def test_stopwords_do_not_have_to_match():
    parsed = search_engine.parse_query("the old trafford")
    assert [c.text for c in parsed.strict] == ["old", "trafford"]


def test_punctuation_is_normalised():
    parsed = search_engine.parse_query("“Old Trafford”")
    assert [c.text for c in parsed.phrases] == ["old trafford"]


def test_scoreline_is_treated_as_a_phrase():
    parsed = search_engine.parse_query("4-0")
    assert [c.text for c in parsed.phrases] == ["4 0"]


def test_quoted_scope_value_keeps_its_spaces():
    parsed = search_engine.parse_query('source:"bbc sport"')
    assert parsed.as_dict()["fields"] == {"source": ["bbc sport"]}
    assert parsed.scoped["source"][0].phrase is True


def test_keyword_bearing_url_is_not_read_as_a_scope():
    parsed = search_engine.parse_query("https://www.skysports.com/football")
    assert parsed.scoped == {}
    assert parsed.is_empty() is False


def test_blank_query_is_empty():
    assert search_engine.parse_query("   ").is_empty() is True


# --- Scoring ----------------------------------------------------------------


def test_multi_word_query_requires_every_keyword():
    parsed = search_engine.parse_query("bruno rashford")
    match = search_engine.score_document(CORPUS[0], parsed)
    assert match is not None
    assert search_engine.score_document(CORPUS[2], parsed) is None


def test_phrase_matches_are_token_aware():
    """A "4 0" phrase must not hide inside "2024 05"."""
    parsed = search_engine.parse_query("4-0")
    assert search_engine.score_document(CORPUS[2], parsed) is None


def test_final_year_in_a_phrase_is_not_matched_mid_token():
    parsed = search_engine.parse_query('"4 0"')
    assert search_engine.score_document(CORPUS[2], parsed) is None


def test_field_scope_narrows_the_search():
    parsed = search_engine.parse_query("source:sky")
    assert search_engine.score_document(CORPUS[0], parsed) is not None
    assert search_engine.score_document(CORPUS[1], parsed) is None


def test_exclusions_remove_documents():
    parsed = search_engine.parse_query("deal -arsenal")
    assert search_engine.score_document(CORPUS[1], parsed) is not None
    assert search_engine.score_document(CORPUS[2], parsed) is None


def test_prefix_matches_a_word_start():
    parsed = search_engine.parse_query("rash*")
    assert search_engine.score_document(CORPUS[0], parsed) is not None
    assert search_engine.score_document(CORPUS[1], parsed) is None


def test_url_and_year_citations_match():
    assert search_engine.score_document(CORPUS[0], search_engine.parse_query("skysports.com")) is not None
    assert search_engine.score_document(CORPUS[1], search_engine.parse_query("united 2025")) is not None
    assert search_engine.score_document(CORPUS[1], search_engine.parse_query("year:2024")) is None
    assert search_engine.score_document(CORPUS[2], search_engine.parse_query("year:2024")) is not None


def test_title_hits_outrank_summary_hits():
    parsed = search_engine.parse_query("striker")
    in_title = search_engine.score_document(CORPUS[1], parsed)
    in_summary = search_engine.score_document(
        {**CORPUS[1], "title": "Something else entirely", "summary": "A striker is wanted."},
        parsed,
    )
    assert in_title is not None and in_summary is not None
    assert in_title.score > in_summary.score


def test_covering_more_terms_scores_higher():
    parsed = search_engine.parse_query("bruno rashford")
    match = search_engine.score_document(CORPUS[0], parsed, require_all=False)
    assert match is not None
    assert match.coverage == 1.0
    assert len(match.matched) == 2


# --- Fuzzy fallback ---------------------------------------------------------


def test_misspelling_is_corrected_against_the_corpus():
    relaxed, changed = search_engine.relax_to_fuzzy(
        search_engine.parse_query("rashfrod"), search_engine.vocabulary(CORPUS)
    )
    assert changed is True
    assert [c.text for c in relaxed.terms] == ["rashford"]


def test_nonsense_term_is_never_corrected():
    relaxed, changed = search_engine.relax_to_fuzzy(
        search_engine.parse_query("zzzzqqq"), search_engine.vocabulary(CORPUS)
    )
    assert changed is False
    assert [c.text for c in relaxed.terms] == ["zzzzqqq"]


def test_short_terms_are_not_fuzzy_matched():
    assert search_engine._closest_word("traf", search_engine.vocabulary(CORPUS)) is None


# --- Whole-engine behaviour -------------------------------------------------


def test_search_reports_strict_matches(database):
    outcome = search_engine.search(load(database), "bruno rashford")
    assert ids(outcome) == ["s1"]
    assert outcome.broadened is False
    assert outcome.fuzzy is False


def test_search_broadens_when_nothing_satisfies_every_keyword(database):
    outcome = search_engine.search(load(database), "rashford gunners")
    assert set(ids(outcome)) == {"s1", "s3"}
    assert outcome.broadened is True
    assert outcome.fuzzy is False


def test_search_falls_back_to_fuzzy(database):
    outcome = search_engine.search(load(database), "rashfrod")
    assert ids(outcome) == ["s1"]
    assert outcome.fuzzy is True


def test_search_keeps_a_nonsense_query_empty(database):
    outcome = search_engine.search(load(database), "zzzzqqq")
    assert outcome.rows == []
    assert outcome.total == 0


def test_search_pages_honestly(database):
    outcome = search_engine.search(load(database), "deal", limit=1)
    assert len(outcome.rows) == 1
    assert outcome.total >= 2


def test_search_only_returns_published_articles(database):
    corpus = [
        *CORPUS,
        doc(
            id="s4",
            slug="draft-deal-story",
            title="Draft deal story",
            source_url="https://www.skysports.com/football/draft-deal",
            status="Draft",
        ),
    ]
    outcome = search_engine.search(load(database, corpus), "deal")
    assert "s4" not in ids(outcome)


def test_prefilter_is_broad_and_excludes_negations():
    criteria = search_engine.prefilter(search_engine.parse_query("bruno -arsenal"))
    assert len(criteria["$or"]) > 1
    assert "$nor" in criteria


# --- Feed ranking integration -----------------------------------------------


def test_rank_prefers_the_document_matching_more_keywords():
    partial = doc(id="a", title="United update on Rashford", summary="Something happened.")
    complete = doc(id="b", title="United: Bruno and Rashford update", summary="Something happened.")
    page, _ = feed_rank.rank([partial, complete], query="bruno rashford", min_items=0)
    assert [item["id"] for item in page] == ["b", "a"]


def test_query_boost_stays_a_tiebreaker():
    united = doc(id="u", title="United news")
    other = doc(id="o", title="Arsenal news", tags=[], category="Transfers")
    assert feed_rank.query_boost(united) > feed_rank.query_boost(other)
    # Bounded: one clear textual advantage outweighs any club relevance nudge.
    assert feed_rank.query_boost(united) < 6.0
