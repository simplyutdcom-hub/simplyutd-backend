"""Cloudinary mirroring: configuration, failure reporting and fallbacks."""
from __future__ import annotations

import pytest

from app.services import cloudinary_service, ingest
from app.services.rss import FeedEntry

from .conftest import ADMIN_HEADERS


@pytest.fixture(autouse=True)
def reset_state():
    """`_configured`/`_last_error` are module globals; keep tests independent."""
    cloudinary_service._configured = False
    cloudinary_service._last_error = None
    yield
    cloudinary_service._configured = False
    cloudinary_service._last_error = None


def _configure(monkeypatch):
    """Pretend the three Cloudinary env vars are present and valid."""
    monkeypatch.setattr(cloudinary_service.settings, "cloudinary_cloud_name", "test-cloud")
    monkeypatch.setattr(cloudinary_service.settings, "cloudinary_api_key", "test-key")
    monkeypatch.setattr(cloudinary_service.settings, "cloudinary_api_secret", "test-secret")


def test_unconfigured_mirror_is_a_no_op(monkeypatch):
    assert cloudinary_service.is_configured() is False
    assert cloudinary_service.upload_remote("https://cdn.example.com/a.jpg") is None
    # Nothing was attempted, so there is no failure to report.
    assert cloudinary_service.last_error() is None


def test_success_records_no_error(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        cloudinary_service.cloudinary.uploader,
        "upload",
        lambda url, **kwargs: {"secure_url": "https://res.cloudinary.com/test-cloud/a.jpg"},
    )

    assert cloudinary_service.is_configured() is True
    assert (
        cloudinary_service.upload_remote("https://cdn.example.com/a.jpg")
        == "https://res.cloudinary.com/test-cloud/a.jpg"
    )
    assert cloudinary_service.last_error() is None


def test_failure_is_captured_and_truncated(monkeypatch):
    _configure(monkeypatch)

    def boom(url, **kwargs):
        raise RuntimeError("x" * 500)

    monkeypatch.setattr(cloudinary_service.cloudinary.uploader, "upload", boom)

    assert cloudinary_service.upload_remote("https://cdn.example.com/a.jpg") is None
    error = cloudinary_service.last_error()
    assert error is not None
    assert error.startswith("RuntimeError: ")
    assert len(error) <= 240


def test_last_error_survives_a_later_success(monkeypatch):
    """The reported failure tracks the latest attempt, not the first one."""
    _configure(monkeypatch)
    monkeypatch.setattr(
        cloudinary_service.cloudinary.uploader,
        "upload",
        lambda url, **kwargs: (_ for _ in ()).throw(RuntimeError("blocked")),
    )
    cloudinary_service.upload_remote("https://cdn.example.com/a.jpg")
    first = cloudinary_service.last_error()
    assert first == "RuntimeError: blocked"

    monkeypatch.setattr(
        cloudinary_service.cloudinary.uploader,
        "upload",
        lambda url, **kwargs: {"secure_url": "https://res.cloudinary.com/test-cloud/b.jpg"},
    )
    assert cloudinary_service.upload_remote("https://cdn.example.com/b.jpg")

    # A success clears it, so whatever is on /api/diag reflects the latest attempt.
    assert cloudinary_service.last_error() is None


def test_ingest_summary_reports_mirror_attempts(seeded, monkeypatch):
    from app.services import news_sources  # noqa: F401  (seeded sources)

    entry = FeedEntry(
        title="United agree terms with new striker",
        link="https://example.com/story-1",
        summary="Details here.",
        image="https://cdn.example.com/team.jpg",
        guid="https://example.com/story-1",
    )

    monkeypatch.setattr(ingest, "collect_entries", lambda **kwargs: [entry])
    # Excerpt enrichment fetches the article HTML; keep the test offline.
    monkeypatch.setattr(ingest, "enrich", lambda entries: 0)
    monkeypatch.setattr(ingest.cloudinary_service, "is_configured", lambda: True)
    monkeypatch.setattr(
        ingest.cloudinary_service,
        "upload_remote",
        lambda url, **kwargs: None,
    )

    summary = ingest.run_ingest(seeded)

    assert summary["mirror_attempts"] == 1
    assert summary["mirrored_images"] == 0
    assert summary["inserted"] == 1
    # The stored story keeps the publisher URL when the mirror fails.
    stored = seeded["news"].find_one({"source_url": entry.link})
    assert stored["image"] == "https://cdn.example.com/team.jpg"


def test_diag_exposes_last_mirror_error(client, monkeypatch):
    assert client.get("/api/diag").json()["cloudinary"]["last_error"] is None

    cloudinary_service._last_error = "Error: Invalid credentials"
    body = client.get("/api/diag", headers=ADMIN_HEADERS).json()
    assert body["cloudinary"]["last_error"] == "Error: Invalid credentials"
