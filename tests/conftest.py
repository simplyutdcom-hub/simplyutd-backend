"""Pytest fixtures: a mongomock-backed app with seeding and admin enabled."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Configure the environment BEFORE importing app modules (settings are cached).
os.environ.setdefault("INGEST_ENABLED", "false")
os.environ.setdefault("HUB_WIKIPEDIA_ENABLED", "false")
# The Transfermarkt and SalaryLeaks reads are off in the suite so no test
# reaches the network; their test modules switch them back on and replace the
# HTTP call.
os.environ.setdefault("TRANSFERMARKT_ENABLED", "false")
os.environ.setdefault("SALARYLEAKS_ENABLED", "false")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("RESEND_API_KEY", "")
os.environ.setdefault("CLOUDINARY_CLOUD_NAME", "")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db as db_module  # noqa: E402
from app.main import app  # noqa: E402
from app.services.seed import seed_all  # noqa: E402

ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}


@pytest.fixture()
def database():
    """A fresh in-memory MongoDB per test."""
    mock = mongomock.MongoClient(tz_aware=True)["simplyutd_test"]
    db_module.set_db(mock)
    yield mock
    db_module._db = None


@pytest.fixture()
def seeded(database):
    seed_all(database)
    return database


@pytest.fixture()
def client(seeded):
    with TestClient(app) as test_client:
        yield test_client
