# rag-tax

법인세 세무조정 실무자(회계사/세무사)를 위한 근거 중심 검색 엔진 기획 문서 저장소입니다.

## 문서 구성
- [MVP 기능명세서](docs/mvp-spec.md)
- [문서 DB/인덱스 스키마](docs/data-schema.md)
- [평가셋/품질평가 설계](docs/evaluation-plan.md)
- [문서 수집/업데이트 구현 계획서](docs/ingestion-update-implementation-plan.md)

## 프로토타입 실행
1. `uv sync`
2. PostgreSQL 준비 후 `DATABASE_URL` 설정
3. `uv run python -m app.bootstrap init-and-seed`
4. `uv run uvicorn app.main:app --reload`
5. 브라우저에서 `http://127.0.0.1:8000` 접속

샘플 데이터 기반으로 검색, 요약, 원문 조회 흐름을 확인할 수 있습니다.

## Docker Compose 로컬 DB
1. `docker compose up -d db`
2. `docker compose run --rm db-init`
3. `export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/rag_tax`
4. `uv run uvicorn app.main:app --reload`

한 번에 실행하려면 `docker compose up db db-init`을 사용할 수 있습니다.
`db-init`은 스키마 생성과 샘플 데이터 적재를 담당하는 일회성 컨테이너입니다.

## 목표 요약
- 특정 쟁점에 대한 법령 조항, 행정해석, 판례를 정교하게 검색
- 원문 전문과 메타데이터를 함께 제공해 검증 가능성 확보
- 상단 AI 요약(출처 인용 필수) + 하단 검색 결과 목록을 분리 제공

## 다음 구현 단계
1. 데이터 수집기 구축 및 문서 정규화 파이프라인 구현
2. 하이브리드 검색(BM25 + 벡터) 및 재랭킹 API 구현
3. 인용 강제형 RAG 요약 API 구현
4. 검색 UI(요약 섹션 + 결과 목록 + 원문 뷰어) 구현
5. 오프라인 평가 자동화 및 품질 게이트 설정
