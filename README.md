# SimplyUtd Waitlist Backend

A small [FastAPI](https://fastapi.tiangolo.com/) backend for the SimplyUtd
"coming soon" waitlist. When someone signs up it:

1. Validates and stores their email in **MongoDB** (or local SQLite in dev).
2. Sends the registrant a branded **welcome email** (matching the SimplyUtd
   black / white / red UI) confirming they're on the list.
3. Sends you a **notification email** via Gmail SMTP so you know who signed up.

## Storage

The backend picks its database from the `MONGO_URI` env var:

| `MONGO_URI`            | Storage used                              |
|------------------------|-------------------------------------------|
| set (e.g. Atlas/Render)| **MongoDB** — durable, survives restarts  |
| empty                  | Local **SQLite** file (`backend/waitlist.db`) for easy local dev |

Emails are stored with a unique index, so duplicates are rejected.

## Local setup

### 1. Configure Gmail SMTP

Gmail won't accept your normal password over SMTP. Create an **App Password**:

1. Enable **2-Step Verification** in your Google Account.
2. **Google Account → Security → App passwords** → create one for `simplyutd`.
3. Copy the 16-character password into `.env`.

```bash
cp .env.example .env   # then edit: SMTP_EMAIL, SMTP_APP_PASSWORD, NOTIFY_TO
```

`NOTIFY_TO` is the inbox that receives signup notifications. Leave blank to
send to `SMTP_EMAIL`.

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
- If SMTP isn't configured the signup is still stored and `email_sent` is `false`.

## Hosting on Render

Deploy **two** things: the React frontend (Static Site) and this backend (Web Service).

### Backend → Render Web Service
1. Render → **New → Web Service** → connect your repo.
2. **Root Directory:** `backend` (if it's in a subfolder of the repo).
3. **Build Command:** `pip install -r requirements.txt`
4. **Start Command:** `uvicorn main:app --host 0.0.0.0 --port 10000`
5. **Environment Variables** (never commit `.env`):
   - `SMTP_EMAIL`, `SMTP_APP_PASSWORD`, `NOTIFY_TO`
   - `MONGO_URI` — from **Render's MongoDB** or [MongoDB Atlas](https://www.mongodb.com/atlas)
   - optionally `MONGO_DB`

Render gives you a URL like `https://<name>.onrender.com`. Because MongoDB is
managed outside Render's ephemeral disk, signups persist across restarts.

### Frontend → Render Static Site
Build command `npm run build` (outputs `dist/`). Set the API base URL so `/api`
calls hit your Render backend, and restrict the backend's CORS to your real
frontend domain (currently `allow_origins=["*"]` for dev).

## Viewing signups

MongoDB (e.g. via `mongosh` against your Atlas/Render DB):
```bash
db.waitlist.find({}, { _id: 0, email: 1, created_at: 1 }).sort({ created_at: -1 })
```
