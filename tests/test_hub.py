"""Tests for the RSS/Wikipedia-derived hub.

All fixtures are paraphrased or synthetic so no publisher prose is reproduced.
Wikipedia is disabled via the environment (see ``conftest``), so the squad
assertions exercise the stored-seed fallback rather than the network.
"""
from __future__ import annotations

import pytest

from app.services import hub_service, wikipedia

# --- Wikipedia parsers ------------------------------------------------------ #

_SEASON_WIKITEXT = """\
== Squad statistics ==
{| class="wikitable"
|-
! No. !! Pos. !! Name !! League !! FA Cup !! League Cup !! Total !! Cards
|-
|align="left"|1||align="left"|GK||align="left"|{{flagicon|TUR}} [[Altay Bayindir]]
|6||0||0||0||0||0||6||0||0||0
|-
|align="left"|8||align="left"|MF||align="left"|[[Bruno Fernandes|B. Fernandes]]
|29(5)||9||1||0||1||0||31(5)||9||5||0
|-
|align="left"|10||align="left"|FW||align="left"|[[Marcus Rashford]]
|18(4)||7||2||1||0||0||20(4)||8||3||1
|}
== Awards ==
Nothing to see here.
"""

_CLUB_WIKITEXT = """\
{{fs start}}
{{fs player|no=1|nat=ENG|pos=GK|name=[[Senne Lammens]]}}
{{fs player|no=8|nat=POR|pos=MF|name=[[Bruno Fernandes]]}}
{{fs player|no=11|nat=NED|pos=FW|name=[[Joshua Zirkzee]]}}
{{fs end}}
"""


def test_parse_squad_statistics_sums_substitute_appearances():
    members = wikipedia.parse_squad_statistics(_SEASON_WIKITEXT)
    assert [m.name for m in members] == ["Altay Bayindir", "B. Fernandes", "Marcus Rashford"]
    bruno = members[1]
    assert (bruno.number, bruno.position) == (8, "MF")
    assert bruno.apps == 36  # 31 + 5 substitute cameos
    assert bruno.goals == 9
    assert bruno.league_apps == 34  # 29 + 5
    assert bruno.yellow == 5


def test_parse_squad_statistics_returns_empty_without_table():
    assert wikipedia.parse_squad_statistics("== Something else ==\nno table here") == []


# A header with a European block, as used by the 2026-27 season article: six
# colspan groups cover the twelve numeric cells in each data row.
_SEASON_WIKITEXT_WITH_EUROPE = """\
== Squad statistics ==
{| class="wikitable"
|-
! rowspan="2" | No.
! rowspan="2" | Pos.
! rowspan="2" | Name
! colspan="2" | League
! colspan="2" | FA Cup
! colspan="2" | EFL Cup
! colspan="2" | Champions League
! colspan="2" | Total
! colspan="2" | Discipline
|-
! Apps !! Goals !! Apps !! Goals !! Apps !! Goals !! Apps !! Goals !! Apps !! Goals !! Yellow !! Red
|-
|align="left"|8||align="left"|MF||align="left"|[[Bruno Fernandes|B. Fernandes]]
|4||3||0||0||0||0||1||1||5||4||0||0
|-
|align="left"|30||align="left"|FW||align="left"|{{flagicon|SVN}} [[Benjamin Šeško]]
|4||1||1||1||0||0||0||0||5||2||0||0
|}
== Awards ==
Nothing to see here.
"""


def test_parse_squad_statistics_aligns_columns_when_a_european_block_shifts_them():
    members = wikipedia.parse_squad_statistics(_SEASON_WIKITEXT_WITH_EUROPE)
    bruno, sesko = members
    assert bruno.name == "B. Fernandes"
    assert (bruno.apps, bruno.goals) == (5, 4)
    assert (bruno.league_apps, bruno.league_goals) == (4, 3)
    assert bruno.cup_goals == 1  # the Champions League goal, not the league one
    # The extra block shifts every column by four; a positional read would have
    # picked up the FA Cup numbers instead.
    assert (sesko.apps, sesko.goals) == (5, 2)
    assert (sesko.league_apps, sesko.league_goals) == (4, 1)
    assert sesko.cup_goals == 1


