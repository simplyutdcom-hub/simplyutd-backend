"""Manchester United relevance ranking for the news corpus.

SimplyUtd is a single-club publication, so every surface that shows news (home
feed, live stream, search, hub derivations) should be United's news and nothing
else. The upstream feeds, however, are general football feeds that happily mix
in results, transfer gossip and opinion about every other club in Europe.

This module turns that mixed corpus into a United-first feed. Each article is
scored on four axes and every score component is explainable:

``relevance``
    How much the article is *about* United — explicit club names weigh most,
    then club-specific context (players, manager, Old Trafford, INEOS) and the
    article's own tags/category. Rival-club-only articles are pushed down.
``recency``
    Exponential decay from ``published_at``, so the feed keeps moving.
``engagement``
    Views and comment counts, capped so one viral piece cannot dominate.
``quality``
    Publisher tier plus an image bonus (cards without artwork look broken).

Two things come out of :func:`analyse`:

* ``united`` — the editorial gate. True when the article names United, uses a
  bare "United" that is not another United (Newcastle/Leeds/...), or carries
  club-specific context.
* ``score`` — the ranking signal, used to order the feed.

The gate is applied with a floor (:func:`rank`): if fewer than ``min_items``
United articles exist (a cold database, or every feed upstream being down) the
non-United articles are kept as filler rather than showing an empty site. They
sort last regardless, because relevance dominates the score.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from pymongo.database import Database

from .. import db as db_module
from ..utils import utcnow
from . import search_engine

# --- Lexicons --------------------------------------------------------------- #

#: Unambiguous names for the club. A hit here is a hard, high-confidence match.
STRONG_TERMS: tuple[str, ...] = (
    "manchester united",
    "manchester utd",
    "man utd",
    "man united",
    "mufc",
    "red devils",
)

#: Club-specific context: people, places and organisations that only make sense
#: in a United article. Broader than the club name, so team-news and transfer
#: stories that never spell out "United" are still recognised as ours.
CONTEXT_TERMS: tuple[str, ...] = (
    "old trafford",
    "carrington",
    "theatre of dreams",
    "stretford end",
    "ineos",
    "glazer",
    "ratcliffe",
    "amorim",
    "ten hag",
    "bruno fernandes",
    "rashford",
    "mainoo",
    "hojlund",
    "højlund",
    "garnacho",
    "mount",
    "dalot",
    "onana",
    "martinez",
    "shaw",
    "casemiro",
    "eriksen",
    "antony",
    "zirkzee",
    "de ligt",
    "mazraoui",
    "ugarte",
    "yoro",
    "amad diallo",
    "bayindir",
    "lammens",
    "fernandes",
    "red devils",
    "united way",
    "m16",
    "europa league",
    "fa cup final",
    "carabao cup",
)

#: Other clubs that also end in "United". A bare "United" next to one of these
#: refers to that club, not ours, so those occurrences are masked out first.
_OTHER_UNITED_RE = re.compile(
    r"\b(?:newcastle|leeds|sheffield|west ham|dundee|cambridge|colchester|southend|"
    r"oxford|peterborough|torquay|boston|hartlepool|carlisle|wycombe|scunthorpe|"
    r"grimsby|chesterfield|halifax|sutton|dorking|wealdstone|maidenhead|aldershot|"
    r"woking|barnet|dagenham|redbridge|hastings|hereford|kettering|milton keynes|"
    r"rotherham|tamworth|welling|dover|york|afc|ballymena|coleraine)\s+united\b",
    re.IGNORECASE,
)
_BARE_UNITED_RE = re.compile(r"\bunited\b", re.IGNORECASE)

#: Rival / competitor clubs. Heavy presence without a United signal means the
#: piece is about somebody else.
RIVAL_TERMS: tuple[str, ...] = (
    "manchester city",
    "man city",
    "liverpool",
    "arsenal",
    "chelsea",
    "tottenham",
    "spurs",
    "newcastle",
    "aston villa",
    "brighton",
    "west ham",
    "everton",
    "fulham",
    "crystal palace",
    "brentford",
    "wolves",
    "bournemouth",
    "nottingham forest",
    "burnley",
    "leeds",
    "sunderland",
    "real madrid",
    "barcelona",
    "bayern",
    "juventus",
    "psg",
    "paris saint-germain",
    "dortmund",
    "napoli",
    "atletico",
    "inter milan",
    "ac milan",
)

#: Publishers we trust for accurate, non-sensational United coverage. The value
#: is added straight to the score.
SOURCE_TIERS: dict[str, int] = {
    "bbc sport": 10,
    "bbc": 10,
    "sky sports": 10,
    "the guardian": 9,
    "manchester evening news": 9,
    "the athletic": 9,
    "the independent": 7,
    "telegraph": 7,
    "times": 7,
    "espn": 7,
    "manchester world": 7,
    "goal": 6,
    "90min": 6,
    "talksport": 6,
    "mirror": 5,
    "express": 4,
    "daily mail": 4,
    "the sun": 2,
}

# --- Weights ---------------------------------------------------------------- #

W_STRONG_TITLE = 70.0
W_STRONG_BODY = 26.0
W_BARE_UNITED_TITLE = 46.0
W_BARE_UNITED_BODY = 18.0
W_CONTEXT_HIT = 9.0
W_CONTEXT_CAP = 45.0
W_TAG_HIT = 8.0
W_CATEGORY_HIT = 5.0
W_RIVAL_PENALTY = -22.0
W_NO_IMAGE = -6.0
W_IMAGE = 6.0
W_ENGAGEMENT_CAP = 22.0
W_RECENCY_MAX = 42.0
RECENCY_HALF_LIFE_HOURS = 22.0
FRESH_BONUS = 8.0
FRESH_HOURS = 3.0
#: Score floor for articles that pass the gate, so a low-signal-but-relevant
#: article still outranks a high-scoring article about another club.
GATE_FLOOR = 30.0


@dataclass
class Analysis:
    """Explained score for a single article."""

    united: bool
    score: float
    relevance: float
    recency: float
    engagement: float
    quality: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "united": self.united,
            "score": round(self.score, 2),
            "relevance": round(self.relevance, 2),
            "recency": round(self.recency, 2),
            "engagement": round(self.engagement, 2),
            "quality": round(self.quality, 2),
            "reasons": self.reasons,
        }


def _text_of(doc: dict[str, Any]) -> tuple[str, str]:
    """Combined title and body text, lower-cased."""
    title = str(doc.get("title") or "")
    body = " ".join(
        str(doc.get(key) or "")
        for key in ("summary", "content", "description")
        if doc.get(key)
    )
    tags = doc.get("tags") or []
    if tags:
        body = f"{body} {' '.join(str(tag) for tag in tags)}"
    return title.lower(), body.lower()


def _mask_other_uniteds(text: str) -> str:
    """Blank out "Newcastle United" etc. so a bare "united" means our club."""
    return _OTHER_UNITED_RE.sub(" ", text)


def _count_terms(haystack: str, terms: Iterable[str]) -> int:
    return sum(1 for term in terms if term in haystack)


def _hours_since(value: Any, now: datetime) -> float:
    if not isinstance(value, datetime):
        return 24 * 7.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (now - value).total_seconds() / 3600.0)


def analyse(doc: dict[str, Any], *, now: datetime | None = None) -> Analysis:
    """Score one article. See the module docstring for the axes."""
    now = now or utcnow()
    title, body = _text_of(doc)
    masked_title = _mask_other_uniteds(title)
    masked_body = _mask_other_uniteds(body)

    reasons: list[str] = []
    relevance = 0.0

    strong_title = _count_terms(masked_title, STRONG_TERMS)
    strong_body = _count_terms(masked_body, STRONG_TERMS)
    if strong_title:
        relevance += W_STRONG_TITLE
        reasons.append("strong-name-in-title")
    if strong_body:
        relevance += W_STRONG_BODY
        reasons.append("strong-name-in-body")

    bare_title = bool(_BARE_UNITED_RE.search(masked_title)) if not strong_title else False
    bare_body = bool(_BARE_UNITED_RE.search(masked_body)) if not (strong_title or strong_body) else False
    if bare_title:
        relevance += W_BARE_UNITED_TITLE
        reasons.append("united-in-title")
    if bare_body:
        relevance += W_BARE_UNITED_BODY
        reasons.append("united-in-body")

    context_hits = _count_terms(masked_title, CONTEXT_TERMS) * 2
    context_hits += _count_terms(masked_body, CONTEXT_TERMS)
    if context_hits:
        relevance += min(W_CONTEXT_CAP, context_hits * W_CONTEXT_HIT)
        reasons.append(f"context:{min(context_hits, 8)}")

    tags = " ".join(str(tag) for tag in (doc.get("tags") or [])).lower()
    if tags and any(term in tags for term in ("manchester united", "mufc", "united")):
        relevance += W_TAG_HIT
        reasons.append("tagged-united")

    category = str(doc.get("category") or "").lower()
    if category in {"transfers", "matchday", "team news", "club"}:
        relevance += W_CATEGORY_HIT

    # Rivals are only a negative when the article has no United signal of its own.
    if not strong_title and not strong_body and not bare_title:
        rival_hits = _count_terms(title, RIVAL_TERMS) + _count_terms(body, RIVAL_TERMS)
        if rival_hits:
            relevance += W_RIVAL_PENALTY
            reasons.append(f"rival-only:{rival_hits}")

    united = bool(
        strong_title
        or strong_body
        or bare_title
        or bare_body
        or context_hits >= 1
        or "tagged-united" in reasons
    )

    engagement = min(
        W_ENGAGEMENT_CAP,
        float(doc.get("views") or 0) / 25.0 + float(doc.get("comments") or 0) * 4.0,
    )

    image = doc.get("image")
    quality = 0.0
    if isinstance(image, str) and image.strip():
        quality += W_IMAGE
    else:
        quality += W_NO_IMAGE
        reasons.append("no-image")
    source = str(doc.get("source") or "").lower()
    for name, points in SOURCE_TIERS.items():
        if name in source:
            quality += points
            reasons.append(f"source:{name}")
            break

    hours = _hours_since(doc.get("published_at"), now)
    recency = W_RECENCY_MAX * math.exp(-hours / RECENCY_HALF_LIFE_HOURS) if hours < 24 * 14 else 0.0
    if hours <= FRESH_HOURS:
        recency += FRESH_BONUS
        reasons.append("breaking")

    score = relevance + recency + engagement + quality
    if united:
        score = max(score, GATE_FLOOR + relevance)

    return Analysis(
        united=united,
        score=score,
        relevance=relevance,
        recency=recency,
        engagement=engagement,
        quality=quality,
        reasons=reasons,
    )


def explain(doc: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Public helper: the score breakdown for one article."""
    return analyse(doc, now=now).as_dict()


