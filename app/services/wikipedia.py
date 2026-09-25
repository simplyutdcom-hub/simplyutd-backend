"""Real Manchester United squad, season stats, schedule and table from Wikipedia.

The hub's "United players", "Player comparison", "Premier League table" and
fixtures/results panels used to show fabricated or headline-derived numbers.
This module replaces them with:

* the club's actual season schedule (dates, opponents, venues, scorelines,
  scorers, attendances) from the ``wikitable`` match tables on the current
  season article,
* the club's actual squad list (numbers, positions, names) and per-player
  season record (appearances, goals, cards) from the ``Squad statistics``
  table on the current season article, and
* the real Premier League table. Wikipedia serves two forms of the same
  ``Sports table`` module: the *rendered* HTML, where the standings are
  already computed, and the raw wikitext, where they are either spelled out
  with ``win_``/``draw_``/``loss_`` tallies or auto-generated from a
  ``match_`` results grid. We prefer the rendered HTML because the grid is
  filled in asymmetrically (and derby cells hold ``[[rivalry|…]]``
  placeholders), so aggregating it double-counts some fixtures and drops
  others.

Wikipedia's content is CC BY-SA. We only surface factual data (numbers,
positions, names, appearance/goal/points counts) - no prose is republished -
and the squad is cached so we hit the API at most once per
``hub_cache_seconds``. The table and the schedule are cached more briefly
(``hub_standings_cache_seconds``) and the table is refreshed in the background.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger("simplyutd.wikipedia")

_API = "https://en.wikipedia.org/w/api.php"

_POSITIONS = {"GK", "DF", "MF", "FW"}

# ``{{fs player|no=10|nat=ENG|pos=MF|name=[[Marcus Rashford]]}}``
_FS_PLAYER_RE = re.compile(r"\{\{\s*fs player\b(.*?)\}\}", re.IGNORECASE | re.DOTALL)
_FS_ARG_RE = re.compile(r"\|\s*(no|nat|pos|name)\s*=\s*([^|}]*)", re.IGNORECASE)

# A row of the season article's "Squad statistics" table:
#   |align="left"|8||align="left"|MF||align="left"|[[Bruno Fernandes]]
#   ||35||9||...
_SQUAD_ROW_RE = re.compile(
    r"\|\s*(\d{1,3})\s*(?:\|\||\|)\s*"
    r"(?:align=\"left\"\|\s*)?(GK|DF|MF|FW)\s*(?:\|\||\|)\s*"
    r"(?:align=\"left\"\|\s*)?(.+?)\n"
    r"\|([0-9()|\s]+)\n",
    re.IGNORECASE,
)

# A competition group in the "Squad statistics" table header, e.g.
#   !colspan="2" width="85"|League
# The number of competitions varies by season (a Champions League block appears
# once the club qualifies), so the columns are located from the header rather
# than assumed.
_SQUAD_GROUP_RE = re.compile(r"!\s*colspan\s*=\s*\"?(\d+)\"?[^|\n]*\|\s*([^\n!]+)")

_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_FLAG_RE = re.compile(r"\{\{\s*(?:flagicon|flag)[^}]*\}\}", re.IGNORECASE)
_REF_RE = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# ``{{#invoke:Sports table|main|style=WDL`` parameters.
#   |name_ARS=[[Arsenal F.C.|Arsenal]]
#   |win_ARS=12           (season articles that spell the table out)
#   |match_ARS_AVL=2–1    (articles that auto-generate it from the results grid)
_TABLE_NAME_RE = re.compile(r"\|\s*name_([A-Za-z0-9]{2,4})\s*=\s*([^\n]*)")
_TABLE_TALLY_RE = re.compile(
    r"\|\s*(win|draw|loss|gf|ga)_([A-Za-z0-9]{2,4})\s*=\s*([^\n|]*)"
)
_TABLE_MATCH_RE = re.compile(r"\|\s*match_([A-Za-z0-9]{2,4})_([A-Za-z0-9]{2,4})\s*=\s*([^\n]*)")
# A scoreline: an en dash, em dash or hyphen between two smallish numbers. The
# bound rejects seasons/years such as ``2026–27`` that appear inside citations.
_SCORE_RE = re.compile(r"(\d{1,2})\s*[\u2013\u2014\u2212-]\s*(\d{1,2})")


@dataclass
class SquadMember:
    """One player as recorded in the club's season statistics."""

    number: int | None
    position: str
    name: str
    apps: int = 0
    goals: int = 0
    league_apps: int = 0
    league_goals: int = 0
    cup_goals: int = 0
    yellow: int = 0
    red: int = 0
    # The season article has no assists column, so this stays zero until the
    # hub merges Transfermarkt's numbers in (see hub_service.apply_assists).
    assists: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "position": self.position,
            "name": self.name,
            "apps": self.apps,
            "goals": self.goals,
            "assists": self.assists,
            "league_apps": self.league_apps,
            "league_goals": self.league_goals,
            "cup_goals": self.cup_goals,
            "yellow": self.yellow,
            "red": self.red,
        }


