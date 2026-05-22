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
첫 검색 시에는 섹션 임베딩을 자동 생성하므로, 모델 다운로드로 인해 약간의 지연이 있을 수 있습니다.

## 로컬 개발 환경 시작

```bash
make install     # 의존성 설치
make db-up       # 개발 DB 시작
make db-init     # 최초 1회: 스키마 생성 + 샘플 시드
make dev         # 앱 서버 실행 → http://127.0.0.1:8000
```

DB 데이터를 완전 초기화하려면:
```bash
make db-reset
```

사용 가능한 모든 태스크 목록:
```bash
make help
```

`db-init`은 `tools` 프로파일로 분리되어 있어 `make db-up`(기본 `docker compose up`)에서 자동 실행되지 않습니다. 필요할 때만 `make db-init`으로 명시 실행합니다.
`make db-init`은 `Dockerfile.db-init` 이미지를 빌드한 뒤 실행하며, 의존성은 `pyproject.toml`/`uv.lock` 기준으로 동기화됩니다. 개별 `pip install ...` 하드코딩 없이 의존성 변경이 반영됩니다.
DB 이미지는 pgvector 확장을 포함한 `pgvector/pgvector:pg16`를 사용합니다.

웹 기반 DB 탐색이 필요하면 Adminer를 사용합니다.

```bash
docker compose up -d adminer
```

브라우저에서 `http://127.0.0.1:8080`을 열고 다음처럼 접속합니다.

- 시스템: `PostgreSQL`
- 서버: `db`
- 사용자명: `postgres`
- 비밀번호: `postgres`
- 데이터베이스: `rag_tax`

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
