"""Tests for the Transfermarkt assists and injuries reads.

Every test feeds synthetic markup or replaces the HTTP call, so the suite never
talks to Transfermarkt. The tables are invented players, not real ones.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services import hub_service, transfermarkt, wikipedia

# --- Markup ---------------------------------------------------------------- #

_PLAYER_CELL = """\
<td class="posrela"><table class="inline-table">
<tr>
<td rowspan="2"><img src="data:image/gif;base64,R0lGOD" data-src="{portrait}"
 title="{name}" alt="{name}" /></td>
<td class="hauptlink"><a title="{name}" href="/{slug}/profil/spieler/1">{name}</a></td>
</tr>
<tr><td>{position}</td></tr>
</table></td>
"""


def _player_cell(name: str, position: str, *, portrait: str = "", slug: str = "player") -> str:
    return _PLAYER_CELL.format(
        name=name,
        position=position,
        portrait=portrait or f"https://img.example/portrait/medium/{slug}.jpg",
        slug=slug,
    )


_STATS_PAGE = f"""\
<html><body>
<table class="items"><thead><tr>
<th>#</th><th>Player</th><th>Age</th><th>Nat.</th><th>In squad</th>
<th><a href="/x"><span class="icons_sprite" title="Appearances">&nbsp;</span></a></th>
<th><a href="/x"><span class="icons_sprite" title="Goals">&nbsp;</span></a></th>
<th><a href="/x"><span class="icons_sprite" title="Assists">&nbsp;</span></a></th>
<th><a href="/x"><span class="icons_sprite" title="Yellow cards">&nbsp;</span></a></th>
</tr></thead><tbody>
<tr class="odd">
<td class="rueckennummer"><div class=rn_nummer>8</div></td>
{_player_cell("Bruno Fernandes", "Attacking Midfield")}
<td>31</td><td><img title="Portugal" src="/flagge/136.png" /></td><td>6</td>
<td>6</td><td>4</td><td>3</td><td>1</td>
</tr>
<tr class="even">
<td class="rueckennummer"><div class=rn_nummer>19</div></td>
{_player_cell("Some Winger", "Right Winger")}
<td>24</td><td><img title="England" src="/flagge/189.png" /></td><td>5</td>
<td>5</td><td>-</td><td>-</td><td>-</td>
</tr>
<tr class="odd">
<td class="rueckennummer"><div class=rn_nummer>22</div></td>
{_player_cell("Third Keeper", "Goalkeeper")}
<td>40</td><td><img title="England" src="/flagge/189.png" /></td><td>-</td>
<td colspan="8" class="zentriert">Not in squad during this season</td>
</tr>
</tbody></table>
</body></html>
"""

_INJURY_PAGE = f"""\
<html><body>
<div class="responsive-table"><table class="items"><thead><tr>
<th>Player</th><th>Age</th><th>Reason</th><th>since</th>
<th>Expected return</th><th>Missed matches</th><th>Days</th><th>Market Value</th>
</tr></thead><tbody>
<tr><td class="extrarow bg_blau_20 hauptlink" colspan="8">Injuries</td></tr>
<tr class="odd">
{_player_cell("Amad Diallo", "Right Winger")}
<td>24</td><td class="img-vat">Knock</td><td>20/08/2026</td><td></td>
<td><a title="Manchester United" href="/schedule">7</a></td><td>32</td><td>€45.00m</td>
</tr>
<tr><td class="extrarow bg_blau_20 hauptlink" colspan="8">Suspensions</td></tr>
<tr class="even">
{_player_cell("Booked Player", "Centre-Back")}
<td>27</td><td class="img-vat">Red card</td><td>01/09/2026</td><td>15/09/2026</td>
<td>2</td><td>14</td><td>€30.00m</td>
</tr>
</tbody></table></div>
</body></html>
"""


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    """Neither read may leak between tests, and both must be enabled.

    The suite runs with the source switched off (see ``conftest``) so no other
    test can reach the network by accident; here it is switched back on and the
    HTTP call is always replaced.
    """
    monkeypatch.setattr(settings, "transfermarkt_enabled", True)
    transfermarkt.reset_cache()
    yield
    transfermarkt.reset_cache()


# --- Squad statistics parser ----------------------------------------------- #


def test_squad_stats_read_appearances_goals_and_assists():
    players = transfermarkt.parse_squad_stats(_STATS_PAGE)
    bruno = next(player for player in players if player["name"] == "Bruno Fernandes")
    assert bruno["apps"] == 6
    assert bruno["goals"] == 4
    assert bruno["assists"] == 3
    assert bruno["position"] == "Attacking Midfield"


def test_squad_stats_read_the_position_out_of_the_player_cell():
    players = transfermarkt.parse_squad_stats(_STATS_PAGE)
    winger = next(player for player in players if player["name"] == "Some Winger")
    # The cell's text runs the name, its short form and the position together.
    assert winger["position"] == "Right Winger"


def test_squad_stats_treat_a_dash_as_a_zero():
    players = transfermarkt.parse_squad_stats(_STATS_PAGE)
    winger = next(player for player in players if player["name"] == "Some Winger")
    assert winger["goals"] == 0
    assert winger["assists"] == 0


def test_squad_stats_skip_a_row_whose_cells_do_not_line_up():
    """A player outside the squad has his stat cells merged into one wide cell."""
    players = transfermarkt.parse_squad_stats(_STATS_PAGE)
    assert "Third Keeper" not in [player["name"] for player in players]
    assert len(players) == 2


def test_squad_stats_name_a_signing_from_his_profile_link_not_the_club_he_left():
    """A new signing's cell also carries the crest of the club he arrived from."""
    page = f"""\
<table class="items"><thead><tr>
<th>#</th><th>Player</th><th>Age</th><th>Nat.</th><th>In squad</th>
<th><span title="Appearances">&nbsp;</span></th><th><span title="Goals">&nbsp;</span></th>
<th><span title="Assists">&nbsp;</span></th>
</tr></thead><tbody><tr class="even">
<td class="rueckennummer"><div class=rn_nummer>12</div></td>
<td class="posrela">
<span class="wechsel-kader-wappen"><a href="/leeds-united/startseite/verein/399"
 ><img title="Leeds United" src="/wappen/kaderquad/399.png" /></a></span>
<a title="Joined from Leeds United; date: 14/07/2026; fee: free transfer" href="/x"
 ><img src="/images/zugang.png" /></a>
{_player_cell("Karl Darlow", "Goalkeeper", slug="karl-darlow")}
</td>
<td>36</td><td><img title="England" src="/flagge/189.png" /></td><td>2</td>
<td>1</td><td>-</td><td>1</td>
</tr></tbody></table>
"""
    players = transfermarkt.parse_squad_stats(page)
    assert [player["name"] for player in players] == ["Karl Darlow"]
    assert players[0]["assists"] == 1
    assert players[0]["goals"] == 0


