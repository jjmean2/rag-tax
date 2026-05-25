"""PostgreSQL 검색·조회 스토어."""
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

    # ------------------------------------------------------------------
    # 임베딩 생성
    # ------------------------------------------------------------------

    def ensure_node_embeddings(self) -> int:
        """embedding IS NULL 인 노드를 일괄 임베딩한다. 처리 수 반환."""
        with self.connect() as conn:
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

            texts = [
                " ".join(filter(None, [
                    r["doc_title"],
                    r["ref"] or "",
                    r["node_title"] or "",
                    r["content"],
                ]))
                for r in rows
            ]
            embeddings = embed_texts(texts)

            with conn.cursor() as cur:
                for row, emb in zip(rows, embeddings, strict=True):
                    cur.execute(
                        "UPDATE document_nodes SET embedding = %s WHERE id = %s",
                        (Vector(emb), row["id"]),
                    )
            conn.commit()
            return len(rows)

    # ------------------------------------------------------------------
    # 내부: 현행 버전 목록
    # ------------------------------------------------------------------

    def _chosen_versions(
        self,
        as_of: date | None,
        doc_types: list[str] | None,
        authorities: list[str] | None,
    ) -> list[dict[str, Any]]:
        doc_types = doc_types or []
        authorities = authorities or []
        with self.connect() as conn:
            with conn.cursor() as cur:
                if as_of is None:
                    cur.execute(
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
                               dv.status
                        FROM documents d
                        JOIN document_versions dv ON dv.id = d.current_version_id
                        WHERE (cardinality(%s::text[]) = 0 OR d.doc_type   = ANY(%s::text[]))
                          AND (cardinality(%s::text[]) = 0 OR d.authority  = ANY(%s::text[]))
                        """,
                        (doc_types, doc_types, authorities, authorities),
                    )
                else:
                    cur.execute(
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
                               dv.status
                        FROM documents d
                        JOIN document_versions dv ON dv.document_id = d.id
                        WHERE (cardinality(%s::text[]) = 0 OR d.doc_type   = ANY(%s::text[]))
                          AND (cardinality(%s::text[]) = 0 OR d.authority  = ANY(%s::text[]))
                          AND (dv.effective_from IS NULL OR dv.effective_from <= %s)
                          AND (dv.effective_to   IS NULL OR dv.effective_to   >= %s)
                        ORDER BY d.id, dv.publish_date DESC NULLS LAST, dv.id DESC
                        """,
                        (doc_types, doc_types, authorities, authorities, as_of, as_of),
                    )
                return cur.fetchall()

    # ------------------------------------------------------------------
    # 검색
    # ------------------------------------------------------------------

    def search_sections(
        self,
        as_of: date | None,
        doc_types: list[str] | None,
        authorities: list[str] | None,
        query_embedding: list[float] | None,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        chosen = self._chosen_versions(as_of, doc_types, authorities)
        if not chosen:
            return []

        version_ids = [r["version_id"] for r in chosen]
        version_map = {r["version_id"]: r for r in chosen}

        with self.connect() as conn:
            with conn.cursor() as cur:
                if query_embedding is None:
                    cur.execute(
                        """
                        SELECT n.id,
                               n.version_id,
                               n.node_type,
                               n.ref,
                               n.title     AS node_title,
                               n.content,
                               n.depth,
                               n.parent_id,
                               0.0::double precision AS semantic_score,
                               p.ref       AS parent_ref,
                               p.title     AS parent_title
                        FROM document_nodes n
                        LEFT JOIN document_nodes p ON p.id = n.parent_id
                        WHERE n.version_id = ANY(%s::text[])
                          AND n.embedding IS NOT NULL
                        ORDER BY n.version_id, n.order_no
                        LIMIT %s
                        """,
                        (version_ids, top_k),
                    )
                else:
                    cur.execute(
                        """
                        SELECT n.id,
                               n.version_id,
                               n.node_type,
                               n.ref,
                               n.title     AS node_title,
                               n.content,
                               n.depth,
                               n.parent_id,
                               1 - (n.embedding <=> %s) AS semantic_score,
                               p.ref       AS parent_ref,
                               p.title     AS parent_title
                        FROM document_nodes n
                        LEFT JOIN document_nodes p ON p.id = n.parent_id
                        WHERE n.version_id = ANY(%s::text[])
                          AND n.embedding IS NOT NULL
                        ORDER BY n.embedding <=> %s
                        LIMIT %s
                        """,
                        (Vector(query_embedding), version_ids,
                         Vector(query_embedding), top_k),
                    )
                nodes = cur.fetchall()

                # 인용 관계 일괄 조회
                node_ids = [n["id"] for n in nodes]
                citations_by_node: dict[str, list[dict[str, Any]]] = {
                    nid: [] for nid in node_ids
                }
                if node_ids:
                    cur.execute(
                        """
                        SELECT id, from_node_id, to_document_id,
                               to_node_ref, citation_text, confidence
                        FROM citations
                        WHERE from_node_id = ANY(%s::text[])
                        """,
                        (node_ids,),
                    )
                    for cit in cur.fetchall():
                        citations_by_node[cit["from_node_id"]].append(cit)

        results: list[dict[str, Any]] = []
        for n in nodes:
            v = version_map[n["version_id"]]
            # 항 노드면 "제19조 ①", 조 노드면 "제19조" 형태로 표시 참조 구성
            if n["parent_ref"]:
                section_ref = f"{n['parent_ref']} {n['ref']}".strip()
            else:
                section_ref = n["ref"] or ""

            results.append({
                "id": n["id"],
                "documentId": v["document_id"],
                "documentVersionId": n["version_id"],
                "title": v["title"],
                "docType": v["doc_type"],
                "authority": v["authority"],
                "canonicalUrl": v["canonical_url"],
                "date": v["publish_date"],
                "nodeType": n["node_type"],
                "depth": n["depth"],
                "sectionRef": section_ref,
                "heading": n["node_title"],
                "parentRef": n["parent_ref"],
                "parentTitle": n["parent_title"],
                "snippet": n["content"],
                "semanticScore": float(n.get("semantic_score") or 0.0),
                "citations": citations_by_node.get(n["id"], []),
            })
        return results

    # ------------------------------------------------------------------
    # 문서 전체 조회
    # ------------------------------------------------------------------

    def get_document(
        self, document_id: str, as_of: date | None
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, title, doc_type, authority,
                           canonical_url, current_version_id
                    FROM documents WHERE id = %s
                    """,
                    (document_id,),
                )
                document = cur.fetchone()
                if document is None:
                    return None

                if as_of is None:
                    cur.execute(
                        """
                        SELECT id, version_label, effective_from, effective_to,
                               publish_date, status, raw_text, metadata_json
                        FROM document_versions WHERE id = %s
                        """,
                        (document["current_version_id"],),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, version_label, effective_from, effective_to,
                               publish_date, status, raw_text, metadata_json
                        FROM document_versions
                        WHERE document_id = %s
                          AND (effective_from IS NULL OR effective_from <= %s)
                          AND (effective_to   IS NULL OR effective_to   >= %s)
                        ORDER BY publish_date DESC NULLS LAST, id DESC
                        LIMIT 1
                        """,
                        (document_id, as_of, as_of),
                    )
                version = cur.fetchone()
                if version is None:
                    return None

                cur.execute(
                    """
                    SELECT id, parent_id, node_type, ref, title,
                           content, depth, order_no, token_count, metadata_json
                    FROM document_nodes
                    WHERE version_id = %s
                    ORDER BY order_no
                    """,
                    (version["id"],),
                )
                nodes = cur.fetchall()

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
                "metadata": version["metadata_json"],
            },
            "nodes": [
                {
                    "id": n["id"],
                    "parentId": n["parent_id"],
                    "nodeType": n["node_type"],
                    "ref": n["ref"],
                    "title": n["title"],
                    "content": n["content"],
                    "depth": n["depth"],
                    "orderNo": n["order_no"],
                    "tokenCount": n["token_count"],
                    "metadata": n["metadata_json"],
                }
                for n in nodes
            ],
        }
