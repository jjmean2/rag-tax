from __future__ import annotations

import argparse
import os

from app.db.migrate import run as migrate


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap PostgreSQL schema")
    parser.add_argument("command", choices=["migrate"])
    args = parser.parse_args()

    if args.command == "migrate":
        dsn = os.getenv("DATABASE_URL")
        migrate(dsn)
        print("schema up to date.")


if __name__ == "__main__":
    main()
