"""Tests for the SalaryLeaks wage read.

Every test feeds synthetic markup or replaces the HTTP call, so the suite never
talks to SalaryLeaks. The players below are invented, not real ones.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services import hub_service, salaryleaks

# --- Markup ---------------------------------------------------------------- #

_PLAYER_CELL = """\
<td class="px-4">
  <img src="data:image/gif;base64,R0lGOD" data-src="/flags/{slug}.svg"
       alt="{nation} Flag" />
  <a href="https://www.salaryleaks.com/football/{slug}">{name}</a>
  <i class="fa-solid fa-circle-check text-emerald"></i>
</td>
"""


def _player_cell(name: str, nation: str) -> str:
    slug = name.lower().replace(" ", "-")
    return _PLAYER_CELL.format(name=name, nation=nation, slug=slug)


_WAGES_PAGE = f"""\
<html><body>
<p>Last updated: <time datetime="2026-09-16">Sep 16, 2026</time></p>
<table class="w-full bg-white rounded-lg shadow-sm">
  <thead><tr>
    <th>#</th><th>Player</th><th>Weekly</th><th>Annual</th>
    <th>Bonus</th><th>Age</th><th>Contract</th>
  </tr></thead>
  <tbody>
    <tr>
      <td>1</td>
      {_player_cell("Big Earner", "England")}
      <td><span class="salary-convert" data-base="325000">£325,000</span></td>
      <td>£16.9M</td>
      <td>+<span class="salary-convert" data-base="2600000">£2.6M</span></td>
      <td>29</td>
      <td>Jun 30, 2029</td>
    </tr>
    <tr>
      <td>2</td>
      {_player_cell("No Bonus", "Portugal")}
      <td>£300</td>
      <td>£18,200</td>
      <td>-</td>
      <td>21</td>
      <td>2027</td>
    </tr>
    <tr class="total-row">
      <td>Total</td><td>£300,300</td><td>£17.4M</td><td>£2.8M</td>
    </tr>
  </tbody>
</table>
<table class="w-full bg-white rounded-lg shadow-sm">
  <thead><tr>
    <th>Player</th><th>Weekly</th><th>Annual</th>
    <th>Bonus</th><th>Age</th><th>Contract</th>
  </tr></thead>
  <tbody>
    <tr>
      {_player_cell("Mobile Copy", "Spain")}
      <td>£1,000</td><td>£52,000</td><td>£0</td><td>30</td><td>2028</td>
    </tr>
  </tbody>