def test_squad_stats_take_the_portrait_not_a_club_crest():
    """The crest of a player's former club comes first in the markup."""
    page = (
        _STATS_PAGE.replace(
            _player_cell("Bruno Fernandes", "Attacking Midfield"),
            '<a href="/leeds/united"><img title="Leeds United" src="/wappen/399.png" /></a>'
            + _player_cell("Bruno Fernandes", "Attacking Midfield"),
        )
    )
    table = transfermarkt._ItemsTable()
    table.feed(page)
    cell = table.rows[1][1]
    assert transfermarkt._portrait(cell).startswith("https://img.example/portrait/")


def test_squad_stats_return_nothing_without_an_assists_column():
    """Assists only exist on the detailed view of the page."""
    page = _STATS_PAGE.replace('title="Assists"', 'title="Clearances"')
    assert transfermarkt.parse_squad_stats(page) == []


def test_squad_stats_return_nothing_without_the_table():
    assert transfermarkt.parse_squad_stats("<html><body>nope</body></html>") == []


# --- Injuries parser ------------------------------------------------------- #


def test_injuries_read_every_detail_column():
    injuries = transfermarkt.parse_injuries(_INJURY_PAGE)
    amad = injuries[0]
    assert amad["name"] == "Amad Diallo"
    assert amad["position"] == "Right Winger"
    assert amad["age"] == 24
    assert amad["reason"] == "Knock"
    assert amad["since"] == "20/08/2026"
    assert amad["missed_matches"] == 7
    assert amad["days_out"] == 32
    assert amad["market_value"] == "€45.00m"


