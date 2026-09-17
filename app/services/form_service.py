"""Each Premier League club's last five results, for the table's form column.

Wikipedia - the Hub's source for the table itself - carries the scores but not
the order they were played in: its results grid is a home/away matrix, so the
"last five" cannot be read out of it. ESPN's per-club schedule can, though: one
request per club returns its played matches with dates and a winner flag, which
is all the form column needs.

Twenty requests is far too much work to put in front of a request, so the
request path only ever reads what has already been fetched
(:func:`cached_form`) and the background hub refresher keeps that cache warm.
Nothing here raises: a club ESPN cannot answer for simply goes without form.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger("simplyutd.form")

# What the table's form column shows, newest result last.
FORM_LENGTH = 5

_cache: dict[str, Any] = {"form": None, "expires": 0.0}


def _get(url: str) -> Any:
    """The JSON at ``url``, or ``None`` when ESPN will not answer."""
    try:
        response = httpx.get(
            url,
            timeout=settings.live_score_timeout,
            follow_redirects=True,
            headers={
                # ESPN's CDN blocks browser-shaped agents, so the shared library
                # agent is kept here too (see AppSettings.live_score_user_agent).
                "User-Agent": settings.live_score_user_agent,
                "Accept": "application/json",
            },
        )
        if response.status_code >= 400:
            logger.debug("Form fetch returned %s: %s", response.status_code, url)
            return None
        return response.json()
    except Exception:  # noqa: BLE001 - a dead endpoint must not break the table
        logger.debug("Form fetch failed: %s", url)
        return None


def _clubs() -> list[tuple[str, str]]:
    """``(team id, team name)`` for every club in the league, as ESPN spells it."""
    payload = _get(f"{settings.live_score_base_url}/{settings.hub_form_league}/teams")
    sports = (payload or {}).get("sports") if isinstance(payload, dict) else None
    leagues = ((sports or [{}])[0].get("leagues") or [{}])
    clubs: list[tuple[str, str]] = []
    for entry in (leagues[0].get("teams") or []):
        team = entry.get("team") or {}
        team_id = str(team.get("id") or "").strip()
        name = (team.get("displayName") or team.get("name") or "").strip()
        if team_id and name:
            clubs.append((team_id, name))
    return clubs


def _result(event: dict[str, Any], team_id: str) -> tuple[str, str] | None:
    """``(kickoff, W|D|L)`` for one of the club's played matches, else ``None``."""
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competition = competitions[0]
    status = (competition.get("status") or {}).get("type") or {}
    if status.get("state") != "post" or not status.get("completed"):
        return None

    competitors = competition.get("competitors") or []
    club = next(
        (
            entry
            for entry in competitors
            if str(((entry.get("team") or {}).get("id")) or "") == team_id
        ),
        None,
    )
    others = [entry for entry in competitors if entry is not club]
    if club is None or len(others) != 1:
        return None

    if club.get("winner"):
        outcome = "W"
    elif others[0].get("winner"):
        outcome = "L"
    else:
        # ESPN leaves both flags false on a draw.
        outcome = "D"
    return str(event.get("date") or ""), outcome


def _club_form(team_id: str) -> str:
    """The club's most recent results, newest last, e.g. ``WDLWW``."""
    url = (
        f"{settings.live_score_base_url}/{settings.hub_form_league}"
        f"/teams/{team_id}/schedule"
    )
    payload = _get(url)
    events = (payload or {}).get("events") if isinstance(payload, dict) else None
    played = [result for result in (_result(e, team_id) for e in events or []) if result]
    played.sort(key=lambda item: item[0])
    return "".join(outcome for _, outcome in played[-FORM_LENGTH:])


def fetch_form(force: bool = False) -> dict[str, str]:
    """Refresh and return ``{club name: form}``, cached for its own interval."""
    if not (settings.hub_form_enabled and settings.hub_wikipedia_enabled):
        return {}

    cached = _cache["form"]
    if cached is not None and not force and time.monotonic() < _cache["expires"]:
        return cached

    clubs = _clubs()
    if not clubs:
        return cached or {}

    workers = max(1, min(settings.hub_form_workers, len(clubs)))
    form: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for (_, name), results in zip(clubs, pool.map(lambda c: _club_form(c[0]), clubs)):
            if results:
                form[name] = results

    if form:
        _cache["form"] = form
        _cache["expires"] = time.monotonic() + settings.hub_form_cache_seconds
        logger.info("Refreshed last-5 form for %d clubs", len(form))
    return form


def cached_form() -> dict[str, str]:
    """The last form fetched, even once it has aged out, and never a fetch.

    Stale form still reads correctly - it is the club's last five results, not a
    live score - so the column keeps its contents while the refresher catches up.
    """
    return _cache["form"] or {}


def reset_cache() -> None:
    """Clear the cached form (used by tests)."""
    _cache["form"] = None
    _cache["expires"] = 0.0