@dataclass
class _Cache:
    value: list[SquadMember] | None = None
    expires: float = 0.0


_cache = _Cache()


def _clean_name(raw: str) -> str:
    """Strip templates, links and markup down to the plain player name."""
    text = _COMMENT_RE.sub("", raw)
    text = _REF_RE.sub("", text)
    text = _FLAG_RE.sub("", text)
    text = _LINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = _TAG_RE.sub("", text)
    text = text.replace("'''", "").replace("''", "")
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,\u00a0|")


def _count(raw: str) -> int:
    """``29(5)`` (29 starts + 5 sub) or ``6``/``0`` -> a single integer."""
    if not raw:
        return 0
    total = 0
    found = False
    for part in re.findall(r"\d+", raw):
        total += int(part)
        found = True
    return total if found else 0


def _is_cup_competition(label: str) -> bool:
    """Is this header group a cup competition rather than league form?

    The competition blocks vary by season ("FA Cup", "League Cup", "Champions
    League", "Europa League"), so anything that is not the league, the running
    total, or the discipline block is treated as a cup.
    """
    if label in {"league", "total"}:
        return False
    return "card" not in label and "discipline" not in label


def _squad_layout(section: str, columns: int) -> list[tuple[str, tuple[int, ...]]]:
    """Locate each header group's numeric cells in the ``Squad statistics`` table.

    ``columns`` is the number of numeric cells in a data row. Leading groups
    (``No.``/``Pos.``/``Name``) are dropped until the remaining widths account
    for exactly those cells, so the same code handles a table with or without a
    European-competition block. Returns an empty list when the header cannot be
    understood, so the caller can fall back to positional defaults.
    """
    first_row = _SQUAD_ROW_RE.search(section)
    header = section[: first_row.start()] if first_row else section
    groups = [(int(w), label.strip()) for w, label in _SQUAD_GROUP_RE.findall(header)]
    while groups and sum(w for w, _ in groups) > columns:
        groups.pop(0)
    if not groups or sum(w for w, _ in groups) != columns:
        return []

    layout: list[tuple[str, tuple[int, ...]]] = []
    start = 0
    for width, label in groups:
        layout.append((label.lower(), tuple(range(start, start + width))))
        start += width
    return layout


def _layout_cell(
    cells: list[str], layout: list[tuple[str, tuple[int, ...]]], label: str, offset: int
) -> int | None:
    """Read ``offset`` columns into the group called ``label`` (``None`` if absent)."""
    for name, indexes in layout:
        if name == label:
            index = offset if offset < len(indexes) else None
            return _count(cells[indexes[index]]) if index is not None else 0
    return None


