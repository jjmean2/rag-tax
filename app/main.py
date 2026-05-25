from __future__ import annotations

import math
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.embeddings import embed_text
from app.llm import generate_answer
from app.storage import PostgresStore

BASE_DIR = Path(__file__).resolve().parent
DOC_TYPE_LABELS = {
    "statute": "법령",
    "ruling": "행정해석",
    "case": "판례",
}
AUTHORITY_LABELS = {
    "moef": "기획재정부",
    "nts": "국세청",
    "scourt": "대법원",
}


class SearchFilters(BaseModel):
    docTypes: list[str] | None = None
    authority: list[str] | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=2)
    filters: SearchFilters | None = None
    sort: str = "relevance"
    asOfDate: date | None = None


app = FastAPI(title="rag-tax prototype", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
STORE = PostgresStore()


def normalize_text(value: str) -> str:
    lowered = value.lower()
    collapsed = re.sub(r"[^0-9a-zA-Z가-힣]+", " ", lowered)
    return re.sub(r"\s+", " ", collapsed).strip()


def tokenize(value: str) -> list[str]:
    return [token for token in normalize_text(value).split(" ") if token]


def keyword_score(query_tokens: list[str], section: dict[str, Any], title: str) -> float:
    title_tokens = tokenize(title)
    ref_tokens = tokenize(section.get("sectionRef") or "")
    context_tokens = tokenize(section.get("context") or section.get("snippet") or "")
    score = 0.0
    for token in query_tokens:
        if token in title_tokens:
            score += 2.5
        if token in ref_tokens:
            score += 2.0
        score += context_tokens.count(token)
    return score


def build_summary(query: str, ranked_results: list[dict[str, Any]]) -> dict[str, Any]:
    if not ranked_results:
        return {
            "text": "검색된 근거 문서가 없어 요약을 생성하지 못했습니다.",
            "citations": [],
            "warnings": ["근거 부족"],
        }

    top_results = ranked_results[:3]
    summary_lines = []
    summary_citations = []
    for result in top_results:
        context_body = result.get("context") or result.get("snippet") or ""
        sentence = (
            f"{result['docTypeLabel']} {result['title']} {result['sectionRef'] or ''}:\n"
            f"{context_body}"
        ).strip()
        summary_lines.append(sentence)
        summary_citations.append(
            {
                "id": result["documentId"],
                "sectionId": result["id"],
                "anchors": [result["sectionRef"]] if result["sectionRef"] else [],
            }
        )

    warnings = []
    if any(result["docType"] != "statute" for result in top_results):
        warnings.append("법령 외 해석/판례가 함께 포함되어 있습니다")
    if len({result["date"] for result in top_results}) > 1:
        warnings.append("시점이 다른 근거가 함께 포함될 수 있습니다")

    return {
        "text": "\n".join(f"- {line}" for line in summary_lines),
        "citations": summary_citations,
        "warnings": warnings,
        "query": query,
    }


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/search")
def search(request: SearchRequest) -> dict[str, Any]:
    query_tokens = tokenize(request.query)
    ranked_results: list[dict[str, Any]] = []
    filters = request.filters or SearchFilters()

    timings: dict[str, float] = {}
    try:
        t0 = time.perf_counter()
        query_embedding = embed_text(request.query)
        timings["embed_ms"] = round((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        sections = STORE.search_sections(
            as_of=request.asOfDate,
            doc_types=filters.docTypes,
            authorities=filters.authority,
            query_embedding=query_embedding,
        )
        timings["search_ms"] = round((time.perf_counter() - t0) * 1000)
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable. Ensure schema is initialized and DATABASE_URL is reachable. {error}",
        ) from error
    except Exception:
        query_embedding = None
        t0 = time.perf_counter()
        sections = STORE.search_sections(
            as_of=request.asOfDate,
            doc_types=filters.docTypes,
            authorities=filters.authority,
            query_embedding=query_embedding,
        )
        timings["search_ms"] = round((time.perf_counter() - t0) * 1000)

    for section in sections:
        kw_score = keyword_score(query_tokens, section, section["title"])
        sem_score = section.get("semanticScore", 0.0)
        combined_score = kw_score * 0.75 + sem_score * 4.0
        if math.isclose(combined_score, 0.0):
            continue
        ranked_results.append(
            {
                "id": section["id"],
                "documentId": section["documentId"],
                "documentVersionId": section["documentVersionId"],
                "title": section["title"],
                "docType": section["docType"],
                "docTypeLabel": DOC_TYPE_LABELS.get(section["docType"], section["docType"]),
                "authority": section["authority"],
                "authorityLabel": AUTHORITY_LABELS.get(section["authority"], section["authority"]),
                "date": section["date"],
                "score": round(combined_score, 4),
                "articleRef": section.get("articleRef"),
                "sectionRef": section.get("sectionRef"),
                "heading": section.get("heading"),
                "snippet": section.get("snippet"),
                "context": section.get("context"),
                "citations": section["citations"],
            }
        )

    reverse = request.sort != "oldest"
    if request.sort == "latest":
        ranked_results.sort(key=lambda item: (item["date"], item["score"]), reverse=True)
    else:
        ranked_results.sort(key=lambda item: item["score"], reverse=reverse)

    t0 = time.perf_counter()
    try:
        summary = generate_answer(request.query, ranked_results)
    except Exception:
        summary = build_summary(request.query, ranked_results)
    timings["llm_ms"] = round((time.perf_counter() - t0) * 1000)

    total = sum(timings.values())
    print(
        f"[search] embed={timings.get('embed_ms')}ms  "
        f"search={timings.get('search_ms')}ms  "
        f"llm={timings.get('llm_ms')}ms  "
        f"total={total}ms",
        flush=True,
    )

    return {
        "summary": summary,
        "results": ranked_results[:10],
        "debug": {
            "queryTokens": query_tokens,
            "semanticSearch": True,
            "totalMatches": len(ranked_results),
            "timings": timings,
        },
    }


@app.get("/api/documents/{document_id}")
def get_document(document_id: str, asOfDate: date | None = None) -> dict[str, Any]:
    try:
        payload = STORE.get_document(document_id, asOfDate)
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable. Ensure schema is initialized and DATABASE_URL is reachable. {error}",
        ) from error

    if payload is None:
        raise HTTPException(status_code=404, detail="Document or document version not found")
    return payload


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
