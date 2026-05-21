from __future__ import annotations

import argparse

from app.data.sample_documents import SAMPLE_DOCUMENTS
from app.storage import PostgresStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap PostgreSQL schema and seed data"
    )
    parser.add_argument("command", choices=["init-db", "seed-sample", "init-and-seed"])
    args = parser.parse_args()

    store = PostgresStore()

    if args.command == "init-db":
        store.init_schema()
        print("schema initialized")
        return

    if args.command == "seed-sample":
        store.seed_documents(SAMPLE_DOCUMENTS)
        print("sample documents seeded")
        return

    store.init_schema()
    store.seed_documents(SAMPLE_DOCUMENTS)
    print("schema initialized and sample documents seeded")


if __name__ == "__main__":
    main()
