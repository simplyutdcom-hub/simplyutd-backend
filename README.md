# SimplyUtd Waitlist Backend

A small [FastAPI](https://fastapi.tiangolo.com/) backend for the SimplyUtd
"coming soon" waitlist. When someone signs up it:

1. Validates and stores their email in **MongoDB** (or local SQLite in dev).
2. Sends the registrant a branded **welcome email** (matching the SimplyUtd
   black / white / red UI) confirming they're on the list.
3. Sends you a **notification email** via Resend so you know who signed up.

Email is delivered through [**Resend**](https://resend.com)'s HTTPS API rather
than SMTP, because Render's **free** instances block outbound SMTP (ports
25/465/587) but allow HTTPS (port 443).

## Storage

The backend picks its database from the `MONGO_URI` env var:

| `MONGO_URI`            | Storage used                              |
|------------------------|-------------------------------------------|
| set (e.g. Atlas/Render)| **MongoDB** — durable, survives restarts  |
| empty                  | Local **SQLite** file (`backend/waitlist.db`) for easy local dev |

Emails are stored with a unique index, so duplicates are rejected.

## Local setup

### 1. Configure Resend email

1. Create a free account at [resend.com](https://resend.com) and generate an
   **API key** (`re_...`).
2. **For testing:** leave `RESEND_FROM` as `SimplyUtd <onboarding@resend.dev>`
   — Resend's sandbox lets you send to your own inbox. To email real
   subscribers you must add and verify a **domain** (Resend → Domains) and set
   `RESEND_FROM` to an address on it (e.g. `no-reply@yourdomain.com`).

```bash
cp .env.example .env   # then edit: RESEND_API_KEY, RESEND_FROM, NOTIFY_TO
```

`NOTIFY_TO` is the inbox that receives "new signup" notifications to you.

### 2. Install and run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --reload --port 8000
```

API: `http://localhost:8000`

## API

| Method | Path            | Body                | Description                     |
|--------|-----------------|---------------------|---------------------------------|
| GET    | `/api/health`   | –                   | Liveness check                  |
| POST   | `/api/waitlist` | `{"email": "a@b"}`  | Register a waitlist signup      |

`POST /api/waitlist` → `201`:

```json
{ "message": "You're on the list! We'll be in touch soon.", "email_sent": true }
```

- Duplicate emails return an "already on the list" message and don't re-notify.
- If Resend isn't configured the signup is still stored and `email_sent` is `false`.

## Hosting on Render

Deploy **two** things: the React frontend (Static Site) and this backend (Web Service).

### Backend → Render Web Service
1. Render → **New → Web Service** → connect your repo.
2. **Root Directory:** `backend` (if it's in a subfolder of the repo).
3. **Build Command:** `pip install -r requirements.txt`
4. **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. **Environment Variables** (never commit `.env`):
   - `RESEND_API_KEY` — from [resend.com/api-keys](https://resend.com/api-keys)
   - `RESEND_FROM` — e.g. `SimplyUtd <no-reply@yourdomain.com>` (see above)
   - `NOTIFY_TO` — your inbox for signup notifications
   - `MONGO_URI` — from **Render's MongoDB** or [MongoDB Atlas](https://www.mongodb.com/atlas)
   - optionally `MONGO_DB`

Render gives you a URL like `https://<name>.onrender.com`. Because MongoDB is
managed outside Render's ephemeral disk, signups persist across restarts.

### Frontend → Render Static Site
Build command `npm run build` (outputs `dist/`). The API base URL comes from
`VITE_API_URL` (see [.env.production](../.env.production), which Vite loads
during production builds and points at your Render backend). For good measure,
restrict the backend's CORS to your real frontend domain (currently
`allow_origins=["*"]` for dev).

## Viewing signups

MongoDB (e.g. via `mongosh` against your Atlas/Render DB):
```bash
db.waitlist.find({}, { _id: 0, email: 1, created_at: 1 }).sort({ created_at: -1 })
```
