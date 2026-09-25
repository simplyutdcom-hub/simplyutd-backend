"""Assists and injuries, the two squad numbers Wikipedia does not carry.

The Hub's squad panel is built from Wikipedia's season article, whose "Squad
statistics" table has appearances, goals and cards but no assists column at all
(the word does not appear in the article), and which says nothing about who is
currently injured. Transfermarkt publishes both as server-rendered HTML:

* ``/leistungsdaten/verein/<id>/plus/1`` - the squad's season totals, one
  column per stat. The ``plus/1`` ("Detailed") view is what adds Assists; the
  default view omits it entirely.
* ``/sperrenundverletzungen/verein/<id>/plus/1`` - the injuries and suspensions
  table, with the days out and market value columns that the default view
  drops.

Both pages are cached well past the Hub's own window - a player picked up a
knock at the weekend, not between two requests - so no path here is on the
critical path of a read. Nothing raises: a page Transfermarkt will not serve
simply yields no assists (or no injuries) rather than breaking the hub.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger("simplyutd.transfermarkt")

# Transfermarkt spells positions out in English, and the player cell's text is
# the name run together with the position ("Amad DialloRight Winger"), so the
# position is recognised rather than split off. Longest first, so "Right Winger"
# is not matched as "Winger".
_POSITIONS = (
    "Attacking Midfield",
    "Defensive Midfield",
    "Central Midfield",
    "Left Midfield",
    "Right Midfield",
    "Centre-Back",
    "Left-Back",
    "Right-Back",
    "Left Winger",
    "Right Winger",
    "Centre-Forward",
    "Second Striker",
    "Goalkeeper",
    "Midfielder",
    "Defender",
    "Forward",
)

# Transfermarkt groups the table's rows under a heading; the singular reads
# better on a card ("Knock" under "Injury").
_STATUS_LABELS = {"Injuries": "Injury", "Suspensions": "Suspension"}

# How a name is compared across the two sources: cast and punctuation dropped,
# so "Benjamin Šeško" matches "Benjamin Sesko" and "Bruno Fernandes" matches
# whatever Wikipedia calls him.
_ACCENTS = str.maketrans(
    "áàâãäåéèêëíìîïóòôõöúùûüýÿñçšžŠŽćčđ",
    "aaaaaaeeeeiiiiooooouuuuyyncszSZccd",
)


def _key(name: str) -> str:
    """A player name in the form the two sources can be matched on."""
    folded = name.translate(_ACCENTS).lower()
    return " ".join(re.sub(r"[^a-z ]", " ", folded).split())


def _surname(name: str) -> str:
    parts = _key(name).split()
    return parts[-1] if parts else ""


class _ItemsTable(HTMLParser):
    """Every row of the page's first ``<table class="items">``.

    The player cell holds a nested ``<table class="inline-table">`` (portrait,
    name, position), so a naive reader loses the row to the inner table. Cells
    are therefore only collected at nesting depth 1, and each one keeps the
    ``title`` attributes and the lazy portrait URL its markup carried.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headers: list[str] = []
        self.rows: list[list[dict[str, Any]]] = []
        self._started = False
        self._closed = False
        self._tdepth = 0
        self._in_row = False
        self._in_cell = False
        self._cell: dict[str, Any] | None = None
        self._row: list[dict[str, Any]] = []
        self._text: list[str] = []

    # -- markup ---------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: (value or "") for name, value in attrs}
        if tag == "table":
            if not self._started:
                if not self._closed and "items" in attributes.get("class", "").split():
                    self._started = True
                    self._tdepth = 1
            else:
                self._tdepth += 1
            return
        if not self._started:
            return
        if self._tdepth == 1:
            if tag == "tr":
                self._in_row = True
                self._in_cell = False
                self._row = []
                return
            if tag in ("td", "th"):
                self._in_cell = True
                self._text = []
                self._cell = {
                    "text": "",
                    "titles": [],
                    "links": [],
                    "images": [],
                    "href": None,
                    "colspan": int(attributes.get("colspan") or 1),
                    "header": tag == "th",
                }
                return
        # The player cell's contents sit in a nested inline-table, so its
        # portrait and profile link are only reachable from below the guard
        # above.
        if not self._in_cell or self._cell is None:
            return
        title = (attributes.get("title") or "").strip()
        if title and title != "&nbsp;":
            self._cell["titles"].append(title)
        if tag == "img":
            source = attributes.get("data-src") or attributes.get("src") or ""
            if source and not source.startswith("data:"):
                self._cell["images"].append(source)
        if tag == "a":
            href = attributes.get("href") or ""
            self._cell["links"].append((href, title))
            if self._cell["href"] is None and "/profil/" in href:
                self._cell["href"] = href

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._started:
                self._tdepth -= 1
                if self._tdepth <= 0:
                    self._started = False
                    self._closed = True
            return
        if not self._started or self._tdepth != 1:
            return
        if tag in ("td", "th"):
            if self._in_cell and self._cell is not None:
                self._cell["text"] = " ".join(" ".join(self._text).split())
                self._row.append(self._cell)
                if tag == "th":
                    self.headers.append(_header_label(self._cell))
                self._in_cell = False
                self._cell = None
            return
        if tag == "tr" and self._in_row:
            if self._row:
                self.rows.append(self._row)
            self._in_row = False
            self._row = []


