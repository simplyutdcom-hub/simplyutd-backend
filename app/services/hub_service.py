"""Derive the public Hub from real, ingested football data.

Nothing here is fabricated. Two real sources feed the Hub:

* the RSS news corpus already in Mongo (match reports and previews), from which
  we parse *facts* only - scorelines, the two clubs, the competition;
* Wikipedia's current-season ``Squad statistics`` for the United squad,
  appearances and goals, its ``Sports table`` module for the Premier League
  table, and its season article's match tables for the real fixture list (see
  :mod:`app.services.wikipedia`).

Prose from publishers is never republished - we only extract structured facts
that are not themselves copyrightable (a 2-1 scoreline, a fixture pairing).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

from .. import db as db_module
from ..config import settings
from . import feed_rank, form_service, wikipedia
from .seed import PLAYER_IMAGES

logger = logging.getLogger("simplyutd.hub")

# Guards :func:`revalidate` so a burst of hub reads starts one Wikipedia refresh.
_revalidating = False

# --------------------------------------------------------------------------- #
# Clubs
# --------------------------------------------------------------------------- #

# canonical name -> aliases found in copy. Canonical names for the well-known
# clubs match the frontend's ``TEAM_LOGOS`` keys so crests resolve.
CLUBS: dict[str, tuple[str, ...]] = {
    "Manchester United": ("manchester united", "man utd", "man united"),
    "Manchester City": ("manchester city", "man city"),
    "Arsenal": ("arsenal",),
    "Chelsea": ("chelsea",),
    "Liverpool": ("liverpool",),
    "Tottenham": ("tottenham hotspur", "tottenham", "spurs"),
    "Newcastle": ("newcastle united", "newcastle"),
    "Aston Villa": ("aston villa", "villa"),
    "Brighton": ("brighton & hove albion", "brighton and hove albion", "brighton"),
    "Everton": ("everton",),
    "Fulham": ("fulham",),
    "Brentford": ("brentford",),
    "Crystal Palace": ("crystal palace",),
    "West Ham": ("west ham united", "west ham"),
    "Wolves": ("wolverhampton wanderers", "wolverhampton", "wolves"),
    "Nottingham Forest": ("nottingham forest", "forest"),
    "Leicester": ("leicester city", "leicester"),
    "Ipswich": ("ipswich town", "ipswich"),
    "Burnley": ("burnley",),
    "Southampton": ("southampton", "saints"),
    "Bournemouth": ("afc bournemouth", "bournemouth"),
    "Hull": ("hull city", "hull"),
    "Leeds": ("leeds united", "leeds"),
    "Sunderland": ("sunderland",),
    "Sheffield United": ("sheffield united",),
    "Sheffield Wednesday": ("sheffield wednesday",),
    "Norwich": ("norwich city", "norwich"),
    "Millwall": ("millwall",),
    "Middlesbrough": ("middlesbrough", "boro"),
    "Blackburn": ("blackburn rovers", "blackburn"),
    "Stoke": ("stoke city", "stoke"),
    "Watford": ("watford",),
    "Swansea": ("swansea city", "swansea"),
    "Charlton": ("charlton athletic", "charlton"),
    "Portsmouth": ("portsmouth",),
    "Bristol City": ("bristol city",),
    "Birmingham": ("birmingham city", "birmingham"),
    "Derby": ("derby county", "derby"),
    "Preston": ("preston north end", "preston"),
    "QPR": ("queens park rangers", "qpr"),
    "Coventry": ("coventry city", "coventry"),
    "West Brom": ("west bromwich albion", "west brom"),
    "Wrexham": ("wrexham",),
    "Lyon": ("lyon",),
    "Sabah": ("sabah",),
}

# alias -> canonical, longest first so "west ham united" beats "west ham".
_ALIAS_INDEX: list[tuple[str, str]] = sorted(
    ((alias, name) for name, aliases in CLUBS.items() for alias in aliases),
    key=lambda pair: len(pair[0]),
    reverse=True,
)
_ALIAS_LOOKUP = {alias: name for alias, name in _ALIAS_INDEX}
_ALIAS_RE = re.compile(
    r"\b(" + "|".join(re.escape(a) for a, _ in _ALIAS_INDEX) + r")\b", re.IGNORECASE
)

# Ground -> the club that plays its home games there.
VENUES: dict[str, str] = {
    "old trafford": "Manchester United",
    "etihad stadium": "Manchester City",
    "etihad": "Manchester City",
    "stamford bridge": "Chelsea",
    "villa park": "Aston Villa",
    "riverside stadium": "Middlesbrough",
    "riverside": "Middlesbrough",
    "ewood park": "Blackburn",
    "st mary's stadium": "Southampton",
    "st mary's": "Southampton",
    "pride park": "Derby",
    "hill dickinson stadium": "Everton",
    "goodison park": "Everton",
    "amex stadium": "Brighton",
    "amex": "Brighton",
    "selhurst park": "Crystal Palace",
    "craven cottage": "Fulham",
    "emirates stadium": "Arsenal",
    "anfield": "Liverpool",
    "st james' park": "Newcastle",
    "vitality stadium": "Bournemouth",
    "mkm stadium": "Hull",
    "kcom stadium": "Hull",
    "london stadium": "West Ham",
    "tottenham hotspur stadium": "Tottenham",
    "bramall lane": "Sheffield United",
    "carrow road": "Norwich",
    "vicarage road": "Watford",
    "bet365 stadium": "Stoke",
    "liberty stadium": "Swansea",
    "the valley": "Charlton",
    "fratton park": "Portsmouth",
    "ashton gate": "Bristol City",
    "st andrew's": "Birmingham",
    "turf moor": "Burnley",
    "molineux": "Wolves",
    "city ground": "Nottingham Forest",
    "elland road": "Leeds",
    "stadium of light": "Sunderland",
}

# Club -> the ground it plays its home games on, as we spell it in the UI. Used
# to name the venue of United's away fixtures, which the season article leaves
# blank. Clubs we have no reliable ground for are simply omitted.
GROUNDS: dict[str, str] = {
    "Manchester United": "Old Trafford",
    "Arsenal": "Emirates Stadium",
    "Aston Villa": "Villa Park",
    "Bournemouth": "Vitality Stadium",
    "Brentford": "Gtech Community Stadium",
    "Brighton": "Amex Stadium",
    "Chelsea": "Stamford Bridge",
    "Coventry": "Coventry Building Society Arena",
    "Crystal Palace": "Selhurst Park",
    "Everton": "Hill Dickinson Stadium",
    "Fulham": "Craven Cottage",
    "Hull": "MKM Stadium",
    "Ipswich": "Portman Road",
    "Leeds": "Elland Road",
    "Liverpool": "Anfield",
    "Manchester City": "Etihad Stadium",
    "Newcastle": "St James' Park",
    "Nottingham Forest": "City Ground",
    "Sunderland": "Stadium of Light",
    "Tottenham": "Tottenham Hotspur Stadium",
}

_YOUTH_RE = re.compile(r"\b(U\d{2}s?|Academy|Women|Youth|Reserves?)\b", re.IGNORECASE)

_COMPETITIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Champions League", ("champions league",)),
    ("Europa League", ("europa league",)),
    ("Conference League", ("conference league",)),
    ("FA Cup", ("fa cup",)),
    ("Carabao Cup", ("carabao cup", "efl cup", "league cup")),
    ("Premier League", ("premier league",)),
    ("Championship", ("championship",)),
)

_SCORE_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*[-\u2013]\s*(\d{1,2})(?![\d.])")
_SLUG_RE = re.compile(
    r"/football/([a-z0-9\-]+?)-(?:vs|v)-([a-z0-9\-]+?)/(?:report|live|match|game|highlights)",
    re.IGNORECASE,
)

_WIN_VERBS = (
    "beat", "beats", "beating",
    "win", "wins", "won", "winning",
    "victory", "victories", "success", "triumph",
    "edge", "edges", "edged", "edging",
    "hammer", "hammers", "hammered", "hammering",
    "thrash", "thrashes", "thrashed", "thrashing",
    "secure", "secures", "secured", "securing",
    "see off", "sees off", "saw off",
    "ease past", "eases past", "eased past",
    "overcome", "overcomes", "overcame",
    "sink", "sinks", "sank", "sunk",
    "claim", "claims", "claimed",
)
_LOSS_VERBS = (
    "defeat", "defeats", "defeated", "defeating",
    "loss", "losses", "lose", "loses", "lost", "losing",
    "slump", "slumps", "slumped", "slumping",
    "suffer", "suffers", "suffered", "suffering",
    "fall", "falls", "fell", "fallen",
    "slip", "slips", "slipped", "slipping",
    "beaten",
    "stumble", "stumbles", "stumbled",
)
_DRAW_VERBS = (
    "draw", "draws", "drew", "drawn", "drawing",
    "held", "hold", "holds", "holding",
    "share", "shares", "shared", "sharing",
    "stalemate", "stalemates",
    "level", "levels", "levelled", "leveled", "levelling",
)


def _verb_re(verbs: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"\b(" + "|".join(re.escape(v) for v in verbs) + r")\b", re.IGNORECASE)


_WIN_RE = _verb_re(_WIN_VERBS)
_LOSS_RE = _verb_re(_LOSS_VERBS)
_DRAW_RE = _verb_re(_DRAW_VERBS)


def canonical(alias: str) -> str | None:
    return _ALIAS_LOOKUP.get(alias.lower())


def mentions(text: str) -> list[tuple[int, str]]:
    """Every club referenced in ``text`` as ``(index, canonical_name)``."""
    found: list[tuple[int, str]] = []
    for match in _ALIAS_RE.finditer(text or ""):
        name = canonical(match.group(1))
        if name:
            found.append((match.start(), name))
    return found


def club_in(text: str) -> str | None:
    """First club named in ``text``."""
    found = mentions(text)
    return found[0][1] if found else None


def _last_verb(regex: re.Pattern[str], text: str, before: int) -> int:
    """Index of the last ``regex`` match starting before ``before``."""
    best = -1
    for match in regex.finditer(text):
        if match.start() < before:
            best = match.start()
        else:
            break
    return best


# --------------------------------------------------------------------------- #
# Competition + venue detection
# --------------------------------------------------------------------------- #


def detect_competition(*parts: Any) -> str:
    """Best-effort real competition label from title/summary/tags."""
    haystack = " ".join(str(p) for p in parts if p).lower()
    for label, needles in _COMPETITIONS:
        if any(needle in haystack for needle in needles):
            return label
    return "League"


def detect_venue(text: str) -> str | None:
    lowered = (text or "").lower()
    venue = next(
        (v for v in sorted(VENUES, key=len, reverse=True) if v in lowered), None
    )
    return venue.title() if venue else None


# --------------------------------------------------------------------------- #
# Result parsing
# --------------------------------------------------------------------------- #


def _slug_club(slug: str) -> str | None:
    tokens = slug.replace("-", " ").split()
    for size in range(len(tokens), 0, -1):
        name = canonical(" ".join(tokens[:size]))
        if name:
            return name
    for size in range(len(tokens), 0, -1):
        name = canonical(" ".join(tokens[-size:]))
        if name:
            return name
    return None


def _slug_pair(url: str | None) -> tuple[str | None, str | None]:
    if not url:
        return None, None
    match = _SLUG_RE.search(url)
    if not match:
        return None, None
    home, away = _slug_club(match.group(1)), _slug_club(match.group(2))
    if home and away and home != away:
        return home, away
    return None, None


def _leftmost_club(text: str) -> str | None:
    found = mentions(text)
    return found[0][1] if found else None


def _rightmost_club(text: str) -> str | None:
    found = mentions(text)
    return found[-1][1] if found else None


def _title_pair(title: str) -> tuple[str, str, int, int] | None:
    """``Manchester United 4-0 Sabah`` style headline -> clubs and scores."""
    match = _SCORE_RE.search(title or "")
    if not match:
        return None
    home = _rightmost_club(title[: match.start()])
    away = _leftmost_club(title[match.end() :])
    if not home or not away or home == away:
        return None
    return home, away, int(match.group(1)), int(match.group(2))


def _decide_winner(home: str, away: str, text: str, score_at: int) -> str | None:
    """Which of ``home``/``away`` won, from the reporting verb (``None`` = draw)."""
    win_at = _last_verb(_WIN_RE, text, score_at)
    loss_at = _last_verb(_LOSS_RE, text, score_at)
    draw_at = _last_verb(_DRAW_RE, text, score_at)
    governing = max(win_at, loss_at, draw_at)
    if governing < 0:
        return None
    if governing == draw_at and draw_at >= win_at and draw_at >= loss_at:
        return None
    found = mentions(text)
    if governing == win_at:
        winner = next((c for i, c in reversed(found) if i < win_at and c in (home, away)), None)
        loser = next(
            (c for i, c in found if i > win_at and c in (home, away) and c != winner), None
        )
        if winner:
            return winner
        if loser:
            return away if loser == home else home
    else:
        loser = next((c for i, c in reversed(found) if i < loss_at and c in (home, away)), None)
        if not loser:
            loser = next((c for i, c in found if i > loss_at and c in (home, away)), None)
        if loser:
            return away if loser == home else home
    return None


def parse_result(item: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a real result from a news item, or ``None`` if there isn't one.

    Orientation comes from the headline (``Home 4-0 Away``) or the article URL
    slug (``.../home-vs-away/report/...``); the winner comes from the reporting
    verb in the summary.
    """
    title = item.get("title") or ""
    summary = item.get("summary") or ""
    if _YOUTH_RE.search(title):
        return None

    home = away = None
    home_score = away_score = None

    titled = _title_pair(title)
    if titled:
        home, away, home_score, away_score = titled

    if home_score is None:
        pair = _slug_pair(item.get("source_url") or item.get("guid") or "")
        if pair[0] and pair[1]:
            home, away = pair
        if home and away:
            text = f"{title}. {summary}"
            match = _SCORE_RE.search(text)
            if not match:
                return None
            first, second = int(match.group(1)), int(match.group(2))
            if first == second:
                home_score = away_score = first
            else:
                winner = _decide_winner(home, away, text, match.start())
                hi, lo = max(first, second), min(first, second)
                if winner == away:
                    home_score, away_score = lo, hi
                else:
                    home_score, away_score = hi, lo

    if home_score is None or not home or not away:
        return None
    if home_score >= 5 or away_score >= 5 or home_score + away_score > 9:
        return None

    return {
        "competition": detect_competition(title, summary, *(item.get("tags") or [])),
        "home": home,
        "away": away,
        "homeScore": home_score,
        "awayScore": away_score,
        "date": item.get("published_at"),
    }


