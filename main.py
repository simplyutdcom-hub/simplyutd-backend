from contextlib import asynccontextmanager
import json
import urllib.request
import urllib.error

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

import mailer
import storage


def _probe(url: str, method: str = "GET", headers=None, data=None) -> dict:
    """Make a raw HTTP request from the server and report status/body/headers."""
    try:
        req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode(errors="replace")
            return {
                "url": url,
                "status": resp.status,
                "ok": True,
                "body": body[:500],
                "server": resp.headers.get("Server", ""),
            }
    except urllib.error.HTTPError as exc:
        return {
            "url": url,
            "status": exc.code,
            "ok": False,
            "body": exc.read().decode(errors="replace")[:500],
            "server": exc.headers.get("Server", "") if exc.headers else "",
        }
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "ok": False, "error": str(exc)}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    storage.init_db()
    yield


app = FastAPI(title="SimplyUtd Waitlist API", lifespan=lifespan)

# Allow the Vite dev server (and any local frontend) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class WaitlistEntry(BaseModel):
    email: EmailStr


class SignupResponse(BaseModel):
    message: str
    email_sent: bool


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/waitlist", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
def join_waitlist(entry: WaitlistEntry) -> SignupResponse:
    email = entry.email.lower()

    created, message = storage.add_signup(email)
    if created:
        # New signup: confirm to the user and notify the site owner.
        welcome_ok, welcome_detail = mailer.send_welcome_email(email)
        owner_ok, owner_detail = mailer.send_signup_notification(email)
        email_ok = welcome_ok and owner_ok
        for detail in (welcome_detail, owner_detail):
            if not detail.startswith("Email sent."):
                print(f"[waitlist] {detail}")
    else:
        # Already on the list; no need to email again.
        email_ok = True

    return SignupResponse(message=message, email_sent=email_ok)


class TestEmailRequest(BaseModel):
    email: EmailStr


class TestEmailResponse(BaseModel):
    sent: bool
    detail: str


@app.post("/api/test-email", response_model=TestEmailResponse)
def test_email(req: TestEmailRequest) -> TestEmailResponse:
    """Send a test email to any address to confirm Resend delivery."""
    ok, detail = mailer.send_test_email(req.email.lower())
    return TestEmailResponse(sent=ok, detail=detail)


@app.get("/api/diag")
def diag() -> dict:
    """Raw egress probes to diagnose why Resend calls fail from Render."""
    key = mailer.RESEND_API_KEY
    results = []
    # 1) A plain site to confirm general egress works.
    results.append(_probe("https://example.com"))
    # 2) Resend's domains endpoint with the real key — shows the raw response.
    results.append(
        _probe(
            "https://api.resend.com/domains",
            headers={"Authorization": f"Bearer {key}"},
        )
    )
    # 3) Resend's root API endpoint with no auth (to see raw 401 JSON).
    results.append(_probe("https://api.resend.com/emails"))
    return {"configured": bool(key), "probes": results}
