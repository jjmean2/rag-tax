FROM python:3.13-slim

WORKDIR /app

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 의존성만 먼저 복사해서 레이어 캐시 활용
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 소스 복사 (migrate.py가 db/migrations/ 를 상대 경로로 참조)
COPY app/ ./app/
COPY db/ ./db/

ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