def parse_fixture(item: dict[str, Any]) -> dict[str, Any] | None:
    """Extract an upcoming fixture pairing from a preview headline."""
    title = item.get("title") or ""
    if _YOUTH_RE.search(title):
        return None
    match = re.search(
        r"([A-Z][\w'&.\-]+(?: [\w'&.\-]+){0,3}?)\s+(?:vs\.?|v)\s+([A-Z][\w'&.\-]+(?: [\w'&.\-]+){0,3})",
        title,
    )
    if not match:
        return None
    home = _rightmost_club(match.group(1))
    away = _leftmost_club(match.group(2))
    if not home or not away or home == away:
        return None
    summary = item.get("summary") or ""
    return {
        "competition": detect_competition(title, summary, *(item.get("tags") or [])),
        "home": home,
        "away": away,
        "date": item.get("published_at"),
        "venue": detect_venue(f"{title} {summary}"),
    }


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def _parse_date(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _pretty_date(value: Any) -> str:
    moment = _parse_date(value)
    if moment.year == 1970:
        return "Date TBC"
    return moment.strftime("%d %b").upper()


def _has_united(match: dict[str, Any]) -> bool:
    return "Manchester United" in (match.get("home"), match.get("away"))


def derive_results(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Real, deduplicated results; United's matches first, then most recent."""
    seen: set[tuple[str, str, int, int]] = set()
    results: list[dict[str, Any]] = []
    for item in items:
        parsed = parse_result(item)
        if not parsed:
            continue
        key = (parsed["home"], parsed["away"], parsed["homeScore"], parsed["awayScore"])
        if key in seen:
            continue
        seen.add(key)
        results.append(parsed)
    results.sort(
        key=lambda r: (_has_united(r), _parse_date(r["date"])),
        reverse=True,
    )
    for index, row in enumerate(results, start=1):
        row["id"] = 100 + index
        row["status"] = "result"
        row["date"] = _pretty_date(row["date"])
    return results


def derive_fixtures(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Upcoming fixture pairings parsed from preview headlines."""
    seen: set[tuple[str, str]] = set()
    fixtures: list[dict[str, Any]] = []
    for item in items:
        parsed = parse_fixture(item)
        if not parsed:
            continue
        key = (parsed["home"], parsed["away"])
        if key in seen:
            continue
        seen.add(key)
        fixtures.append(parsed)
    fixtures.sort(key=lambda f: _parse_date(f["date"]), reverse=True)
    for index, row in enumerate(fixtures, start=1):
        row["id"] = index
        row["status"] = "upcoming"
        row["date"] = _pretty_date(row["date"])
        if not row.get("venue"):
            row.pop("venue", None)
    return fixtures


# The fixtures and results panels show the whole season: every match United
# have played and every one they have still to play, all of it from the season
# article rather than a truncated window.


_SEASON_LABEL_RE = re.compile(r"(\d{4})\s*[\u2013\u2014-]\s*(\d{2,4})")
_UNITED_OUTCOME = {"W": "win", "D": "draw", "L": "loss"}


def _season_label() -> str:
    """``2026–27 Manchester United F.C. season`` -> ``2026/27``."""
    match = _SEASON_LABEL_RE.search(settings.hub_season_page or "")
    if not match:
        return ""
    return f"{match.group(1)}/{match.group(2)[-2:]}"


def _ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _schedule_venue(match: wikipedia.MatchRow, opponent: str) -> str | None:
    if match.neutral:
        return match.venue
    if match.home:
        return GROUNDS["Manchester United"]
    return GROUNDS.get(opponent)


def derive_schedule(
    matches: list[wikipedia.MatchRow],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """United's real season, split into played results and upcoming fixtures.

    The season article publishes one table per competition and always writes
    United's score first, whichever ground the match was played on, so each row
    is re-oriented around the club that was actually at home. A filled-in score
    means the match has been played; an empty one means it is still to come.
    """
    played: list[tuple[date, dict[str, Any]]] = []
    upcoming: list[tuple[date, dict[str, Any]]] = []

    for match in matches:
        opponent = _canonical_team(match.opponent)
        united_home = match.home
        home, away = (
            ("Manchester United", opponent) if united_home else (opponent, "Manchester United")
        )
        row: dict[str, Any] = {
            "competition": match.competition,
            "date": match.date.strftime("%d %b %Y").upper(),
            "home": home,
            "away": away,
        }
        venue = _schedule_venue(match, opponent)
        if venue:
            row["venue"] = venue
        if match.round:
            row["round"] = match.round
        if not match.played:
            row["status"] = "upcoming"
            upcoming.append((match.date, row))
            continue
        row["status"] = "result"
        row["homeScore"], row["awayScore"] = (
            (match.united_goals, match.opponent_goals)
            if united_home
            else (match.opponent_goals, match.united_goals)
        )
        row["outcome"] = _UNITED_OUTCOME.get(match.outcome, "draw")
        if match.scorers:
            row["scorers"] = match.scorers
        if match.attendance:
            row["attendance"] = match.attendance
        if match.position:
            row["position"] = match.position
        played.append((match.date, row))

    played.sort(key=lambda pair: pair[0], reverse=True)
    upcoming.sort(key=lambda pair: pair[0])

    results = [row for _, row in played]
    fixtures = [row for _, row in upcoming]
    for index, row in enumerate(results, start=1):
        row["id"] = 100 + index
    for index, row in enumerate(fixtures, start=1):
        row["id"] = 200 + index
    return results, fixtures


def _canonical_team(name: str) -> str:
    """Map a Wikipedia / publisher club name onto our canonical club name.

    ``West Ham United`` -> ``West Ham``, ``Brighton & Hove Albion`` ->
    ``Brighton``. Names we do not know are returned unchanged.
    """
    key = " ".join(name.lower().split())
    if not key:
        return name
    canonical = _ALIAS_LOOKUP.get(key)
    if canonical:
        return canonical
    for alias, canonical in _ALIAS_INDEX:
        if re.search(rf"\b{re.escape(alias)}\b", key):
            return canonical
    return name.strip()


def derive_table(rows: list[wikipedia.StandingRow]) -> list[dict[str, Any]]:
    """Canonicalise Wikipedia's league table into the hub's standings shape."""
    table: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        team = _canonical_team(row.team)
        table.append(
            {
                **row.as_dict(),
                "team": team,
                "pos": index,
                "isUnited": team == "Manchester United",
            }
        )
    return table


def _with_form(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add each club's last five results, when ESPN has answered for them.

    ESPN names clubs the way the league does, so both sides of the lookup go
    through :func:`_canonical_team` before they meet. The form cache is only
    read, never fetched: the column is a nicety, and 20 ESPN requests have no
    business holding up a hub response.
    """
    form = form_service.cached_form()
    if not rows or not form:
        return rows
    by_club = {_canonical_team(name): value for name, value in form.items()}
    return [
        {**row, "form": by_club.get(_canonical_team(str(row.get("team") or "")))}
        for row in rows
    ]


def derive_standings(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback mini-table built only from Premier League results we parsed.

    Used when Wikipedia is unavailable, so the panel still shows something
    real rather than a fabricated table.
    """
    table: dict[str, dict[str, int]] = {}

    def bucket(team: str) -> dict[str, int]:
        return table.setdefault(
            team, {"played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0, "pts": 0}
        )

    for row in results:
        if row.get("competition") != "Premier League":
            continue
        home, away = bucket(row["home"]), bucket(row["away"])
        hs, as_ = row["homeScore"], row["awayScore"]
        for side, scored, conceded in ((home, hs, as_), (away, as_, hs)):
            side["played"] += 1
            side["gf"] += scored
            side["ga"] += conceded
        if hs > as_:
            home["won"] += 1
            home["pts"] += 3
            away["lost"] += 1
        elif hs < as_:
            away["won"] += 1
            away["pts"] += 3
            home["lost"] += 1
        else:
            home["drawn"] += 1
            away["drawn"] += 1
            home["pts"] += 1
            away["pts"] += 1

    rows = [
        {"team": team, **stats, "isUnited": team == "Manchester United"}
        for team, stats in table.items()
    ]
    rows.sort(key=lambda r: (-r["pts"], -(r["gf"] - r["ga"]), -r["gf"], r["team"]))
    for index, row in enumerate(rows, start=1):
        row["pos"] = index
    return rows


def _short_name(full: str) -> str:
    parts = full.split()
    if len(parts) < 2:
        return full
    return f"{parts[0][0]}. {parts[-1]}"


def _united_goals(results: list[dict[str, Any]]) -> tuple[int, int]:
    """United's goals for and against across the matches we have."""
    scored = conceded = 0
    for row in results:
        if not _has_united(row):
            continue
        united_home = row.get("home") == "Manchester United"
        scored += row["homeScore"] if united_home else row["awayScore"]
        conceded += row["awayScore"] if united_home else row["homeScore"]
    return scored, conceded


def _united_position(
    results: list[dict[str, Any]], standings: list[dict[str, Any]]
) -> str | None:
    """United's league position, from the live table or the last match's cell."""
    united_row = next((row for row in standings if row.get("isUnited")), None)
    if united_row and isinstance(united_row.get("pos"), int):
        return _ordinal(united_row["pos"])
    return next((r["position"] for r in results if r.get("position")), None)


def derive_overview(
    results: list[dict[str, Any]],
    standings: list[dict[str, Any]],
    squad: list[wikipedia.SquadMember],
) -> dict[str, Any]:
    """United's season so far plus the club's actual top scorers."""
    played = [r for r in results if _has_united(r)]
    scored, conceded = _united_goals(played)
    season = _season_label()
    stats = [
        {
            "value": len(played),
            "label": "Matches Played",
            "sub": f"{season} season" if season else "this season",
        },
        {
            "value": scored,
            "label": "Goals Scored",
            "sub": f"{conceded} conceded",
        },
        {
            "value": _united_position(played, standings) or "—",
            "label": "League Position",
            "sub": "Premier League",
        },
        {
            "value": len(squad),
            "label": "Squad Players",
            "sub": "current season",
        },
    ]
    scorers = [
        {
            "name": _short_name(member.name),
            "goals": member.goals,
            "apps": member.apps,
        }
        for member in sorted(squad, key=lambda m: (-m.goals, -m.apps))
        if member.goals > 0
    ][:5]
    return {"stats": stats, "top_scorers": scorers}


_POSITION_LABEL = {"GK": "Goalkeeper", "DF": "Defender", "MF": "Midfielder", "FW": "Forward"}


def _player_image(member: wikipedia.SquadMember) -> str | None:
    """The player's cutout portrait, matched by squad name."""
    return PLAYER_IMAGES.get(member.name)


def derive_compare(squad: list[wikipedia.SquadMember]) -> dict[str, Any]:
    ranked = sorted(squad, key=lambda m: (-m.goals, -m.apps))
    if len(ranked) < 2:
        return {}
    a, b = ranked[0], ranked[1]

    def player(member: wikipedia.SquadMember) -> dict[str, Any]:
        position = _POSITION_LABEL.get(member.position, member.position)
        number = f" #{member.number}" if member.number is not None else ""
        return {
            "name": member.name,
            "role": f"{position}{number}",
            "color": "#FF2616",
            "image": _player_image(member),
        }

    return {
        "players": {"a": player(a), "b": player(b)},
        "stats": [
            {"label": "Appearances", "a": a.apps, "b": b.apps},
            {"label": "Goals", "a": a.goals, "b": b.goals},
            {"label": "League Appearances", "a": a.league_apps, "b": b.league_apps},
            {"label": "League Goals", "a": a.league_goals, "b": b.league_goals},
            {"label": "Cup Goals", "a": a.cup_goals, "b": b.cup_goals},
            {"label": "Yellow Cards", "a": a.yellow, "b": b.yellow},
            {"label": "Red Cards", "a": a.red, "b": b.red},
        ],
    }


def derive_squad(squad: list[wikipedia.SquadMember]) -> list[dict[str, Any]]:
    return [
        {
            "number": member.number,
            "name": member.name,
            "position": member.position,
            "apps": member.apps,
            "goals": member.goals,
            "league_apps": member.league_apps,
            "league_goals": member.league_goals,
            "cup_goals": member.cup_goals,
            "yellow": member.yellow,
            "red": member.red,
            "image": _player_image(member),
        }
        for member in sorted(
            squad,
            key=lambda m: (
                {"GK": 0, "DF": 1, "MF": 2, "FW": 3}.get(m.position, 4),
                m.number if m.number is not None else 999,
            ),
        )
    ]


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def derive_hero(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the United image pool the hub banner draws from.

    The banner used to be one hard-coded picture. Instead we offer every
    United-relevant article that carries an ``https`` image, ranked by the same
    relevance score the feed uses, so the biggest image on the page is always
    about the club and always fresh. The client shows one entry per calendar
    day, so the banner reads the same for every visitor.
    """
    scored: list[tuple[float, dict[str, Any]]] = []
    seen: set[str] = set()
    for item in items:
        image = str(item.get("image") or "").strip()
        if not image.startswith("https://") or image in seen:
            continue
        analysis = feed_rank.analyse(item)
        if not analysis.united and len(scored) >= 2:
            # Allow a couple of fallbacks on a cold corpus, but prefer United.
            continue
        seen.add(image)
        scored.append(
            (
                analysis.score,
                {
                    "id": item.get("id"),
                    "slug": item.get("slug"),
                    "image": image,
                    "title": item.get("title"),
                    "source": item.get("source"),
                    "category": item.get("category"),
                    "published_at": item.get("published_at"),
                },
            )
        )
    scored.sort(key=lambda row: row[0], reverse=True)
    slides = [row[1] for row in scored[:HERO_POOL_SIZE]]
    if not slides:
        return {}
    return {"slides": slides}

_NEWS_PROJECTION = {
    "_id": 0,
    "id": 1,
    "slug": 1,
    "image": 1,
    "category": 1,
    "title": 1,
    "summary": 1,
    "tags": 1,
    "source": 1,
    "published_at": 1,
    "source_url": 1,
    "guid": 1,
    "status": 1,
}

# How many United images make up the banner pool the client draws from.
HERO_POOL_SIZE = 8


def build_hub(database) -> dict[str, Any]:
    """Compose the public hub payload from real data.

    Each derived section falls back to the stored (seeded/admin-edited) section
    when the derivation yields too little to be useful, so the UI is never
    empty. Admin overrides always win.
    """
    stored = {
        doc["key"]: doc.get("data")
        for doc in database[db_module.HUB].find({}, {"_id": 0, "key": 1, "data": 1})
    }
    overrides = {
        doc["key"]
        for doc in database[db_module.HUB].find({"override": True}, {"_id": 0, "key": 1})
    }

    items = list(
        database[db_module.NEWS]
        .find({"status": "Published"}, _NEWS_PROJECTION)
        .sort("published_at", -1)
        .limit(200)
    )

    # The hub is a Manchester United page: the fixtures and results panels show
    # only United's matches, taken from Wikipedia's season article. Parsing
    # them out of news headlines is kept as a fallback, because headlines are
    # unreliable - the same match turns up as both a result and a fixture, with
    # the wrong competition - but it is never the first choice.
    schedule_rows = wikipedia.fetch_matches()
    schedule_meta: dict[str, Any] = {}
    if schedule_rows:
        united_results, united_fixtures = derive_schedule(schedule_rows)
        standings_source = united_results
        schedule_meta = dict(wikipedia.matches_meta())
    else:
        parsed_results = derive_results(items)
        parsed_fixtures = derive_fixtures(items)
        united_results = [row for row in parsed_results if _has_united(row)]
        united_fixtures = [row for row in parsed_fixtures if _has_united(row)]
        standings_source = parsed_results
        schedule_meta = {"source": "Aggregated from the news feed", "updated_at": None}
    schedule_meta["fixtures_total"] = len(united_fixtures)

    wikipedia_rows = wikipedia.fetch_standings()
    derived_standings = derive_table(wikipedia_rows)
    if derived_standings:
        standings = derived_standings
        standings_meta = wikipedia.standings_meta()
    else:
        standings = derive_standings(standings_source)
        standings_meta = {}
    squad_members = wikipedia.fetch_squad()
    squad = derive_squad(squad_members)
    overview = derive_overview(united_results, standings, squad_members)
    compare = derive_compare(squad_members)
    hero = derive_hero(items)

    def pick(key: str, derived: Any, *, minimum: int = 1) -> Any:
        if key in overrides:
            return stored.get(key)
        if derived and (not isinstance(derived, (list, dict)) or len(derived) >= minimum):
            return derived
        return stored.get(key)

    overview_result = pick("overview", overview)
    if (
        "overview" not in overrides
        and not (overview_result or {}).get("top_scorers")
        and (stored.get("overview") or {}).get("top_scorers")
    ):
        # Squad stats were unavailable (e.g. Wikipedia disabled) - keep the
        # stored scorers rather than showing an empty list.
        overview_result = {
            **(overview_result or {}),
            "top_scorers": stored["overview"]["top_scorers"],
        }

    chosen_standings = pick("standings", standings, minimum=4)
    if chosen_standings is not standings:
        # The panel is showing a stored snapshot rather than anything we just
        # fetched, so don't attribute it to a live source.
        standings_meta = {"source": "SimplyUtd snapshot", "updated_at": None}
    elif not standings_meta and chosen_standings:
        standings_meta = {"source": "Aggregated from the news feed", "updated_at": None}
    chosen_standings = _with_form(chosen_standings)

    return {
        "hero": pick("hero", hero),
        "overview": overview_result,
        "fixtures": pick("fixtures", united_fixtures),
        "results": pick("results", united_results),
        "standings": chosen_standings,
        "standings_meta": standings_meta,
        "schedule_meta": schedule_meta,
        "compare": pick("compare", compare),
        "squad": pick("squad", squad, minimum=5),
    }


def section(database, key: str) -> Any:
    return build_hub(database).get(key)


def revalidate() -> None:
    """Refetch the season schedule and table off the request path when stale.

    A visitor then always reads the cached payload, and the next request - the
    poll that follows, or simply the next visit - paints the new result without
    anyone waiting on Wikipedia. Guarded so a burst of panel reads triggers one
    refresh, and it never raises: a failed read leaves the cached one in place
    and is retried by the next visitor or the scheduled refresher.
    """
    global _revalidating
    if _revalidating or not settings.hub_wikipedia_enabled:
        return
    if not wikipedia.schedule_stale():
        return
    _revalidating = True
    try:
        wikipedia.fetch_matches(force=True)
        wikipedia.fetch_standings(force=True)
    except Exception:  # noqa: BLE001 - a refresh must never break a response
        logger.exception("Scheduled hub revalidation failed")
    finally:
        _revalidating = False


async def hub_refresher() -> None:
    """Keep the league table and fixture list warm so no request waits on Wikipedia.

    These are the parts of the hub that visibly go stale, so they are polled on
    ``HUB_REFRESH_SECONDS`` and written into the module caches with
    ``force=True``. Requests then always read a fresh, already-computed payload -
    "assisted by the algorithm" rather than fetched on the critical path.
    """
    if not settings.hub_wikipedia_enabled:
        logger.info("Hub refresher disabled (Wikipedia off)")
        return

    interval = max(60, settings.hub_refresh_seconds)
    # Let startup (seed + index creation) settle first.
    await asyncio.sleep(5)
    try:
        # Nothing on the request path fetches form, so the first pass fills the
        # cache the table's "Last 5" column reads; later passes only re-read it
        # once its (much longer) interval has passed.
        await asyncio.to_thread(form_service.fetch_form)
        logger.info("Refreshed last-5 form (%d clubs)", len(form_service.cached_form()))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the loop must survive any failure
        logger.exception("Scheduled form refresh failed")
    while True:
        try:
            rows = await asyncio.to_thread(wikipedia.fetch_standings, True)
            logger.info("Refreshed Premier League table (%d rows)", len(rows))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive any failure
            logger.exception("Scheduled hub refresh failed")
        try:
            matches = await asyncio.to_thread(wikipedia.fetch_matches, True)
            logger.info("Refreshed the season schedule (%d matches)", len(matches))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive any failure
            logger.exception("Scheduled hub refresh failed")
        try:
            await asyncio.to_thread(form_service.fetch_form)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive any failure
            logger.exception("Scheduled form refresh failed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
