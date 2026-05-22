from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def embed_text(value: str) -> list[float]:
    embedding = get_embedding_model().encode(
        [value], normalize_embeddings=True, convert_to_numpy=True
    )[0]
    return embedding.astype(float).tolist()


def embed_texts(values: list[str]) -> list[list[float]]:
    if not values:
        return []

    embeddings = get_embedding_model().encode(
        values, normalize_embeddings=True, convert_to_numpy=True
    )
    return [embedding.astype(float).tolist() for embedding in embeddings]