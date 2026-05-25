"""PostgreSQL 검색·조회 스토어."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date
from typing import Any, cast

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.rows import DictRow, dict_row

DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/rag_tax"


class PostgresStore:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

    @contextmanager
    def connect(
        self, register_pgvector: bool = True
    ) -> Generator[psycopg.Connection[DictRow], None, None]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:  # type: ignore[call-overload]
            conn = cast(psycopg.Connection[DictRow], connection)
            if register_pgvector:
                register_vector(conn)
            yield conn

    # ------------------------------------------------------------------
    # 임베딩 생성
    # ------------------------------------------------------------------

    def ensure_node_embeddings(self) -> int:
        """미처리 노드를 임베딩한다. 처리된 청크 수 반환."""
        from app.ingestion.writers import IngestWriter

        return IngestWriter(dsn=self.dsn).embed_pending()

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

    @staticmethod
    def _build_article_context(nodes: list[dict[str, Any]]) -> str:
        """노드 목록을 LLM 컨텍스트용 텍스트로 조립한다."""
        indent = ["", "  ", "    ", "      "]
        lines = []
        for n in sorted(nodes, key=lambda x: x["order_no"]):
            pad = indent[min(n["depth"], 3)]
            ref = n["ref"] or ""
            title_part = f" ({n['title']})" if n.get("title") else ""
            content = n["content"] or ""
            prefix = f"{pad}{ref}{title_part}".rstrip()
            if content:
                line = f"{prefix} {content}".strip() if prefix.strip() else content
            else:
                line = prefix
            if line.strip():
                lines.append(line)
        return "\n".join(lines)

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
                        (
                            Vector(query_embedding),
                            version_ids,
                            Vector(query_embedding),
                            top_k,
                        ),
                    )
                nodes = cur.fetchall()

                # 매칭 노드 → depth=0 조(article) 루트 매핑
                # depth > 1 노드도 있으므로 upward CTE로 depth=0 조상을 정확히 찾는다
                matched_ids = [n["id"] for n in nodes]
                node_to_root: dict[str, str] = {}
                if matched_ids:
                    cur.execute(
                        """
                        WITH RECURSIVE up AS (
                            SELECT id AS origin_id, id, parent_id, depth
                            FROM document_nodes
                            WHERE id = ANY(%s::text[])
                            UNION ALL
                            SELECT up.origin_id, n.id, n.parent_id, n.depth
                            FROM document_nodes n
                            JOIN up ON n.id = up.parent_id
                            WHERE up.depth > 0
                        )
                        SELECT DISTINCT ON (origin_id) origin_id, id AS root_id
                        FROM up
                        WHERE depth = 0
                        ORDER BY origin_id
                        """,
                        (matched_ids,),
                    )
                    for row in cur.fetchall():
                        node_to_root[row["origin_id"]] = row["root_id"]

                # node_to_root 에 없는 노드(이미 depth=0인 경우) 보완
                for n in nodes:
                    if n["id"] not in node_to_root:
                        node_to_root[n["id"]] = n["id"]

                root_ids_set: set[str] = set(node_to_root.values())
                root_ids: list[str] = list(root_ids_set)

                # 조 루트의 전체 서브트리(항/호/목 포함)를 recursive CTE로 일괄 조회
                context_by_root: dict[str, list[dict[str, Any]]] = {rid: [] for rid in root_ids}
                root_node_ref: dict[str, str] = {}
                if root_ids:
                    cur.execute(
                        """
                        WITH RECURSIVE subtree AS (
                            SELECT id, parent_id, node_type, ref, title,
                                   content, depth, order_no,
                                   id AS root_id
                            FROM document_nodes
                            WHERE id = ANY(%s::text[])
                            UNION ALL
                            SELECT n.id, n.parent_id, n.node_type, n.ref, n.title,
                                   n.content, n.depth, n.order_no,
                                   s.root_id
                            FROM document_nodes n
                            JOIN subtree s ON n.parent_id = s.id
                        )
                        SELECT * FROM subtree ORDER BY root_id, order_no
                        """,
                        (root_ids,),
                    )
                    for cn in cur.fetchall():
                        context_by_root[cn["root_id"]].append(cn)
                        if cn["depth"] == 0:
                            root_node_ref[cn["root_id"]] = cn["ref"] or ""

                # 인용 관계 일괄 조회
                node_ids = [n["id"] for n in nodes]
                citations_by_node: dict[str, list[dict[str, Any]]] = {nid: [] for nid in node_ids}
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
            root_id = node_to_root[n["id"]]
            article_ref = root_node_ref.get(root_id, n["parent_ref"] or "")
            section_ref = (
                f"{n['parent_ref']} {n['ref']}".strip() if n["parent_ref"] else n["ref"] or ""
            )
            context_text = self._build_article_context(context_by_root.get(root_id, []))

            results.append(
                {
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
                    "articleRef": article_ref,
                    "sectionRef": section_ref,
                    "heading": n["node_title"],
                    "snippet": n["content"],
                    "context": context_text,
                    "semanticScore": float(n.get("semantic_score") or 0.0),
                    "citations": citations_by_node.get(n["id"], []),
                }
            )
        return results

    # ------------------------------------------------------------------
    # 문서 전체 조회
    # ------------------------------------------------------------------

    def get_document(self, document_id: str, as_of: date | None) -> dict[str, Any] | None:
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
