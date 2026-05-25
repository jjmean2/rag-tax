-- document_chunks 에 embed_text 풀텍스트 검색용 tsvector 컬럼 추가
-- 'simple' 설정: 어간 분석 없이 공백/구두점으로 토크나이징 (한국어에 적합)

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS embed_text_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', coalesce(embed_text, ''))) STORED;

CREATE INDEX IF NOT EXISTS idx_chunks_embed_text_tsv
    ON document_chunks USING GIN (embed_text_tsv);