def parse_squad_statistics(wikitext: str) -> list[SquadMember]:
    """Parse the season article's ``Squad statistics`` table into players.

    Returns an empty list when the table cannot be found so callers can fall
    back to the roster-only source.
    """
    heading = re.search(r"^=+\s*Squad statistics\s*=+\s*$", wikitext, re.MULTILINE)
    if not heading:
        return []
    tail = wikitext[heading.end() :]
    next_heading = re.search(r"^=+[^=\n]+=+\s*$", tail, re.MULTILINE)
    section = tail[: next_heading.start()] if next_heading else tail

    rows = list(_SQUAD_ROW_RE.finditer(section))
    if not rows:
        return []
    layout = _squad_layout(section, len([c for c in rows[0].group(4).split("||")]))

    members: list[SquadMember] = []
    for match in rows:
        number, position, name_raw, numbers_raw = match.groups()
        cells = [c.strip() for c in numbers_raw.split("||")]
        name = _clean_name(name_raw)
        if not name:
            continue

        if layout:
            league_apps = _layout_cell(cells, layout, "league", 0) or 0
            league_goals = _layout_cell(cells, layout, "league", 1) or 0
            cup_goals = sum(
                _layout_cell(cells, layout, label, 1) or 0
                for label, _ in layout
                if _is_cup_competition(label)
            )
            apps = _layout_cell(cells, layout, "total", 0)
            goals = _layout_cell(cells, layout, "total", 1)
            apps = league_apps if apps is None else apps
            goals = league_goals if goals is None else goals
            yellow = _layout_cell(cells, layout, "discipline", 0) or 0
            red = _layout_cell(cells, layout, "discipline", 1) or 0
        else:
            league_apps = _count(cells[0]) if len(cells) > 0 else 0
            league_goals = _count(cells[1]) if len(cells) > 1 else 0
            cup_goals = (_count(cells[3]) if len(cells) > 3 else 0) + (
                _count(cells[5]) if len(cells) > 5 else 0
            )
            apps = _count(cells[6]) if len(cells) > 6 else league_apps
            goals = _count(cells[7]) if len(cells) > 7 else league_goals
            yellow = _count(cells[8]) if len(cells) > 8 else 0
            red = _count(cells[9]) if len(cells) > 9 else 0

        members.append(
            SquadMember(
                number=int(number),
                position=position.upper(),
                name=name,
                apps=apps,
                goals=goals,
                league_apps=league_apps,
                league_goals=league_goals,
                cup_goals=cup_goals,
                yellow=yellow,
                red=red,
            )
        )
    return _prune(members)


def parse_roster(wikitext: str) -> list[SquadMember]:
    """Fallback: parse the club article's ``{{fs player}}`` squad list."""
    members: list[SquadMember] = []
    for match in _FS_PLAYER_RE.finditer(wikitext):
        args = {k.lower(): v for k, v in _FS_ARG_RE.findall(match.group(1))}
        position = (args.get("pos") or "").strip().upper()
        name = _clean_name(args.get("name", ""))
        if not name or position not in _POSITIONS:
            continue
        number_raw = re.sub(r"\D", "", args.get("no", ""))
        members.append(
            SquadMember(
                number=int(number_raw) if number_raw else None,
                position=position,
                name=name,
            )
        )
    seen: set[str] = set()
    unique: list[SquadMember] = []
    for member in members:
        if member.name in seen:
            continue
        seen.add(member.name)
        unique.append(member)
    return unique


def _prune(members: list[SquadMember]) -> list[SquadMember]:
    """Drop academy/reserve rows (no appearances and no shirt number)."""
    kept = [m for m in members if m.apps > 0 or m.number is not None]
    return kept or members


@dataclass
class StandingRow:
    """One club's line in the Premier League table."""

    team: str
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    # Points the league has added or removed (deductions show as a negative).
    points_adjustment: int = 0

    @property
    def points(self) -> int:
        return self.won * 3 + self.drawn + self.points_adjustment

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against

    def as_dict(self) -> dict[str, Any]:
        return {
            "team": self.team,
            "played": self.played,
            "won": self.won,
            "drawn": self.drawn,
            "lost": self.lost,
            "gf": self.goals_for,
            "ga": self.goals_against,
            "pts": self.points,
        }


def _team_display(raw: str) -> str:
    """``[[Arsenal F.C.|Arsenal]]`` / ``Arsenal`` -> ``Arsenal``."""
    link = _LINK_RE.search(raw)
    if link:
        return (link.group(2) or link.group(1)).strip()
    return _clean_name(raw)


