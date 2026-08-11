"""Seed JSON → Neon/PGlite (pg-dms, pg-sim, pg-core). One transaction per DB, idempotent per company.

    uv run python scripts/warranty/seed.py [--slug default]
"""
from __future__ import annotations

import argparse

from wc_db.seeding import seed

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="default")
    seed(ap.parse_args().slug)
