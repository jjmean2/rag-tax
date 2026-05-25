.PHONY: help dev db-up db-down db-init db-reset install lint test

DATABASE_URL ?= postgresql://postgres:postgres@localhost:5432/rag_tax

help:           ## 사용 가능한 태스크 목록 출력
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*##"}; {printf "  %-14s %s\n", $$1, $$2}'

install:        ## 의존성 설치 (uv sync)
	uv sync --dev

dev:            ## 앱 서버 실행 (hot-reload)
	DATABASE_URL=$(DATABASE_URL) uv run uvicorn app.main:app --reload

db-up:          ## 개발 DB 시작 (헬스체크 통과까지 대기)
	docker compose up -d --wait db

db-down:        ## 개발 DB 중단 (데이터 유지)
	docker compose stop db

db-init:        ## 마이그레이션 적용 (최초 1회 또는 새 마이그레이션 추가 후)
	DATABASE_URL=$(DATABASE_URL) uv run python -m app.db.migrate

db-reset:       ## DB 볼륨 삭제 후 재초기화 (데이터 전체 삭제)
	docker compose down -v
	$(MAKE) db-up
	$(MAKE) db-init

db-ui:          ## DB 웹 인터페이스
	docker compose up -d adminer


ingest:         ## 법제처 법령 수집 + 임베딩 (LAW_API_KEY 필수)
	LAW_API_KEY=$(LAW_API_KEY) DATABASE_URL=$(DATABASE_URL) uv run python -m app.ingestion.runner

ingest-collect: ## 법령 수집 + DB 저장만 (임베딩 생략)
	LAW_API_KEY=$(LAW_API_KEY) DATABASE_URL=$(DATABASE_URL) uv run python -m app.ingestion.runner --skip-embed

ingest-dry:     ## 수집 파싱 테스트 (DB 반영 없음)
	LAW_API_KEY=$(LAW_API_KEY) uv run python -m app.ingestion.runner --dry-run

ingest-debug:   ## API 요청/응답 원문 출력 (dry-run 포함)
	LAW_API_KEY=$(LAW_API_KEY) uv run python -m app.ingestion.runner --dry-run --debug

ingest-embed:   ## 미완료 임베딩만 생성
	DATABASE_URL=$(DATABASE_URL) uv run python -m app.ingestion.runner --embed-only

lint:           ## 코드 스타일 검사 (ruff)
	uv run ruff check app

test:           ## 테스트 실행
	uv run pytest