def test_injuries_keep_the_portrait_and_the_profile_link():
    amad = transfermarkt.parse_injuries(_INJURY_PAGE)[0]
    assert "/portrait/" in amad["image"]
    assert amad["url"].endswith("/profil/spieler/1")


def test_injuries_leave_an_unknown_return_date_empty():
    """Transfermarkt leaves the cell blank when it does not know."""
    assert transfermarkt.parse_injuries(_INJURY_PAGE)[0]["expected_return"] is None


def test_injuries_label_a_row_with_the_group_it_sits_under():
    injuries = transfermarkt.parse_injuries(_INJURY_PAGE)
    assert [injury["status"] for injury in injuries] == ["Injury", "Suspension"]


def test_injuries_return_nothing_without_the_table():
    assert transfermarkt.parse_injuries("<html><body>nope</body></html>") == []


def test_a_page_with_another_table_first_still_parses():
    page = '<table class="foo"><tr><td>decoy</td></tr></table>' + _INJURY_PAGE
    assert len(transfermarkt.parse_injuries(page)) == 2


# --- Fetching and caching -------------------------------------------------- #

_STATS_PAGE_ONE = _STATS_PAGE.replace(">3</td>", ">9</td>")


def _serve(monkeypatch, pages: dict[str, str], calls: list[str] | None = None):
    """Replace the HTTP call with the page matching the requested URL."""

    def fake_get(url: str) -> str | None:
        if calls is not None:
            calls.append(url)
        for fragment, page in pages.items():
            if fragment in url:
                return page
        return None

    monkeypatch.setattr(transfermarkt, "_get", fake_get)


def test_squad_stats_are_fetched_once_and_then_cached(monkeypatch):
    calls: list[str] = []
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE}, calls)
    transfermarkt.squad_stats()
    transfermarkt.squad_stats()
    assert len(calls) == 1


def test_an_expired_cache_is_refetched(monkeypatch):
    calls: list[str] = []
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE}, calls)
    transfermarkt.squad_stats()
    transfermarkt._cache["stats_expires"] = 0.0
    transfermarkt.squad_stats()
    assert len(calls) == 2


def test_a_failed_read_keeps_the_last_good_list(monkeypatch):
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE})
    first = transfermarkt.squad_stats()
    transfermarkt._cache["stats_expires"] = 0.0
    _serve(monkeypatch, {})
    assert transfermarkt.squad_stats() == first


def test_a_failed_read_is_not_retried_on_the_next_call(monkeypatch):
    calls: list[str] = []
    _serve(monkeypatch, {}, calls)
    transfermarkt.squad_stats()
    transfermarkt.squad_stats()
    assert len(calls) == 1


def test_a_changed_page_is_picked_up_once_the_cache_expires(monkeypatch):
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE})
    transfermarkt.squad_stats()
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE_ONE})
    transfermarkt._cache["stats_expires"] = 0.0
    stats = {player["name"]: player["assists"] for player in transfermarkt.squad_stats()}
    assert stats["Bruno Fernandes"] == 9


def test_disabling_the_source_reads_nothing(monkeypatch):
    calls: list[str] = []
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE, "sperren": _INJURY_PAGE}, calls)
    monkeypatch.setattr(settings, "transfermarkt_enabled", False)
    assert transfermarkt.squad_stats() == []
    assert transfermarkt.injuries() == []
    assert calls == []


def test_injuries_report_when_they_were_last_read(monkeypatch):
    _serve(monkeypatch, {"sperren": _INJURY_PAGE})
    transfermarkt.injuries()
    assert transfermarkt.injuries_updated_at() is not None


def test_the_two_reads_use_different_urls(monkeypatch):
    calls: list[str] = []
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE, "sperren": _INJURY_PAGE}, calls)
    transfermarkt.squad_stats()
    transfermarkt.injuries()
    assert len(calls) == 2
    assert "plus/1" in calls[0] and "plus/1" in calls[1]


# --- Matching -------------------------------------------------------------- #


def test_assists_are_keyed_by_the_players_full_name(monkeypatch):
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE})
    assert transfermarkt.assists_by_player()["Bruno Fernandes"] == 3


def test_a_name_matches_exactly():
    assert transfermarkt.match_assists("Bruno Fernandes", {"Bruno Fernandes": 3}) == 3


