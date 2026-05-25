"""DB 업서트 및 임베딩 생성."""

from __future__ import annotations

import hashlib
import os
import sys
from collections import defaultdict

import psycopg
import psycopg.rows
import psycopg.types.json
from pgvector import Vector
from pgvector.psycopg import register_vector

from app.ingestion.models import RawDocument

DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/rag_tax"
EMBED_BATCH_SIZE = 256
TOKEN_LIMIT = 400  # 청크당 최대 토큰 수 (어절 기준 추정)


# ---------------------------------------------------------------------------
# 청크 할당 헬퍼 (모듈 레벨 순수 함수)
# ---------------------------------------------------------------------------


def _token_estimate(text: str) -> int:
    """어절 수 기반 토큰 수 추정."""
    return len(text.split())


def _format_node_line(node: dict) -> str:
    """노드 한 줄 텍스트: 'ref (title) content' 형태.

    content가 이미 ref로 시작하면(법제처 XML의 일반적 패턴) ref/title 프리픽스를 생략한다.
    """
    ref = node.get("ref") or ""
    content = node.get("content") or ""
    if content and ref and content.lstrip().startswith(ref):
        return content
    title = f"({node['title']})" if node.get("title") else ""
    return f"{ref}{title} {content}".strip()


def _build_subtree_text(
    node_id: str,
    nodes_by_id: dict,
    children_map: dict,
    _indent: int = 0,
) -> str:
    """노드와 모든 자손의 텍스트를 들여쓰기 포함해 조립한다."""
    node = nodes_by_id[node_id]
    pad = "  " * _indent
    line = _format_node_line(node)
    parts = [f"{pad}{line}"] if line else []
    for child_id in children_map.get(node_id, []):
        child_text = _build_subtree_text(child_id, nodes_by_id, children_map, _indent + 1)
        if child_text:
            parts.append(child_text)
    return "\n".join(parts)


def _print_chunk_plan(
    chunk_assignments: list[tuple[str, str]],
    covered_ids: list[str],
) -> None:
    """청킹 계획을 stdout에 출력한다 (dry-run용)."""

    try:
        print(f"\n청킹 계획 — 청크 {len(chunk_assignments)}개 / 커버된 노드 {len(covered_ids)}개\n")
        for i, (node_id, text) in enumerate(chunk_assignments, 1):
            tokens = _token_estimate(text)
            preview = text
            short_id = node_id.split(":")[-1] if ":" in node_id else node_id
            print(f"  [{i:3}] {short_id}  ({tokens} 토큰)")
            print(f"        {preview}\n")
            print("-" * 20)
        print()
    except BrokenPipeError:
        sys.stderr.close()  # Python 종료 시 "Exception ignored" 메시지 억제
        sys.exit(0)


def _get_descendants(node_id: str, children_map: dict) -> set[str]:
    """children_map 기준 node_id의 모든 자손 ID를 반환한다."""
    result: set[str] = set()
    stack = list(children_map.get(node_id, []))
    while stack:
        child = stack.pop()
        result.add(child)
        stack.extend(children_map.get(child, []))
    return result


def _assign_chunks(
    node_id: str,
    nodes_by_id: dict,
    children_map: dict,
    token_limit: int,
    context_prefix: str = "",
) -> list[tuple[str, str]]:
    """depth-agnostic 바텀업 청크 할당.

    노드와 모든 자손을 합친 텍스트가 token_limit 이내이면 이 노드를 청크 루트로 확정.
    초과하면 자식 각각에 재귀 적용하고, 현재 노드의 텍스트를 자식 청크의 컨텍스트 프리픽스로 추가.

    반환: [(chunk_root_node_id, embed_text), ...]
    """
    children = children_map.get(node_id, [])
    subtree_text = _build_subtree_text(node_id, nodes_by_id, children_map)

    if not subtree_text.strip():
        return []

    candidate = f"{context_prefix}\n{subtree_text}".strip() if context_prefix else subtree_text

    if _token_estimate(candidate) <= token_limit or not children:
        # 토큰 한계 이내이거나 리프 노드 → 이 노드가 청크 루트
        return [(node_id, candidate)]

    # 초과: 자식으로 분기. 현재 노드의 한 줄 텍스트를 자식 청크 프리픽스에 추가
    own_line = _format_node_line(nodes_by_id[node_id])
    child_prefix = f"{context_prefix}\n{own_line}".strip() if own_line else context_prefix

    result = []
    for child_id in sorted(children, key=lambda cid: nodes_by_id[cid]["order_no"]):
        result.extend(
            _assign_chunks(child_id, nodes_by_id, children_map, token_limit, child_prefix)
        )
    return result


