"""Tests for the table's last-five form column.

Every test replaces the ESPN fetch with synthetic payloads, so the suite never
talks to ESPN. The fixtures are invented scorelines, not real results.
"""
from __future__ import annotations

import pytest

from app.services import form_service


def _competitor(team_id: str, name: str, *, winner: bool = False, home: bool = True) -> dict:
    return {
        "homeAway": "home" if home else "away",
        "winner": winner,
        "team": {"id": team_id, "displayName": name},
    }


def _event(
    date: str,
    *,
    state: str = "post",
    completed: bool | None = None,
    winners: tuple[bool, bool] = (True, False),
) -> dict:
    """One ESPN-shaped schedule entry between team 1 and team 2."""
    if completed is None:
        completed = state == "post"
    return {
        "id": date,
        "date": date,
        "competitions": [
            {
                "status": {"type": {"state": state, "completed": completed}},
                "competitors": [
                    _competitor("1", "Club One", winner=winners[0], home=True),
                    _competitor("2", "Club Two", winner=winners[1], home=False),
                ],
            }
        ],
    }


_CLUBS = {
    "sports": [
        {
            "leagues": [
                {
                    "teams": [
                        {"team": {"id": "1", "displayName": "Club One"}},
                        {"team": {"id": "2", "displayName": "Club Two"}},
                    ]
                }
            ]
        }
    ]
}

# Five played matches for club 1, in the order ESPN returns them (newest first).
_SCHEDULE_ONE = {
    "events": [
        _event("2026-09-12T19:00Z", winners=(False, True)),
        _event("2026-09-06T15:30Z", winners=(True, False)),
        _event("2026-08-31T19:00Z", winners=(False, False)),
        _event("2026-08-24T19:00Z"),
        _event("2026-08-17T19:00Z", winners=(True, False)),
        # Not played yet, and therefore not a form result.
        _event("2026-09-20T15:30Z", state="pre"),
    ]
}


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    monkeypatch.setattr(form_service.settings, "hub_form_enabled", True)
    monkeypatch.setattr(form_service.settings, "hub_wikipedia_enabled", True)
    form_service.reset_cache()
    yield
    form_service.reset_cache()


def test_clubs_reads_every_team_in_the_league(monkeypatch):
    payload = {"sports": [{"leagues": [{"teams": [
        {"team": {"id": "360", "displayName": "Manchester United"}},
        {"team": {"id": "359", "name": "Arsenal"}},
        {"team": {"id": "", "displayName": "Ignore me"}},
    ]}]}]}
    monkeypatch.setattr(form_service, "_get", lambda url: payload)
    assert form_service._clubs() == [("360", "Manchester United"), ("359", "Arsenal")]


def test_result_reads_the_winner_flag_and_ignores_unplayed_matches():
    assert form_service._result(_event("2026-09-12T19:00Z"), "1") == ("2026-09-12T19:00Z", "W")
    assert form_service._result(_event("2026-09-12T19:00Z", winners=(False, True)), "1") == (
        "2026-09-12T19:00Z",
        "L",
    )
    assert form_service._result(_event("2026-09-12T19:00Z", winners=(False, False)), "1") == (
        "2026-09-12T19:00Z",
        "D",
    )
    assert form_service._result(_event("2026-09-20T15:30Z", state="pre"), "1") is None
    # A match ESPN still thinks is in progress is not a result either.
    assert form_service._result(_event("2026-09-20T15:30Z", state="in"), "1") is None
    assert form_service._result({"date": "2026-09-12T19:00Z"}, "1") is None


def test_club_form_is_oldest_first_and_capped_to_five(monkeypatch):
    monkeypatch.setattr(form_service, "_get", lambda url: _SCHEDULE_ONE)
    form = form_service._club_form("1")
    # Read oldest to newest, and the unplayed match does not count towards the five.
    assert form == "WWDWL"


def test_fetch_form_maps_clubs_and_caches(monkeypatch):
    calls: list[str] = []

    def fake_get(url: str):
        calls.append(url)
        if url.endswith("/teams"):
            return _CLUBS
        return _SCHEDULE_ONE

    monkeypatch.setattr(form_service, "_get", fake_get)

    form = form_service.fetch_form()
    assert form == {"Club One": "WWDWL", "Club Two": "LLDLW"}
    assert sum(url.endswith("/teams") for url in calls) == 1

    # The second read is answered from the cache.
    before = len(calls)
    assert form_service.fetch_form() == form
    assert len(calls) == before


def test_fetch_form_refetches_when_forced(monkeypatch):
    calls: list[str] = []

    def fake_get(url: str):
        calls.append(url)
        return _CLUBS if url.endswith("/teams") else _SCHEDULE_ONE

    monkeypatch.setattr(form_service, "_get", fake_get)
    form_service.fetch_form()
    before = len(calls)
    form_service.fetch_form(force=True)
    assert len(calls) > before


def test_fetch_form_is_quiet_when_espn_will_not_answer(monkeypatch):
    monkeypatch.setattr(form_service, "_get", lambda url: None)
    assert form_service.fetch_form() == {}
    assert form_service.cached_form() == {}


def test_fetch_form_keeps_the_last_good_read(monkeypatch):
    monkeypatch.setattr(
        form_service, "_get", lambda url: _CLUBS if url.endswith("/teams") else _SCHEDULE_ONE
    )
    assert form_service.fetch_form()
    monkeypatch.setattr(form_service, "_get", lambda url: None)
    # A failed refresh must not empty a column that was already accurate.
    assert form_service.cached_form() == {"Club One": "WWDWL", "Club Two": "LLDLW"}


def test_form_is_skipped_when_the_hub_sources_are_off(monkeypatch):
    monkeypatch.setattr(form_service.settings, "hub_form_enabled", False)
    monkeypatch.setattr(form_service, "_get", lambda url: _CLUBS)
    assert form_service.fetch_form() == {}

    monkeypatch.setattr(form_service.settings, "hub_form_enabled", True)
    monkeypatch.setattr(form_service.settings, "hub_wikipedia_enabled", False)
    assert form_service.fetch_form() == {}


def test_the_hub_carries_form_onto_the_table_rows():
    from app.services import hub_service

    form_service._cache["form"] = {"Club One": "WWDLW", "Club Two": "LLDWW"}
    rows = [{"team": "Club One", "pos": 1}, {"team": "Club Two", "pos": 2}]
    assert [row.get("form") for row in hub_service._with_form(rows)] == ["WWDLW", "LLDWW"]


def test_the_hub_leaves_the_table_alone_without_form():
    from app.services import hub_service

    rows = [{"team": "Club One", "pos": 1}]
    assert hub_service._with_form(rows) is rows
