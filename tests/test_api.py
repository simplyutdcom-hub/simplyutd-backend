"""End-to-end API tests against a mongomock database."""
from __future__ import annotations

from datetime import timedelta

from app.utils import utcnow

from .conftest import ADMIN_HEADERS


def test_root_and_health(client):
    assert client.get("/").status_code == 200
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_diag_reports_config(client):
    diag = client.get("/api/diag").json()
    assert diag["mongo"]["connected"] is True
    assert diag["admin"]["enabled"] is True
    assert diag["cloudinary"]["configured"] is False


def test_seed_populates_hub_store_and_news(seeded):
    assert seeded["hub"].count_documents({}) >= 6
    assert seeded["products"].count_documents({}) > 0
    assert seeded["news"].count_documents({}) > 0


def test_home_feed(client):
    body = client.get("/api/home").json()
    assert body["ticker"]
    assert body["stories"]
    assert all("title" in story for story in body["stories"])


def test_news_list_and_detail_and_view(client):
    listing = client.get("/api/news").json()
    assert listing["total"] > 0
    first = listing["items"][0]

    detail = client.get(f"/api/news/{first['id']}").json()
    assert detail["body"]  # falls back to the summary when there is no content
    assert detail["related"]
    assert all(item["id"] != first["id"] for item in detail["related"])
    assert all("title" in item for item in detail["related"])

    assert client.get(f"/api/news/{first['id']}?related=0").json()["related"] == []

    assert client.get("/api/news/does-not-exist").status_code == 404

    viewed = client.post(f"/api/news/{first['id']}/view").json()
    assert viewed["views"] >= 1

    ticker = client.get("/api/news/ticker").json()
    assert len(ticker["items"]) >= 1


def test_news_detail_uses_content_when_present(client, database):
    doc = database["news"].find_one({"status": "Published"})
    database["news"].update_one({"id": doc["id"]}, {"$set": {"content": "Full syndicated body."}})

    detail = client.get(f"/api/news/{doc['id']}").json()
    assert detail["body"] == "Full syndicated body."


def test_live_stream_and_detail(client):
    stream = client.get("/api/live").json()
    assert stream["items"]
    first = stream["items"][0]
    assert isinstance(first["time"], int)
    assert first["description"]

    detail = client.get(f"/api/live/{first['id']}").json()
    assert detail["body"]
    assert detail["live"]
    assert all(item["id"] != first["id"] for item in detail["live"])

    assert client.get("/api/live/does-not-exist").status_code == 404


def test_home_live_items_carry_detail_fields(client):
    live = client.get("/api/home").json()["live"]
    assert live
    assert all("image" in item and isinstance(item["time"], int) for item in live)


def test_hub_endpoints(client):
    all_sections = client.get("/api/hub").json()
    assert all_sections["standings"]
    assert client.get("/api/hub/standings").status_code == 200
    assert client.get("/api/hub/nonsense").status_code == 404


def test_store_endpoints(client):
    products = client.get("/api/store/products").json()
    assert products["total"] > 0
    assert client.get("/api/store/categories").json()["categories"][0] == "All"
    assert client.get("/api/store/featured").json()["product"] is not None
    assert client.get("/api/store/testimonials").json()["items"]

    product_id = products["items"][0]["id"]
    assert client.post(f"/api/store/products/{product_id}/click").json()["ok"] is True


def test_contact_submission_creates_message(client, database):
    response = client.post(
        "/api/contact",
        json={
            "name": "Sarah Thompson",
            "email": "sarah@example.com",
            "type": "Feedback",
            "subject": "Kit availability",
            "message": "When is the away kit back in stock?",
        },
    )
    assert response.status_code == 201
    assert response.json()["ok"] is True

    stored = database["messages"].find_one({"email": "sarah@example.com"})
    assert stored is not None
    assert stored["initials"] == "ST"
    assert stored["unread"] is True


def test_contact_validation(client):
    bad = client.post("/api/contact", json={"name": "x", "email": "not-an-email", "subject": "s", "message": "m"})
    assert bad.status_code == 422


def test_newsletter_subscribe_and_duplicate(client):
    payload = {"email": "fan@example.com"}
    first = client.post("/api/newsletter/subscribe", json=payload)
    assert first.status_code == 201
    assert first.json()["already_subscribed"] is False

    second = client.post("/api/newsletter/subscribe", json=payload)
    assert second.json()["already_subscribed"] is True


def test_analytics_event(client, database):
    assert client.post("/api/analytics/event", json={"type": "page_view", "path": "/"}).status_code == 202
    assert database["events"].count_documents({}) == 1