# --- League table (the ``Sports table`` module) ----------------------------- #

_EXPLICIT_STANDINGS = """\
{{#invoke:Sports table|main|style=WDL
|team1=MCI|team2=ARS|team3=MUN
|name_MCI=[[Manchester City F.C.|Manchester City]]
|name_ARS=[[Arsenal F.C.|Arsenal]]
|name_MUN=[[Manchester United F.C.|Manchester United]]
|win_MCI=4|draw_MCI=1|loss_MCI=0|gf_MCI=10|ga_MCI=4
|win_ARS=4|draw_ARS=0|loss_ARS=1|gf_ARS=8|ga_ARS=5
|win_MUN=2|draw_MUN=0|loss_MUN=2|gf_MUN=7|ga_MUN=5
|matches_style=FBR|auto_generate_standings=no
}}"""

_AUTO_STANDINGS = """\
{{#invoke:Sports table|main|style=WDL
|team1=MCI|team2=ARS|team3=LIV
|name_MCI=[[Manchester City F.C.|Manchester City]]
|name_ARS=[[Arsenal F.C.|Arsenal]]
|name_LIV=[[Liverpool F.C.|Liverpool]]
|match_MCI_ARS=3\u20131
|match_ARS_MCI=1\u20131
|match_MCI_LIV=[[Manchester City F.C.\u2013Liverpool F.C. rivalry|2\u20132]]
|match_ARS_LIV=0-1
|matches_style=FBR|auto_generate_standings=yes
}}"""


def test_parse_standings_reads_explicit_tallies_in_league_order():
    rows = wikipedia.parse_standings(_EXPLICIT_STANDINGS)
    assert [row.team for row in rows] == ["Manchester City", "Arsenal", "Manchester United"]
    city = rows[0]
    assert (city.played, city.won, city.drawn, city.lost) == (5, 4, 1, 0)
    assert (city.goals_for, city.goals_against, city.points) == (10, 4, 13)
    united = rows[2]
    assert (united.played, united.points, united.goal_difference) == (4, 6, 2)


def test_parse_standings_aggregates_an_auto_generated_results_grid():
    rows = wikipedia.parse_standings(_AUTO_STANDINGS)
    assert [row.team for row in rows] == ["Manchester City", "Liverpool", "Arsenal"]
    # City only appear in three of the six cells, so they have played three games
    # while Liverpool have played two. Every cell counts as its own fixture.
    city = rows[0]
    assert (city.played, city.won, city.drawn, city.lost) == (3, 1, 2, 0)
    assert (city.goals_for, city.goals_against, city.points) == (6, 4, 5)
    liverpool = rows[1]
    assert (liverpool.played, liverpool.won, liverpool.drawn, liverpool.lost) == (2, 1, 1, 0)
    assert (liverpool.goals_for, liverpool.goals_against, liverpool.points) == (3, 2, 4)
    arsenal = rows[2]
    assert (arsenal.played, arsenal.won, arsenal.drawn, arsenal.lost) == (3, 0, 1, 2)
    assert (arsenal.goals_for, arsenal.goals_against, arsenal.points) == (2, 5, 1)


def test_parse_standings_drops_rows_without_any_readable_scoreline():
    text = """{{#invoke:Sports table|main|style=WDL
|team1=MCI|team2=ARS
|name_MCI=[[Manchester City F.C.|Manchester City]]
|name_ARS=[[Arsenal F.C.|Arsenal]]
|match_MCI_ARS=<ref name="pl">{{cite web|title=2026\u201327 Premier League}}</ref>
|match_ARS_MCI=<ref name="pl" />
}}"""
    # Neither scoreline survives reference-stripping, so both clubs have played
    # nothing and the table is empty rather than full of zero-game rows.
    assert wikipedia.parse_standings(text) == []


