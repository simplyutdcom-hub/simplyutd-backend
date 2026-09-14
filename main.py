"""Entrypoint for `uvicorn main:app` (Render's default start command).

The application itself lives in :mod:`app.main`; this module only re-exports it
so that both `uvicorn main:app` and `uvicorn app.main:app` work from the
repository root.
"""
from app.main import app

__all__ = ["app"]
