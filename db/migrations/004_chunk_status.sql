-- document_nodes.embedding_model → chunk_status 로 리네임 및 값 정리
--
-- 상태 정의:
--   NULL          → 미처리 (아직 청킹되지 않음)
--   'chunk-root'  → 청크 루트 (document_chunks 행 존재, 비정규화된 상태)
--   'chunk-child' → 상위 청크에 포함됨

ALTER TABLE document_nodes RENAME COLUMN embedding_model TO chunk_status;

-- 기존 'parent-chunk' 값을 'chunk-child' 로 변경
UPDATE document_nodes SET chunk_status = 'chunk-child' WHERE chunk_status = 'parent-chunk';

-- 현재 document_chunks 행이 있는 노드를 'chunk-root' 로 표시
-- (003 적용 후 재임베딩 전이라면 해당 없음)
UPDATE document_nodes
SET chunk_status = 'chunk-root'
WHERE EXISTS (SELECT 1 FROM document_chunks dc WHERE dc.node_id = document_nodes.id);
