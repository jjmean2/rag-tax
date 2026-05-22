.PHONY: help dev db-up db-down db-init db-reset install lint test

DATABASE_URL ?= postgresql://postgres:postgres@localhost:5432/rag_tax

help:           ## 사용 가능한 태스크 목록 출력
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*##"}; {printf "  %-14s %s\n", $$1, $$2}'

install:        ## 의존성 설치 (uv sync)
	uv sync --dev

dev:            ## 앱 서버 실행 (hot-reload)
	DATABASE_URL=$(DATABASE_URL) uv run uvicorn app.main:app --reload

db-up:          ## 개발 DB 시작 (백그라운드)
	docker compose up -d db

db-down:        ## 개발 DB 중단 (데이터 유지)
	docker compose stop db

db-init:        ## 스키마 생성 + 샘플 시드 (최초 1회 또는 리셋 후)
	docker compose build db-init
	docker compose --profile tools run --rm db-init

db-reset:       ## DB 볼륨 삭제 후 초기화 (데이터 전체 삭제)
	docker compose down -v
	docker compose up -d db
	$(MAKE) db-init

lint:           ## 코드 스타일 검사 (ruff)
	uv run ruff check app

test:           ## 테스트 실행
	uv run pytest