def test_a_name_matches_with_its_accents_folded():
    assert transfermarkt.match_assists("Benjamin Šeško", {"Benjamin Sesko": 2}) == 2


def test_a_name_matches_on_a_unique_surname():
    assert transfermarkt.match_assists("Bruno Fernandes", {"B. Fernandes": 4}) == 4


def test_an_ambiguous_surname_matches_nothing():
    """Two players called Fernandes cannot be told apart, so neither is used."""
    assert transfermarkt.match_assists("Bruno Fernandes", {"A. Fernandes": 1, "B. Fernandes": 2}) is None


def test_an_unknown_player_matches_nothing():
    assert transfermarkt.match_assists("Nobody At All", {"Bruno Fernandes": 3}) is None


# --- Hub integration ------------------------------------------------------- #


def _member(name: str, *, goals: int = 0, apps: int = 0) -> wikipedia.SquadMember:
    return wikipedia.SquadMember(
        number=1, position="MF", name=name, apps=apps, goals=goals
    )


def test_assists_are_merged_onto_the_squad(monkeypatch):
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE})
    squad = hub_service.apply_assists([_member("Bruno Fernandes"), _member("Some Winger")])
    assert [member.assists for member in squad] == [3, 0]


def test_a_player_transfermarkt_does_not_list_keeps_zero(monkeypatch):
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE})
    squad = hub_service.apply_assists([_member("Academy Kid")])
    assert squad[0].assists == 0


def test_an_empty_source_leaves_the_squad_alone(monkeypatch):
    _serve(monkeypatch, {})
    squad = hub_service.apply_assists([_member("Bruno Fernandes")])
    assert squad[0].assists == 0


def test_assist_leaders_are_ranked_best_first_and_skip_nil(monkeypatch):
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE})
    squad = hub_service.apply_assists(
        [_member("Some Winger", apps=9), _member("Bruno Fernandes", apps=6)]
    )
    leaders = hub_service.derive_assists(squad)
    assert [row["name"] for row in leaders] == ["B. Fernandes"]


def test_the_overview_carries_the_assist_leaders(monkeypatch):
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE})
    squad = hub_service.apply_assists([_member("Bruno Fernandes", goals=4, apps=6)])
    overview = hub_service.derive_overview([], [], squad)
    assert overview["assist_leaders"] == [
        {"name": "B. Fernandes", "assists": 3, "apps": 6}
    ]


def test_top_scorers_carry_their_assists(monkeypatch):
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE})
    squad = hub_service.apply_assists([_member("Bruno Fernandes", goals=4, apps=6)])
    overview = hub_service.derive_overview([], [], squad)
    assert overview["top_scorers"][0]["assists"] == 3


def test_the_comparison_carries_an_assists_row(monkeypatch):
    _serve(monkeypatch, {"leistungsdaten": _STATS_PAGE})
    squad = hub_service.apply_assists(
        [_member("Bruno Fernandes", goals=4, apps=6), _member("Some Winger", goals=3, apps=5)]
    )
    labels = [row["label"] for row in hub_service.derive_compare(squad)["stats"]]
    assert "Assists" in labels


def test_injuries_summarise_the_time_lost(monkeypatch):
    _serve(monkeypatch, {"sperren": _INJURY_PAGE})
    payload = hub_service.derive_injuries()
    assert payload["total"] == 2
    assert payload["days_out"] == 46
    assert payload["matches_missed"] == 9
    assert payload["source"] == "Transfermarkt"


def test_no_injuries_is_a_valid_answer(monkeypatch):
    _serve(monkeypatch, {})
    payload = hub_service.derive_injuries()
    assert payload["total"] == 0
    assert payload["players"] == []


# --- Route ----------------------------------------------------------------- #


def test_hub_serves_the_injuries_section(client, monkeypatch):
    _serve(monkeypatch, {"sperren": _INJURY_PAGE})
    payload = client.get("/api/hub").json()
    assert payload["injuries"]["total"] == 2


def test_hub_serves_the_injuries_section_on_its_own(client, monkeypatch):
    _serve(monkeypatch, {"sperren": _INJURY_PAGE})
    body = client.get("/api/hub/injuries").json()
    assert [injury["name"] for injury in body["players"]] == ["Amad Diallo", "Booked Player"]
