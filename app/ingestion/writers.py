"""DB 업서트 및 임베딩 생성."""
from __future__ import annotations

import hashlib
import os

import psycopg
import psycopg.rows
import psycopg.types.json
from pgvector import Vector
from pgvector.psycopg import register_vector

from app.ingestion.models import RawDocument

DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/rag_tax"
EMBED_BATCH_SIZE = 256


class IngestWriter:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

    def upsert(self, doc: RawDocument) -> tuple[int, int]:
        """문서·버전·섹션을 upsert한다. (신규 섹션 수, 갱신 섹션 수) 반환."""
        doc_id = f"{doc.source_system}:{doc.source_id}"
        v = doc.version
        content_hash = hashlib.md5(v.raw_text.encode()).hexdigest()
        version_id = f"{doc_id}:{content_hash[:12]}"

        with psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents
                        (id, source_system, source_id, doc_type, authority,
                         title, canonical_url, current_version_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                    SET title              = EXCLUDED.title,
                        canonical_url      = EXCLUDED.canonical_url,
                        current_version_id = EXCLUDED.current_version_id,
                        updated_at         = NOW()
                    """,
                    (doc_id, doc.source_system, doc.source_id, doc.doc_type,
                     doc.authority, doc.title, doc.canonical_url, version_id),
                )

                cur.execute(
                    """
                    INSERT INTO document_versions
                        (id, document_id, version_label, effective_from, effective_to,
                         publish_date, status, raw_text, normalized_text,
                         hash_sha256, metadata_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (version_id, doc_id, v.version_label,
                     v.effective_from, v.effective_to, v.publish_date,
                     v.status, v.raw_text, v.raw_text,
                     content_hash, psycopg.types.json.Jsonb(v.metadata)),
                )

                inserted = updated = 0
                for sec in v.sections:
                    section_id = f"{version_id}:{sec.section_ref or sec.order_no}"
                    cur.execute(
                        """
                        INSERT INTO document_sections
                            (id, version_id, section_type, section_ref, heading,
                             content, order_no, token_count, metadata_json)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (id) DO UPDATE
                        SET content       = EXCLUDED.content,
                            heading       = EXCLUDED.heading,
                            order_no      = EXCLUDED.order_no,
                            token_count   = EXCLUDED.token_count,
                            embedding     = NULL
                        RETURNING (xmax = 0) AS is_insert
                        """,
                        (section_id, version_id, sec.section_type, sec.section_ref,
                         sec.heading, sec.content, sec.order_no,
                         len(sec.content.split()),
                         psycopg.types.json.Jsonb(sec.metadata)),
                    )
                    row = cur.fetchone()
                    if row and row["is_insert"]:
                        inserted += 1
                    else:
                        updated += 1

            conn.commit()

        return inserted, updated

    def embed_pending(self, batch_size: int = EMBED_BATCH_SIZE) -> int:
        """embedding IS NULL 인 섹션을 모두 임베딩한다. 처리한 섹션 수 반환."""
        from app.embeddings import embed_texts

        with psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ds.id, d.title, ds.section_ref, ds.heading, ds.content
                    FROM document_sections ds
                    JOIN document_versions dv ON dv.id = ds.version_id
                    JOIN documents d          ON d.id  = dv.document_id
                    WHERE ds.embedding IS NULL
                    ORDER BY ds.created_at, ds.id
                    """
                )
                rows = cur.fetchall()

            if not rows:
                return 0

            register_vector(conn)
            total = 0

            for i in range(0, len(rows), batch_size):
                batch = rows[i : i + batch_size]
                texts = [
                    " ".join(filter(None, [
                        r["title"],
                        r["section_ref"] or "",
                        r["heading"] or "",
                        r["content"],
                    ]))
                    for r in batch
                ]
                embeddings = embed_texts(texts)

                with conn.cursor() as cur:
                    for row, emb in zip(batch, embeddings, strict=True):
                        cur.execute(
                            "UPDATE document_sections SET embedding = %s WHERE id = %s",
                            (Vector(emb), row["id"]),
                        )
                conn.commit()
                total += len(batch)
                print(f"  임베딩: {total}/{len(rows)}", end="\r", flush=True)

            print()
            return total
