"""Explicit PostgreSQL schema migration; never print credentials."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
from services.storage import migrate

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply migrations to configured database")
    parser.add_argument("--env-file", type=Path, help="Explicit environment file, e.g. .env.production")
    args = parser.parse_args()
    if not args.apply:
        print("Pending migration files: " + ", ".join(p.name for p in sorted((ROOT / "migrations").glob("*.sql"))))
        print("Set DATABASE_URL_UNPOOLED then run with --apply.")
    else:
        values = dotenv_values(args.env_file or ROOT / '.env')
        url = values.get('DATABASE_URL_UNPOOLED') if args.env_file else os.environ.get("DATABASE_URL_UNPOOLED") or values.get('DATABASE_URL_UNPOOLED')
        if not url:
            parser.error("DATABASE_URL_UNPOOLED is required; configure it privately")
        migrate(url)
        print("Schema migrations applied successfully.")
