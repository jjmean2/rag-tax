-- 검색 단위(청크)를 document_nodes에서 분리
--
-- 변경 내용:
--   1. document_chunks 테이블 신설 — embed_text + embedding 보관
--   2. document_nodes.embedding 컬럼 제거 (document_chunks 로 이전)
--   3. document_nodes.embedding_model 은 처리 상태 마커로 유지
--        NULL          → 미처리
--        'parent-chunk'→ 상위 청크에 포함됨
--   4. 기존 임베딩 상태 초기화 → make ingest-embed 로 재생성 필요

-- ── document_chunks ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS document_chunks (
    id              TEXT PRIMARY KEY,           -- = node_id (chunk root)
    version_id      TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    node_id         TEXT NOT NULL REFERENCES document_nodes(id)    ON DELETE CASCADE,
    embed_text      TEXT NOT NULL,              -- 실제 임베딩된 텍스트
    embedding       VECTOR(1536),
    embedding_model TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunks_embedding
ON document_chunks USING hnsw (embedding vector_cosine_ops)
WHERE embedding IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chunks_version
ON document_chunks (version_id);

-- ── document_nodes 에서 embedding 컬럼 제거 ──────────────────────────────────
DROP INDEX IF EXISTS idx_nodes_embedding;
ALTER TABLE document_nodes DROP COLUMN IF EXISTS embedding;

-- ── 기존 임베딩 상태 초기화 ──────────────────────────────────────────────────
-- chunk root 마커(모델명) → NULL, parent-chunk 는 유지
UPDATE document_nodes
SET embedding_model = NULL
WHERE embedding_model IS NOT NULL
  AND embedding_model != 'parent-chunk';
