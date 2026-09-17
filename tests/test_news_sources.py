"""Admin-managed RSS news sources."""
from __future__ import annotations

from tests.conftest import ADMIN_HEADERS

SOURCES = "/api/admin/news/sources"


def _by_url(items: list[dict], url: str) -> dict | None:
    return next((item for item in items if item["url"] == url), None)


def test_defaults_are_seeded_once(client, database):
    from app.services import news_sources

    first = client.get(SOURCES, headers=ADMIN_HEADERS).json()
    assert first["total"] == len(news_sources.default_sources())
    assert all(item["builtin"] for item in first["items"])
    assert all(item["enabled"] for item in first["items"])

    # Seeding is a no-op once rows exist, so admin edits are never overwritten.
    assert news_sources.seed_defaults(database) == 0
    assert client.get(SOURCES, headers=ADMIN_HEADERS).json()["total"] == first["total"]


def test_sources_require_the_admin_key(client):
    assert client.get(SOURCES).status_code == 401
    assert client.get(SOURCES, headers={"X-Admin-Key": "wrong"}).status_code == 401


def test_create_rename_and_toggle(client):
    created = client.post(
        SOURCES,
        headers=ADMIN_HEADERS,
        json={"name": "United Blog", "url": "https://example.com/united.rss"},
    )
    assert created.status_code == 201
    source = created.json()
    assert source["builtin"] is False
    assert source["enabled"] is True

    renamed = client.patch(
        f"{SOURCES}/{source['id']}", headers=ADMIN_HEADERS, json={"name": "United Blog (new)"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "United Blog (new)"

    off = client.patch(f"{SOURCES}/{source['id']}", headers=ADMIN_HEADERS, json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["enabled"] is False


def test_duplicate_url_is_rejected(client):
    payload = {"name": "Duplicate", "url": "https://example.com/dupe.rss"}
    assert client.post(SOURCES, headers=ADMIN_HEADERS, json=payload).status_code == 201
    assert client.post(SOURCES, headers=ADMIN_HEADERS, json=payload).status_code == 409


def test_custom_sources_can_be_deleted_but_builtins_cannot(client, database):
    from app.services import news_sources

    builtin = client.get(SOURCES, headers=ADMIN_HEADERS).json()["items"][0]
    assert client.delete(f"{SOURCES}/{builtin['id']}", headers=ADMIN_HEADERS).status_code == 409

    custom = client.post(
        SOURCES, headers=ADMIN_HEADERS, json={"name": "Temp", "url": "https://example.com/temp.rss"}
    ).json()
    assert client.delete(f"{SOURCES}/{custom['id']}", headers=ADMIN_HEADERS).status_code == 200
    assert _by_url(client.get(SOURCES, headers=ADMIN_HEADERS).json()["items"], custom["url"]) is None

    # A restart must not resurrect anything the admin removed, or re-add a
    # default they renamed away from the configured value.
    assert news_sources.seed_defaults(database) == 0
    assert _by_url(client.get(SOURCES, headers=ADMIN_HEADERS).json()["items"], custom["url"]) is None


def test_restore_defaults_reinstates_a_removed_builtin(client, database):
    from app.services import news_sources

    removed = news_sources.default_sources()[0]["url"]
    news_sources._collection(database).delete_one({"url": removed})

    restored = client.post(f"{SOURCES}/restore-defaults", headers=ADMIN_HEADERS).json()
    assert restored["added"] == 1
    assert _by_url(client.get(SOURCES, headers=ADMIN_HEADERS).json()["items"], removed) is not None


def test_missing_source_returns_404(client):
    assert client.get(f"{SOURCES}/nope", headers=ADMIN_HEADERS).status_code == 404
    assert client.patch(f"{SOURCES}/nope", headers=ADMIN_HEADERS, json={"enabled": False}).status_code == 404
    assert client.delete(f"{SOURCES}/nope", headers=ADMIN_HEADERS).status_code == 404


def test_enabled_urls_drive_ingestion(seeded, monkeypatch):
    from app.services import ingest, news_sources

    calls: list[dict] = []

    def fake_collect(feeds=None, **kwargs):
        calls.append({"feeds": feeds, **kwargs})
        return []

    monkeypatch.setattr(ingest, "collect_entries", fake_collect)
    ingest.run_ingest(seeded, mirror_images=False)
    assert calls[0]["feeds"] == news_sources.enabled_urls(seeded)
    assert calls[0]["include_google_news"] is False

    # Switching everything off stops the pull rather than silently reverting.
    news_sources._collection(seeded).update_many({}, {"$set": {"enabled": False}})
    calls.clear()
    ingest.run_ingest(seeded, mirror_images=False)
    assert calls[0]["feeds"] == []


def test_default_labels_are_readable():
    from app.services import news_sources

    labels = {source["name"] for source in news_sources.default_sources()}
    assert "BBC Sport" in labels
    assert any(label.startswith("Google News ·") for label in labels)
