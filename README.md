# SimplyUtd Backend

FastAPI backend powering the whole SimplyUtd site — the **user-facing** pages
(home, hub, store, contact/newsletter) and the **admin** dashboard — backed by
**MongoDB**, **Cloudinary** for media, and an **RSS ingestion** pipeline that
pulls Manchester United news (with images).

> The previous "waitlist" backend has been preserved under
> [`_legacy/`](./_legacy) together with [`_legacy/FLOW.md`](./_legacy/FLOW.md)
> which documents its flow.

## Features

| Area | Endpoints |
|------|-----------|
| System | `GET /api/health`, `GET /api/diag`, `GET /` |
| Home | `GET /api/home` (ticker, live, stories, trending) |
| News | `GET /api/news`, `GET /api/news/ticker`, `GET /api/news/{id}`, `POST /api/news/{id}/view` |
| Live | `GET /api/live` (`{items, total, limit, skip}`, `?limit=&skip=`) |
| Search | `GET /api/search` (`?q=&limit=&skip=`) |
| Auth | `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` |
| Hub | `GET /api/hub` (incl. a daily-picked `hero`), `GET /api/hub/{section}` |
| Store | `GET /api/store/products`, `/products/{id}`, `/categories`, `/testimonials`, `/featured`, `POST /products/{id}/click` |
| Contact | `GET /api/contact/info`, `POST /api/contact` |
| Newsletter | `POST /api/newsletter/subscribe` |
| Analytics | `POST /api/analytics/event` |
| Admin (🔒 `X-Admin-Key`) | `/api/admin/news`, `/products`, `/messages`, `/subscribers`, `/stats/*`, `/uploads`, `/hub/{section}` |

Full interactive documentation is served at `/docs` (Swagger UI) and `/redoc`.

## Architecture

```
backend/
├─ app/
│  ├─ config.py        # env-driven settings
│  ├─ db.py            # Mongo client + indexes
│  ├─ security.py      # X-Admin-Key dependency
│  ├─ schemas.py       # Pydantic models
│  ├─ utils.py         # ids, slugs, dates, serialisation
│  ├─ services/        # cloudinary, mailer, rss, news, ingest, seed,
│  │                   # hub_service (RSS→hub derivation), wikipedia (squad)
│  ├─ routers/         # public endpoints
│  │  └─ admin/        # admin endpoints (all key-protected)
│  └─ main.py          # app factory, CORS, lifespan, ingestion loop
├─ scripts/seed.py     # seed defaults / run an ingest
├─ tests/              # pytest + mongomock
└─ _legacy/            # the previous waitlist backend (frozen)
```

## Local setup

```bash
cd backend
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill in the values below
.venv/bin/uvicorn app.main:app --reload --port 8000
```

The Vite dev server proxies `/api` → `http://localhost:8000`, so no extra
frontend config is needed in development.

### Required / optional environment variables