#: Cap on how much club relevance a search result may gain over a pure text
#: score. It is a tiebreaker, not a filter: the best textual match must still
#: win, but between two equally good matches the United story comes first.
QUERY_BOOST_RELEVANCE = 0.05
QUERY_BOOST_RECENCY = 0.04
QUERY_BOOST_RELEVANCE_CAP = 120.0

#: Article analysis scores run to roughly 0-200, while text match scores from
#: :mod:`app.services.search_engine` are compact (a title hit is worth ~6). Lift
#: query matches into the same range so a real textual match outranks an article
#: that is merely a little more club-relevant.
QUERY_SCORE_SCALE = 8.0


def query_boost(doc: dict[str, Any], *, now: datetime | None = None) -> float:
    """Small club-relevance nudge used to break ties between search hits."""
    analysis = analyse(doc, now=now)
    relevance = min(max(analysis.relevance, 0.0), QUERY_BOOST_RELEVANCE_CAP)
    return QUERY_BOOST_RELEVANCE * relevance + QUERY_BOOST_RECENCY * analysis.recency


def _query_score(doc: dict[str, Any], query: str) -> float:
    """How well ``doc`` answers a search query, independent of club relevance.

    Delegates to :mod:`app.services.search_engine`, which understands multi-word
    queries, quoted phrases, ``field:`` scopes, exclusions and prefixes. The
    engine scores on a compact scale, so the result is lifted by
    :data:`QUERY_SCORE_SCALE` before it is added to an analysis score.
    """
    return QUERY_SCORE_SCALE * search_engine.match_score(doc, query)