def test_parse_standings_needs_at_least_two_named_teams():
    assert wikipedia.parse_standings("|name_MUN=[[Manchester United F.C.|Manchester United]]") == []


def test_parse_standings_ignores_result_grid_metadata():
    rows = wikipedia.parse_standings(
        _AUTO_STANDINGS.replace(
            "|matches_style=FBR",
            "|matches_style=FBR|matches_source=[https://example.com/pl Premier League results]",
        )
    )
    assert len(rows) == 3


def test_derive_table_canonicalises_names_and_flags_united():
    rows = [
        wikipedia.StandingRow("Manchester United F.C.", 4, 2, 0, 2, 7, 5),
        wikipedia.StandingRow("Hull City", 5, 3, 2, 0, 8, 4),
    ]
    table = hub_service.derive_table(rows)
    assert [row["team"] for row in table] == ["Manchester United", "Hull"]
    assert [row["pos"] for row in table] == [1, 2]
    assert [row["isUnited"] for row in table] == [True, False]


def test_parse_roster_reads_fs_player_list():
    roster = wikipedia.parse_roster(_CLUB_WIKITEXT)
    assert [(m.number, m.position, m.name) for m in roster] == [
        (1, "GK", "Senne Lammens"),
        (8, "MF", "Bruno Fernandes"),
        (11, "FW", "Joshua Zirkzee"),
    ]


# --- Result / fixture parsers ---------------------------------------------- #


def test_parse_result_from_headline_scoreline():
    item = {
        "title": "Manchester United 3-0 Fulham: Premier League - as it happened",
        "summary": "A comfortable afternoon at Old Trafford.",
        "tags": ["Premier League"],
    }
    parsed = hub_service.parse_result(item)
    assert parsed["home"] == "Manchester United"
    assert parsed["away"] == "Fulham"
    assert (parsed["homeScore"], parsed["awayScore"]) == (3, 0)
    assert parsed["competition"] == "Premier League"


def test_parse_result_orients_away_win_from_report_verb():
    item = {
        "title": "Stoke grind out a result on the road",
        "summary": "Stoke secured a 2-1 away victory at Watford.",
        "source_url": "https://example.com/football/watford-vs-stoke-city/report/1",
    }
    parsed = hub_service.parse_result(item)
    assert (parsed["home"], parsed["away"]) == ("Watford", "Stoke")
    assert (parsed["homeScore"], parsed["awayScore"]) == (1, 2)


def test_parse_result_orients_home_win_from_loss_verb():
    item = {
        "title": "Swansea pile on the misery",
        "summary": "Burnley suffered a 3-1 defeat at Swansea.",
        "source_url": "https://example.com/football/swansea-city-vs-burnley/report/2",
    }
    parsed = hub_service.parse_result(item)
    assert (parsed["home"], parsed["away"]) == ("Swansea", "Burnley")
    assert (parsed["homeScore"], parsed["awayScore"]) == (3, 1)


def test_parse_result_handles_draws():
    item = {
        "title": "Honours even",
        "summary": "Charlton held Portsmouth to a 0-0 draw.",
        "source_url": "https://example.com/football/charlton-athletic-vs-portsmouth/report/3",
    }
    parsed = hub_service.parse_result(item)
    assert (parsed["homeScore"], parsed["awayScore"]) == (0, 0)


def test_parse_result_skips_youth_and_non_matches():
    assert hub_service.parse_result({"title": "Man City vs Man Utd U21s called off"}) is None
    assert hub_service.parse_result({"title": "Transfer news round-up"}) is None


def test_parse_fixture_reads_preview_headline():
    parsed = hub_service.parse_fixture(
        {"title": "Manchester United vs Man City: Premier League - predictions"}
    )
    assert (parsed["home"], parsed["away"]) == ("Manchester United", "Manchester City")
    assert parsed["competition"] == "Premier League"


