from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date
from typing import Any

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from app.embeddings import embed_texts

DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/rag_tax"


class PostgresStore:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

    @contextmanager
    def connect(self, register_pgvector: bool = True):
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            if register_pgvector:
                register_vector(connection)
            yield connection

    def init_schema(self) -> None:
        ddl = """
        CREATE EXTENSION IF NOT EXISTS vector;

        CREATE TABLE IF NOT EXISTS documents (
          id                 TEXT PRIMARY KEY,
          source_system      TEXT NOT NULL,
          source_id          TEXT NOT NULL,
          doc_type           TEXT NOT NULL,
          authority          TEXT NOT NULL,
          title              TEXT NOT NULL,
          canonical_url      TEXT,
          current_version_id TEXT,
          created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          UNIQUE (source_system, source_id)
        );

        CREATE TABLE IF NOT EXISTS document_versions (
          id               TEXT PRIMARY KEY,
          document_id      TEXT NOT NULL REFERENCES documents(id),
          version_label    TEXT,
          effective_from   DATE,
          effective_to     DATE,
          publish_date     DATE,
          status           TEXT NOT NULL,
          raw_text         TEXT NOT NULL,
          normalized_text  TEXT,
          hash_sha256      TEXT NOT NULL,
          metadata_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          UNIQUE (document_id, hash_sha256)
        );

        CREATE INDEX IF NOT EXISTS idx_document_versions_effective
        ON document_versions (effective_from, effective_to);

        CREATE TABLE IF NOT EXISTS document_sections (
          id                TEXT PRIMARY KEY,
          version_id        TEXT NOT NULL REFERENCES document_versions(id),
          parent_section_id TEXT REFERENCES document_sections(id),
          section_type      TEXT NOT NULL,
          section_ref       TEXT,
          heading           TEXT,
          content           TEXT NOT NULL,
                    embedding         VECTOR(384),
          order_no          INT NOT NULL,
          token_count       INT,
          metadata_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

                ALTER TABLE IF EXISTS document_sections
                    ADD COLUMN IF NOT EXISTS embedding VECTOR(384);

        CREATE INDEX IF NOT EXISTS idx_document_sections_version_order
        ON document_sections (version_id, order_no);

                CREATE INDEX IF NOT EXISTS idx_document_sections_embedding
                ON document_sections USING hnsw (embedding vector_cosine_ops);

        CREATE TABLE IF NOT EXISTS citations (
          id              TEXT PRIMARY KEY,
          from_section_id TEXT NOT NULL REFERENCES document_sections(id),
          to_document_id  TEXT REFERENCES documents(id),
          to_section_ref  TEXT,
          citation_text   TEXT,
          confidence      NUMERIC(4,3),
          created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS tags (
          id       TEXT PRIMARY KEY,
          name     TEXT NOT NULL UNIQUE,
          category TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS document_tags (
          document_id TEXT NOT NULL REFERENCES documents(id),
          tag_id      TEXT NOT NULL REFERENCES tags(id),
          PRIMARY KEY (document_id, tag_id)
        );

        CREATE TABLE IF NOT EXISTS ingestion_jobs (
          id             TEXT PRIMARY KEY,
          source_system  TEXT NOT NULL,
          started_at     TIMESTAMPTZ NOT NULL,
          finished_at    TIMESTAMPTZ,
          status         TEXT NOT NULL,
          inserted_count INT NOT NULL DEFAULT 0,
          updated_count  INT NOT NULL DEFAULT 0,
          error_log      TEXT
        );
        """

        # The vector extension may not exist yet on a fresh database.
        with self.connect(register_pgvector=False) as connection:
            with connection.cursor() as cursor:
                cursor.execute(ddl)
            connection.commit()

    def seed_documents(self, sample_documents: dict[str, Any]) -> None:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                for document in sample_documents["documents"]:
                    cursor.execute(
                        """
                        INSERT INTO documents (id, source_system, source_id, doc_type, authority, title, canonical_url, current_version_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE
                        SET source_system = EXCLUDED.source_system,
                            source_id = EXCLUDED.source_id,
                            doc_type = EXCLUDED.doc_type,
                            authority = EXCLUDED.authority,
                            title = EXCLUDED.title,
                            canonical_url = EXCLUDED.canonical_url,
                            current_version_id = EXCLUDED.current_version_id,
                            updated_at = NOW()
                        """,
                        (
                            document["id"],
                            document["source_system"],
                            document["source_id"],
                            document["doc_type"],
                            document["authority"],
                            document["title"],
                            document["canonical_url"],
                            document["current_version_id"],
                        ),
                    )

                    for version in document["versions"]:
                        cursor.execute(
                            """
                            INSERT INTO document_versions (
                              id, document_id, version_label, effective_from, effective_to,
                              publish_date, status, raw_text, normalized_text,
                              hash_sha256, metadata_json
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, md5(%s), %s::jsonb)
                            ON CONFLICT (id) DO UPDATE
                            SET version_label = EXCLUDED.version_label,
                                effective_from = EXCLUDED.effective_from,
                                effective_to = EXCLUDED.effective_to,
                                publish_date = EXCLUDED.publish_date,
                                status = EXCLUDED.status,
                                raw_text = EXCLUDED.raw_text,
                                normalized_text = EXCLUDED.normalized_text,
                                metadata_json = EXCLUDED.metadata_json
                            """,
                            (
                                version["id"],
                                document["id"],
                                version["version_label"],
                                version["effective_from"],
                                version["effective_to"],
                                version["publish_date"],
                                version["status"],
                                version["raw_text"],
                                version["normalized_text"],
                                version["raw_text"],
                                psycopg.types.json.Jsonb(version.get("metadata", {})),
                            ),
                        )

                        for section in version["sections"]:
                            token_count = len(section["content"].split())
                            cursor.execute(
                                """
                                INSERT INTO document_sections (
                                                                    id, version_id, parent_section_id, section_type, section_ref,
                                                                    heading, content, embedding, order_no, token_count, metadata_json
                                )
                                                                VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s::jsonb)
                                ON CONFLICT (id) DO UPDATE
                                SET parent_section_id = EXCLUDED.parent_section_id,
                                    section_type = EXCLUDED.section_type,
                                    section_ref = EXCLUDED.section_ref,
                                    heading = EXCLUDED.heading,
                                    content = EXCLUDED.content,
                                                                        embedding = EXCLUDED.embedding,
                                    order_no = EXCLUDED.order_no,
                                    token_count = EXCLUDED.token_count,
                                    metadata_json = EXCLUDED.metadata_json
                                """,
                                (
                                    section["id"],
                                    version["id"],
                                    section["parent_section_id"],
                                    section["section_type"],
                                    section["section_ref"],
                                    section["heading"],
                                    section["content"],
                                    section["order_no"],
                                    token_count,
                                    psycopg.types.json.Jsonb(
                                        section.get("metadata", {})
                                    ),
                                ),
                            )

                for citation in sample_documents["citations"]:
                    cursor.execute(
                        """
                        INSERT INTO citations (id, from_section_id, to_document_id, to_section_ref, citation_text, confidence)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE
                        SET from_section_id = EXCLUDED.from_section_id,
                            to_document_id = EXCLUDED.to_document_id,
                            to_section_ref = EXCLUDED.to_section_ref,
                            citation_text = EXCLUDED.citation_text,
                            confidence = EXCLUDED.confidence
                        """,
                        (
                            citation["id"],
                            citation["from_section_id"],
                            citation.get("to_document_id"),
                            citation.get("to_section_ref"),
                            citation.get("citation_text"),
                            citation.get("confidence"),
                        ),
                    )

            connection.commit()

    def ensure_section_embeddings(self) -> int:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT ds.id,
                           d.title,
                           ds.section_ref,
                           ds.heading,
                           ds.content
                    FROM document_sections ds
                    JOIN document_versions dv ON dv.id = ds.version_id
                    JOIN documents d ON d.id = dv.document_id
                    WHERE ds.embedding IS NULL
                    ORDER BY ds.created_at, ds.id
                    """
                )
                rows = cursor.fetchall()

                if not rows:
                    return 0

                texts = [
                    " ".join(
                        part
                        for part in (
                            row["title"],
                            row["section_ref"] or "",
                            row["heading"] or "",
                            row["content"],
                        )
                        if part
                    )
                    for row in rows
                ]
                embeddings = embed_texts(texts)

                for row, embedding in zip(rows, embeddings, strict=True):
                    cursor.execute(
                        """
                        UPDATE document_sections
                        SET embedding = %s
                        WHERE id = %s
                        """,
                        (Vector(embedding), row["id"]),
                    )

            connection.commit()
            return len(rows)

    def _chosen_versions(
        self,
        as_of: date | None,
        doc_types: list[str] | None,
        authorities: list[str] | None,
    ) -> list[dict[str, Any]]:
        doc_types = doc_types or []
        authorities = authorities or []
        with self.connect() as connection:
            with connection.cursor() as cursor:
                if as_of is None:
                    cursor.execute(
                        """
                        SELECT d.id AS document_id,
                               d.title,
                               d.doc_type,
                               d.authority,
                               d.canonical_url,
                               dv.id AS version_id,
                               dv.publish_date,
                               dv.effective_from,
                               dv.effective_to,
                               dv.status,
                               dv.raw_text,
                               dv.normalized_text,
                               dv.metadata_json
                        FROM documents d
                        JOIN document_versions dv ON dv.id = d.current_version_id
                        WHERE (%s::text[] IS NULL OR cardinality(%s::text[]) = 0 OR d.doc_type = ANY(%s::text[]))
                          AND (%s::text[] IS NULL OR cardinality(%s::text[]) = 0 OR d.authority = ANY(%s::text[]))
                        """,
                        (
                            doc_types,
                            doc_types,
                            doc_types,
                            authorities,
                            authorities,
                            authorities,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT DISTINCT ON (d.id)
                               d.id AS document_id,
                               d.title,
                               d.doc_type,
                               d.authority,
                               d.canonical_url,
                               dv.id AS version_id,
                               dv.publish_date,
                               dv.effective_from,
                               dv.effective_to,
                               dv.status,
                               dv.raw_text,
                               dv.normalized_text,
                               dv.metadata_json
                        FROM documents d
                        JOIN document_versions dv ON dv.document_id = d.id
                        WHERE (%s::text[] IS NULL OR cardinality(%s::text[]) = 0 OR d.doc_type = ANY(%s::text[]))
                          AND (%s::text[] IS NULL OR cardinality(%s::text[]) = 0 OR d.authority = ANY(%s::text[]))
                          AND (dv.effective_from IS NULL OR dv.effective_from <= %s)
                          AND (dv.effective_to IS NULL OR dv.effective_to >= %s)
                        ORDER BY d.id, dv.publish_date DESC NULLS LAST, dv.id DESC
                        """,
                        (
                            doc_types,
                            doc_types,
                            doc_types,
                            authorities,
                            authorities,
                            authorities,
                            as_of,
                            as_of,
                        ),
                    )
                return cursor.fetchall()

    def search_sections(
        self,
        as_of: date | None,
        doc_types: list[str] | None,
        authorities: list[str] | None,
        query_embedding: list[float] | None,
    ) -> list[dict[str, Any]]:
        chosen_versions = self._chosen_versions(as_of, doc_types, authorities)
        if not chosen_versions:
            return []

        version_map = {row["version_id"]: row for row in chosen_versions}
        version_ids = list(version_map.keys())

        with self.connect() as connection:
            with connection.cursor() as cursor:
                if query_embedding is None:
                    cursor.execute(
                        """
                        SELECT id,
                               version_id,
                               section_type,
                               section_ref,
                               heading,
                               content,
                               order_no,
                               0.0::double precision AS semantic_score
                        FROM document_sections
                        WHERE version_id = ANY(%s::text[])
                        """,
                        (version_ids,),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT id,
                               version_id,
                               section_type,
                               section_ref,
                               heading,
                               content,
                               order_no,
                               1 - (embedding <=> %s) AS semantic_score
                        FROM document_sections
                        WHERE version_id = ANY(%s::text[])
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> %s, order_no
                        """,
                        (Vector(query_embedding), version_ids, Vector(query_embedding)),
                    )
                sections = cursor.fetchall()

                section_ids = [section["id"] for section in sections]
                citations_by_section: dict[str, list[dict[str, Any]]] = {
                    section_id: [] for section_id in section_ids
                }
                if section_ids:
                    cursor.execute(
                        """
                        SELECT id,
                               from_section_id,
                               to_document_id,
                               to_section_ref,
                               citation_text,
                               confidence
                        FROM citations
                        WHERE from_section_id = ANY(%s::text[])
                        """,
                        (section_ids,),
                    )
                    for citation in cursor.fetchall():
                        citations_by_section[citation["from_section_id"]].append(
                            citation
                        )

        combined: list[dict[str, Any]] = []
        for section in sections:
            version = version_map[section["version_id"]]
            combined.append(
                {
                    "id": section["id"],
                    "documentId": version["document_id"],
                    "documentVersionId": section["version_id"],
                    "title": version["title"],
                    "docType": version["doc_type"],
                    "authority": version["authority"],
                    "canonicalUrl": version["canonical_url"],
                    "date": version["publish_date"],
                    "sectionRef": section["section_ref"],
                    "heading": section["heading"],
                    "snippet": section["content"],
                    "semanticScore": float(section.get("semantic_score") or 0.0),
                    "citations": citations_by_section.get(section["id"], []),
                }
            )
        return combined

    def get_document(
        self, document_id: str, as_of: date | None
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id,
                           title,
                           doc_type,
                           authority,
                           canonical_url,
                           current_version_id
                    FROM documents
                    WHERE id = %s
                    """,
                    (document_id,),
                )
                document = cursor.fetchone()
                if document is None:
                    return None

                if as_of is None:
                    cursor.execute(
                        """
                        SELECT id,
                               version_label,
                               effective_from,
                               effective_to,
                               publish_date,
                               status,
                               raw_text,
                               normalized_text,
                               metadata_json
                        FROM document_versions
                        WHERE id = %s
                        """,
                        (document["current_version_id"],),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT id,
                               version_label,
                               effective_from,
                               effective_to,
                               publish_date,
                               status,
                               raw_text,
                               normalized_text,
                               metadata_json
                        FROM document_versions
                        WHERE document_id = %s
                          AND (effective_from IS NULL OR effective_from <= %s)
                          AND (effective_to IS NULL OR effective_to >= %s)
                        ORDER BY publish_date DESC NULLS LAST, id DESC
                        LIMIT 1
                        """,
                        (document_id, as_of, as_of),
                    )

                version = cursor.fetchone()
                if version is None:
                    return None

                cursor.execute(
                    """
                    SELECT id,
                           version_id,
                           parent_section_id,
                           section_type,
                           section_ref,
                           heading,
                           content,
                           order_no,
                           token_count,
                           metadata_json
                    FROM document_sections
                    WHERE version_id = %s
                    ORDER BY order_no
                    """,
                    (version["id"],),
                )
                sections = cursor.fetchall()

        return {
            "document": {
                "id": document["id"],
                "title": document["title"],
                "docType": document["doc_type"],
                "authority": document["authority"],
                "canonicalUrl": document["canonical_url"],
            },
            "version": {
                "id": version["id"],
                "versionLabel": version["version_label"],
                "effectiveFrom": version["effective_from"],
                "effectiveTo": version["effective_to"],
                "publishDate": version["publish_date"],
                "status": version["status"],
                "rawText": version["raw_text"],
                "normalizedText": version["normalized_text"],
                "metadata": version["metadata_json"],
            },
            "sections": [
                {
                    "id": section["id"],
                    "versionId": section["version_id"],
                    "parentSectionId": section["parent_section_id"],
                    "sectionType": section["section_type"],
                    "sectionRef": section["section_ref"],
                    "heading": section["heading"],
                    "content": section["content"],
                    "orderNo": section["order_no"],
                    "tokenCount": section["token_count"],
                    "metadata": section["metadata_json"],
                }
                for section in sections
            ],
        }
