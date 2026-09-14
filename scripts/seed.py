"""Seed the database with default content and (optionally) run RSS ingestion.

Usage (from the backend directory):

    python -m scripts.seed          # seed hub/store/news defaults
    python -m scripts.seed --ingest # also pull the latest RSS news
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db as db_module  # noqa: E402
from app.services.ingest import run_ingest  # noqa: E402
from app.services.seed import seed_all  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed SimplyUtd data.")
    parser.add_argument("--ingest", action="store_true", help="Also fetch RSS news.")
    args = parser.parse_args()

    database = db_module.connect()
    print("Seeding defaults ->", seed_all(database))
    if args.ingest:
        print("Ingesting RSS ->", run_ingest(database))
    db_module.close()


if __name__ == "__main__":
    main()