# ---------------------------------------------------------------------------
# IngestWriter
# ---------------------------------------------------------------------------


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
                        SET content         = EXCLUDED.content,
                            title           = EXCLUDED.title,
                            order_no        = EXCLUDED.order_no,
                            token_count     = EXCLUDED.token_count,
                            chunk_status    = NULL
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
        token_limit: int = TOKEN_LIMIT,
        batch_size: int = EMBED_BATCH_SIZE,
        model: str = "text-embedding-3-small",
        limit: int | None = None,
        dry_run: bool = False,
    ) -> int:
        """미처리 노드를 depth-agnostic 바텀업 청킹으로 임베딩한다. 처리된 청크 수 반환.

        토큰 한계(token_limit) 이내에서 가능한 한 큰 단위를 청크로 확정하고,
        초과하면 자식 노드로 재귀한다. 청크에 포함된 자식 노드는 'parent-chunk'로
        표시해 중복 임베딩을 방지한다.
        dry_run=True 이면 청킹 계획만 출력하고 API 호출 및 DB 변경을 하지 않는다.
        limit 을 지정하면 청크 수를 제한한다 (테스트용).
        """
        from app.embeddings import embed_texts

        with psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row) as conn:  # type: ignore[call-overload]
            with conn.cursor() as cur:
                # depth 제한 없이 미처리 노드 전체 조회
                cur.execute(
                    """
                    SELECT dn.id, dn.version_id, dn.parent_id, dn.depth, dn.order_no,
                           dn.ref, dn.title, dn.content,
                           d.title AS doc_title
                    FROM document_nodes dn
                    JOIN document_versions dv ON dv.id = dn.version_id
                    JOIN documents d          ON d.id  = dv.document_id
                    WHERE dn.chunk_status IS NULL
                    ORDER BY dn.version_id, dn.depth, dn.order_no
                    """
                )
                rows: list[dict] = cur.fetchall()  # type: ignore[assignment]

        if not rows:
            return 0

        # 트리 구조 구성
        nodes_by_id: dict[str, dict] = {r["id"]: r for r in rows}
        children_map: dict[str, list[str]] = defaultdict(list)
        for r in rows:
            if r["parent_id"] and r["parent_id"] in nodes_by_id:
                children_map[r["parent_id"]].append(r["id"])

        # 루트 노드: 부모가 없거나 부모가 이미 처리된(본 배치에 없는) 노드
        roots = [r for r in rows if not r["parent_id"] or r["parent_id"] not in nodes_by_id]

        # 청크 할당
        chunk_assignments: list[tuple[str, str]] = []
        for root in roots:
            chunk_assignments.extend(
                _assign_chunks(
                    root["id"],
                    nodes_by_id,
                    children_map,
                    token_limit,
                    context_prefix=root["doc_title"] or "",
                )
            )

        if limit is not None:
            chunk_assignments = chunk_assignments[:limit]

        if not chunk_assignments:
            return 0

        chunk_root_ids = {nid for nid, _ in chunk_assignments}
        # 처리된 청크 루트의 자손만 covered로 마킹한다.
        # rows 전체에서 chunk_root_ids를 뺀 값을 쓰면 limit 적용 시
        # 미처리 청크의 노드까지 parent-chunk로 마킹되는 버그가 생긴다.
        covered_ids = list(
            {desc for nid in chunk_root_ids for desc in _get_descendants(nid, children_map)}
            - chunk_root_ids
        )

        if dry_run:
            _print_chunk_plan(chunk_assignments, covered_ids)
            return len(chunk_assignments)

        # 임베딩 생성 및 document_chunks 저장
        with psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row) as conn:  # type: ignore[call-overload]
            register_vector(conn)
            total = 0

            for i in range(0, len(chunk_assignments), batch_size):
                batch = chunk_assignments[i : i + batch_size]
                texts = [text for _, text in batch]
                embeddings = embed_texts(texts)

                with conn.cursor() as cur:
                    for (node_id, embed_text), emb in zip(batch, embeddings, strict=True):
                        version_id = nodes_by_id[node_id]["version_id"]
                        cur.execute(
                            """
                            INSERT INTO document_chunks
                                (id, version_id, node_id, embed_text, embedding, embedding_model)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO UPDATE
                            SET embed_text      = EXCLUDED.embed_text,
                                embedding       = EXCLUDED.embedding,
                                embedding_model = EXCLUDED.embedding_model
                            """,
                            (node_id, version_id, node_id, embed_text, Vector(emb), model),
                        )
                        cur.execute(
                            "UPDATE document_nodes SET chunk_status = 'chunk-root' WHERE id = %s",
                            (node_id,),
                        )
                conn.commit()
                total += len(batch)
                print(
                    f"  임베딩: {total}/{len(chunk_assignments)}",
                    end="\r",
                    flush=True,
                    file=sys.stderr,
                )

            # chunk-child: 청크 루트의 자손
            if covered_ids:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE document_nodes SET chunk_status = 'chunk-child'"
                        " WHERE id = ANY(%s::text[])",
                        (covered_ids,),
                    )
                conn.commit()

            # chunk-split: 배치에 있었지만 청크 루트도 자손도 아닌 노드
            # (서브트리가 너무 커서 자식으로 분기됨 — 내용은 자식 청크의 context prefix에 포함)
            # limit 사용 시 처리가 불완전하므로 마킹하지 않음
            if limit is None:
                all_assigned_ids = chunk_root_ids | set(covered_ids)
                split_ids = [r["id"] for r in rows if r["id"] not in all_assigned_ids]
                if split_ids:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE document_nodes SET chunk_status = 'chunk-split'"
                            " WHERE id = ANY(%s::text[])",
                            (split_ids,),
                        )
                    conn.commit()

        print()
        return total

    def verify_embed_coverage(self) -> dict:
        """임베딩 커버리지를 검증한다.

        확인 항목:
        1. 고아 노드: content가 있으나 chunk_status가 NULL인 채로 남은 노드
        2. 잘못된 chunk-child: 'chunk-child'로 마킹됐지만 'chunk-root' 조상이 없는 노드
        """
        with psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row) as conn:  # type: ignore[call-overload]
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*)                                                    AS total,
                        COUNT(*) FILTER (WHERE chunk_status = 'chunk-root')         AS chunk_roots,
                        COUNT(*) FILTER (WHERE chunk_status = 'chunk-child')        AS covered,
                        COUNT(*) FILTER (WHERE chunk_status = 'chunk-split')        AS split,
                        COUNT(*) FILTER (WHERE chunk_status IS NULL)                AS unprocessed,
                        COUNT(*) FILTER (WHERE chunk_status IS NULL
                                           AND content IS NOT NULL
                                           AND content != '')                       AS orphaned
                    FROM document_nodes
                    """
                )
                stats: dict = dict(cur.fetchone())  # type: ignore[arg-type]

                cur.execute(
                    """
                    SELECT id, version_id, depth, ref, LEFT(content, 60) AS content_preview
                    FROM document_nodes
                    WHERE chunk_status IS NULL
                      AND content IS NOT NULL
                      AND content != ''
                    LIMIT 10
                    """
                )
                orphaned_rows: list[dict] = cur.fetchall()  # type: ignore[assignment]

                # chunk-child 노드 중 chunk-root 조상이 없는 것 탐색
                cur.execute("SELECT id, parent_id, chunk_status FROM document_nodes")
                all_nodes: dict[str, dict] = {r["id"]: r for r in cur.fetchall()}  # type: ignore[assignment]

        invalid_chunk_children: list[str] = []
        for node_id, node in all_nodes.items():
            if node["chunk_status"] != "chunk-child":
                continue
            current = node
            found = False
            while current["parent_id"] and current["parent_id"] in all_nodes:
                current = all_nodes[current["parent_id"]]
                if current["chunk_status"] == "chunk-root":
                    found = True
                    break
            if not found:
                invalid_chunk_children.append(node_id)

        valid = stats["orphaned"] == 0 and len(invalid_chunk_children) == 0
        return {
            "valid": valid,
            "stats": stats,
            "orphaned_nodes": orphaned_rows,
            "invalid_chunk_children": invalid_chunk_children[:10],
        }
