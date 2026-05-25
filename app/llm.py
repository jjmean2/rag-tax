"""LLM 기반 답변 생성."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

ANSWER_MODEL = "gpt-4o-mini"

_SYSTEM_PROMPT = """\
당신은 한국 세법 전문 AI 어시스턴트입니다.
사용자의 질문에 아래 제공된 법령 조문을 근거로 정확하게 답변하세요.

답변 원칙:
1. 반드시 제공된 법령 조문에만 근거하세요. 조문 외의 내용을 추측하지 마세요.
2. 조문 인용 형식: [법률명 제X조 제X항] — 예: [법인세법 제28조 제1항]
3. 제공된 조문에서 명확한 근거를 찾기 어려우면 솔직하게 밝히세요.
4. 답변 구성: 핵심 결론 → 근거 조문 인용 → 주의사항(해당 시)
5. 마지막 문장: "본 답변은 참고용이며, 실제 세무 처리는 세무 전문가와 상담하시기 바랍니다."\
"""


_CONTEXT_CHAR_LIMIT = 800  # 결과당 최대 글자 수 (~400 한글 토큰)


def _build_context(results: list[dict[str, Any]]) -> str:
    parts = []
    for i, r in enumerate(results, 1):
        header = (
            f"[{i}] {r.get('title', '')} {r.get('sectionRef') or r.get('articleRef') or ''}".strip()
        )
        body = r.get("context") or r.get("snippet") or ""
        if len(body) > _CONTEXT_CHAR_LIMIT:
            body = body[:_CONTEXT_CHAR_LIMIT] + "…"
        parts.append(f"{header}\n{body}")
    return "\n\n".join(parts)


def generate_answer(
    query: str,
    results: list[dict[str, Any]],
    model: str = ANSWER_MODEL,
) -> dict[str, Any]:
    """검색 결과를 바탕으로 LLM 답변을 생성한다."""
    if not results:
        return {
            "text": "관련 법령을 찾지 못해 답변을 생성할 수 없습니다.",
            "citations": [],
            "warnings": ["관련 법령 없음"],
        }

    client = OpenAI()
    context = _build_context(results[:5])
    user_msg = f"[참고 법령 조문]\n{context}\n\n[질문]\n{query}"

    input_chars = len(_SYSTEM_PROMPT) + len(user_msg)
    print(f"[llm] input_chars={input_chars} (~{round(input_chars / 1.5)} tokens est.)", flush=True)

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
        max_tokens=600,
    )

    usage = resp.usage
    if usage:
        print(
            f"[llm] actual tokens — input={usage.prompt_tokens}  output={usage.completion_tokens}",
            flush=True,
        )

    answer_text = resp.choices[0].message.content or ""
    citations = [
        {
            "id": r["documentId"],
            "sectionId": r["id"],
            "anchors": [r["sectionRef"]] if r.get("sectionRef") else [],
            "title": r.get("title", ""),
        }
        for r in results[:5]
    ]
    return {
        "text": answer_text,
        "citations": citations,
        "warnings": [],
    }
