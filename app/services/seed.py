"""Idempotent seed data.

Mirrors the frontend's static content (hub data, store products, contact
content) so the SPA can be powered entirely by the API. Hub sections are stored
one document per section in the ``hub`` collection; store products and a couple
of starter news items are inserted only when their collections are empty.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from pymongo.database import Database

from .. import db as db_module
from ..utils import new_id, slugify, utcnow
from .comment_service import seed_demo_comments

logger = logging.getLogger("simplyutd.seed")

TEAM_LOGOS: dict[str, str] = {
    "Arsenal": "https://r2.thesportsdb.com/images/media/team/badge/uyhbfe1612467038.png",
    "Aston Villa": "https://r2.thesportsdb.com/images/media/team/badge/uwzw561787679026.png",
    "Birmingham": "https://r2.thesportsdb.com/images/media/team/badge/wufs551672950865.png",
    "Blackburn": "https://r2.thesportsdb.com/images/media/team/badge/rvryut1448810814.png",
    "Bournemouth": "https://r2.thesportsdb.com/images/media/team/badge/y08nak1534071116.png",
    "Brentford": "https://r2.thesportsdb.com/images/media/team/badge/grv1aw1546453779.png",
    "Brighton": "https://r2.thesportsdb.com/images/media/team/badge/ywypts1448810904.png",
    "Bristol City": "https://r2.thesportsdb.com/images/media/team/badge/0ejxwz1601721013.png",
    "Burnley": "https://r2.thesportsdb.com/images/media/team/badge/ql7nl31686893820.png",
    "Charlton": "https://r2.thesportsdb.com/images/media/team/badge/o08wvi1635872307.png",
    "Chelsea": "https://r2.thesportsdb.com/images/media/team/badge/pbf4ul1782638263.png",
    "Coventry": "https://r2.thesportsdb.com/images/media/team/badge/uxyqys1424033798.png",
    "Crystal Palace": "https://r2.thesportsdb.com/images/media/team/badge/ia6i3m1656014992.png",
    "Derby": "https://r2.thesportsdb.com/images/media/team/badge/jioo4z1557155744.png",
    "Everton": "https://r2.thesportsdb.com/images/media/team/badge/eqayrf1523184794.png",
    "Fulham": "https://r2.thesportsdb.com/images/media/team/badge/xwwvyt1448811086.png",
    "Hull": "https://r2.thesportsdb.com/images/media/team/badge/fbqqda1601726113.png",
    "Ipswich": "https://r2.thesportsdb.com/images/media/team/badge/mdj1ey1634670785.png",
    "Leeds": "https://r2.thesportsdb.com/images/media/team/badge/jcgrml1756649030.png",
    "Leicester": "https://r2.thesportsdb.com/images/media/team/badge/xtxwtu1448813356.png",
    "Liverpool": "https://r2.thesportsdb.com/images/media/team/badge/kfaher1737969724.png",
    "Lyon": "https://r2.thesportsdb.com/images/media/team/badge/blk9771656932845.png",
    "Manchester City": "https://r2.thesportsdb.com/images/media/team/badge/vwpvry1467462651.png",
    "Manchester United": "https://r2.thesportsdb.com/images/media/team/badge/xzqdr11517660252.png",
    "Middlesbrough": "https://r2.thesportsdb.com/images/media/team/badge/advjg71780068902.png",
    "Millwall": "https://r2.thesportsdb.com/images/media/team/badge/5p1z5k1537208888.png",
    "Newcastle": "https://r2.thesportsdb.com/images/media/team/badge/lhwuiz1621593302.png",
    "Norwich": "https://r2.thesportsdb.com/images/media/team/badge/pabczm1679951464.png",
    "Nottingham Forest": "https://r2.thesportsdb.com/images/media/team/badge/1i2kvh1719918076.png",
    "Portsmouth": "https://r2.thesportsdb.com/images/media/team/badge/j13pfe1601726274.png",
    "Preston": "https://r2.thesportsdb.com/images/media/team/badge/wqtwvw1448811512.png",
    "QPR": "https://r2.thesportsdb.com/images/media/team/badge/l4qscx1601721022.png",
    "Sheffield United": "https://r2.thesportsdb.com/images/media/team/badge/w7f8pj1672950689.png",
    "Sheffield Wednesday": "https://r2.thesportsdb.com/images/media/team/badge/m3km7p1781284189.png",
    "Southampton": "https://r2.thesportsdb.com/images/media/team/badge/ggqtd01621593274.png",
    "Stoke": "https://r2.thesportsdb.com/images/media/team/badge/lz89v21781718914.png",
    "Sunderland": "https://r2.thesportsdb.com/images/media/team/badge/tprtus1448813498.png",
    "Swansea": "https://r2.thesportsdb.com/images/media/team/badge/474rco1686920744.png",
    "Tottenham": "https://r2.thesportsdb.com/images/media/team/badge/dfyfhl1604094109.png",
    "Watford": "https://r2.thesportsdb.com/images/media/team/badge/rsuswy1448813519.png",
    "West Brom": "https://r2.thesportsdb.com/images/media/team/badge/rsvuxw1448813527.png",
    "West Ham": "https://r2.thesportsdb.com/images/media/team/badge/yutyxs1467459956.png",
    "Wolves": "https://r2.thesportsdb.com/images/media/team/badge/u9qr031621593327.png",
    "Wrexham": "https://r2.thesportsdb.com/images/media/team/badge/ezpymt1675092551.png",
}

# Player cutout portraits, keyed by the squad name used in the season
# article (accented spellings included). Same idea as ``TEAM_LOGOS``: the
# hub shows a real face rather than a silhouette.
PLAYER_IMAGES: dict[str, str] = {
    "Senne Lammens": "https://www.thesportsdb.com/images/media/player/cutout/oeha4x1789119701.png",
    "Karl Darlow": "https://www.thesportsdb.com/images/media/player/cutout/ago1y81789120011.png",
    "Tom Heaton": "https://www.thesportsdb.com/images/media/player/cutout/pnh5vp1789120296.png",
    "Dermot Mee": "https://www.thesportsdb.com/images/media/player/cutout/m9zetc1789120669.png",
    "Diogo Dalot": "https://www.thesportsdb.com/images/media/player/cutout/ljpt7b1789119760.png",
    "Noussair Mazraoui": "https://www.thesportsdb.com/images/media/player/cutout/e0ud5w1789119792.png",
    "Matthijs de Ligt": "https://www.thesportsdb.com/images/media/player/cutout/tunblk1789119820.png",
    "Harry Maguire": "https://www.thesportsdb.com/images/media/player/cutout/56w2px1789119842.png",
    "Lisandro Mart\u00ednez": "https://www.thesportsdb.com/images/media/player/cutout/gfwp7z1789119870.png",
    "Patrick Dorgu": "https://www.thesportsdb.com/images/media/player/cutout/3nvnrs1789120036.png",
    "Leny Yoro": "https://www.thesportsdb.com/images/media/player/cutout/qy4qlx1789120067.png",
    "Luke Shaw": "https://www.thesportsdb.com/images/media/player/cutout/n5e6mm1789120319.png",
    "Ayden Heaven": "https://www.thesportsdb.com/images/media/player/cutout/8wxw2y1789120411.png",
    "Harry Amass": "https://www.thesportsdb.com/images/media/player/cutout/azc1701789120597.png",
    "Mason Mount": "https://www.thesportsdb.com/images/media/player/cutout/oeihjk1789119902.png",
    "Bruno Fernandes": "https://www.thesportsdb.com/images/media/player/cutout/utrk0y1789119923.png",
    "Andrey Santos": "https://www.thesportsdb.com/images/media/player/cutout/tc6lcf1789120127.png",
    "Youri Tielemans": "https://www.thesportsdb.com/images/media/player/cutout/5fh5l91789120153.png",
    "Carlos Baleba": "https://www.thesportsdb.com/images/media/player/cutout/lhr2y31789120270.png",
    "Manuel Ugarte": "https://www.thesportsdb.com/images/media/player/cutout/8cjw941789120390.png",
    "Kobbie Mainoo": "https://www.thesportsdb.com/images/media/player/cutout/cqc0oh1789120518.png",
    "Jack Fletcher": "https://www.thesportsdb.com/images/media/player/cutout/prt7k81789120538.png",
    "Tyler Fletcher": "https://www.thesportsdb.com/images/media/player/cutout/yw00ne1789120560.png",
    "Marcus Rashford": "https://www.thesportsdb.com/images/media/player/cutout/ucqzx51789120947.png",
    "Matheus Cunha": "https://www.thesportsdb.com/images/media/player/cutout/i9s5nj1789119955.png",
    "Joshua Zirkzee": "https://www.thesportsdb.com/images/media/player/cutout/a2hp3r1789119983.png",
    "Amad Diallo": "https://www.thesportsdb.com/images/media/player/cutout/rg6wt01789120099.png",
    "Bryan Mbeumo": "https://www.thesportsdb.com/images/media/player/cutout/ajz1961789120176.png",
    "Benjamin \u0160e\u0161ko": "https://www.thesportsdb.com/images/media/player/cutout/71xbb31789120433.png",
    "Shea Lacey": "https://www.thesportsdb.com/images/media/player/cutout/5o000d1789120769.png",
}

HUB_SECTIONS: dict[str, Any] = {
    "overview": {
        "stats": [
            {"value": 30, "label": "Squad Players", "sub": "current season"},
            {"value": 20, "label": "League Clubs", "sub": "Premier League"},
            {"value": 5, "label": "United Fixtures", "sub": "upcoming"},
            {"value": 5, "label": "Top Scorers", "sub": "on the scoresheet"},
        ],
        "top_scorers": [
            {"name": "B. Fernandes", "goals": 4, "apps": 5},
            {"name": "B. Mbeumo", "goals": 2, "apps": 5},
            {"name": "B. Šeško", "goals": 2, "apps": 5},
            {"name": "M. Cunha", "goals": 1, "apps": 5},
            {"name": "L. Martínez", "goals": 1, "apps": 4},
        ],
    },
    "fixtures": [
        {"id": 1, "competition": "Premier League", "date": "20 SEP 2026 • 15:00", "home": "Manchester United", "away": "Arsenal", "status": "upcoming", "venue": "Old Trafford"},
        {"id": 2, "competition": "Carabao Cup", "date": "24 SEP 2026 • 20:00", "home": "Brighton", "away": "Manchester United", "status": "upcoming", "venue": "Amex Stadium"},
        {"id": 3, "competition": "Premier League", "date": "27 SEP 2026 • 16:30", "home": "Manchester United", "away": "Chelsea", "status": "upcoming", "venue": "Old Trafford"},
        {"id": 4, "competition": "Europa League", "date": "2 OCT 2026 • 20:00", "home": "Lyon", "away": "Manchester United", "status": "upcoming", "venue": "Groupama Stadium"},
        {"id": 5, "competition": "Premier League", "date": "5 OCT 2026 • 15:00", "home": "Manchester United", "away": "Newcastle", "status": "upcoming", "venue": "Old Trafford"},
    ],
    "results": [
        {"id": 11, "competition": "Premier League", "date": "30 AUG 2026", "home": "Manchester United", "away": "Tottenham", "homeScore": 2, "awayScore": 0, "status": "result"},
        {"id": 12, "competition": "Premier League", "date": "23 AUG 2026", "home": "Liverpool", "away": "Manchester United", "homeScore": 1, "awayScore": 1, "status": "result"},
        {"id": 13, "competition": "Premier League", "date": "16 AUG 2026", "home": "Manchester United", "away": "Fulham", "homeScore": 3, "awayScore": 1, "status": "result"},
        {"id": 14, "competition": "Community Shield", "date": "10 AUG 2026", "home": "Manchester City", "away": "Manchester United", "homeScore": 0, "awayScore": 1, "status": "result"},
    ],
    "standings": [
        {"pos": 1, "team": "Manchester City", "played": 5, "won": 4, "drawn": 1, "lost": 0, "gf": 10, "ga": 4, "pts": 13},
        {"pos": 2, "team": "Arsenal", "played": 5, "won": 4, "drawn": 0, "lost": 1, "gf": 8, "ga": 5, "pts": 12},
        {"pos": 3, "team": "Hull", "played": 5, "won": 3, "drawn": 2, "lost": 0, "gf": 8, "ga": 4, "pts": 11},
        {"pos": 4, "team": "Brighton", "played": 5, "won": 3, "drawn": 1, "lost": 1, "gf": 16, "ga": 7, "pts": 10},
        {"pos": 5, "team": "Brentford", "played": 4, "won": 1, "drawn": 3, "lost": 0, "gf": 7, "ga": 4, "pts": 6},
        {"pos": 6, "team": "Manchester United", "played": 4, "won": 2, "drawn": 0, "lost": 2, "gf": 7, "ga": 5, "pts": 6, "isUnited": True},
        {"pos": 7, "team": "Liverpool", "played": 4, "won": 1, "drawn": 3, "lost": 0, "gf": 6, "ga": 4, "pts": 6},
        {"pos": 8, "team": "Everton", "played": 4, "won": 1, "drawn": 3, "lost": 0, "gf": 5, "ga": 3, "pts": 6},
        {"pos": 9, "team": "Newcastle", "played": 4, "won": 1, "drawn": 3, "lost": 0, "gf": 8, "ga": 7, "pts": 6},
        {"pos": 10, "team": "Ipswich", "played": 4, "won": 2, "drawn": 0, "lost": 2, "gf": 5, "ga": 8, "pts": 6},
        {"pos": 11, "team": "Sunderland", "played": 5, "won": 1, "drawn": 2, "lost": 2, "gf": 3, "ga": 5, "pts": 5},
        {"pos": 12, "team": "Chelsea", "played": 4, "won": 1, "drawn": 1, "lost": 2, "gf": 7, "ga": 8, "pts": 4},
        {"pos": 13, "team": "Bournemouth", "played": 3, "won": 0, "drawn": 3, "lost": 0, "gf": 5, "ga": 5, "pts": 3},
        {"pos": 14, "team": "Coventry", "played": 4, "won": 1, "drawn": 0, "lost": 3, "gf": 3, "ga": 11, "pts": 3},
        {"pos": 15, "team": "Leeds", "played": 2, "won": 0, "drawn": 2, "lost": 0, "gf": 2, "ga": 2, "pts": 2},
        {"pos": 16, "team": "Nottingham Forest", "played": 2, "won": 0, "drawn": 1, "lost": 1, "gf": 0, "ga": 1, "pts": 1},
        {"pos": 17, "team": "Fulham", "played": 4, "won": 0, "drawn": 1, "lost": 3, "gf": 4, "ga": 7, "pts": 1},
        {"pos": 18, "team": "Aston Villa", "played": 4, "won": 0, "drawn": 1, "lost": 3, "gf": 2, "ga": 5, "pts": 1},
        {"pos": 19, "team": "Tottenham", "played": 3, "won": 0, "drawn": 1, "lost": 2, "gf": 0, "ga": 5, "pts": 1},
        {"pos": 20, "team": "Crystal Palace", "played": 3, "won": 0, "drawn": 0, "lost": 3, "gf": 3, "ga": 9, "pts": 0},
    ],
    "compare": {
        "players": {
            "a": {"name": "Bruno Fernandes", "role": "Midfielder #8", "color": "#FF2616"},
            "b": {"name": "Bryan Mbeumo", "role": "Forward #19", "color": "#FF2616"},
        },
        "stats": [
            {"label": "Appearances", "a": 5, "b": 5},
            {"label": "Goals", "a": 4, "b": 2},
            {"label": "League Appearances", "a": 4, "b": 4},
            {"label": "League Goals", "a": 3, "b": 2},
            {"label": "Cup Goals", "a": 0, "b": 0},
            {"label": "Yellow Cards", "a": 0, "b": 1},
            {"label": "Red Cards", "a": 0, "b": 0},
        ],
    },
    "squad": [
        {"number": 1, "name": "Senne Lammens", "position": "GK", "apps": 5, "goals": 0, "league_apps": 4, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 2, "name": "Diogo Dalot", "position": "DF", "apps": 4, "goals": 0, "league_apps": 4, "league_goals": 0, "cup_goals": 0, "yellow": 1, "red": 0},
        {"number": 3, "name": "Noussair Mazraoui", "position": "DF", "apps": 4, "goals": 0, "league_apps": 3, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 4, "name": "Matthijs de Ligt", "position": "DF", "apps": 0, "goals": 0, "league_apps": 0, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 5, "name": "Harry Maguire", "position": "DF", "apps": 4, "goals": 0, "league_apps": 4, "league_goals": 0, "cup_goals": 0, "yellow": 2, "red": 0},
        {"number": 6, "name": "Lisandro Martínez", "position": "DF", "apps": 4, "goals": 1, "league_apps": 3, "league_goals": 0, "cup_goals": 1, "yellow": 0, "red": 0},
        {"number": 7, "name": "Mason Mount", "position": "MF", "apps": 1, "goals": 0, "league_apps": 0, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 8, "name": "Bruno Fernandes", "position": "MF", "apps": 5, "goals": 4, "league_apps": 4, "league_goals": 3, "cup_goals": 1, "yellow": 0, "red": 0},
        {"number": 9, "name": "Marcus Rashford", "position": "FW", "apps": 4, "goals": 0, "league_apps": 4, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 10, "name": "Matheus Cunha", "position": "FW", "apps": 5, "goals": 1, "league_apps": 4, "league_goals": 0, "cup_goals": 1, "yellow": 0, "red": 0},
        {"number": 11, "name": "Joshua Zirkzee", "position": "FW", "apps": 2, "goals": 0, "league_apps": 1, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 12, "name": "Karl Darlow", "position": "GK", "apps": 0, "goals": 0, "league_apps": 0, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 13, "name": "Patrick Dorgu", "position": "DF", "apps": 5, "goals": 0, "league_apps": 4, "league_goals": 0, "cup_goals": 0, "yellow": 2, "red": 0},
        {"number": 15, "name": "Leny Yoro", "position": "DF", "apps": 3, "goals": 0, "league_apps": 2, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 16, "name": "Amad Diallo", "position": "FW", "apps": 0, "goals": 0, "league_apps": 0, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 17, "name": "Andrey Santos", "position": "MF", "apps": 4, "goals": 0, "league_apps": 3, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 18, "name": "Youri Tielemans", "position": "MF", "apps": 5, "goals": 0, "league_apps": 4, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 19, "name": "Bryan Mbeumo", "position": "FW", "apps": 5, "goals": 2, "league_apps": 4, "league_goals": 2, "cup_goals": 0, "yellow": 1, "red": 0},
        {"number": 20, "name": "Carlos Baleba", "position": "MF", "apps": 0, "goals": 0, "league_apps": 0, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 22, "name": "Tom Heaton", "position": "GK", "apps": 0, "goals": 0, "league_apps": 0, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 23, "name": "Luke Shaw", "position": "DF", "apps": 3, "goals": 0, "league_apps": 3, "league_goals": 0, "cup_goals": 0, "yellow": 1, "red": 0},
        {"number": 25, "name": "Manuel Ugarte", "position": "MF", "apps": 0, "goals": 0, "league_apps": 0, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 26, "name": "Ayden Heaven", "position": "DF", "apps": 1, "goals": 0, "league_apps": 1, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 30, "name": "Benjamin Šeško", "position": "FW", "apps": 5, "goals": 2, "league_apps": 4, "league_goals": 1, "cup_goals": 1, "yellow": 0, "red": 0},
        {"number": 31, "name": "Shea Lacey", "position": "FW", "apps": 2, "goals": 0, "league_apps": 1, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 37, "name": "Kobbie Mainoo", "position": "MF", "apps": 5, "goals": 0, "league_apps": 4, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 38, "name": "Jack Fletcher", "position": "MF", "apps": 0, "goals": 0, "league_apps": 0, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 39, "name": "Tyler Fletcher", "position": "MF", "apps": 0, "goals": 0, "league_apps": 0, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 41, "name": "Harry Amass", "position": "DF", "apps": 1, "goals": 0, "league_apps": 0, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
        {"number": 45, "name": "Dermot Mee", "position": "GK", "apps": 0, "goals": 0, "league_apps": 0, "league_goals": 0, "cup_goals": 0, "yellow": 0, "red": 0},
    ],
    "position_order": ["GK", "DF", "MF", "FW"],
    "team_logos": TEAM_LOGOS,
}

# Give the seeded squad the same cutout portraits the live derivation attaches.
for _member in HUB_SECTIONS["squad"]:
    _member["image"] = PLAYER_IMAGES.get(_member["name"])
del _member

STORE_CATEGORIES = ["All", "Clothing", "Accessories", "Collections"]

STORE_PRODUCTS: list[dict[str, Any]] = [
    {"name": "SimplyUtd Training Top", "category": "Clothing", "brand": "Adidas", "price": "$34.99", "status": "Published", "featured": False},
    {"name": "SimplyUtd Cap", "category": "Accessories", "brand": "Adidas", "price": "$34.99", "status": "Published", "featured": False},
    {"name": "Heritage Collection Pin Set", "category": "Collections", "brand": "SimplyUtd", "price": "$34.99", "status": "Published", "featured": False},
    {"name": "SimplyUtd Matchday Jacket", "category": "Clothing", "brand": "SimplyUtd", "price": "$34.99", "status": "Published", "featured": True},
    {"name": "United Spirit Scarf", "category": "Accessories", "brand": "SimplyUtd", "price": "$24.99", "status": "Published", "featured": False},
    {"name": "Retro 1999 Tee", "category": "Clothing", "brand": "SimplyUtd", "price": "$29.99", "status": "Published", "featured": False},
    {"name": "Trophy Room Mug", "category": "Collections", "brand": "SimplyUtd", "price": "$14.99", "status": "Draft", "featured": False},
    {"name": "Training Bottle", "category": "Accessories", "brand": "SimplyUtd", "price": "$19.99", "status": "Published", "featured": False},
]

TESTIMONIALS = [
    {"name": "Sanjer Carter", "quote": "The quality is honestly better than I expected. Definitely wearing this to the next matchday."},
    {"name": "Oliver Bennett", "quote": "Really impressed with the quality. You can tell a lot of thought went into the design and details."},
    {"name": "Harry Mitchell", "quote": "Clean, comfortable and proper United vibes. Definitely getting another one for the next game."},
    {"name": "Jack Thompson", "quote": "Honestly love the quality. It looks even better in person and the fit is absolutely perfect for matchday."},
]

CONTACT_INFO: dict[str, Any] = {
    "types": ["General question", "Feedback", "Partnership idea", "Report an issue"],
    "email": "hello@simplyutd.com",
    "faqs": [
        {"q": "What is SimplyUtd?", "a": "SimplyUtd is your home for everything Manchester United — bringing the latest news, live updates, player information, fixtures, stats, community and more together in one place."},
        {"q": "Is SimplyUtd affiliated with Manchester United?", "a": "No. SimplyUtd is an independent fan platform and is not officially affiliated with or endorsed by Manchester United."},
        {"q": "Can I stay connected with SimplyUtd outside the website?", "a": "Yes — follow us on X, WhatsApp and Telegram for updates wherever you prefer to receive them."},
        {"q": "What is the Hub?", "a": "The Hub is our community space where fans can discuss fixtures, share opinions and connect with fellow supporters."},
        {"q": "How can I report an issue with the website?", "a": "The easiest way is to fill in our contact form on this page, or email us directly at hello@simplyutd.com. We aim to respond within 2 working days."},
    ],
    "socials": [
        {"name": "X (Twitter)", "key": "x", "description": "Breaking updates, conversations and quick news as it happens."},
        {"name": "WhatsApp", "key": "whatsapp", "description": "Direct updates and community communication, straight to your phone."},
        {"name": "Telegram", "key": "telegram", "description": "Fast United news and updates, delivered the moment they break."},
    ],
}

SAMPLE_NEWS: list[dict[str, Any]] = [
    {"title": "United close in on €85m Bundesliga midfielder ahead of January window", "source": "BBC Sport", "category": "Transfers", "featured": True},
    {"title": "Kobbie Mainoo returns to full training — available for the weekend", "source": "Sky Sports", "category": "Team News", "featured": False},
    {"title": "Rashford to hold talks with club this week over long-term future", "source": "The Guardian", "category": "Transfers", "featured": False},
    {"title": "Confirmed XI drops at 1pm — Europa League group stage clash", "source": "Manchester Evening News", "category": "Matchday", "featured": False},
    {"title": "Old Trafford redevelopment: everything we know about the £2bn project", "source": "The Athletic", "category": "Club", "featured": False},
    {"title": "Ranking every United signing under the INEOS era — from best to worst", "source": "90min", "category": "Opinion", "featured": False},
]


def _seed_hub(database: Database) -> int:
    count = 0
    for key, data in HUB_SECTIONS.items():
        # ``$setOnInsert`` keeps admin overrides (and any previously seeded copy)
        # intact instead of clobbering them on every boot.
        result = database[db_module.HUB].update_one(
            {"key": key},
            {"$setOnInsert": {"key": key, "data": data, "updated_at": utcnow()}},
            upsert=True,
        )
        if result.upserted_id is not None:
            count += 1
    return count


def _seed_products(database: Database) -> int:
    if database[db_module.PRODUCTS].estimated_document_count() > 0:
        return 0
    now = utcnow()
    docs = []
    for index, product in enumerate(STORE_PRODUCTS):
        docs.append(
            {
                "id": new_id(),
                "slug": f"{slugify(product['name'])}-{index}",
                "image": None,
                "description": None,
                "clicks": 0,
                "created_at": now,
                "updated_at": now,
                **product,
            }
        )
    if docs:
        database[db_module.PRODUCTS].insert_many(docs)
    return len(docs)


def _seed_store_meta(database: Database) -> None:
    database[db_module.META].update_one(
        {"key": "store_meta"},
        {"$set": {"key": "store_meta", "categories": STORE_CATEGORIES, "testimonials": TESTIMONIALS, "updated_at": utcnow()}},
        upsert=True,
    )
    database[db_module.META].update_one(
        {"key": "contact_info"},
        {"$set": {"key": "contact_info", "data": CONTACT_INFO, "updated_at": utcnow()}},
        upsert=True,
    )


def _seed_news(database: Database) -> int:
    if database[db_module.NEWS].estimated_document_count() > 0:
        return 0
    now = utcnow()
    docs = []
    for index, item in enumerate(SAMPLE_NEWS):
        published = now - timedelta(hours=index * 3 + 1)
        docs.append(
            {
                "id": new_id(),
                "slug": f"{slugify(item['title'])}-{index}",
                "summary": item["title"],
                "content": None,
                "image": None,
                "source_url": None,
                "tags": [],
                "author": None,
                "status": "Published",
                "views": 0,
                "comments": 0,
                "external": False,
                "guid": None,
                "published_at": published,
                "created_at": published,
                "updated_at": published,
                **item,
            }
        )
    if docs:
        database[db_module.NEWS].insert_many(docs)
    return len(docs)


def seed_all(database: Database) -> dict[str, int]:
    """Populate default content. Safe to run on every startup."""
    summary = {
        "hub_sections": _seed_hub(database),
        "products": _seed_products(database),
        "news": _seed_news(database),
    }
    try:
        summary["comments"] = seed_demo_comments(database)
    except Exception:  # noqa: BLE001 - demo chatter must never block startup
        logger.debug("Demo comment seeding skipped", exc_info=True)
        summary["comments"] = 0
    _seed_store_meta(database)
    logger.info("Seed complete: %s", summary)
    return summary