def _header_label(cell: dict[str, Any]) -> str:
    """A column name for a ``<th>``: the icon title, else the visible text.

    Most stat columns are icon-only, and their meaning lives in a
    ``title="Assists"`` on a sprite span.
    """
    titles = [title for title in cell["titles"] if title not in ("", "&nbsp;")]
    for title in titles:
        if not title.lower().startswith(("sort", "points average")):
            return title
    return (cell.get("text") or "").strip()


def _position_in(text: str) -> str | None:
    lowered = text.lower()
    for position in _POSITIONS:
        if position.lower() in lowered:
            return position
    return None


def _number(value: str) -> int | None:
    """The integer in a stat cell, or ``None`` when it holds prose.

    Transfermarkt writes ``-`` for a zero and whole phrases such as "Not used
    during this season" for a player who never took the field, and those cells
    are also where the column count shifts.
    """
    text = (value or "").strip().replace(",", "")
    if not text:
        return None
    match = re.fullmatch(r"(\d+)", text)
    if match:
        return int(match.group(1))
    return None


def _stat(value: str) -> int:
    return _number(value) or 0


def _get(url: str) -> str | None:
    """The page at ``url``, or ``None`` when Transfermarkt will not serve it."""
    try:
        response = httpx.get(
            url,
            timeout=settings.transfermarkt_http_timeout,
            follow_redirects=True,
            headers={
                # The pages are plain HTML and Transfermarkt turns away anything
                # that does not look like a browser.
                "User-Agent": settings.transfermarkt_user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-GB,en;q=0.9",
            },
        )
        if response.status_code >= 400:
            logger.debug("Transfermarkt returned %s: %s", response.status_code, url)
            return None
        return response.text
    except Exception:  # noqa: BLE001 - a dead source must not break the hub
        logger.debug("Transfermarkt fetch failed: %s", url)
        return None


def _squad_stats_url() -> str:
    return (
        f"{settings.transfermarkt_base_url}/{settings.transfermarkt_club_path}"
        f"/leistungsdaten/verein/{settings.transfermarkt_club_id}/plus/1"
    )


def _injuries_url() -> str:
    return (
        f"{settings.transfermarkt_base_url}/{settings.transfermarkt_club_path}"
        f"/sperrenundverletzungen/verein/{settings.transfermarkt_club_id}/plus/1"
    )


def parse_squad_stats(html: str) -> list[dict[str, Any]]:
    """The squad's season totals, including assists.

    Only rows that line up with the header are read: a player who is "not in
    squad during this season" has his stat cells merged into one wide cell, so
    his row has fewer of them and cannot be read positionally.
    """
    table = _ItemsTable()
    table.feed(html)
    columns = {label: index for index, label in enumerate(table.headers)}
    if "Assists" not in columns or "Player" not in columns:
        logger.debug("Transfermarkt squad page had no Assists column")
        return []

    players: list[dict[str, Any]] = []
    width = len(table.headers)
    for row in table.rows:
        if len(row) != width or _is_header_row(row):
            continue
        player = _player(row[columns["Player"]])
        if not player:
            continue
        players.append(
            {
                "name": player,
                "position": _position_in(row[columns["Player"]]["text"]),
                "apps": _stat(_cell_text(row, columns.get("Appearances"))),
                "goals": _stat(_cell_text(row, columns.get("Goals"))),
                "assists": _stat(_cell_text(row, columns.get("Assists"))),
            }
        )
    return players