def _diversify(rows: list[tuple[float, dict[str, Any]]], *, max_per_source: int) -> list[tuple[float, dict[str, Any]]]:
    """Nudge repeats of the same publisher down the feed.

    A single prolific wire can otherwise own the whole front page. Sources are
    penalised progressively past ``max_per_source``, never removed, so nothing
    becomes unreachable through paging.
    """
    seen: Counter[str] = Counter()
    adjusted: list[tuple[float, dict[str, Any]]] = []
    for score, doc in rows:
        key = str(doc.get("source") or "unknown").lower()
        repeats = seen[key]
        seen[key] += 1
        if repeats >= max_per_source:
            score -= 12.0 * (repeats - max_per_source + 1)
        adjusted.append((score, doc))
    adjusted.sort(key=lambda row: row[0], reverse=True)
    return adjusted


def rank(
    docs: list[dict[str, Any]],
    *,
    limit: int | None = None,
    skip: int = 0,
    query: str | None = None,
    united_only: bool = True,
    min_items: int = 8,
    max_per_source: int = 3,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Order a pool of articles.

    Returns ``(page, total)``. ``total`` is the size of the eligible pool so the
    caller can paginate honestly.
    """
    now = now or utcnow()
    scored: list[tuple[float, dict[str, Any]]] = []
    gated: list[tuple[float, dict[str, Any]]] = []

    for doc in docs:
        analysis = analyse(doc, now=now)
        total = analysis.score
        if query:
            total += _query_score(doc, query)
        row = (total, doc)
        scored.append(row)
        if analysis.united:
            gated.append(row)

    # Prefer the gated pool, but never let a cold corpus empty the site.
    pool = gated if (united_only and len(gated) >= min_items) else scored
    if query and gated and len(gated) < min_items:
        # Even with few hits, a query the club matches should win outright.
        pool = gated

    pool.sort(key=lambda row: row[0], reverse=True)
    pool = _diversify(pool, max_per_source=max_per_source)

    total = len(pool)
    page = [doc for _, doc in pool[skip : skip + limit if limit else None]]
    return page, total


def fetch(
    database: Database,
    *,
    limit: int = 20,
    skip: int = 0,
    query: str | None = None,
    category: str | None = None,
    source: str | None = None,
    featured: bool | None = None,
    status: str = "Published",
    united_only: bool = True,
    min_items: int = 8,
    max_per_source: int = 3,
    pool_size: int = 500,
) -> tuple[list[dict[str, Any]], int]:
    """Load a candidate pool from Mongo and return a ranked page of it."""
    criteria: dict[str, Any] = {}
    if status and status.lower() != "all":
        criteria["status"] = status
    if category and category.lower() not in {"all", ""}:
        criteria["category"] = category
    if source:
        criteria["source"] = source
    if featured is not None:
        criteria["featured"] = featured
    if query:
        criteria.update(search_engine.prefilter(search_engine.parse_query(query)))

    cursor = (
        database[db_module.NEWS]
        .find(criteria, {"_id": 0})
        .sort("published_at", -1)
        .limit(max(1, pool_size))
    )
    return rank(
        list(cursor),
        limit=limit,
        skip=skip,
        query=query,
        united_only=united_only,
        min_items=min_items,
        max_per_source=max_per_source,
    )


def annotate(doc: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Return ``doc`` with the algorithm's verdict stored on it."""
    analysis = analyse(doc, now=now)
    return {
        "united": analysis.united,
        "united_score": round(analysis.score, 2),
        "united_relevance": round(analysis.relevance, 2),
    }


def backfill(database: Database, *, limit: int = 2000) -> int:
    """Score any stored article that predates the algorithm (or was edited).

    Cheap and idempotent: only documents missing ``united_score`` are revisited,
    so the daily ingest keeps the whole corpus annotated without a full rewrite.
    """
    updated = 0
    cursor = (
        database[db_module.NEWS]
        .find({"united_score": {"$exists": False}}, {"_id": 1, "title": 1, "summary": 1, "content": 1, "tags": 1, "source": 1, "category": 1, "views": 1, "comments": 1, "image": 1, "published_at": 1})
        .limit(limit)
    )
    for doc in cursor:
        fields = annotate(doc)
        database[db_module.NEWS].update_one({"_id": doc["_id"]}, {"$set": fields})
        updated += 1
    return updated
