"""First-team wages, which no feed carries at all.

Nothing else the Hub reads publishes salaries: Wikipedia's season article stops
at appearances, goals and cards, and Transfermarkt gives market value (what a
player is worth) rather than what he is paid. SalaryLeaks publishes the club's
wage bill as server-rendered HTML - one row per player with his weekly wage,
annual wage, bonus, age and contract expiry, plus a published total:

    https://www.salaryleaks.com/football/teams/manchester-united

The page renders two copies of the table (a desktop one with a rank column and
a mobile one without), so only the first table whose headers look right is read.

These are **reported** figures, not club-confirmed ones - English clubs publish
a wage bill in their accounts but never a per-player breakdown - so the panel
presents them as such. Wages move rarely, so the read is cached far past the
Hub's own window and is never on the critical path. Nothing raises here: a page
the source will not serve yields no wages rather than a broken Hub.
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

logger = logging.getLogger("simplyutd.salaryleaks")

# The money columns are written short - "£16.9M", "£520,000", "£300".
_MONEY = re.compile(r"£\s*([\d,]+(?:\.\d+)?)\s*([KMB]?)", re.IGNORECASE)
_SCALE = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
# "Last updated:" followed by a machine-readable <time datetime="2026-09-16">.
_UPDATED = re.compile(r"Last updated:.{0,400}?datetime=\"([\d-]{10})\"", re.DOTALL)
_CONTRACT = re.compile(r"(20\d{2})")


class _WagesTable(HTMLParser):
    """Every ``<table>`` on the page as ``(headers, rows)``.

    A cell records its text, its links, its images *with their alt text* (the
    nation flag is only identifiable from its alt) and whether it carries a
    verification tick.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict[str, Any]] = []
        self._depth = 0
        self._in_row = False
        self._in_cell = False
        self._row: list[dict[str, Any]] = []
        self._cell: dict[str, Any] | None = None
        self._text: list[str] = []
        self._headers: list[str] = []
        self._rows: list[list[dict[str, Any]]] = []

    # -- markup ---------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: (value or "") for name, value in attrs}
        if tag == "table":
            if self._depth == 0:
                self._headers, self._rows = [], []
                self._in_row = self._in_cell = False
                self._cell = None
                self._row = []
            self._depth += 1
            return
        if self._depth == 0:
            return
        if self._depth == 1:
            if tag == "tr":
                self._in_row = True
                self._row = []
                return
            if tag in ("td", "th"):
                self._in_cell = True
                self._text = []
                self._cell = {
                    "text": "",
                    "href": None,
                    "images": [],
                    "verified": False,
                    "header": tag == "th",
                }
                return
        if not self._in_cell or self._cell is None:
            return
        if tag == "img":
            source = attributes.get("data-src") or attributes.get("src") or ""
            if source and not source.startswith("data:"):
                self._cell["images"].append((source, (attributes.get("alt") or "").strip()))
        if tag == "a" and self._cell["href"] is None:
            href = attributes.get("href") or ""
            if href.startswith("http"):
                self._cell["href"] = href
        if "circle-check" in attributes.get("class", ""):
            self._cell["verified"] = True

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._depth:
                self._depth -= 1
                if self._depth == 0:
                    if self._headers and self._rows:
                        self.tables.append({"headers": self._headers, "rows": self._rows})
                    self._headers, self._rows = [], []
            return
        if self._depth != 1:
            return
        if tag in ("td", "th"):
            if self._in_cell and self._cell is not None:
                self._cell["text"] = " ".join(" ".join(self._text).split())
                self._row.append(self._cell)
                if tag == "th":
                    self._headers.append(self._cell["text"])
                self._in_cell = False
                self._cell = None
            return
        if tag == "tr" and self._in_row:
            if self._row:
                self._rows.append(self._row)
            self._in_row = False
            self._row = []


def _money(text: str) -> int | None:
    """The number of pounds in a money cell, or ``None`` when it holds a dash."""
    match = _MONEY.search(text or "")
    if not match:
        return None
    amount = float(match.group(1).replace(",", ""))
    return int(round(amount * _SCALE.get(match.group(2).upper(), 1)))


def _nation(cell: dict[str, Any]) -> str | None:
    """The country from the flag's alt text ("England Flag")."""
    for _, alt in cell["images"]:
        if alt.endswith(" Flag"):
            return alt[: -len(" Flag")].strip() or None
    return None


def _integer(text: str) -> int | None:
    match = re.search(r"\d+", text or "")
    return int(match.group(0)) if match else None


