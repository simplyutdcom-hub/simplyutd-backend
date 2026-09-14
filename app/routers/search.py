"""Site search.

One endpoint behind the navbar search box. The heavy lifting lives in
:mod:`app.services.search_engine`, which parses whatever the reader typed
(several keywords in any order, quoted phrases, ``source:sky`` scopes,
``-exclusions``, ``rashford*`` prefixes, citations, typos) and scores it against
every searchable field.

Ordering is text match first with the United-first relevance
(:func:`app.services.feed_rank.query_boost`) folded in as a tiebreaker, so
"amorim" still puts the tactically-relevant piece on top rather than merely the
newest article that happens to contain the string.

The response carries ``parsed``, ``broadened`` and ``fuzzy`` so the UI can tell
the reader when it had to widen its interpretation to find anything.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from ..db import get_db
from ..services import feed_rank, news_service, search_engine

router = APIRouter(prefix="/api/search", tags=["search"])


def _response(
    term: str,
    items: list[dict],
    total: int,
    limit: int,
    skip: int,
    parsed: search_engine.ParsedQuery,
    *,
    broadened: bool = False,
    fuzzy: bool = False,
) -> dict:
    return {
        "query": term,
        "total": total,
        "items": items,
        "limit": limit,
        "skip": skip,
        "parsed": parsed.as_dict(),
        "broadened": broadened,
        "fuzzy": fuzzy,
    }


@router.get("")
def search(
    q: str = Query("", max_length=160),
    limit: int = Query(20, ge=1, le=50),
    skip: int = Query(0, ge=0),
    database: Database = Depends(get_db),
) -> dict:
    """Search published articles across titles, summaries, tags, sources and slugs."""
    term = q.strip()
    if not term:
        return _response("", [], 0, limit, skip, search_engine.parse_query(""))

    outcome = search_engine.search(
        database,
        term,
        limit=limit,
        skip=skip,
        boost=feed_rank.query_boost,
    )
    items = [
        {
            **news_service.story_card(doc),
            "time": news_service.minutes_ago(doc.get("published_at")),
            "score": round(match.score, 2),
            "matched": list(match.matched),
        }
        for doc, match in outcome.rows
    ]
    return _response(
        term,
        items,
        outcome.total,
        limit,
        skip,
        outcome.parsed,
        broadened=outcome.broadened,
        fuzzy=outcome.fuzzy,
    )
