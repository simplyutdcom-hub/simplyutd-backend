"""United's current match, for the live score card in the home page X panel.

Wikipedia - the Hub's source - carries dates but neither kickoff times nor
scores, so the card reads ESPN's public soccer JSON instead. Every competition
United can appear in is queried in one pass and the match inside the window
wins: the card is shown while a match is being played and for two hours after
the final whistle. The same pass picks up United's next scheduled fixture, so
the panel can count down to it while there is no match to report.

Like the other upstream integrations this never raises. An unreachable or
unreadable league is skipped, and a total failure reads as "no match" rather
than an error, because the panel also carries the timeline.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_cache: dict[str, Any] = {"payload": None, "expires": 0.0}


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def _score(entry: dict[str, Any]) -> str | None:
    """ESPN reports a score as either a display object or a bare value."""
    value = entry.get("score")
    if isinstance(value, dict):
        value = value.get("displayValue") or value.get("value")
    if value is None:
        return None
    return str(value)


def _competitor(entry: dict[str, Any]) -> dict[str, Any]:
    team = entry.get("team") or {}
    logos = team.get("logos") or []
    return {
        "id": str(team.get("id") or ""),
        "name": team.get("displayName"),
        "short_name": team.get("shortDisplayName"),
        "abbreviation": team.get("abbreviation"),
        "logo": (logos[0] or {}).get("href") if logos else None,
        "score": _score(entry),
        "winner": bool(entry.get("winner")),
        "home": entry.get("homeAway") == "home",
    }


def _parse_match(event: dict[str, Any], league: str) -> dict[str, Any] | None:
    """One ESPN event reshaped for the card, or ``None`` when it is not United's."""
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competition = competitions[0]

    sides = [_competitor(entry) for entry in competition.get("competitors") or []]
    united = next((side for side in sides if side["id"] == settings.live_score_team_id), None)
    if united is None:
        return None

    status = (competition.get("status") or {}).get("type") or {}
    clock = competition.get("status") or {}
    return {
        "id": str(event.get("id") or ""),
        "league": league,
        "competition": (event.get("league") or {}).get("abbreviation") or league,
        "kickoff": event.get("date"),
        "state": status.get("state"),
        "status": status.get("description"),
        "detail": status.get("detail"),
        "short_detail": status.get("shortDetail"),
        "completed": bool(status.get("completed")),
        "period": clock.get("period"),
        "clock": clock.get("displayClock"),
        "venue": (competition.get("venue") or {}).get("fullName"),
        "home": next((side for side in sides if side["home"]), None),
        "away": next((side for side in sides if not side["home"]), None),
        "united": united,
        "opponent": next((side for side in sides if side is not united), None),
    }


def _in_window(match: dict[str, Any], now: datetime) -> bool:
    """Being played, or finished inside the grace period after the final whistle.

    A scheduled match is deliberately excluded, which is what keeps the card
    away from the panel until there is something to report.
    """
    if match.get("state") == "in":
        return True
    if match.get("state") != "post" or not match.get("completed"):
        return False
    kickoff = _as_utc(match.get("kickoff"))
    if kickoff is None:
        return False
    ends = kickoff + timedelta(minutes=settings.live_score_match_minutes)
    return now <= ends + timedelta(minutes=settings.live_score_grace_minutes)


def _fetch_league(league: str, upcoming: bool = False) -> list[dict[str, Any]]:
    """United's fixtures for one league.

    ESPN keeps played matches and upcoming ones behind different views of the
    same endpoint: the plain schedule only carries results, so the fixtures still
    to come have to be asked for explicitly.
    """
    url = (
        f"{settings.live_score_base_url}/{league}"
        f"/teams/{settings.live_score_team_id}/schedule"
    )
    if upcoming:
        url += "?fixture=true"
    try:
        response = httpx.get(
            url,
            timeout=settings.live_score_timeout,
            follow_redirects=True,
            headers={
                "User-Agent": settings.live_score_user_agent,
                "Accept": "application/json",
            },
        )
    except Exception:  # noqa: BLE001 - a dead league must not break the panel
        logger.debug("Live score fetch failed: %s", url)
        return []
    if response.status_code >= 400:
        logger.debug("Live score fetch returned %s: %s", response.status_code, url)
        return []
    try:
        return (response.json() or {}).get("events") or []
    except ValueError:
        logger.debug("Live score payload unreadable: %s", url)
        return []


def _league_matches(league: str, upcoming: bool = False) -> list[dict[str, Any]]:
    """United's matches for one league, skipping a league that is down."""
    try:
        events = _fetch_league(league, upcoming)
    except Exception:  # noqa: BLE001 - one bad league must not blank the panel
        logger.debug("Live score league failed: %s", league)
        return []
    matches = []
    for event in events:
        match = _parse_match(event, league)
        if match:
            matches.append(match)
    return matches


def united_match(now: datetime | None = None, force: bool = False) -> dict[str, Any]:
    """The match to show and the next one to count down to, each possibly ``None``."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not settings.live_score_enabled:
        return {"match": None, "next": None, "updated_at": _iso(moment)}

    cached = _cache["payload"]
    if cached is not None and not force and time.monotonic() < _cache["expires"]:
        return cached

    playing: list[dict[str, Any]] = []
    finished: list[dict[str, Any]] = []
    for league in settings.live_score_leagues:
        for match in _league_matches(league):
            if _in_window(match, moment):
                (playing if match["state"] == "in" else finished).append(match)

    # A match in progress always wins; otherwise the most recent final whistle.
    chosen = None
    if playing:
        chosen = playing[0]
    elif finished:
        chosen = max(finished, key=lambda match: match.get("kickoff") or "")

    # With a score on the card there is nothing to count down to, so the extra
    # round trip for the fixture list is only spent when the card would be empty.
    upcoming: list[dict[str, Any]] = []
    if chosen is None:
        for league in settings.live_score_leagues:
            for match in _league_matches(league, upcoming=True):
                kickoff = _as_utc(match.get("kickoff"))
                # A kickoff still ahead of us, so a postponed game ESPN leaves
                # marked as scheduled drops out instead of counting up.
                if match["state"] == "pre" and kickoff and kickoff > moment:
                    upcoming.append(match)
        upcoming.sort(key=lambda match: match.get("kickoff") or "")

    payload = {
        "match": chosen,
        "next": upcoming[0] if upcoming else None,
        "updated_at": _iso(moment),
    }
    _cache["payload"] = payload
    _cache["expires"] = time.monotonic() + settings.live_score_cache_seconds
    return payload


def reset_cache() -> None:
    """Clear the cached match (used by tests)."""
    _cache["payload"] = None
    _cache["expires"] = 0.0