def test_derive_standings_aggregates_premier_league_only():
    results = [
        {"competition": "Premier League", "home": "Chelsea", "away": "Hull", "homeScore": 2, "awayScore": 2},
        {"competition": "Premier League", "home": "Aston Villa", "away": "Nottingham Forest", "homeScore": 1, "awayScore": 2},
        {"competition": "Championship", "home": "Swansea", "away": "Burnley", "homeScore": 3, "awayScore": 1},
    ]
    table = {row["team"]: row for row in hub_service.derive_standings(results)}
    assert "Swansea" not in table
    assert table["Nottingham Forest"]["pts"] == 3
    assert table["Chelsea"]["pts"] == 1 and table["Chelsea"]["drawn"] == 1
    assert table["Aston Villa"]["lost"] == 1


# --- Endpoint integration --------------------------------------------------- #


def _insert_report(database, title, summary, url, competition=None):
    database["news"].insert_one(
        {
            "status": "Published",
            "title": title,
            "summary": summary,
            "source_url": url,
            "tags": [competition] if competition else [],
            "source": "Test Wire",
        }
    )


def test_hub_derives_results_from_news(client, database):
    for index in range(4):
        _insert_report(
            database,
            "Manchester United dominant at home",
            "Manchester United beat Everton 2-0 at Old Trafford.",
            f"https://example.com/football/manchester-united-vs-everton/report/{index}",
            "Premier League",
        )
    body = client.get("/api/hub").json()
    assert body["results"]
    united = body["results"][0]
    assert united["home"] == "Manchester United"
    assert (united["homeScore"], united["awayScore"]) == (2, 0)
    assert body["standings"]


def test_hub_admin_override_beats_derivation(client, database):
    _insert_report(
        database,
        "Manchester United win again",
        "Manchester United beat Everton 2-0 at Old Trafford.",
        "https://example.com/football/manchester-united-vs-everton/report/x",
        "Premier League",
    )
    from .conftest import ADMIN_HEADERS

    client.put(
        "/api/admin/hub/results",
        headers=ADMIN_HEADERS,
        json={"data": [{"id": 1, "competition": "Hand-picked", "home": "A", "away": "B"}]},
    )
    assert client.get("/api/hub/results").json()[0]["competition"] == "Hand-picked"


def test_hub_falls_back_to_seed_without_news(client):
    # No scoreline-bearing news and Wikipedia disabled -> seeded sections serve.
    body = client.get("/api/hub").json()
    assert body["standings"]
    assert body["squad"]
    assert body["overview"]["top_scorers"]


# --- United-only panels and the rotating hero ------------------------------- #


def _insert_article(database, *, title, summary, url, image=None, hours_old=1, source="Test Wire"):
    from datetime import datetime, timedelta, timezone

    database["news"].insert_one(
        {
            "id": f"news-{abs(hash(url)) % 100000}",
            "slug": f"slug-{abs(hash(url)) % 100000}",
            "status": "Published",
            "title": title,
            "summary": summary,
            "source_url": url,
            "source": source,
            "image": image,
            "category": "News",
            "tags": [],
            "views": 5,
            "published_at": datetime.now(timezone.utc) - timedelta(hours=hours_old),
        }
    )


def test_hub_results_section_is_united_only(client, database):
    _insert_article(
        database,
        title="Manchester United beat Everton at Old Trafford",
        summary="Manchester United beat Everton 2-0 at Old Trafford.",
        url="https://example.com/football/manchester-united-vs-everton/report/u1",
    )
    _insert_article(
        database,
        title="Chelsea edge Arsenal in London derby",
        summary="Chelsea beat Arsenal 2-1 at Stamford Bridge.",
        url="https://example.com/football/chelsea-vs-arsenal/report/u2",
    )
    body = client.get("/api/hub").json()
    assert body["results"]
    assert all(
        "Manchester United" in (row.get("home"), row.get("away")) for row in body["results"]
    )
    # The league table still uses every parsed result, not just United's.
    assert len(body["standings"]) > 1


