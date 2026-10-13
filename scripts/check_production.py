"""Read-only readiness CLI. Never loads dotenv or imports the Flask app."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.deployment import database_errors, production_mode, validate_environment


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "FAIL cli.arguments\n")


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--offline", action="store_true", help="Validate environment only (default)")
    modes.add_argument("--database", action="store_true", help="Also check database schema and migration versions")
    parser.add_argument("--env-file",type=Path,help="Explicit private production environment file; never loads .env automatically")
    args = parser.parse_args(argv)
    config = os.environ
    if args.env_file:
        if not args.env_file.is_file():
            print('FAIL environment.file')
            return 1
        from dotenv import dotenv_values
        config = {key:value for key,value in dotenv_values(args.env_file).items() if value is not None}
    errors = validate_environment(config)
    if not production_mode(config):
        errors.insert(0, "ZGUA_ENV")
    if not errors and args.database:
        if args.env_file:
            os.environ.update(config)
        errors = database_errors()
    for error in errors:
        print("FAIL " + error)
    if not errors:
        print("PASS environment")
        if args.database:
            print("PASS database.readiness")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
