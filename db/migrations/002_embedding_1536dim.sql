-- 임베딩 모델 교체: sentence-transformers(384dim) → text-embedding-3-small(1536dim)
-- 기존 임베딩 데이터는 모두 무효화하고 새로 생성해야 한다.

-- 기존 HNSW 인덱스 제거 (차원 변경 전 필수)
DROP INDEX IF EXISTS idx_nodes_embedding;

-- 차원 변경
ALTER TABLE document_nodes
    ALTER COLUMN embedding TYPE VECTOR(1536);

-- 기존 임베딩 초기화 (모델이 다르므로 재생성 필요)
UPDATE document_nodes SET embedding = NULL, embedding_model = NULL;

-- 새 HNSW 인덱스 생성
CREATE INDEX idx_nodes_embedding
    ON document_nodes USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;