def _score(raw: str) -> tuple[int, int] | None:
    """``2–1`` / ``[[Rivalry|2–1]]`` -> ``(2, 1)``; anything else -> ``None``."""
    text = _COMMENT_RE.sub("", raw)
    text = _REF_RE.sub("", text)
    link = _LINK_RE.search(text)
    if link and link.group(2):
        # The score is the piped display text; the target may hold a season year.
        text = link.group(2)
    match = _SCORE_RE.search(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _tally(raw: str) -> int:
    match = re.search(r"\d+", raw)
    return int(match.group()) if match else 0


def parse_standings(wikitext: str) -> list[StandingRow]:
    """Build the league table from the season article's ``Sports table`` module.

    Season articles either spell the table out with ``win_``/``draw_``/
    ``loss_``/``gf_``/``ga_`` parameters or set ``auto_generate_standings`` and
    carry a ``match_`` results grid instead. Both are supported; the grid is
    aggregated into the same tallies. Rows come back in league order.
    """
    names = {
        code: _team_display(raw) for code, raw in _TABLE_NAME_RE.findall(wikitext)
    }
    if len(names) < 2:
        return []

    tallies: dict[str, dict[str, int]] = {
        code: {"win": 0, "draw": 0, "loss": 0, "gf": 0, "ga": 0} for code in names
    }

    explicit = False
    for key, code, raw in _TABLE_TALLY_RE.findall(wikitext):
        if code in tallies:
            tallies[code][key] = _tally(raw)
            explicit = True

    if not explicit:
        for home, away, raw in _TABLE_MATCH_RE.findall(wikitext):
            if home not in tallies or away not in tallies or home == away:
                continue
            score = _score(raw)
            if score is None:
                continue
            home_goals, away_goals = score
            for code, scored, conceded in (
                (home, home_goals, away_goals),
                (away, away_goals, home_goals),
            ):
                stats = tallies[code]
                stats["gf"] += scored
                stats["ga"] += conceded
                if scored > conceded:
                    stats["win"] += 1
                elif scored == conceded:
                    stats["draw"] += 1
                else:
                    stats["loss"] += 1

    rows: list[StandingRow] = []
    for code, stats in tallies.items():
        played = stats["win"] + stats["draw"] + stats["loss"]
        if played == 0:
            continue
        rows.append(
            StandingRow(
                team=names[code],
                played=played,
                won=stats["win"],
                drawn=stats["draw"],
                lost=stats["loss"],
                goals_for=stats["gf"],
                goals_against=stats["ga"],
            )
        )
    rows.sort(
        key=lambda row: (
            -row.points,
            -row.goal_difference,
            -row.goals_for,
            row.team,
        )
    )
    return rows


class _StandingsTableParser(HTMLParser):
    """Collects the cell text of every top-level ``<table>`` on a page.

    Wikipedia renders ``{{#invoke:Sports table}}`` into a plain ``<table>``
    whose columns are computed server-side, so the numbers read here are the
    same ones a reader sees. Footnote markers (``<sup>``) are dropped and
    runs of whitespace are collapsed.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._depth = 0
        self._rows: list[list[str]] = []
        self._cells: list[str] | None = None
        self._text: list[str] = []
        self._skips = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._rows = []
            return
        if self._depth != 1:
            return
        if tag == "tr":
            self._cells = []
        elif tag in ("td", "th"):
            self._text = []
        elif tag in ("sup", "style", "script"):
            self._skips += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("sup", "style", "script"):
            self._skips = max(0, self._skips - 1)
            return
        if tag == "table":
            if self._depth == 1:
                self.tables.append(self._rows)
            self._depth = max(0, self._depth - 1)
            return
        if self._depth != 1 or self._cells is None:
            return
        if tag in ("td", "th"):
            self._cells.append(" ".join("".join(self._text).split()))
            self._text = []
        elif tag == "tr":
            self._rows.append(self._cells)
            self._cells = None

    def handle_data(self, data: str) -> None:
        if self._cells is not None and not self._skips:
            self._text.append(data)


# Column headings of a rendered standings table, keyed by our own field names.
_STANDING_COLUMNS = {
    "team": "team",
    "pld": "played",
    "w": "won",
    "d": "drawn",
    "l": "lost",
    "gf": "goals_for",
    "ga": "goals_against",
    "pts": "pts",
}
_FOOTNOTE_RE = re.compile(r"\[\s*[a-z0-9]+\s*\]", re.IGNORECASE)


def _numeric_cell(value: str) -> int:
    """``"12"`` / ``"+7"`` / ``"−3"`` / ``""`` -> an int (0 when absent)."""
    match = re.search(r"-?\d+", value.replace("\u2212", "-"))
    return int(match.group()) if match else 0


def parse_standings_html(markup: str) -> list[StandingRow]:
    """Read an already-rendered Premier League table out of Wikipedia HTML.

    The rows are returned in the order the page shows them, which already
    accounts for goal difference and any points deductions, so callers must
    not re-sort. Returns an empty list when no standings table is present.
    """
    parser = _StandingsTableParser()
    parser.feed(markup)

    for rows in parser.tables:
        if not rows:
            continue
        header = {cell.strip().lower() for cell in rows[0]}
        if not {"team", "pld", "w", "d", "l", "gf", "ga", "pts"} <= header:
            continue
        index = {
            field_name: column
            for column, cell in enumerate(rows[0])
            if (field_name := _STANDING_COLUMNS.get(cell.strip().lower()))
        }

        parsed: list[StandingRow] = []
        for row in rows[1:]:
            if len(row) <= max(index.values()):
                continue
            team = _FOOTNOTE_RE.sub("", row[index["team"]]).strip()
            if not team:
                continue
            won = _numeric_cell(row[index["won"]])
            drawn = _numeric_cell(row[index["drawn"]])
            parsed.append(
                StandingRow(
                    team=team,
                    played=_numeric_cell(row[index["played"]]),
                    won=won,
                    drawn=drawn,
                    lost=_numeric_cell(row[index["lost"]]),
                    goals_for=_numeric_cell(row[index["goals_for"]]),
                    goals_against=_numeric_cell(row[index["goals_against"]]),
                    # The page's own Pts column wins, so any points deduction
                    # the league applied is reflected rather than recomputed.
                    points_adjustment=_numeric_cell(row[index["pts"]]) - (won * 3 + drawn),
                )
            )
        if len(parsed) >= 2:
            return parsed
    return []


# --------------------------------------------------------------------------- #
# Season fixtures and results
# --------------------------------------------------------------------------- #
#
# The hub's fixtures/results panels used to be parsed out of news headlines,
# which produced contradictions (the same opponent as both a played result and
# an upcoming fixture) and missed matches entirely. Wikipedia's season article
# carries the authoritative schedule as ``wikitable`` match tables, one per
# competition, so that is what we read.

# Level-2 section heading -> the competition name the hub shows. Anything not
# listed here (pre-season friendlies, transfers, awards) is ignored.
_COMPETITION_SECTIONS: tuple[tuple[str, str], ...] = (
    ("premier league", "Premier League"),
    ("carabao cup", "Carabao Cup"),
    ("efl cup", "Carabao Cup"),
    ("league cup", "Carabao Cup"),
    ("fa cup", "FA Cup"),
    ("champions league", "Champions League"),
    ("europa league", "Europa League"),
    ("conference league", "Conference League"),
    ("community shield", "Community Shield"),
)

# ``18 July 2026`` / ``12 August 2026`` - also accepts a ``18–19 August`` range,
# in which case the first day is used.
_MATCH_DATE_RE = re.compile(
    r"(\d{1,2})(?:\s*[\u2013\u2014-]\s*\d{1,2})?\s+([A-Za-z]{3,9})\.?\s+(\d{4})"
)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# ``[https://example.com/report-united-0-wrexham-1 0–1]`` - the display text is
# the score, so it must replace the whole link before a score is looked for.
# (The URL itself can contain digit-dash-digit spans that look like scorelines.)
_EXTLINK_RE = re.compile(r"\[(https?://[^\s\]]+)(?:\s+([^\]]*))?\]")
_NEUTRAL_SIDE = {"n", "neutral"}


@dataclass
class MatchRow:
    """One fixture or result from the club's season article."""

    competition: str
    date: date
    opponent: str
    home: bool = False
    neutral: bool = False
    united_goals: int | None = None
    opponent_goals: int | None = None
    venue: str | None = None
    round: str | None = None
    scorers: str | None = None
    attendance: str | None = None
    position: str | None = None

    @property
    def played(self) -> bool:
        """A filled-in scoreline means the match has been played."""
        return self.united_goals is not None and self.opponent_goals is not None

    @property
    def outcome(self) -> str | None:
        """``W``/``D``/``L`` from United's point of view, for played matches."""
        if not self.played:
            return None
        if self.united_goals > self.opponent_goals:
            return "W"
        if self.united_goals < self.opponent_goals:
            return "L"
        return "D"

    def as_dict(self) -> dict[str, Any]:
        return {
            "competition": self.competition,
            "date": self.date.isoformat(),
            "opponent": self.opponent,
            "home": self.home,
            "neutral": self.neutral,
            "united_goals": self.united_goals,
            "opponent_goals": self.opponent_goals,
            "venue": self.venue,
            "round": self.round,
            "scorers": self.scorers,
            "attendance": self.attendance,
            "position": self.position,
        }


def _plain(raw: str) -> str:
    """Wikitext cell -> readable plain text, keeping bracketed detail intact.

    Unlike :func:`_clean_name` this keeps parenthetical content, because the
    scorers cell uses it (``Fernandes (3) 40' (pen.)``).
    """
    text = _COMMENT_RE.sub("", raw)
    text = _REF_RE.sub("", text)
    text = _FLAG_RE.sub("", text)
    text = _EXTLINK_RE.sub(lambda m: m.group(2) or "", text)
    text = _LINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = text.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", text).strip(" ,\u00a0|")


def _split_cells(line: str, separator: str = "||") -> list[str]:
    """Split a table row on ``||`` (or ``!!``), ignoring pipes inside links."""
    cells: list[str] = []
    buffer: list[str] = []
    depth = 0
    index = 0
    while index < len(line):
        if line.startswith("[[", index):
            depth += 1
            buffer.append("[[")
            index += 2
        elif line.startswith("]]", index) and depth:
            depth -= 1
            buffer.append("]]")
            index += 2
        elif depth == 0 and line.startswith(separator, index):
            cells.append("".join(buffer))
            buffer = []
            index += len(separator)
        else:
            buffer.append(line[index])
            index += 1
    cells.append("".join(buffer))
    return cells


def _strip_attrs(cell: str) -> str:
    """``align="left"|[[Bruno Fernandes]]`` -> ``[[Bruno Fernandes]]``."""
    while True:
        head, separator, tail = cell.partition("|")
        if not separator or "=" not in head or "[" in head:
            return cell
        cell = tail


def _heading_label(line: str) -> tuple[int, str]:
    """``====League phase====`` -> ``(4, 'League phase')``."""
    level = len(line) - len(line.lstrip("="))
    label = _plain(line.strip("=")).strip()
    return level, label


def _competition_for(heading: str) -> str | None:
    lowered = heading.lower()
    for needle, competition in _COMPETITION_SECTIONS:
        if needle in lowered:
            return competition
    return None


def _match_columns(header: list[str]) -> dict[str, int] | None:
    """Locate the columns we care about, or ``None`` if this is not a match table."""
    columns: dict[str, int] = {}

    def put(key: str, position: int) -> None:
        columns.setdefault(key, position)

    for position, raw in enumerate(header):
        label = _plain(raw).lower()
        if not label:
            continue
        if "date" in label:
            put("date", position)
        elif "opponent" in label:
            put("opponent", position)
        elif label.startswith("h") and "/" in label:
            put("side", position)
        elif "scorer" in label:
            # Checked before the score/result column: "scorers" contains "score".
            put("scorers", position)
        elif "result" in label or "score" in label:
            put("result", position)
        elif "attendance" in label:
            put("attendance", position)
        elif "position" in label:
            put("position", position)
        elif "round" in label:
            put("round", position)

    if "date" not in columns or "opponent" not in columns:
        return None
    return columns


def _match_date(raw: str) -> date | None:
    match = _MATCH_DATE_RE.search(_plain(raw))
    if not match:
        return None
    month = _MONTHS.get(match.group(2)[:3].lower())
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def _match_side(raw: str) -> tuple[bool, bool, str | None]:
    """``H`` / ``A`` / ``[[Helsinki Olympic Stadium|N]]`` -> home, neutral, venue."""
    venue: str | None = None
    link = _LINK_RE.search(raw)
    if link:
        display = (link.group(2) or link.group(1)).strip().lower()
        if display in _NEUTRAL_SIDE:
            venue = _COMMENT_RE.sub("", link.group(1)).replace("_", " ").strip()
    text = _plain(raw).strip().lower()
    if text in {"h", "home"}:
        return True, False, None
    if text in {"a", "away"}:
        return False, False, None
    if text in _NEUTRAL_SIDE:
        # Neutral venues: the club is listed second, but the scoreline stays
        # United-first either way, so the caller keeps them aligned.
        return False, True, venue
    return False, False, None


def _match_score(raw: str) -> tuple[int, int] | None:
    """``0–2`` -> ``(0, 2)``; an empty (unplayed) cell -> ``None``."""
    match = _SCORE_RE.search(_plain(raw))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _match_row(cells: list[str], columns: dict[str, int], competition: str) -> MatchRow | None:
    def cell(key: str) -> str:
        position = columns.get(key)
        if position is None or position >= len(cells):
            return ""
        return cells[position]

    played_on = _match_date(cell("date"))
    opponent = _team_display(cell("opponent"))
    if played_on is None or not opponent:
        return None
    home, neutral, venue = _match_side(cell("side"))
    score = _match_score(cell("result"))
    return MatchRow(
        competition=competition,
        date=played_on,
        opponent=opponent,
        home=home,
        neutral=neutral,
        united_goals=score[0] if score else None,
        opponent_goals=score[1] if score else None,
        venue=venue,
        round=_plain(cell("round")) or None,
        scorers=_plain(cell("scorers")) or None,
        attendance=_plain(cell("attendance")) or None,
        position=_plain(cell("position")) or None,
    )


def _parse_match_table(table: list[str], heading: str) -> list[MatchRow]:
    """Read one ``wikitable`` body into match rows (empty when it is not one)."""
    competition = _competition_for(heading)
    if not competition:
        return []

    header: list[str] = []
    body: list[list[str]] = []
    for line in table:
        if line.startswith("!"):
            header.extend(_strip_attrs(part) for part in _split_cells(line[1:], "!!"))
        elif line.startswith("|-"):
            body.append([])
        elif line.startswith("|"):
            if not body:
                body.append([])
            body[-1].extend(_strip_attrs(part) for part in _split_cells(line[1:]))

    columns = _match_columns(header)
    if columns is None:
        return []

    rows: list[MatchRow] = []
    for cells in body:
        row = _match_row(cells, columns, competition)
        if row is not None:
            rows.append(row)
    return rows


def parse_matches(wikitext: str) -> list[MatchRow]:
    """Every scheduled match on the club's season article, in page order.

    Tables are located by their header cells rather than by offset, so a new
    competition block (or a re-ordered article) is picked up automatically. The
    competition comes from the enclosing level-2 heading, which is what keeps
    pre-season friendlies out of the season record.
    """
    rows: list[MatchRow] = []
    section = ""
    table: list[str] | None = None
    depth = 0

    for raw in wikitext.splitlines():
        line = raw.strip()
        if depth == 0:
            if line.startswith("==") and line.endswith("=") and not line.startswith("{{"):
                level, label = _heading_label(line)
                if level == 2:
                    section = label
                continue
            if line.startswith("{|"):
                table = []
                depth = 1
            continue

        if line.startswith("{|"):
            depth += 1
            table.append(line)  # type: ignore[union-attr]
        elif line.startswith("|}"):
            depth -= 1
            if depth == 0:
                rows.extend(_parse_match_table(table or [], section))
                table = None
        else:
            table.append(line)  # type: ignore[union-attr]

    return rows


def _fetch_wikitext(page: str) -> str | None:
    try:
        response = httpx.get(
            _API,
            params={
                "action": "parse",
                "page": page,
                "prop": "wikitext",
                "format": "json",
                "formatversion": "2",
                "redirects": "1",
            },
            headers={"User-Agent": settings.hub_user_agent},
            timeout=settings.hub_http_timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Wikipedia fetch failed for %r: %s", page, exc)
        return None
    return (payload.get("parse") or {}).get("wikitext")


def _fetch_rendered(page: str) -> str | None:
    """The page's server-rendered HTML (templates already expanded)."""
    try:
        response = httpx.get(
            _API,
            params={
                "action": "parse",
                "page": page,
                "prop": "text",
                "format": "json",
                "formatversion": "2",
                "redirects": "1",
            },
            headers={"User-Agent": settings.hub_user_agent},
            timeout=settings.hub_http_timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Wikipedia render failed for %r: %s", page, exc)
        return None
    return (payload.get("parse") or {}).get("text")


def fetch_squad(force: bool = False) -> list[SquadMember]:
    """Real squad with season stats, cached for ``hub_cache_seconds``.

    Falls back from the season table to the club roster when the table is
    unavailable. Returns an empty list (never raises) when Wikipedia is
    disabled or unreachable.
    """
    if not settings.hub_wikipedia_enabled:
        return []
    now = time.monotonic()
    if not force and _cache.value is not None and now < _cache.expires:
        return _cache.value

    members: list[SquadMember] = []
    season_wikitext = _fetch_wikitext(settings.hub_season_page)
    if season_wikitext:
        members = parse_squad_statistics(season_wikitext)
    if not members:
        club_wikitext = _fetch_wikitext(settings.hub_squad_page)
        if club_wikitext:
            members = parse_roster(club_wikitext)

    _cache.value = members
    _cache.expires = now + settings.hub_cache_seconds
    return members


class _StandingsCache:
    value: list[StandingRow] | None = None
    expires: float = 0.0
    fetched_at: float | None = None
    source: str | None = None


_standings_cache = _StandingsCache()


def standings_meta() -> dict[str, Any]:
    """When the table was last refreshed and from where (for the UI caption)."""
    if _standings_cache.fetched_at is None:
        return {}
    return {
        "source": _standings_cache.source,
        "updated_at": datetime.fromtimestamp(
            _standings_cache.fetched_at, tz=timezone.utc
        ).isoformat(),
    }


def fetch_standings(force: bool = False) -> list[StandingRow]:
    """The real Premier League table, refreshed every ``hub_standings_cache_seconds``.

    The rendered table is preferred: Wikipedia computes it from the season's
    results and it stays in step with the live page, whereas the raw grid has
    to be aggregated by us and is filled in inconsistently. The wikitext parse
    is kept as a fallback for the explicit-tally style of article, and an empty
    list (never an exception) is returned when Wikipedia is disabled or
    unreachable so callers can fall back to a locally derived table.
    """
    if not settings.hub_wikipedia_enabled:
        return []
    now = time.monotonic()
    if (
        not force
        and _standings_cache.value is not None
        and now < _standings_cache.expires
    ):
        return _standings_cache.value

    rows: list[StandingRow] = []
    source = ""
    rendered = _fetch_rendered(settings.hub_table_page)
    if rendered:
        rows = parse_standings_html(rendered)
        if rows:
            source = "Wikipedia (rendered table)"
    if not rows:
        wikitext = _fetch_wikitext(settings.hub_table_page)
        if wikitext:
            rows = parse_standings(wikitext)
            if rows:
                source = "Wikipedia (season results)"

    # Only a successful read is cached, so a transient failure retries on the
    # next request instead of freezing an empty table for the whole interval.
    if rows:
        _standings_cache.value = rows
        _standings_cache.expires = now + settings.hub_standings_cache_seconds
        _standings_cache.fetched_at = time.time()
        _standings_cache.source = source
    return rows


class _MatchesCache:
    value: list[MatchRow] | None = None
    expires: float = 0.0
    fetched_at: float | None = None


_matches_cache = _MatchesCache()


def matches_meta() -> dict[str, Any]:
    """When the schedule was last refreshed and from where (for the UI caption)."""
    if _matches_cache.fetched_at is None:
        return {}
    return {
        "source": "Wikipedia season article",
        "updated_at": datetime.fromtimestamp(
            _matches_cache.fetched_at, tz=timezone.utc
        ).isoformat(),
    }


def schedule_stale() -> bool:
    """True when a cached schedule has outlived its interval.

    A read still serves that cached list; this only says a refresh is worth
    starting off the request path. False before the first read, because that
    read has to fetch anyway.
    """
    return _matches_cache.value is not None and time.monotonic() >= _matches_cache.expires


def fetch_matches(force: bool = False) -> list[MatchRow]:
    """United's real season schedule, cached for ``hub_standings_cache_seconds``.

    A new result lands on Wikipedia within minutes of full time, so the
    schedule is cached on the same short interval as the table. As with the
    other fetchers, only a successful read is cached and an empty list (never an
    exception) is returned when Wikipedia is disabled or unreachable.
    """
    if not settings.hub_wikipedia_enabled:
        return []
    now = time.monotonic()
    if not force and _matches_cache.value is not None and now < _matches_cache.expires:
        return _matches_cache.value

    rows: list[MatchRow] = []
    wikitext = _fetch_wikitext(settings.hub_season_page)
    if wikitext:
        rows = parse_matches(wikitext)

    if rows:
        _matches_cache.value = rows
        _matches_cache.expires = now + settings.hub_standings_cache_seconds
        _matches_cache.fetched_at = time.time()
    return rows


def reset_cache() -> None:
    """Clear the cached squad, table and schedule (used by tests)."""
    _cache.value = None
    _cache.expires = 0.0
    _standings_cache.value = None
    _standings_cache.expires = 0.0
    _standings_cache.fetched_at = None
    _standings_cache.source = None
    _matches_cache.value = None
    _matches_cache.expires = 0.0
    _matches_cache.fetched_at = None
