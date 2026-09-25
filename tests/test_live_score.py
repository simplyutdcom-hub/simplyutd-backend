"""Tests for the Live United score card.

Every test replaces the ESPN fetch with a synthetic event, so the suite never
talks to ESPN. The fixture events are invented scorelines, not real results.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import live_score_service

NOW = datetime(2026, 9, 14, 16, 30, tzinfo=timezone.utc)

_UNITED = {
    "id": "360",
    "displayName": "Manchester United",
    "shortDisplayName": "Man United",
    "abbreviation": "MAN",
    "logos": [{"href": "https://a.espncdn.com/i/teamlogos/soccer/500/360.png"}],
}
_CITY = {
    "id": "382",
    "displayName": "Manchester City",
    "shortDisplayName": "Man City",
    "abbreviation": "MCI",
    "logos": [],
}


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _event(
    kickoff: datetime,
    state: str,
    *,
    completed: bool | None = None,
    united_home: bool = True,
    score: tuple[str, str] = ("1", "2"),
) -> dict:
    """One ESPN-shaped event, with United on either side of the fixture."""
    if completed is None:
        completed = state == "post"
    home, away = (_UNITED, _CITY) if united_home else (_CITY, _UNITED)
    detail = {"in": "67'", "post": "FT", "pre": "Scheduled"}[state]
    return {
        "id": "401",
        "date": _iso(kickoff),
        "league": {"abbreviation": "Premier League"},
        "competitions": [
            {
                "status": {
                    "period": 2,
                    "displayClock": "67'",
                    "type": {
                        "state": state,
                        "description": detail,
                        "detail": detail,
                        "shortDetail": detail,
                        "completed": completed,
                    },
                },
                "venue": {"fullName": "Old Trafford"},
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": {"displayValue": score[0]},
                        "winner": False,
                        "team": home,
                    },
                    {
                        "homeAway": "away",
                        "score": {"displayValue": score[1]},
                        "winner": True,
                        "team": away,
                    },
                ],
            }
        ],
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    live_score_service.reset_cache()
    yield
    live_score_service.reset_cache()


@pytest.fixture()
def espn(monkeypatch):
    """Serve the given events for one league and nothing for the others.

Every league is asked twice when the card would otherwise be empty: once for
results and once for the fixtures still to come.
"""

    def install(events: list[dict], league: str = "eng.1") -> list[str]:
        asked: list[str] = []

        def fake_league(slug: str, upcoming: bool = False) -> list[dict]:
            asked.append(slug)
            return list(events) if slug == league else []

        monkeypatch.setattr(live_score_service, "_fetch_league", fake_league)
        return asked

    return install


def _soon(days: int = 5) -> datetime:
    """A kickoff the route will still see as upcoming.

