from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.data.sample_documents import SAMPLE_DOCUMENTS

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


def normalize_text(value: str) -> str:
    lowered = value.lower()
    collapsed = re.sub(r"[^0-9a-zA-Z가-힣]+", " ", lowered)
    return re.sub(r"\s+", " ", collapsed).strip()


def tokenize(value: str) -> list[str]:
    return [token for token in normalize_text(value).split(" ") if token]


def keyword_score(
    query_tokens: list[str], section: dict[str, Any], document: dict[str, Any]
) -> float:
    title_tokens = tokenize(document["title"])
    ref_tokens = tokenize(section.get("section_ref") or "")
    content_tokens = tokenize(section["content"])
    score = 0.0
    for token in query_tokens:
        if token in title_tokens:
            score += 2.5
        if token in ref_tokens:
            score += 2.0
        score += content_tokens.count(token)
    return score


def semantic_score(query_tokens: list[str], section: dict[str, Any]) -> float:
    content_tokens = set(tokenize(section["content"]))
    if not query_tokens or not content_tokens:
        return 0.0
    intersection = len(content_tokens.intersection(query_tokens))
    union = len(content_tokens.union(query_tokens))
    return intersection / union if union else 0.0


def parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def choose_version(
    document: dict[str, Any], as_of: date | None
) -> dict[str, Any] | None:
    versions = document["versions"]
    if as_of is None:
        current_version_id = document["current_version_id"]
        for version in versions:
            if version["id"] == current_version_id:
                return version
        return versions[0]

    candidates: list[dict[str, Any]] = []
    for version in versions:
        effective_from = parse_date(version["effective_from"])
        effective_to = parse_date(version["effective_to"])
        if effective_from and effective_from > as_of:
            continue
        if effective_to and effective_to < as_of:
            continue
        candidates.append(version)

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item["publish_date"], item["id"]), reverse=True)
    return candidates[0]


def matches_filters(document: dict[str, Any], filters: SearchFilters | None) -> bool:
    if filters is None:
        return True
    if filters.docTypes and document["doc_type"] not in filters.docTypes:
        return False
    if filters.authority and document["authority"] not in filters.authority:
        return False
    return True


def build_index() -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    documents_by_id: dict[str, dict[str, Any]] = {}
    sections_by_id: dict[str, dict[str, Any]] = {}
    citations_by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for document in SAMPLE_DOCUMENTS["documents"]:
        documents_by_id[document["id"]] = document
        for version in document["versions"]:
            for section in version["sections"]:
                sections_by_id[section["id"]] = section

    for citation in SAMPLE_DOCUMENTS["citations"]:
        citations_by_section[citation["from_section_id"]].append(citation)

    return documents_by_id, sections_by_id, citations_by_section


DOCUMENTS_BY_ID, SECTIONS_BY_ID, CITATIONS_BY_SECTION = build_index()


def format_result(
    document: dict[str, Any],
    version: dict[str, Any],
    section: dict[str, Any],
    score: float,
) -> dict[str, Any]:
    citations = CITATIONS_BY_SECTION.get(section["id"], [])
    return {
        "id": section["id"],
        "documentId": document["id"],
        "documentVersionId": version["id"],
        "title": document["title"],
        "docType": document["doc_type"],
        "docTypeLabel": DOC_TYPE_LABELS.get(document["doc_type"], document["doc_type"]),
        "authority": document["authority"],
        "authorityLabel": AUTHORITY_LABELS.get(
            document["authority"], document["authority"]
        ),
        "date": version["publish_date"],
        "score": round(score, 4),
        "sectionRef": section.get("section_ref"),
        "heading": section.get("heading"),
        "snippet": section["content"],
        "citations": citations,
    }


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
        sentence = (
            f"{result['docTypeLabel']} {result['title']} {result['sectionRef'] or ''}는 "
            f"{result['snippet']}"
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

    for document in SAMPLE_DOCUMENTS["documents"]:
        if not matches_filters(document, request.filters):
            continue

        version = choose_version(document, request.asOfDate)
        if version is None:
            continue

        for section in version["sections"]:
            kw_score = keyword_score(query_tokens, section, document)
            sem_score = semantic_score(query_tokens, section)
            combined_score = kw_score * 0.75 + sem_score * 4.0
            if math.isclose(combined_score, 0.0):
                continue
            ranked_results.append(
                format_result(document, version, section, combined_score)
            )

    reverse = request.sort != "oldest"
    if request.sort == "latest":
        ranked_results.sort(
            key=lambda item: (item["date"], item["score"]), reverse=True
        )
    else:
        ranked_results.sort(key=lambda item: item["score"], reverse=reverse)

    return {
        "summary": build_summary(request.query, ranked_results),
        "results": ranked_results[:10],
        "debug": {
            "queryTokens": query_tokens,
            "totalMatches": len(ranked_results),
        },
    }


@app.get("/api/documents/{document_id}")
def get_document(document_id: str, asOfDate: date | None = None) -> dict[str, Any]:
    document = DOCUMENTS_BY_ID.get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    version = choose_version(document, asOfDate)
    if version is None:
        raise HTTPException(
            status_code=404, detail="Document version not found for the requested date"
        )

    return {
        "document": {
            "id": document["id"],
            "title": document["title"],
            "docType": document["doc_type"],
            "authority": document["authority"],
            "canonicalUrl": document["canonical_url"],
        },
        "version": {
            "id": version["id"],
            "versionLabel": version["version_label"],
            "effectiveFrom": version["effective_from"],
            "effectiveTo": version["effective_to"],
            "publishDate": version["publish_date"],
            "status": version["status"],
            "rawText": version["raw_text"],
            "normalizedText": version["normalized_text"],
            "metadata": version["metadata"],
        },
        "sections": version["sections"],
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
