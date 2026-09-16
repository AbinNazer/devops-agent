#!/usr/bin/env python3
"""Explicitly apply reviewed SaaS PostgreSQL migrations."""
import argparse
import sys
from pathlib import Path

# Allow direct execution from the repository root:
# python scripts/migrate_postgres.py --database-url ...
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.postgres import run_migrations


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply JARVIS PostgreSQL migrations")
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    for name in run_migrations(args.database_url):
        print(f"applied {name}")


if __name__ == "__main__":
    main()