def test_hub_hero_is_a_rotating_pool_of_united_images(client, database):
    for index in range(3):
        _insert_article(
            database,
            title=f"Manchester United training gallery {index}",
            summary="Manchester United prepare for the weekend.",
            url=f"https://example.com/manchester-united/gallery/{index}",
            image=f"https://cdn.example.com/hero-{index}.jpg",
            hours_old=index,
        )
    hero = client.get("/api/hub/hero").json()
    assert hero["slides"]
    assert hero["interval_seconds"] > 0
    assert all(slide["image"].startswith("https://") for slide in hero["slides"])
    # Distinct images only - the pool is for crossfading, not repetition.
    assert len({slide["image"] for slide in hero["slides"]}) == len(hero["slides"])


def test_hub_hero_skips_insecure_and_missing_images(client, database):
    _insert_article(
        database,
        title="Manchester United announce squad numbers",
        summary="Manchester United confirm the list.",
        url="https://example.com/manchester-united/squad-numbers",
        image="http://insecure.example.com/a.jpg",
    )
    _insert_article(
        database,
        title="Manchester United press conference recap",
        summary="Manchester United speak ahead of the trip.",
        url="https://example.com/manchester-united/presser",
        image=None,
    )
    hero = client.get("/api/hub/hero").json()
    assert hero.get("slides", []) == []


# --- Rendered (HTML) standings --------------------------------------------- #

_RENDERED_TABLE = """\
<table class="wikitable">
<tr>
<th>Pos</th><th>Team</th><th>Pld</th><th>W</th><th>D</th><th>L</th><th>GF</th><th>GA</th><th>GD</th><th>Pts</th><th>Qualification or relegation</th>
</tr>
<tr>
<td>1</td>
<td><a href="/wiki/Arsenal_F.C.">Arsenal</a><sup class="reference">[a]</sup></td>
<td>4</td><td>3</td><td>0</td><td>1</td><td>8</td><td>2</td><td>+6</td><td>9</td><td>Champions League</td>
</tr>
<tr>
<td>2</td>
<td><a href="/wiki/Manchester_United_F.C.">Manchester United</a></td>
<td>4</td><td>4</td><td>0</td><td>0</td><td>9</td><td>1</td><td>+8</td><td>12</td><td></td>
</tr>
<tr>
<td>3</td>
<td><a href="/wiki/Everton_F.C.">Everton</a></td>
<td>4</td><td>0</td><td>1</td><td>3</td><td>2</td><td>9</td><td>\u22127</td><td>1</td><td>Relegation</td>
</tr>
</table>
"""


@pytest.fixture()
def rendered_wikipedia(monkeypatch):
    """Wikipedia on, with the network replaced by a caller-supplied renderer."""
    wikipedia.reset_cache()
    monkeypatch.setattr(wikipedia.settings, "hub_wikipedia_enabled", True)
    yield
    wikipedia.reset_cache()


def test_parse_standings_html_keeps_the_order_the_page_shows():
    rows = wikipedia.parse_standings_html(_RENDERED_TABLE)
    # United out-point Arsenal, but the page's order already encodes the
    # tie-breakers (and any deductions), so it must survive untouched.
    assert [row.team for row in rows] == ["Arsenal", "Manchester United", "Everton"]
    assert [row.points for row in rows] == [9, 12, 1]


def test_parse_standings_html_reads_totals_and_strips_footnote_markers():
    rows = wikipedia.parse_standings_html(_RENDERED_TABLE)
    arsenal, united, everton = rows
    assert arsenal.team == "Arsenal"
    assert (arsenal.played, arsenal.won, arsenal.drawn, arsenal.lost) == (4, 3, 0, 1)
    assert (arsenal.goals_for, arsenal.goals_against, arsenal.goal_difference) == (8, 2, 6)
    assert (united.played, united.won, united.points) == (4, 4, 12)
    # A leading Unicode minus must not become part of the number.
    assert everton.goal_difference == -7
    assert (everton.won, everton.drawn, everton.lost) == (0, 1, 3)


