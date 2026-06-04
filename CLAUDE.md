# Project Overview

한국 법인세법 문서를 수집·색인하여 자연어 질문에 관련 법령 조문을 검색·인용하고 LLM으로 답변을 생성하는 RAG 시스템.

## Stack

- **API**: FastAPI + psycopg3 (app/)
- **DB**: PostgreSQL 16 + pgvector (벡터 검색) + tsvector (전문 검색)
- **Embedding**: text-embedding-3-small (1536차원)
- **LLM**: gpt-4o-mini (HyDE 가상 문서 생성 + 답변 생성)
- **패키지 관리**: uv
- **배포**: GCP e2-micro (us-west1-b), Docker Compose

## Key Architecture

```
법제처 API → Ingestion Pipeline → PostgreSQL
                                      ↓
사용자 질문 → HyDE → 임베딩 → 하이브리드 검색(벡터+전문) → RRF 병합 → LLM 답변
```

**검색 흐름 (app/main.py → app/storage.py):**
1. HyDE: 질문 → GPT-4o-mini → 가상 법조문 생성
2. 임베딩: 가상 법조문을 벡터화 (실패 시 원본 질문으로 fallback)
3. 벡터 검색: pgvector cosine similarity, 후보 40건
4. 전문 검색: tsvector + websearch_to_tsquery('simple'), 후보 40건
5. RRF 병합: score = 1/(60 + rank), 상위 20건
6. LLM 답변: 상위 5건 컨텍스트 (결과당 2,000자 truncation)

**청킹 (app/ingestion/writers.py):**
- depth-agnostic bottom-up: 서브트리가 400 토큰 이내면 하나의 청크
- 초과 시 자식으로 재귀, 조상 텍스트를 context_prefix로 전달
- chunk_status: chunk-root / chunk-child / chunk-split

## DB Schema (핵심 테이블)

- `documents`: 법령 마스터 (법인세법 자체)
- `document_versions`: 개정 이력 (effective_from/to로 시점 쿼리)
- `document_nodes`: 조/항/호/목 트리, chunk_status 추적
- `document_chunks`: 임베딩 + embed_text_tsv (generated tsvector)
- `schema_migrations`: 마이그레이션 버전 추적

## Ingestion Targets (runner.py)

법인세법, 법인세법 시행령, 법인세법 시행규칙, 조세특례제한법,
국세기본법, 국세기본법 시행령, 법인세법 기본통칙

## Known Issues / TODO

- **kw=0 문제**: websearch_to_tsquery AND 시맨틱으로 긴 질문에서 키워드 검색 히트율 0%.
  개선 방향: OR 시맨틱 전환 또는 핵심어 추출 후 검색
- **리프 청크 초과**: 리프 노드가 토큰 한도 초과 시 그냥 허용 중 (현재 데이터에서 미발생).
  개선 방향: 문장 경계 분할 + 오버랩, document_chunks 1:다 구조로 전환
- **Recall@K 현황**: @1=40%, @3=73%, @5=73% (eval/dataset.json 기준 15개 Q&A)
  실패 원인: 동일 주제 조문 간 순위 경쟁, HyDE 조문 번호 오생성
- **수집 법령 확대**: 행정해석(예규), 판례 미수집

## Deployment (GCP)

- VM: instance-20260526-034415, us-west1-b, e2-micro
- External IP: 8.229.70.152:8000
- 마이그레이션 001~005 모두 적용 완료
- 재시작: `sudo docker compose up -d --build` (~/rag-tax/)
- 코드 업데이트: `git pull && sudo docker compose up -d --build`

## Environment Variables

- `OPENAI_API_KEY`: OpenAI API 키 (임베딩 + LLM)
- `LAW_API_KEY`: 법제처 Open API OC 키 (수집 전용, API 서버에는 불필요)
- `DATABASE_URL`: PostgreSQL 연결 문자열 (기본: localhost:5432)

---

# Commit Message Convention

- Use Conventional Commits format: `<type>(<scope>): <subject>`
- Subject: concise English, imperative mood, lowercase first letter, no trailing period
- Body: write **only** when the background or reason behind the change is not obvious from the diff; write in **Korean**
- Separate header and body with a blank line

Types: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `style`, `perf`