| Variable | Purpose |
|----------|---------|
| `MONGO_URI` | MongoDB connection string (Atlas or local). |
| `MONGO_DB` | Database name (default `simplyutd`). |
| `ADMIN_API_KEY` | Shared secret for `/api/admin` (`X-Admin-Key` header). Empty ⇒ admin disabled. |
| `RESEND_API_KEY`, `RESEND_FROM`, `NOTIFY_TO` | Transactional email (welcome + contact notifications). |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | Media storage & RSS image mirroring. |
| `RSS_FEEDS`, `INGEST_ENABLED`, `INGEST_INTERVAL_MINUTES` | News ingestion (interval defaults to **10** minutes). |
| `HUB_WIKIPEDIA_ENABLED`, `HUB_SQUAD_PAGE`, `HUB_SEASON_PAGE`, `HUB_TABLE_PAGE`, `HUB_CACHE_SECONDS`, `HUB_STANDINGS_CACHE_SECONDS`, `HUB_REFRESH_SECONDS` | Hub squad/statistics/league-table source (both page defaults are `2026–27`). The table has its own shorter TTL and a background refresher — see [Hub data](#hub-data). |
| `FRONTEND_ORIGINS` | Comma-separated CORS allow-list. |

## News ingestion (RSS)

On startup the service seeds default content, then a background task fetches
every configured feed (plus Google News topic searches for "Manchester United")
on an interval and stores new articles.

- **Deduplication** — articles are matched on RSS `guid` / canonical URL with
  unique indexes, so a feed refresh never creates duplicates.
- **Images** — the best image (`media:content` → `media:thumbnail` → enclosure
  → `<img>` in the description) is extracted; when Cloudinary is configured it
  is mirrored there, otherwise the remote URL is kept.
- **Attribution** — only syndicated metadata (headline, short summary, image,
  source, link back) is stored. The full article body is **not** copied.

> ⚠️ **Copyright note:** there is no genuinely "copyright-free" Manchester
> United news feed. RSS is a syndication format that permits headline +
> summary + link-back. SimplyUtd therefore aggregates with attribution and
> links to the publisher. Review each publisher's terms before republishing.

## Ranked United feed

Every user-facing list (home, live, search) is ordered by
[`app/services/feed_rank.py`](./app/services/feed_rank.py) instead of a plain
date sort:

- **Relevance score** — club/entity tokens in the title and summary, the
  source's United-specificity, and story freshness (exponential time decay).
- **United gate** — items the scorer recognises as Manchester United stories
  are preferred. The gate is applied as a *floor*: if fewer than `min_items`
  United stories exist the pool widens so a cold corpus still renders.
- **Backfill** — on each ingest the newest articles are re-scored and their
  annotations persisted, so ranking stays cheap at read time.

The scheduler runs ingestion on startup and then every
`INGEST_INTERVAL_MINUTES` (default **10**) minutes.

## Live, search and auth

| Endpoint | Notes |
|----------|-------|
| `GET /api/live?limit=&skip=` | Paged live-update stream, ranked by the feed algorithm. Returns `{items, total, limit, skip}`. |
| `GET /api/search?q=&limit=&skip=` | Broad text search across every indexed field. See [Search syntax](#search-syntax). |
| `POST /api/auth/register` | Creates an account, returns `{user, token}`. |
| `POST /api/auth/login` / `POST /api/auth/logout` | Session login / token invalidation. |
| `GET /api/auth/me` | Validates a bearer token; returns `{"user": null}` for a guest. |

Comments are stored per target (`story` / `live`) and streamed over
`WS /api/comments/ws/{target_type}/{target_id}`. The home page's **Live United**
panel is a matchday chat over the same transport: it opens one shared room per
calendar day (`live` target `matchday-YYYY-MM-DD`), so every fan on the page
joins the same conversation, with presence, typing indicators and the REST
fallback when the socket is unavailable. Article comment threads are unchanged.
A `story`/`live` target normally has to resolve to a `news` document; matchday
rooms are virtual, so `comment_service.is_live_room()` exempts exactly that id
shape and nothing else (a `story` post or a non-matchday `live` id still 404s).

The panel renders as an X-style feed: posts carry a like toggle
(`POST /api/comments/{id}/like` returns the new count and the server broadcasts
it to the room) and can be deleted by their author, and any post can be replied
to. `parent_id` is accepted on create, but the service **flattens
replies-to-replies onto the root** so a thread is never deeper than two levels;
`GET /api/comments` returns roots with their `replies` and `reply_count` nested.

### Search syntax

Search is handled by `app/services/search_engine.py`, a self-contained text
engine (Mongo only pre-filters candidates; all scoring happens in Python).

| You type | What happens |
|----------|--------------|
| `bruno rashford` | Every term must match somewhere in the story (AND across terms, OR across fields). |
| `"old trafford"` | Exact phrase — matched as a token sequence, not a substring. |
| `4-0`, `man-united` | Punctuation becomes a phrase, so the scoreline is searched as `4 0`. |
| `source:sky`, `tag:transfers`, `author:romano`, `category:transfers`, `year:2026`, `url:bbc.co.uk` | Scopes the term to one field. Aliases work too (`publisher:`, `cite:`, `topic:`, `link:`, `month:`, `date:`). |
| `source:"bbc sport"` | A quoted scope value keeps its spaces. |
| `rash*` | Prefix match, scored at 70% of a full match. |
| `-arsenal` | Excludes any story matching `arsenal`. |
| `€85m`, `[1]`, `“smart”` | Punctuation, currency symbols and smart quotes are normalised away. |
| `amorrim` | Unknown words of ≥5 chars are fuzzy-corrected against the corpus vocabulary (55% score factor). |

Scoring weights headline highest, then tags, slug, source, category, author,
summary, URL, date and finally body text, with bonuses for phrase hits, term
coverage, an all-terms-in-headline match and term proximity. The response also
carries how the query was understood:

```json
{
  "query": "bruno rashford",
  "total": 2,
  "parsed": { "terms": ["bruno", "rashford"], "phrases": [], "fields": {}, "excluded": [] },
  "broadened": false,
  "fuzzy": false,
  "items": [ { "id": "…", "score": 81.46, "matched": ["bruno", "rashford"], "…": "…" } ]
}
```

- `broadened` — not every term was found, so the closest matches are returned.
- `fuzzy` — nothing matched exactly and the nearest spellings were searched.
- Scoped keywords still need the scope operator; plain keywords search *all*
  fields, so `raf` finds "Rashford" in a headline or `85m` finds a fee in a
  summary.

Trigger an ingest manually from the admin API or CLI:

```bash
curl -X POST http://localhost:8000/api/admin/news/ingest -H "X-Admin-Key: $ADMIN_API_KEY"
.venv/bin/python -m scripts.seed --ingest
```

## Hub data

The hub is **not** hard-coded. Every section is derived from data already on the
site, so nothing is invented:

| Section | Source |
|---------|--------|
| `results` | Scorelines parsed out of ingested headlines/summaries and URL slugs. |
| `fixtures` | Preview headlines whose slug names two clubs (e.g. `man-united-vs-man-city`). |
| `standings` | Wikipedia's **rendered** season-article league table, in the order the page shows it. Falls back to the wikitext `{{#invoke:Sports table}}` grid, then to a mini-table accumulated from the parsed **Premier League** results. |
| `squad` / `compare` | The club's live squad + season statistics (Wikipedia `{{fs player}}` roster and the season article's *Squad statistics* table), each player carrying a portrait from TheSportsDB. |
| `overview` | Feed aggregates (`stories tracked`, sources, competitions) plus the real top scorers from the squad statistics. |

The league table is read from the page named by `HUB_TABLE_PAGE` (default
`2026–27 Premier League`, so it tracks the season in progress) via the
`action=parse&prop=text` API — i.e. the HTML a reader sees, with the standings
template already expanded by Wikipedia. The rows are returned **in page order**:
that order already encodes goal difference, goals-for and any points deduction,
so the table is never re-sorted, and the page's own `Pts` column is honoured over
a recomputed total. Aggregating the raw `match_X_Y` results grid is kept only as
a fallback for articles that still use explicit tallies.

Because the table moves every matchday it has its own, much shorter cache —
`HUB_STANDINGS_CACHE_SECONDS` (default **600**) versus `HUB_CACHE_SECONDS`
(6 h) for everything else — and the app lifespan starts a `hub_refresher` task
that rewrites it every `HUB_REFRESH_SECONDS` (default **600**), so a request
never waits on Wikipedia and `/api/hub` reports its provenance:

```json
{ "standings_meta": { "source": "Wikipedia (rendered table)", "updated_at": "2026-09-14T14:35:24+00:00" } }
```

Only a successful read is cached, so a transient failure retries on the next
refresh instead of freezing an empty table for the whole interval. An admin
override reports `"SimplyUtd snapshot"` instead.

The squad comes from `HUB_SQUAD_PAGE` / `HUB_SEASON_PAGE` (both default to
`2026–27 Manchester United F.C. season`). The *Squad statistics* header is read
column-by-column, so the parser copes with a season that adds a Champions League
block; all non-league, non-total competition groups count towards `cup_goals`.

Only non-copyrightable facts (scorelines, club pairings, appearance/goal counts)
are extracted — no publisher prose is stored or republished. When a section
cannot be derived yet it falls back to the seeded sample, and an admin edit
(`PUT /api/admin/hub/{section}`) always wins over both the derived and seeded
values. Wikipedia fetching can be turned off with `HUB_WIKIPEDIA_ENABLED=false`
(used by the test-suite, which stays offline).

## Admin dashboard

The frontend admin dashboard lives at `/admin-crud-access-gfxtuy` and is fully
wired to the endpoints below.

| Area | Endpoints (all under `/api/admin`) |
|------|-----------|
| Overview | `GET /stats/overview`, `/stats/chart?days=1..30`, `/stats/activity?limit=1..50` |
| News | `GET/POST /news`, `GET/PUT/DELETE /news/{id}`, `POST /news/ingest`, `GET /news/ingest/status` |
| Products | `GET/POST /products`, `GET/PUT/DELETE /products/{id}` |
| Messages | `GET /messages`, `GET /messages/stats`, `GET/PATCH/DELETE /messages/{id}`, `POST /messages/{id}/reply` |
| Subscribers | `GET /subscribers`, `GET /subscribers/export`, `DELETE /subscribers/{id}` |
| Hub | `GET /hub`, `PUT /hub/{section}` |
| Uploads | `GET /uploads/status`, `POST /uploads` (multipart `file` + `folder`) |

`GET /api/admin/news` returns the **whole corpus** in plain chronological order
(newest first), including traffic that the public feed filters out — unlike the
public `/api/news`, which is United-only and ranked by `feed_rank`. Similarly,
`GET /api/admin/products` lists unpublished items, and `GET /api/admin/messages`
exposes `unread` / `favourite` filters.

Message replies are appended to the message document:

```bash
curl -X POST http://localhost:8000/api/admin/messages/$ID/reply \
  -H "X-Admin-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"body": "Thanks for reaching out!"}'
```

The reply also clears `unread`; `MessageOut.replies` is a list of
`{id, body, created_at}` objects.

## Analytics

The dashboard charts and activity feed are driven by the public
`POST /api/analytics/event` endpoint. The frontend calls it from
`src/lib/analytics.ts` (`trackEvent`) on every route change (`page_view` /
`visitor`) and on store product clicks (`product_click`); admin routes are
excluded. Because events are what populate the charts, a freshly started
backend will show an empty chart until the site is browsed (or events are
posted manually):

```bash
curl -X POST http://localhost:8000/api/analytics/event \
  -H "Content-Type: application/json" \
  -d '{"type": "page_view", "path": "/"}'
```

## Admin authentication

Every `/api/admin/*` route requires the `X-Admin-Key` header to equal
`ADMIN_API_KEY` (compared in constant time). If `ADMIN_API_KEY` is unset the
admin API returns `503` (disabled) rather than being left open.

```bash
curl http://localhost:8000/api/admin/stats/overview -H "X-Admin-Key: $ADMIN_API_KEY"
```

## Tests

```bash
.venv/bin/python -m pytest -q
```

Tests use [`mongomock`](https://github.com/mongomock/mongomock) — no running
MongoDB is required.

## Deployment (Render)

The repository at
[`simplyutdcom-hub/simplyutd-backend`](https://github.com/simplyutdcom-hub/simplyutd-backend)
contains the **contents of this `backend/` directory at its root**, so the
service is deployed with an empty Root Directory.

- **Root directory:** *(leave empty — the repo root is the backend)*
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Python version:** pinned by [`.python-version`](.python-version)
- **Environment:** `MONGO_URI`, `MONGO_DB`, `ADMIN_API_KEY`, the `RESEND_*`,
  `CLOUDINARY_*`, `RSS_FEEDS` and `FRONTEND_ORIGINS` variables.

Render's default start command (`uvicorn main:app`) also works: [`main.py`](main.py)
re-exports the app from `app.main`, and [`Procfile`](Procfile) declares the
explicit `app.main:app` form.

Point the frontend at the service with `VITE_API_URL` (see
[`../.env.production`](../.env.production)); CORS additionally allows
`*.vercel.app` preview deployments.
