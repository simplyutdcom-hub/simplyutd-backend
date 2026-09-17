"""Editorial category for an article.

Every feed we ingest lands under the same generic ``News`` label, which makes a
sectioned "all stories" page useless. This module derives a real section from
the headline, summary and tags the publisher already gave us — no article body
is read, and nothing is stored beyond one short label.

The rules are deliberately plain keyword sets rather than a model: they are
auditable, deterministic, cheap, and easy for an operator to extend. Anything
that matches nothing stays ``News``, and an article an editor has filed by hand
is never touched.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from pymongo.database import Database

from .. import db as db_module

DEFAULT_CATEGORY = "News"

#: Section names, in tie-break order — the first with the highest score wins.
#: Editorial framing is the least ambiguous signal a headline carries, so a tie
#: between a "ranking"/"verdict" piece and a straight news story reads as Opinion.
CATEGORIES: tuple[str, ...] = ("Opinion", "Transfers", "Team News", "Matchday", "Club")

_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Transfers",
        (
            "transfer", "signing", "sign", "signed", "signs", "bid", "loan",
            "loanee", "fee", "release clause", "swap deal", "medical",
            "negotiat", "asking price", "wages", "contract", "sell", "sale",
            "departure", "exit clause", "move to", "agent", "valuation",
            "approached", "target", "window", "deal", "recruit",
            "talks", "agree", "agreed", "agreement", "hijack",
        ),
    ),
    (
        "Team News",
        (
            "injury", "injured", "injuries", "fitness", "doubtful", "doubt",
            "ruled out", "hamstring", "knock", "sidelined", "surgery", "scan",
            "suspended", "suspension", "red card", "illness", "calf", "ankle",
            "knee", "groin", "thigh", "muscle", "concussion", "return to training",
            "training", "available", "absent", "withdrawn from",
        ),
    ),
    (
        "Matchday",
        (
            "predicted xi", "starting xi", "confirmed xi", "line-up", "lineup",
            "team sheet", "kick-off", "kick off", "full-time", "half-time",
            "match report", "player ratings", "how to watch", "as it happened",
            "live blog", "preview", "fixture", "old trafford clash",
            "at old trafford", "away day", "group stage", "quarter-final",
            "semi-final", "final whistle", "team news",
        ),
    ),
    (
        "Opinion",
        (
            "opinion", "verdict", "ranked", "ranking", "analysis", "column",
            "talking points", "editorial", "debate", "poll", "fans say",
            "graded", "grades", "what we learned", "why", "should", "could",
            "must", "best and worst",
        ),
    ),
    (
        "Club",
        (
            "old trafford", "stadium", "takeover", "ineos", "glazer", "ratcliffe",
            "board", "ceo", "appointed", "appoint", "sacked", "sack", "kit",
            "shirt", "sponsor", "tickets", "ticket", "finance", "revenue",
            "training ground", "carrington", "director of football", "ownership",
            "wage bill", "pre-season tour", "membership", "museum", "foundation",
        ),
    ),
)

_TAG_WEIGHT = 4
_TITLE_WEIGHT = 3
_TEXT_WEIGHT = 1
#: A lone mention buried in the summary is not enough to file an article.
MIN_SCORE = 2

_WORDS_BY_CATEGORY = {name: words for name, words in _RULES}

#: Compiled in `CATEGORIES` order so the documented tie-break actually applies.
_PATTERNS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = tuple(
    (
        name,
        tuple(re.compile(rf"\b{re.escape(word)}\w*\b", re.IGNORECASE) for word in _WORDS_BY_CATEGORY[name]),
    )
    for name in CATEGORIES
)


def _hits(text: str, patterns: Iterable[re.Pattern[str]]) -> int:
    """Distinct mentions in ``text``.

    Counted by start offset so overlapping keywords ("sign" and "signing")
    describe one mention rather than inflating the score.
    """
    return len({match.start() for pattern in patterns if (match := pattern.search(text))})


def classify(
    title: str | None,
    summary: str | None = None,
    tags: Iterable[str] | None = None,
) -> str:
    """The section an article belongs to, or ``"News"`` when nothing fits."""
    title = title or ""
    text = summary or ""
    tag_text = " ".join(tag or "" for tag in (tags or ()))

    best_category = DEFAULT_CATEGORY
    best_score = 0
    for category, patterns in _PATTERNS:
        score = (
            _TAG_WEIGHT * _hits(tag_text, patterns)
            + _TITLE_WEIGHT * _hits(title, patterns)
            + _TEXT_WEIGHT * _hits(text, patterns)
        )
        if score > best_score:
            best_category, best_score = category, score

    return best_category if best_score >= MIN_SCORE else DEFAULT_CATEGORY


def for_entry(entry: Any) -> str:
    """Classify a :class:`~app.services.rss.FeedEntry`."""
    return classify(entry.title, entry.summary, entry.tags)


def backfill(database: Database, *, limit: int = 2000) -> int:
    """File anything ingested earlier under the generic label.

    Only machine-ingested articles sitting on ``News`` are revisited, so an
    editor's own category is never overwritten. Returns how many changed.
    """
    cursor = (
        database[db_module.NEWS]
        .find(
            {"external": True, "category": {"$in": [None, "", DEFAULT_CATEGORY]}},
            {"_id": 1, "title": 1, "summary": 1, "tags": 1, "category": 1},
        )
        .limit(max(1, limit))
    )

    changed = 0
    for doc in cursor:
        category = classify(doc.get("title"), doc.get("summary"), doc.get("tags"))
        if category == (doc.get("category") or DEFAULT_CATEGORY):
            continue
        database[db_module.NEWS].update_one({"_id": doc["_id"]}, {"$set": {"category": category}})
        changed += 1
    return changed


__all__ = ["CATEGORIES", "DEFAULT_CATEGORY", "backfill", "classify", "for_entry"]
