"""DB 마이그레이션 러너.

db/migrations/NNN_*.sql 파일을 번호 순으로 적용한다.
이미 적용된 버전은 schema_migrations 테이블로 추적해 건너뜀.

사용법:
    uv run python -m app.db.migrate
    DATABASE_URL=postgresql://... uv run python -m app.db.migrate
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "db" / "migrations"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5432/rag_tax"


def run(dsn: str | None = None) -> None:
    dsn = dsn or os.getenv("DATABASE_URL", DEFAULT_DSN)

    # schema_migrations 테이블을 먼저 보장
    with psycopg.connect(dsn) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.commit()

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        print("마이그레이션 파일 없음.")
        return

    with psycopg.connect(dsn) as conn:
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

        for path in migration_files:
            version = path.stem.split("_")[0]  # "001" ← "001_init_schema"
            if version in applied:
                print(f"  skip {path.name} (already applied)")
                continue

            print(f"  applying {path.name} ...")
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            conn.commit()
            print(f"  ✓ {version} applied")


if __name__ == "__main__":
    run()
    print("done.")