def test_admin_requires_key(client):
    assert client.get("/api/admin/news").status_code == 401
    assert client.get("/api/admin/news", headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert client.get("/api/admin/news", headers=ADMIN_HEADERS).status_code == 200


def test_admin_news_crud(client):
    created = client.post(
        "/api/admin/news",
        headers=ADMIN_HEADERS,
        json={"title": "Mainoo signs new deal", "status": "Published", "category": "Club"},
    )
    assert created.status_code == 201
    news_id = created.json()["id"]

    updated = client.put(
        f"/api/admin/news/{news_id}", headers=ADMIN_HEADERS, json={"featured": True}
    )
    assert updated.json()["featured"] is True

    assert client.delete(f"/api/admin/news/{news_id}", headers=ADMIN_HEADERS).status_code == 200
    assert client.get(f"/api/admin/news/{news_id}", headers=ADMIN_HEADERS).status_code == 404


def test_admin_news_list_is_chronological(client, database):
    """Admin listings show the whole corpus newest-first, not the ranked feed."""
    old = client.post(
        "/api/admin/news",
        headers=ADMIN_HEADERS,
        json={"title": "Old news item", "status": "Published", "category": "Club"},
    ).json()["id"]
    database["news"].update_one(
        {"id": old}, {"$set": {"published_at": utcnow() - timedelta(days=30)}}
    )
    newest = client.post(
        "/api/admin/news",
        headers=ADMIN_HEADERS,
        json={"title": "Brand new news item", "status": "Published", "category": "Club"},
    ).json()["id"]

    body = client.get("/api/admin/news?limit=100", headers=ADMIN_HEADERS).json()
    ids = [item["id"] for item in body["items"]]
    # Every stored document is listed, including non-United aggregation traffic.
    assert body["total"] == database["news"].count_documents({})
    assert ids.index(newest) < ids.index(old)
    dates = [item["published_at"] for item in body["items"] if item["published_at"]]
    assert dates == sorted(dates, reverse=True)


def test_admin_products_crud(client):
    created = client.post(
        "/api/admin/products",
        headers=ADMIN_HEADERS,
        json={"name": "Test Cap", "category": "Accessories", "price": "$19.99"},
    )
    product_id = created.json()["id"]
    assert client.put(
        f"/api/admin/products/{product_id}", headers=ADMIN_HEADERS, json={"price": "$24.99"}
    ).json()["price"] == "$24.99"
    assert client.delete(f"/api/admin/products/{product_id}", headers=ADMIN_HEADERS).status_code == 200


def test_admin_messages_flow(client, database):
    client.post(
        "/api/contact",
        json={"name": "Ali Khan", "email": "ali@example.com", "type": "General question", "subject": "Hi", "message": "Hello"},
    )
    listing = client.get("/api/admin/messages", headers=ADMIN_HEADERS).json()
    assert listing["total"] == 1
    message_id = listing["items"][0]["id"]

    patched = client.patch(
        f"/api/admin/messages/{message_id}", headers=ADMIN_HEADERS, json={"unread": False, "favourite": True}
    ).json()
    assert patched["unread"] is False and patched["favourite"] is True

    stats = client.get("/api/admin/messages/stats", headers=ADMIN_HEADERS).json()
    assert stats["total"] == 1 and stats["unread"] == 0

    assert client.delete(f"/api/admin/messages/{message_id}", headers=ADMIN_HEADERS).status_code == 200


def test_admin_message_reply(client):
    client.post(
        "/api/contact",
        json={"name": "Jo Bell", "email": "jo@example.com", "type": "General question", "subject": "Merch", "message": "Where can I buy?"},
    )
    message_id = client.get("/api/admin/messages", headers=ADMIN_HEADERS).json()["items"][0]["id"]

    replied = client.post(
        f"/api/admin/messages/{message_id}/reply",
        headers=ADMIN_HEADERS,
        json={"body": "Thanks — the store ships worldwide."},
    )
    assert replied.status_code == 200
    body = replied.json()
    assert body["unread"] is False
    assert body["replies"][0]["body"] == "Thanks — the store ships worldwide."

    assert client.post(
        "/api/admin/messages/missing/reply", headers=ADMIN_HEADERS, json={"body": "hi"}
    ).status_code == 404


def test_admin_subscribers_and_export(client):
    client.post("/api/newsletter/subscribe", json={"email": "sub@example.com"})
    listing = client.get("/api/admin/subscribers", headers=ADMIN_HEADERS).json()
    assert listing["total"] == 1

    export = client.get("/api/admin/subscribers/export", headers=ADMIN_HEADERS)
    assert export.status_code == 200
    assert "sub@example.com" in export.text


def test_admin_stats(client):
    overview = client.get("/api/admin/stats/overview", headers=ADMIN_HEADERS).json()
    assert any(stat["label"] == "Total Users" for stat in overview["stats"])

    chart = client.get("/api/admin/stats/chart", headers=ADMIN_HEADERS).json()
    assert len(chart["series"]) >= 1

    activity = client.get("/api/admin/stats/activity", headers=ADMIN_HEADERS).json()
    assert "events" in activity and "messages" in activity


def test_admin_uploads_status(client):
    status = client.get("/api/admin/uploads/status", headers=ADMIN_HEADERS).json()
    assert status["configured"] is False


def test_admin_hub_upsert(client):
    response = client.put(
        "/api/admin/hub/overview", headers=ADMIN_HEADERS, json={"data": {"stats": [{"value": 1, "label": "x"}]}}
    )
    assert response.status_code == 200
    assert client.get("/api/hub/overview").json()["stats"][0]["label"] == "x"
    assert client.put("/api/admin/hub/bogus", headers=ADMIN_HEADERS, json={"data": {}}).status_code == 404


def test_admin_ingest_status(client):
    body = client.get("/api/admin/news/ingest/status", headers=ADMIN_HEADERS).json()
    assert "last" in body
