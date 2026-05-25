"""OpenAI 임베딩 유틸리티.

모델: text-embedding-3-small (1536차원)
환경변수: OPENAI_API_KEY (필수)
"""

from __future__ import annotations

import os
from functools import lru_cache

from openai import OpenAI

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY 환경변수가 설정되지 않았습니다.\n  export OPENAI_API_KEY=sk-..."
        )
    return OpenAI(api_key=api_key)


def embed_text(value: str) -> list[float]:
    resp = _client().embeddings.create(input=value, model=EMBEDDING_MODEL)
    return resp.data[0].embedding


def embed_texts(values: list[str]) -> list[list[float]]:
    """최대 2048개 텍스트를 한 번의 API 호출로 임베딩한다."""
    if not values:
        return []
    resp = _client().embeddings.create(input=values, model=EMBEDDING_MODEL)
    # API는 index 순서를 보장하므로 정렬 없이 반환
    return [item.embedding for item in resp.data]