def parse_injuries(html: str) -> list[dict[str, Any]]:
    """The club's current injuries and suspensions, newest absence first."""
    table = _ItemsTable()
    table.feed(html)
    columns = {label: index for index, label in enumerate(table.headers)}
    if "Player" not in columns:
        return []

    injuries: list[dict[str, Any]] = []
    group: str | None = None
    for row in table.rows:
        if _is_header_row(row):
            continue
        if len(row) == 1:
            # The single wide cell that introduces a group: "Injuries" or
            # "Suspensions".
            group = row[0]["text"] or None
            continue
        name = _player(row[columns["Player"]])
        if not name:
            continue
        cell = row[columns["Player"]]
        injuries.append(
            {
                "name": name,
                "position": _position_in(cell["text"]),
                "image": _portrait(cell),
                "url": _profile_url(cell["href"]),
                "age": _number(_cell_text(row, columns.get("Age"))),
                "reason": _cell_text(row, columns.get("Reason")) or None,
                "since": _cell_text(row, columns.get("since")) or None,
                "expected_return": _cell_text(row, columns.get("Expected return")) or None,
                "missed_matches": _stat(_cell_text(row, columns.get("Missed matches"))),
                "days_out": _number(_cell_text(row, columns.get("Days"))),
                "market_value": _cell_text(row, columns.get("Market Value")) or None,
                "status": _STATUS_LABELS.get(group or "", group or "Injury"),
            }
        )
    return injuries


def _cell_text(row: list[dict[str, Any]], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index]["text"]


def _is_header_row(row: list[dict[str, Any]]) -> bool:
    """The ``<thead>`` row arrives here too, because it is a ``<tr>``."""
    return bool(row) and all(cell.get("header") for cell in row)


def _portrait(cell: dict[str, Any]) -> str | None:
    """The player's portrait from his cell.

    A new signing's cell also carries the crest of the club he arrived from,
    which comes first in the markup, so only a portrait URL is accepted.
    """
    for source in cell.get("images") or []:
        if "/portrait/" in source:
            return source
    return None


def _player(cell: dict[str, Any]) -> str | None:
    """The player's name from his cell.

    The cell's text runs the name, the short name and the position together,
    and a new signing's cell also carries the club he arrived from, so the name
    is taken from the profile link's ``title`` instead.
    """
    for href, title in cell.get("links") or []:
        if "/profil/" in href and title:
            return title
    for title in cell["titles"]:
        if title:
            return title
    return (cell.get("text") or "").strip() or None


def _profile_url(href: str | None) -> str | None:
    if not href or "/profil/" not in href:
        return None
    return f"{settings.transfermarkt_base_url}{href}"


_cache: dict[str, Any] = {
    "stats": None,
    "stats_expires": 0.0,
    "stats_at": None,
    "injuries": None,
    "injuries_expires": 0.0,
    "injuries_at": None,
}


def squad_stats() -> list[dict[str, Any]]:
    """The club's assists (and goals/appearances) per player, cached."""
    return _cached("stats", _squad_stats_url, parse_squad_stats, settings.transfermarkt_stats_seconds)


def injuries() -> list[dict[str, Any]]:
    """The club's current injuries, cached."""
    return _cached(
        "injuries", _injuries_url, parse_injuries, settings.transfermarkt_injury_seconds
    )


def _cached(
    key: str, url: str, parse, ttl: int  # noqa: ANN001 - internal plumbing
) -> list[dict[str, Any]]:
    now = time.time()
    # The expiry is also set when a read fails, so a source that is down is not
    # asked again on every request; there may be nothing cached to return yet.
    if now < _cache[f"{key}_expires"]:
        return _cache[key] or []
    if not settings.transfermarkt_enabled:
        return _cache[key] or []
    html = _get(url())
    if html is None:
        # Keep whatever was last read rather than emptying the panel because one
        # fetch failed.
        _cache[f"{key}_expires"] = now + settings.transfermarkt_retry_seconds
        return _cache[key] or []
    parsed = parse(html)
    if not parsed:
        _cache[f"{key}_expires"] = now + settings.transfermarkt_retry_seconds
        return _cache[key] or []
    _cache[key] = parsed
    _cache[f"{key}_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _cache[f"{key}_expires"] = now + ttl
    return parsed


def injuries_updated_at() -> str | None:
    """When the injury list was last read, for the panel's provenance line."""
    return _cache["injuries_at"]


def assists_by_player() -> dict[str, int]:
    """Assists keyed by the player's full name, as Transfermarkt spells it."""
    return {player["name"]: player["assists"] for player in squad_stats()}


def match_assists(name: str, table: dict[str, int]) -> int | None:
    """The assists recorded for ``name``, or ``None`` when he is not listed."""
    if name in table:
        return table[name]
    key = _key(name)
    if key in table:
        return table[key]
    surname = _surname(name)
    if not surname:
        return None
    # Wikipedia may write a player's name slightly differently ("Benjamin
    # Šeško" against "Benjamin Sesko"), so a unique surname is accepted too.
    hits = [value for other, value in table.items() if _surname(other) == surname]
    return hits[0] if len(hits) == 1 else None


def reset_cache() -> None:
    """Drop both cached reads; the next call refetches."""
    for key in ("stats", "injuries"):
        _cache[key] = None
        _cache[f"{key}_at"] = None
        _cache[f"{key}_expires"] = 0.0