``NOW`` pins the clock for the service-level tests, but the route reads the
wall clock, so its fixtures have to be dated from the real present.
"""
    return datetime.now(timezone.utc) + timedelta(days=days)


def _route_payload(client):
    return client.get("/api/hub/live-match").json()


# --- Window ----------------------------------------------------------------- #


def test_a_match_in_progress_is_shown(espn):
    espn([_event(NOW - timedelta(minutes=40), "in")])
    match = live_score_service.united_match(NOW)["match"]

    assert match is not None
    assert match["state"] == "in"
    assert match["detail"] == "67'"
    assert match["clock"] == "67'"


def test_a_match_that_has_not_kicked_off_is_not_shown(espn):
    espn([_event(NOW + timedelta(hours=2), "pre")])
    assert live_score_service.united_match(NOW)["match"] is None


def test_a_recent_final_whistle_is_still_shown(espn):
    espn([_event(NOW - timedelta(minutes=150), "post")])
    match = live_score_service.united_match(NOW)["match"]

    assert match is not None
    assert match["state"] == "post"
    assert match["completed"] is True


def test_a_match_is_dropped_once_the_two_hour_window_closes(espn):
    # Kick-off 135 minutes before the last whistle, plus the 120-minute grace.
    espn([_event(NOW - timedelta(minutes=300), "post")])
    assert live_score_service.united_match(NOW)["match"] is None


def test_a_match_inside_the_grace_period_is_kept_to_the_minute(espn):
    espn([_event(NOW - timedelta(minutes=254), "post")])
    assert live_score_service.united_match(NOW)["match"] is not None

    espn([_event(NOW - timedelta(minutes=257), "post")])
    live_score_service.reset_cache()
    assert live_score_service.united_match(NOW)["match"] is None


def test_an_abandoned_match_is_not_shown(espn):
    espn([_event(NOW - timedelta(minutes=30), "post", completed=False)])
    assert live_score_service.united_match(NOW)["match"] is None


def test_a_match_in_progress_beats_one_that_has_finished(espn):
    espn(
        [
            _event(NOW - timedelta(minutes=180), "post"),
            _event(NOW - timedelta(minutes=20), "in"),
        ]
    )
    assert live_score_service.united_match(NOW)["match"]["state"] == "in"


def test_the_most_recent_of_two_finished_matches_wins(espn):
    espn(
        [
            _event(NOW - timedelta(minutes=200), "post"),
            _event(NOW - timedelta(minutes=150), "post"),
        ]
    )
    match = live_score_service.united_match(NOW)["match"]
    assert match["kickoff"] == _iso(NOW - timedelta(minutes=150))


# --- Shape ------------------------------------------------------------------ #


def test_united_is_identified_whichever_end_they_play(espn):
    espn([_event(NOW - timedelta(minutes=40), "in", united_home=False, score=("3", "0"))])
    match = live_score_service.united_match(NOW)["match"]

    assert match["united"]["abbreviation"] == "MAN"
    assert match["opponent"]["abbreviation"] == "MCI"
    assert match["united"]["home"] is False
    assert match["away"]["abbreviation"] == "MAN"
    assert match["united"]["logo"].endswith("/360.png")


def test_the_payload_carries_the_competition_venue_and_score(espn):
    espn([_event(NOW - timedelta(minutes=40), "in", score=("1", "2"))])
    match = live_score_service.united_match(NOW)["match"]

    assert match["competition"] == "Premier League"
    assert match["league"] == "eng.1"
    assert match["venue"] == "Old Trafford"
    assert match["home"]["score"] == "1"
    assert match["away"]["score"] == "2"
    assert match["opponent"]["winner"] is True


def test_a_bare_score_value_is_read_as_well(espn):
    event = _event(NOW - timedelta(minutes=40), "in")
    event["competitions"][0]["competitors"][0]["score"] = "4"
    espn([event])
    assert live_score_service.united_match(NOW)["match"]["home"]["score"] == "4"


def test_an_event_without_united_is_ignored(espn):
    event = _event(NOW - timedelta(minutes=40), "in")
    event["competitions"][0]["competitors"][0]["team"] = _CITY
    espn([event])
    assert live_score_service.united_match(NOW)["match"] is None


def test_an_event_with_no_competition_is_ignored(espn):
    espn([{"id": "1", "date": _iso(NOW), "competitions": []}])
    assert live_score_service.united_match(NOW)["match"] is None


def test_every_league_is_asked(espn):
    asked = espn([])
    live_score_service.united_match(NOW)
    leagues = list(live_score_service.settings.live_score_leagues)
    assert asked == leagues + leagues


def test_a_league_that_fails_does_not_hide_the_match(monkeypatch):
    def flaky(slug: str, upcoming: bool = False) -> list[dict]:
        if slug == "eng.1":
            return [_event(NOW - timedelta(minutes=40), "in")]
        raise RuntimeError("league is down")

    monkeypatch.setattr(live_score_service, "_fetch_league", flaky)
    match = live_score_service.united_match(NOW)["match"]

    assert match is not None
    assert match["league"] == "eng.1"


# --- Upstream --------------------------------------------------------------- #


class _Response:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture()
def upstream(monkeypatch):
    def install(response: _Response | Exception) -> list[str]:
        asked: list[str] = []

        def fake_get(url: str, **kwargs):
            asked.append(url)
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setattr(live_score_service.httpx, "get", fake_get)
        return asked

    return install


def test_fetch_league_can_ask_for_the_fixtures_still_to_come(upstream):
    asked = upstream(_Response(payload={"events": [{"id": "1"}]}))
    events = live_score_service._fetch_league("eng.1", True)

    assert events == [{"id": "1"}]
    assert asked == [
        f"{live_score_service.settings.live_score_base_url}/eng.1"
        f"/teams/{live_score_service.settings.live_score_team_id}/schedule?fixture=true"
    ]


def test_fetch_league_reads_the_team_schedule(upstream):
    asked = upstream(_Response(payload={"events": [{"id": "1"}]}))
    events = live_score_service._fetch_league("eng.1")

    assert events == [{"id": "1"}]
    assert asked == [
        f"{live_score_service.settings.live_score_base_url}/eng.1"
        f"/teams/{live_score_service.settings.live_score_team_id}/schedule"
    ]


def test_fetch_league_sends_an_agent_espn_answers(monkeypatch):
    sent: list[dict] = []

    def fake_get(url: str, **kwargs):
        sent.append(kwargs.get("headers") or {})
        return _Response(payload={"events": []})

    monkeypatch.setattr(live_score_service.httpx, "get", fake_get)
    live_score_service._fetch_league("eng.1")

    assert sent == [
        {"User-Agent": live_score_service.settings.live_score_user_agent, "Accept": "application/json"}
    ]
    # ESPN's CDN 403s browser-shaped agents and requests with no agent at all.
    assert sent[0]["User-Agent"].startswith("python-httpx/")


def test_fetch_league_is_empty_on_an_error_status(upstream):
    upstream(_Response(status_code=500))
    assert live_score_service._fetch_league("eng.1") == []


def test_fetch_league_is_empty_when_unreachable(upstream):
    upstream(live_score_service.httpx.ConnectError("boom"))
    assert live_score_service._fetch_league("eng.1") == []


def test_fetch_league_is_empty_on_an_unreadable_body(upstream):
    upstream(_Response(payload=ValueError("not json")))
    assert live_score_service._fetch_league("eng.1") == []


def test_the_match_is_cached_between_calls(espn):
    asked = espn([_event(NOW - timedelta(minutes=40), "in")])
    live_score_service.united_match(NOW)
    live_score_service.united_match(NOW)
    assert len(asked) == len(live_score_service.settings.live_score_leagues)


def test_an_empty_lookup_is_cached_too(espn):
    asked = espn([])
    live_score_service.united_match(NOW)
    live_score_service.united_match(NOW)
    assert len(asked) == 2 * len(live_score_service.settings.live_score_leagues)


def test_a_forced_lookup_bypasses_the_cache(espn):
    asked = espn([_event(NOW - timedelta(minutes=40), "in")])
    live_score_service.united_match(NOW)
    live_score_service.united_match(NOW, force=True)
    assert len(asked) == 2 * len(live_score_service.settings.live_score_leagues)


def test_the_fixture_list_is_only_read_when_the_card_is_empty(espn):
    asked = espn([_event(NOW - timedelta(minutes=40), "in")])
    live_score_service.united_match(NOW)
    assert len(asked) == len(live_score_service.settings.live_score_leagues)


def test_the_card_can_be_switched_off(espn, monkeypatch):
    asked = espn([_event(NOW - timedelta(minutes=40), "in")])
    monkeypatch.setattr(live_score_service.settings, "live_score_enabled", False)

    assert live_score_service.united_match(NOW)["match"] is None
    assert asked == []


def test_the_payload_is_timestamped(espn):
    espn([])
    payload = live_score_service.united_match(NOW)
    assert payload["updated_at"] == _iso(NOW)


# --- Route ------------------------------------------------------------------ #


def test_the_route_returns_the_match(client, espn):
    espn([_event(NOW - timedelta(minutes=40), "in")])
    payload = _route_payload(client)

    assert payload["match"]["state"] == "in"
    assert payload["updated_at"] is not None


def test_the_route_returns_nothing_when_united_are_not_playing(client, espn):
    espn([])
    payload = _route_payload(client)

    assert payload["match"] is None
    assert payload["updated_at"] is not None


def test_the_live_match_route_is_never_cached(client, espn):
    espn([_event(_soon(), "pre")])
    response = client.get("/api/hub/live-match")

    assert response.headers["cache-control"] == "no-store"
    assert response.json()["next"] is not None


def test_the_live_match_route_is_not_read_as_a_hub_section(client):
    assert client.get("/api/hub/not-a-section").status_code == 404
    assert client.get("/api/hub/live-match").status_code == 200


# --- Next fixture ----------------------------------------------------------- #


def test_the_next_fixture_is_offered_while_nothing_is_being_played(espn):
    espn([_event(NOW + timedelta(days=3), "pre")])
    payload = live_score_service.united_match(NOW)

    assert payload["match"] is None
    assert payload["next"]["opponent"]["short_name"] == "Man City"
    assert payload["next"]["state"] == "pre"
    assert payload["next"]["venue"] == "Old Trafford"


def test_the_soonest_of_several_fixtures_is_the_next_one(espn):
    espn(
        [
            _event(NOW + timedelta(days=6), "pre"),
            _event(NOW + timedelta(days=2), "pre"),
        ]
    )
    payload = live_score_service.united_match(NOW)

    assert payload["next"]["kickoff"] == _iso(NOW + timedelta(days=2))


def test_a_kickoff_that_has_already_passed_is_not_the_next_fixture(espn):
    # ESPN can leave a postponed game marked as scheduled.
    espn([_event(NOW - timedelta(hours=2), "pre")])
    assert live_score_service.united_match(NOW)["next"] is None


def test_a_played_match_is_never_offered_as_the_next_fixture(espn):
    espn([_event(NOW - timedelta(minutes=40), "post")])
    payload = live_score_service.united_match(NOW)

    assert payload["match"]["state"] == "post"
    assert payload["next"] is None


def test_a_score_on_the_card_means_no_countdown(espn):
    espn([_event(NOW - timedelta(minutes=10), "in"), _event(NOW + timedelta(days=4), "pre")])
    payload = live_score_service.united_match(NOW)

    assert payload["match"]["state"] == "in"
    assert payload["next"] is None


def test_there_is_nothing_to_show_without_a_match_or_a_fixture(espn):
    espn([])
    payload = live_score_service.united_match(NOW)

    assert payload["match"] is None
    assert payload["next"] is None


def test_the_route_carries_the_next_fixture(client, espn):
    kickoff = _soon()
    espn([_event(kickoff, "pre")])
    payload = _route_payload(client)

    assert payload["match"] is None
    assert payload["next"]["kickoff"] == _iso(kickoff)
