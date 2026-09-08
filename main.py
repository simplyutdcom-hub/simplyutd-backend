from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

import mailer
import storage


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
