-- 법제처 RAG 시스템 초기 스키마
-- 적용: uv run python -m app.db.migrate

CREATE EXTENSION IF NOT EXISTS vector;

-- ── documents ────────────────────────────────────────────────────────────────
-- 문서 마스터. "법인세법" 자체를 가리키는 단일 레코드.
-- 개정이 몇 번 되든 이 레코드는 하나.
CREATE TABLE IF NOT EXISTS documents (
  id                 TEXT PRIMARY KEY,    -- "{source_system}:{source_id}"
  source_system      TEXT NOT NULL,       -- "law_go_kr"
  source_id          TEXT NOT NULL,
  doc_type           TEXT NOT NULL,       -- statute | ruling | case | circular
  authority          TEXT NOT NULL,       -- moef | nts | scourt | klri
  title              TEXT NOT NULL,
  canonical_url      TEXT,
  current_version_id TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (source_system, source_id)
);

-- ── document_versions ────────────────────────────────────────────────────────
-- 개정 이력. 동일 법령의 2024년판 / 2023년판 등.
CREATE TABLE IF NOT EXISTS document_versions (
  id             TEXT PRIMARY KEY,    -- "{doc_id}:{content_hash[:12]}"
  document_id    TEXT NOT NULL REFERENCES documents(id),
  version_label  TEXT,                -- 공포번호 등
  publish_date   DATE,
  effective_from DATE,
  effective_to   DATE,
  status         TEXT NOT NULL,       -- current | superseded | repealed
  raw_text       TEXT,                -- 전체 원문 (선택)
  hash_sha256    TEXT NOT NULL,
  metadata_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (document_id, hash_sha256)
);

CREATE INDEX IF NOT EXISTS idx_versions_effective
ON document_versions (effective_from, effective_to);

-- ── document_nodes ────────────────────────────────────────────────────────────
-- 검색·표시의 기본 단위. 문서의 계층 구조를 트리로 저장.
--
-- parent_id = NULL   → 최상위 노드 (조 또는 편/장)
-- embedding NOT NULL → 벡터 검색 대상 (주로 항, 단독 조)
--
-- node_type 관용값:
--   법령:  article(조) | paragraph(항) | item(호) | subitem(목)
--          chapter(편) | division(장)  | subsection(절)
--   판례:  holding(판결요지) | reasoning(이유) | issue(쟁점)
--   예규:  question(질의요지) | answer(회신내용)
--   통칙:  provision(규정)
CREATE TABLE IF NOT EXISTS document_nodes (
  id              TEXT PRIMARY KEY,
  version_id      TEXT NOT NULL REFERENCES document_versions(id),
  parent_id       TEXT REFERENCES document_nodes(id),

  node_type       TEXT NOT NULL,
  ref             TEXT,               -- "제19조", "①", "1.", "가."
  title           TEXT,               -- "손금의 범위"
  content         TEXT,               -- 본문; 컨테이너 노드는 NULL 가능

  depth           SMALLINT NOT NULL DEFAULT 0,  -- 0=조, 1=항, 2=호, 3=목
  order_no        INT NOT NULL,

  token_count     INT,
  embedding       VECTOR(384),
  embedding_model TEXT,               -- 임베딩 모델명

  metadata_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 버전 내 순서 탐색 (문서 전체 표시용)
CREATE INDEX IF NOT EXISTS idx_nodes_version_order
ON document_nodes (version_id, order_no);

-- 부모→자식 탐색 (컨텍스트 조립용)
CREATE INDEX IF NOT EXISTS idx_nodes_parent
ON document_nodes (parent_id)
WHERE parent_id IS NOT NULL;

-- 벡터 검색 (임베딩 있는 노드만)
CREATE INDEX IF NOT EXISTS idx_nodes_embedding
ON document_nodes USING hnsw (embedding vector_cosine_ops)
WHERE embedding IS NOT NULL;

-- 노드 타입별 조회
CREATE INDEX IF NOT EXISTS idx_nodes_type_version
ON document_nodes (node_type, version_id);

-- ── citations ────────────────────────────────────────────────────────────────
-- 노드 간 인용 관계.
CREATE TABLE IF NOT EXISTS citations (
  id             TEXT PRIMARY KEY,
  from_node_id   TEXT NOT NULL REFERENCES document_nodes(id),
  to_document_id TEXT REFERENCES documents(id),
  to_node_ref    TEXT,                -- "제19조 제1항" 등 자유 텍스트 참조
  citation_text  TEXT,
  confidence     NUMERIC(4,3),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_citations_from
ON citations (from_node_id);

-- ── tags ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tags (
  id       TEXT PRIMARY KEY,
  name     TEXT NOT NULL UNIQUE,
  category TEXT NOT NULL              -- issue | concept | industry
);

CREATE TABLE IF NOT EXISTS document_tags (
  document_id TEXT NOT NULL REFERENCES documents(id),
  tag_id      TEXT NOT NULL REFERENCES tags(id),
  PRIMARY KEY (document_id, tag_id)
);

-- ── ingestion_jobs ────────────────────────────────────────────────────────────
-- 수집 작업 이력.
CREATE TABLE IF NOT EXISTS ingestion_jobs (
  id             TEXT PRIMARY KEY,
  source_system  TEXT NOT NULL,
  started_at     TIMESTAMPTZ NOT NULL,
  finished_at    TIMESTAMPTZ,
  status         TEXT NOT NULL,       -- running | success | failed
  inserted_count INT NOT NULL DEFAULT 0,
  updated_count  INT NOT NULL DEFAULT 0,
  error_log      TEXT
);

-- ── schema_migrations ─────────────────────────────────────────────────────────
-- 적용된 마이그레이션 파일 추적.
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    TEXT PRIMARY KEY,        -- "001" (파일명 앞 숫자)
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