</table>
</body></html>
"""


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    """The read may not leak between tests, and it must be enabled.

    The suite runs with the source switched off (see ``conftest``) so no other
    test can reach the network by accident; here it is switched back on and the
    HTTP call is always replaced.
    """
    monkeypatch.setattr(settings, "salaryleaks_enabled", True)
    salaryleaks.reset_cache()
    yield
    salaryleaks.reset_cache()


def _serve(monkeypatch, page: str | None, calls: list[str] | None = None):
    """Replace the HTTP call with a page, or with a failure when ``None``."""

    def fake_get(url: str) -> str | None:
        if calls is not None:
            calls.append(url)
        return page

    monkeypatch.setattr(salaryleaks, "_get", fake_get)


# --- The table ------------------------------------------------------------- #


def test_every_player_is_read():
    data = salaryleaks.parse_salaries(_WAGES_PAGE)
    assert [player["name"] for player in data["players"]] == ["Big Earner", "No Bonus"]


def test_the_desktop_copy_is_the_one_read():
    """The page renders the table twice; the first copy wins."""
    data = salaryleaks.parse_salaries(_WAGES_PAGE)
    assert "Mobile Copy" not in [player["name"] for player in data["players"]]


def test_a_row_is_mapped_by_its_header_not_its_position():
    data = salaryleaks.parse_salaries(_WAGES_PAGE)
    first = data["players"][0]
    assert first["weekly"] == "£325,000"
    assert first["annual"] == "£16.9M"
    assert first["age"] == 29
    assert first["contract"] == "2029"


def test_the_player_cell_carries_the_profile_link():
    data = salaryleaks.parse_salaries(_WAGES_PAGE)
    assert data["players"][0]["url"] == "https://www.salaryleaks.com/football/big-earner"


def test_the_nation_comes_from_the_flag_alt_text():
    data = salaryleaks.parse_salaries(_WAGES_PAGE)
    assert [player["nation"] for player in data["players"]] == ["England", "Portugal"]


def test_the_verification_tick_is_recorded():
    data = salaryleaks.parse_salaries(_WAGES_PAGE)
    assert all(player["verified"] for player in data["players"])


def test_a_player_without_the_tick_is_not_verified():
    html = _WAGES_PAGE.replace("fa-circle-check", "fa-circle-question")
    data = salaryleaks.parse_salaries(html)
    assert not any(player["verified"] for player in data["players"])


def test_a_dash_bonus_becomes_no_bonus():
    data = salaryleaks.parse_salaries(_WAGES_PAGE)
    assert data["players"][1]["bonus"] is None
    assert data["players"][1]["bonus_amount"] is None


def test_a_bonus_keeps_its_short_form_and_its_amount():
    data = salaryleaks.parse_salaries(_WAGES_PAGE)
    assert data["players"][0]["bonus"] == "£2.6M"
    assert data["players"][0]["bonus_amount"] == 2_600_000


def test_the_published_totals_are_read_from_the_short_row():
    data = salaryleaks.parse_salaries(_WAGES_PAGE)
    assert data["totals"] == {"weekly": 300_300, "annual": 17_400_000, "bonus": 2_800_000}


def test_the_updated_stamp_comes_from_the_time_element():
    data = salaryleaks.parse_salaries(_WAGES_PAGE)
    assert data["source_updated"] == "2026-09-16"


def test_a_page_without_the_table_yields_nothing():
    data = salaryleaks.parse_salaries("<html><body><table><tr><td>x</td></tr></table>")
    assert data == {"players": [], "totals": {}, "source_updated": None}


def test_a_page_with_a_different_table_first_still_parses():
    html = _WAGES_PAGE.replace(
        '<table class="w-full bg-white rounded-lg shadow-sm">',
        "<table><tr><th>Fixtures</th><th>Date</th></tr></table>"
        '<table class="w-full bg-white rounded-lg shadow-sm">',
        1,
    )
    data = salaryleaks.parse_salaries(html)
    assert len(data["players"]) == 2


def test_a_row_that_does_not_line_up_is_skipped():
    html = _WAGES_PAGE.replace(
        '<td><span class="salary-convert" data-base="325000">£325,000</span></td>',
        "",
        1,
    )
    data = salaryleaks.parse_salaries(html)
    assert [player["name"] for player in data["players"]] == ["No Bonus"]


def test_empty_and_garbage_input_are_safe():
    assert salaryleaks.parse_salaries("")["players"] == []
    assert salaryleaks.parse_salaries("<<<>>>")["players"] == []


def test_the_headline_row_is_not_taken_for_a_player():
    """The header row is all <th>, so it cannot be mistaken for a wage."""
    data = salaryleaks.parse_salaries(_WAGES_PAGE)
    assert "Weekly" not in [player["name"] for player in data["players"]]


# --- Money ----------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("£325,000", 325_000),
        ("£16.9M", 16_900_000),
        ("£18,200", 18_200),
        ("£300", 300),
        ("£2.6M", 2_600_000),
        ("£1.2B", 1_200_000_000),
        ("£500K", 500_000),
        ("£0", 0),
    ],
)
def test_money_understands_the_short_forms(text, expected):
    assert salaryleaks._money(text) == expected


@pytest.mark.parametrize("text", ["-", "", "n/a", None])
def test_money_returns_nothing_for_a_dash(text):
    assert salaryleaks._money(text) is None


def test_money_tolerates_a_space_after_the_sign():
    assert salaryleaks._money("£ 1,000") == 1_000


# --- The HTTP read --------------------------------------------------------- #


def test_the_read_is_cached(monkeypatch):
    calls: list[str] = []
    _serve(monkeypatch, _WAGES_PAGE, calls)
    salaryleaks.salaries()
    salaryleaks.salaries()
    assert len(calls) == 1


def test_the_read_uses_the_configured_club_url(monkeypatch):
    calls: list[str] = []
    _serve(monkeypatch, _WAGES_PAGE, calls)
    salaryleaks.salaries()
    assert calls[0].endswith("/football/teams/manchester-united")


def test_an_expired_cache_is_refetched(monkeypatch):
    calls: list[str] = []
    _serve(monkeypatch, _WAGES_PAGE, calls)
    salaryleaks.salaries()
    salaryleaks._cache["salaries_expires"] = 0.0
    salaryleaks.salaries()
    assert len(calls) == 2


def test_a_failed_read_keeps_the_last_good_read(monkeypatch):
    _serve(monkeypatch, _WAGES_PAGE)
    salaryleaks.salaries()
    _serve(monkeypatch, None)
    salaryleaks._cache["salaries_expires"] = 0.0
    assert salaryleaks.salaries()["players"]


def test_a_failed_read_is_not_retried_on_the_next_call(monkeypatch):
    calls: list[str] = []
    _serve(monkeypatch, None, calls)
    salaryleaks.salaries()
    salaryleaks.salaries()
    assert len(calls) == 1


def test_a_page_that_parses_to_nothing_is_treated_as_a_failure(monkeypatch):
    calls: list[str] = []
    _serve(monkeypatch, "<html></html>", calls)
    salaryleaks.salaries()
    salaryleaks.salaries()
    assert len(calls) == 1


def test_disabling_the_source_reads_nothing(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(settings, "salaryleaks_enabled", False)
    _serve(monkeypatch, _WAGES_PAGE, calls)
    assert salaryleaks.salaries()["players"] == []
    assert calls == []


def test_the_read_reports_when_it_happened(monkeypatch):
    _serve(monkeypatch, _WAGES_PAGE)
    assert salaryleaks.salaries_updated_at() is None
    salaryleaks.salaries()
    assert salaryleaks.salaries_updated_at()


def test_wages_are_keyed_by_the_players_name(monkeypatch):
    _serve(monkeypatch, _WAGES_PAGE)
    assert salaryleaks.wages_by_player() == {"Big Earner": 325_000, "No Bonus": 300}


# --- Hub wiring ------------------------------------------------------------ #


def _patch_salaries(monkeypatch, page: str):
    monkeypatch.setattr(salaryleaks, "_get", lambda url: page)


def test_the_hub_carries_the_wage_bill(monkeypatch):
    _patch_salaries(monkeypatch, _WAGES_PAGE)
    salaries = hub_service.derive_salaries()
    assert salaries["total"] == 2
    assert salaries["players"][0]["name"] == "Big Earner"


def test_the_hub_prefers_the_sources_published_totals(monkeypatch):
    """The panel must foot to what SalaryLeaks published, not to our own sum."""
    _patch_salaries(monkeypatch, _WAGES_PAGE)
    salaries = hub_service.derive_salaries()
    assert salaries["weekly_total"] == 300_300
    assert salaries["annual_total"] == 17_400_000
    assert salaries["bonus_total"] == 2_800_000


def test_the_hub_falls_back_to_its_own_sum_without_published_totals(monkeypatch):
    html = _WAGES_PAGE.replace("<td>Total</td><td>£300,300</td><td>£17.4M</td><td>£2.8M</td>", "")
    _patch_salaries(monkeypatch, html)
    salaries = hub_service.derive_salaries()
    assert salaries["weekly_total"] == 325_300
    assert salaries["annual_total"] == 16_918_200


def test_the_hub_names_the_top_earner(monkeypatch):
    _patch_salaries(monkeypatch, _WAGES_PAGE)
    salaries = hub_service.derive_salaries()
    assert salaries["top_earner"]["name"] == "Big Earner"
    assert salaries["highest_weekly"] == 325_000


def test_the_hub_names_its_source(monkeypatch):
    _patch_salaries(monkeypatch, _WAGES_PAGE)
    salaries = hub_service.derive_salaries()
    assert salaries["source"] == "SalaryLeaks"
    assert salaries["source_updated"] == "2026-09-16"


def test_an_empty_source_leaves_the_hub_with_nothing(monkeypatch):
    _patch_salaries(monkeypatch, "<html></html>")
    salaries = hub_service.derive_salaries()
    assert salaries["players"] == []
    assert salaries["total"] == 0
    assert salaries["top_earner"] is None


def test_the_hub_payload_serves_salaries(client, monkeypatch):
    _patch_salaries(monkeypatch, _WAGES_PAGE)
    body = client.get("/api/hub").json()
    assert body["salaries"]["total"] == 2


def test_the_salaries_section_is_served_on_its_own(client, monkeypatch):
    _patch_salaries(monkeypatch, _WAGES_PAGE)
    body = client.get("/api/hub/salaries").json()
    assert body["total"] == 2
