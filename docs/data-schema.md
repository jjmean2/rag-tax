# 문서 DB 및 검색 인덱스 데이터 스키마 초안

## 1. 설계 원칙
- 원문 보존: 검색용 가공 데이터와 별도로 원문 전문을 보존
- 추적 가능성: 모든 요약/검색 결과가 원문 근거로 역추적 가능
- 버전 관리: 법령 개정 및 해석 변경 이력 관리
- 타입 안전성: 문서유형별 공통/전용 필드 분리

## 2. 엔터티 개요
- documents: 문서 마스터
- document_versions: 문서 버전(개정/변경 이력)
- document_sections: 검색/표시 단위 섹션(조문/판결요지 등)
- citations: 문서 간 인용 관계
- tags: 쟁점/키워드 태그
- embeddings: 섹션 임베딩 벡터
- ingestion_jobs: 수집/정제 작업 이력

## 3. 관계형 스키마 (PostgreSQL)

### 3.1 documents
```sql
CREATE TABLE documents (
  id                UUID PRIMARY KEY,
  source_system     TEXT NOT NULL,
  source_id         TEXT NOT NULL,
  doc_type          TEXT NOT NULL, -- statute | ruling | case
  authority         TEXT NOT NULL, -- nts | moef | scourt | tt ...
  title             TEXT NOT NULL,
  canonical_url     TEXT,
  current_version_id UUID,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (source_system, source_id)
);
```

### 3.2 document_versions
```sql
CREATE TABLE document_versions (
  id                UUID PRIMARY KEY,
  document_id       UUID NOT NULL REFERENCES documents(id),
  version_label     TEXT,
  effective_from    DATE,
  effective_to      DATE,
  publish_date      DATE,
  status            TEXT NOT NULL, -- active | inactive | repealed
  raw_text          TEXT NOT NULL,
  normalized_text   TEXT,
  hash_sha256       TEXT NOT NULL,
  metadata_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (document_id, hash_sha256)
);
CREATE INDEX idx_document_versions_effective
ON document_versions (effective_from, effective_to);
```

### 3.3 document_sections
```sql
CREATE TABLE document_sections (
  id                UUID PRIMARY KEY,
  version_id        UUID NOT NULL REFERENCES document_versions(id),
  parent_section_id UUID REFERENCES document_sections(id),
  section_type      TEXT NOT NULL, -- article | paragraph | issue | holding | conclusion
  section_ref       TEXT,          -- 예: 제19조 제1항
  heading           TEXT,
  content           TEXT NOT NULL,
  order_no          INT NOT NULL,
  token_count       INT,
  metadata_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_document_sections_version_order
ON document_sections (version_id, order_no);
```

### 3.4 citations
```sql
CREATE TABLE citations (
  id                     UUID PRIMARY KEY,
  from_section_id        UUID NOT NULL REFERENCES document_sections(id),
  to_document_id         UUID REFERENCES documents(id),
  to_section_ref         TEXT,
  citation_text          TEXT,
  confidence             NUMERIC(4,3),
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 3.5 tags / document_tags
```sql
CREATE TABLE tags (
  id                UUID PRIMARY KEY,
  name              TEXT NOT NULL UNIQUE,
  category          TEXT NOT NULL -- issue | concept | industry
);

CREATE TABLE document_tags (
  document_id       UUID NOT NULL REFERENCES documents(id),
  tag_id            UUID NOT NULL REFERENCES tags(id),
  PRIMARY KEY (document_id, tag_id)
);
```

### 3.6 ingestion_jobs
```sql
CREATE TABLE ingestion_jobs (
  id                UUID PRIMARY KEY,
  source_system     TEXT NOT NULL,
  started_at        TIMESTAMPTZ NOT NULL,
  finished_at       TIMESTAMPTZ,
  status            TEXT NOT NULL, -- running | success | failed
  inserted_count    INT NOT NULL DEFAULT 0,
  updated_count     INT NOT NULL DEFAULT 0,
  error_log         TEXT
);
```

## 4. 검색 인덱스 문서 구조

### 4.1 키워드 인덱스(OpenSearch/Elasticsearch)
```json
{
  "section_id": "uuid",
  "document_id": "uuid",
  "doc_type": "statute",
  "authority": "moef",
  "title": "법인세법 제19조",
  "section_ref": "제19조 제1항",
  "content": "...",
  "effective_from": "2025-01-01",
  "effective_to": null,
  "is_current": true,
  "tags": ["손금", "업무무관자산"]
}
```
- 분석기 권장
  - 한국어 형태소 분석 + 사용자 사전(세무 용어)
  - 법령 조문 패턴 토크나이징 커스텀 필터

### 4.2 벡터 인덱스(pgvector/Qdrant)
```json
{
  "id": "section_id",
  "vector": [0.0123, -0.381, ...],
  "payload": {
    "document_id": "uuid",
    "doc_type": "case",
    "authority": "scourt",
    "section_ref": "판시사항",
    "effective_from": "2020-01-01",
    "is_current": true
  }
}
```

## 5. 문서유형별 metadata_json 권장 필드
- statute
  - law_name, article_no, paragraph_no, item_no, amendment_type
- ruling
  - ruling_no, issue_date, query_summary, conclusion
- case
  - case_no, court_level, decision_date, issue, holding, disposition

## 6. 버전/현행성 판정 로직
- 기준일 as_of_date가 주어지면
  - effective_from <= as_of_date
  - effective_to IS NULL OR effective_to >= as_of_date
- 동률 발생 시 publish_date 최신 버전 우선

## 7. 품질 검증 규칙
- raw_text 공백 금지
- 같은 (source_system, source_id, hash_sha256) 중복 금지
- section_ref 정규화 패턴 검사
- citations confidence 임계치 미만은 인용 링크 비활성

## 8. 확장 포인트
- 쟁점 그래프 테이블(issue_graph)
- 회사/산업별 커스텀 태그
- 판례 인용 네트워크 중심성 점수
