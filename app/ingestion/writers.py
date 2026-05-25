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
        """문서·버전·노드 트리를 upsert한다. (신규 노드 수, 갱신 노드 수) 반환."""
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
                    (
                        doc_id,
                        doc.source_system,
                        doc.source_id,
                        doc.doc_type,
                        doc.authority,
                        doc.title,
                        doc.canonical_url,
                        version_id,
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO document_versions
                        (id, document_id, version_label, effective_from, effective_to,
                         publish_date, status, raw_text, hash_sha256, metadata_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        version_id,
                        doc_id,
                        v.version_label,
                        v.effective_from,
                        v.effective_to,
                        v.publish_date,
                        v.status,
                        v.raw_text,
                        content_hash,
                        psycopg.types.json.Jsonb(v.metadata),
                    ),
                )

                # 노드를 depth 순으로 정렬해 부모가 항상 먼저 삽입되도록 보장
                sorted_nodes = sorted(v.nodes, key=lambda n: n.depth)

                inserted = updated = 0
                for node in sorted_nodes:
                    node_db_id = f"{version_id}:{node.node_id}"
                    parent_db_id = f"{version_id}:{node.parent_id}" if node.parent_id else None
                    token_count = len(node.content.split()) if node.content else None

                    cur.execute(
                        """
                        INSERT INTO document_nodes
                            (id, version_id, parent_id, node_type, ref, title,
                             content, depth, order_no, token_count, metadata_json)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (id) DO UPDATE
                        SET content       = EXCLUDED.content,
                            title         = EXCLUDED.title,
                            order_no      = EXCLUDED.order_no,
                            token_count   = EXCLUDED.token_count,
                            embedding     = NULL,
                            embedding_model = NULL
                        RETURNING (xmax = 0) AS is_insert
                        """,
                        (
                            node_db_id,
                            version_id,
                            parent_db_id,
                            node.node_type,
                            node.ref,
                            node.title,
                            node.content,
                            node.depth,
                            node.order_no,
                            token_count,
                            psycopg.types.json.Jsonb(node.metadata),
                        ),
                    )
                    row = cur.fetchone()
                    if row and row["is_insert"]:
                        inserted += 1
                    else:
                        updated += 1

            conn.commit()

        return inserted, updated

    def embed_pending(
        self,
        batch_size: int = EMBED_BATCH_SIZE,
        model: str = "text-embedding-3-small",
    ) -> int:
        """embedding IS NULL 인 노드를 일괄 임베딩한다. 처리 수 반환.

        depth <= 1 (조·항) 이고 content 가 있는 노드만 임베딩 대상으로 삼는다.
        호·목은 너무 짧고 항 임베딩이 커버한다.
        """
        from app.embeddings import embed_texts

        with psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT dn.id,
                           d.title  AS doc_title,
                           dn.ref,
                           dn.title AS node_title,
                           dn.content
                    FROM document_nodes dn
                    JOIN document_versions dv ON dv.id = dn.version_id
                    JOIN documents d          ON d.id  = dv.document_id
                    WHERE dn.embedding IS NULL
                      AND dn.content   IS NOT NULL
                      AND dn.depth     <= 1
                    ORDER BY dn.created_at, dn.id
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
                    " ".join(
                        filter(
                            None,
                            [
                                r["doc_title"],
                                r["ref"] or "",
                                r["node_title"] or "",
                                r["content"],
                            ],
                        )
                    )
                    for r in batch
                ]
                embeddings = embed_texts(texts)

                with conn.cursor() as cur:
                    for row, emb in zip(batch, embeddings, strict=True):
                        cur.execute(
                            """
                            UPDATE document_nodes
                            SET embedding = %s, embedding_model = %s
                            WHERE id = %s
                            """,
                            (Vector(emb), model, row["id"]),
                        )
                conn.commit()
                total += len(batch)
                print(f"  임베딩: {total}/{len(rows)}", end="\r", flush=True)

            print()
            return total