def _first_table(tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The table that looks like the wage table.

    The page renders it twice, desktop and mobile; the desktop copy is first and
    is the one with the rank column, so the first match is kept.
    """
    for table in tables:
        headers = [header.strip().lower() for header in table["headers"]]
        if "player" in headers and "weekly" in headers:
            return table
    return None


def _columns(headers: list[str]) -> dict[str, int]:
    return {header.strip().lower(): index for index, header in enumerate(headers)}


def _cell_text(row: list[dict[str, Any]], columns: dict[str, int], name: str) -> str:
    """The text of a named column's cell, or ``""`` when the column is absent."""
    index = columns.get(name)
    if index is None or index >= len(row):
        return ""
    return (row[index]["text"] or "").strip()


def parse_salaries(html: str) -> dict[str, Any]:
    """The wage table: one entry per player, plus the published totals.

    A row is only read when it is the width of the header - the totals row is
    shorter (its cells span columns) and is picked out by its own label.
    """
    parser = _WagesTable()
    parser.feed(html or "")
    parser.close()
    table = _first_table(parser.tables)
    if table is None:
        return {"players": [], "totals": {}, "source_updated": None}

    columns = _columns(table["headers"])
    width = len(table["headers"])
    player_at = columns.get("player")
    if player_at is None:
        return {"players": [], "totals": {}, "source_updated": None}

    players: list[dict[str, Any]] = []
    totals: dict[str, Any] = {}
    seen: set[str] = set()

    for row in table["rows"]:
        if all(cell["header"] for cell in row):
            continue
        label = (row[0]["text"] if row else "").strip().lower()
        if label.startswith("total"):
            weekly, annual, bonus = (cell["text"] for cell in row[1:4]) if len(row) >= 4 else ("", "", "")
            totals = {
                "weekly": _money(weekly),
                "annual": _money(annual),
                "bonus": _money(bonus),
            }
            continue
        if len(row) != width or player_at >= len(row):
            continue
        cell = row[player_at]
        name = (cell["text"] or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        # A player on no bonus shows a dash in that cell.
        bonus_text = _cell_text(row, columns, "bonus")
        bonus_amount = _money(bonus_text)
        contract = _CONTRACT.search(_cell_text(row, columns, "contract"))
        players.append(
            {
                "name": name,
                "url": cell["href"],
                "nation": _nation(cell),
                "verified": bool(cell["verified"]),
                "weekly": _cell_text(row, columns, "weekly"),
                "weekly_amount": _money(_cell_text(row, columns, "weekly")),
                "annual": _cell_text(row, columns, "annual"),
                "annual_amount": _money(_cell_text(row, columns, "annual")),
                # A dash means the player is on no bonus at all, so it is absent
                # rather than a zero.
                "bonus": bonus_text.lstrip("+ ").strip() if bonus_amount is not None else None,
                "bonus_amount": bonus_amount,
                "age": _integer(_cell_text(row, columns, "age")),
                "contract": contract.group(1) if contract else None,
            }
        )

    updated = _UPDATED.search(html or "")
    return {
        "players": players,
        "totals": totals,
        "source_updated": updated.group(1) if updated else None,
    }


def _salaries_url() -> str:
    return f"{settings.salaryleaks_base_url}/football/teams/{settings.salaryleaks_club_path}"


def _get(url: str) -> str | None:
    """The page at ``url``, or ``None`` when the source will not serve it."""
    try:
        response = httpx.get(
            url,
            timeout=settings.salaryleaks_http_timeout,
            follow_redirects=True,
            headers={
                "User-Agent": settings.salaryleaks_user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-GB,en;q=0.9",
            },
        )
        if response.status_code >= 400:
            logger.debug("SalaryLeaks returned %s: %s", response.status_code, url)
            return None
        return response.text
    except Exception:  # noqa: BLE001 - a dead source must not break the hub
        logger.debug("SalaryLeaks fetch failed: %s", url)
        return None


_EMPTY: dict[str, Any] = {"players": [], "totals": {}, "source_updated": None}

_cache: dict[str, Any] = {"salaries": None, "salaries_expires": 0.0, "salaries_at": None}


def salaries() -> dict[str, Any]:
    """The club's per-player wages plus the published totals, cached."""
    now = time.time()
    # The expiry is also set when a read fails, so a source that is down is not
    # asked again on every request; there may be nothing cached to return yet.
    if now < _cache["salaries_expires"]:
        return _cache["salaries"] or _EMPTY
    if not settings.salaryleaks_enabled:
        return _cache["salaries"] or _EMPTY
    html = _get(_salaries_url())
    if html is None:
        # Keep whatever was last read rather than emptying the panel because one
        # fetch failed.
        _cache["salaries_expires"] = now + settings.salaryleaks_retry_seconds
        return _cache["salaries"] or _EMPTY
    parsed = parse_salaries(html)
    if not parsed["players"]:
        _cache["salaries_expires"] = now + settings.salaryleaks_retry_seconds
        return _cache["salaries"] or _EMPTY
    _cache["salaries"] = parsed
    _cache["salaries_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _cache["salaries_expires"] = now + settings.salaryleaks_seconds
    return parsed


def wages_by_player() -> dict[str, int]:
    """Weekly wages keyed by the player's name, as the source spells it."""
    return {
        player["name"]: player["weekly_amount"]
        for player in salaries()["players"]
        if player["weekly_amount"]
    }


def salaries_updated_at() -> str | None:
    """When the wage table was last read, for the panel's provenance line."""
    return _cache["salaries_at"]


def reset_cache() -> None:
    """Drop the cached read; the next call refetches."""
    _cache["salaries"] = None
    _cache["salaries_at"] = None
    _cache["salaries_expires"] = 0.0
