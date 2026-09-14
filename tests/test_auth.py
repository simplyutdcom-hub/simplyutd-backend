"""Tests for reader accounts, sessions and search."""
from __future__ import annotations

from datetime import timedelta

from app.services import user_service

GOOD = {"email": "Red@Example.com", "password": "mufc-2026!x", "name": "Red Devil"}


def _register(client, **overrides):
    payload = {**GOOD, **overrides}
    return client.post("/api/auth/register", json=payload)


def test_register_creates_an_account_and_session(client):
    response = _register(client)
    assert response.status_code == 201
    body = response.json()
    assert body["token"]
    assert body["user"]["email"] == "red@example.com"
    assert body["user"]["name"] == "Red Devil"
    assert body["user"]["initials"] == "RD"
    # The password hash must never leave the server.
    assert "password" not in body["user"]


def test_register_subscribes_the_new_account(client, seeded):
    _register(client)
    assert seeded["subscribers"].find_one({"email": "red@example.com"}) is not None


def test_register_rejects_a_duplicate(client):
    assert _register(client).status_code == 201
    duplicate = _register(client)
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]


def test_register_rejects_a_short_password(client):
    response = _register(client, password="short")
    assert response.status_code == 422


def test_login_returns_a_token(client):
    _register(client)
    response = client.post(
        "/api/auth/login", json={"email": "RED@example.com", "password": GOOD["password"]}
    )
    assert response.status_code == 200
    assert response.json()["token"]


def test_login_rejects_a_bad_password(client):
    _register(client)
    response = client.post(
        "/api/auth/login", json={"email": GOOD["email"], "password": "not-the-password"}
    )
    assert response.status_code == 401


def test_login_rejects_an_unknown_email(client):
    response = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever1"})
    assert response.status_code == 401


def test_me_bootstraps_from_the_token(client):
    token = _register(client).json()["token"]
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "red@example.com"


def test_me_is_null_without_a_token(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["user"] is None


def test_me_is_null_for_a_stale_token(client):
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer nope"})
    assert response.json()["user"] is None


def test_logout_revokes_the_session(client):
    token = _register(client).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/auth/logout", headers=headers).json()["revoked"] is True
    assert client.get("/api/auth/me", headers=headers).json()["user"] is None


def test_expired_sessions_are_refused(client, seeded):
    token = _register(client).json()["token"]
    expired = user_service.utcnow() - timedelta(days=1)
    seeded["sessions"].update_many({}, {"$set": {"expires_at": expired}})
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["user"] is None
    assert seeded["sessions"].count_documents({"token": token}) == 0


def test_passwords_are_salted_and_hashed(client, seeded):
    _register(client)
    stored = seeded["users"].find_one({"email": "red@example.com"})["password"]
    assert GOOD["password"] not in stored
    assert stored.count("$") == 2
    assert user_service.verify_password(GOOD["password"], stored) is True
    assert user_service.verify_password("wrong-password", stored) is False


def test_verify_password_tolerates_garbage():
    assert user_service.verify_password("x", None) is False
    assert user_service.verify_password("x", "not-a-hash") is False


# --- Search -----------------------------------------------------------------

def test_search_finds_a_seeded_story(client):
    response = client.get("/api/search", params={"q": "united"})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "united"
    assert body["total"] > 0
    assert body["items"]


def test_search_without_a_query_is_empty(client):
    body = client.get("/api/search", params={"q": "   "}).json()
    assert body["total"] == 0
    assert body["items"] == []


def test_search_for_a_nonsense_term_is_empty(client):
    body = client.get("/api/search", params={"q": "zzzzqqq"}).json()
    assert body["items"] == []


def test_search_items_carry_card_fields(client):
    item = client.get("/api/search", params={"q": "united"}).json()["items"][0]
    for field in ("id", "title", "source", "published_at", "time"):
        assert field in item