def test_parse_standings_html_honours_a_points_deduction():
    deducted = _RENDERED_TABLE.replace(
        "<td>4</td><td>0</td><td>1</td><td>3</td><td>2</td><td>9</td><td>\u22127</td><td>1</td>",
        "<td>4</td><td>1</td><td>1</td><td>2</td><td>4</td><td>9</td><td>\u22125</td><td>1</td>",
    )
    everton = wikipedia.parse_standings_html(deducted)[2]
    assert (everton.won, everton.drawn) == (1, 1)
    assert everton.points_adjustment == -3
    # The league's own total wins over a plain recomputation.
    assert everton.points == 1


def test_parse_standings_html_ignores_tables_with_an_incomplete_header():
    partial = """\
<table>
<tr><th>Pos</th><th>Team</th><th>Pld</th><th>W</th></tr>
<tr><td>1</td><td>Arsenal</td><td>4</td><td>3</td></tr>
<tr><td>2</td><td>Everton</td><td>4</td><td>0</td></tr>
</table>
"""
    assert wikipedia.parse_standings_html(partial) == []


def test_parse_standings_html_returns_empty_without_a_table():
    assert wikipedia.parse_standings_html("<p>No standings here.</p>") == []
    assert wikipedia.parse_standings_html("") == []


def test_numeric_cell_reads_signed_and_missing_values():
    assert wikipedia._numeric_cell("12") == 12
    assert wikipedia._numeric_cell("+7") == 7
    assert wikipedia._numeric_cell("\u22123") == -3
    assert wikipedia._numeric_cell("") == 0
    assert wikipedia._numeric_cell("n/a") == 0


def test_fetch_standings_prefers_the_rendered_table(rendered_wikipedia, monkeypatch):
    monkeypatch.setattr(wikipedia, "_fetch_rendered", lambda page: _RENDERED_TABLE)
    monkeypatch.setattr(
        wikipedia,
        "_fetch_wikitext",
        lambda page: pytest.fail("wikitext must not be consulted when the render works"),
    )
    rows = wikipedia.fetch_standings(force=True)
    assert [row.team for row in rows] == ["Arsenal", "Manchester United", "Everton"]
    assert wikipedia.standings_meta()["source"] == "Wikipedia (rendered table)"


def test_fetch_standings_falls_back_to_wikitext_when_rendering_fails(
    rendered_wikipedia, monkeypatch
):
    monkeypatch.setattr(wikipedia, "_fetch_rendered", lambda page: None)
    monkeypatch.setattr(wikipedia, "_fetch_wikitext", lambda page: _AUTO_STANDINGS)
    rows = wikipedia.fetch_standings(force=True)
    assert [row.team for row in rows] == ["Manchester City", "Liverpool", "Arsenal"]
    assert wikipedia.standings_meta()["source"] == "Wikipedia (season results)"


def test_fetch_standings_does_not_cache_a_failed_read(rendered_wikipedia, monkeypatch):
    monkeypatch.setattr(wikipedia, "_fetch_rendered", lambda page: None)
    monkeypatch.setattr(wikipedia, "_fetch_wikitext", lambda page: None)
    assert wikipedia.fetch_standings(force=True) == []
    assert wikipedia.standings_meta() == {}


def test_standings_meta_is_empty_until_a_table_is_read():
    wikipedia.reset_cache()
    assert wikipedia.standings_meta() == {}


def test_hub_payload_carries_the_standings_provenance(client):
    payload = client.get("/api/hub").json()
    # The combined response carries the refresh metadata alongside the panels.
    assert set(payload) >= {
        "hero",
        "overview",
        "fixtures",
        "results",
        "standings",
        "compare",
        "squad",
        "standings_meta",
    }
    # Wikipedia is off in tests, so the table comes from the seeded snapshot.
    assert payload["standings_meta"]["source"] in {"SimplyUtd snapshot", "Aggregated from the news feed"}
