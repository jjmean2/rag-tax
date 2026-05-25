"""법령 검색 품질 평가 스크립트 (Recall@K).

사용법:
    uv run python eval/run_eval.py
    uv run python eval/run_eval.py --api http://localhost:8000
    uv run python eval/run_eval.py --k 1 3 5
    uv run python eval/run_eval.py --id tc001 tc010   # 특정 케이스만 실행
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

DATASET_PATH = Path(__file__).parent / "dataset.json"
DEFAULT_API = "http://localhost:8000"
DEFAULT_KS = [1, 3, 5]


def search(api_url: str, query: str) -> dict:
    resp = requests.post(
        f"{api_url}/api/search",
        json={"query": query},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def matches(result: dict, expected: dict) -> bool:
    doc_ok = expected["doc_title"] in (result.get("title") or "")
    ref = expected["ref"]
    article_ref = result.get("articleRef") or ""
    section_ref = result.get("sectionRef") or ""
    ref_ok = ref in article_ref or ref in section_ref
    return doc_ok and ref_ok


def run_eval(api_url: str, ks: list[int], filter_ids: list[str] | None) -> None:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    cases = [
        c for c in dataset["cases"]
        if filter_ids is None or c["id"] in filter_ids
    ]

    hit_by_k: dict[int, list[bool]] = {k: [] for k in ks}
    max_k = max(ks)

    for case in cases:
        print(f"\n[{case['id']}] {case['query']}")

        try:
            payload = search(api_url, case["query"])
        except Exception as exc:
            print(f"  오류: {exc}")
            for k in ks:
                hit_by_k[k].append(False)
            continue

        results = payload.get("results", [])
        debug = payload.get("debug", {})

        hypo = debug.get("hypotheticalDoc")
        if hypo:
            print(f"  [HyDE] {hypo[:120].replace(chr(10), ' ')}{'…' if len(hypo) > 120 else ''}")

        for k in ks:
            top_k = results[:k]
            hit = any(
                matches(r, exp)
                for r in top_k
                for exp in case["expected"]
            )
            hit_by_k[k].append(hit)

        marks = "  ".join(
            f"@{k}:{'✓' if hit_by_k[k][-1] else '✗'}" for k in ks
        )
        print(f"  {marks}")

        expected_strs = [f"{e['doc_title']} {e['ref']}" for e in case["expected"]]
        print(f"  기대: {', '.join(expected_strs)}")

        top_refs = [
            f"{(r.get('title') or '')[:6]} {r.get('articleRef') or ''}"
            for r in results[:max_k]
        ]
        print(f"  top{max_k}: {top_refs}")

        time.sleep(0.3)

    n = len(cases)
    print("\n" + "=" * 55)
    print(f"평가 결과 ({n}개 케이스, API: {api_url})")
    print("-" * 55)
    for k in ks:
        hits = sum(hit_by_k[k])
        bar = "█" * hits + "░" * (n - hits)
        print(f"  Recall@{k}: {hits:2d}/{n}  {bar}  {hits/n:.0%}")
    print("=" * 55)


def main() -> None:
    parser = argparse.ArgumentParser(description="검색 품질 평가 (Recall@K)")
    parser.add_argument("--api", default=DEFAULT_API, help="API 서버 URL")
    parser.add_argument("--k", nargs="+", type=int, default=DEFAULT_KS, metavar="N")
    parser.add_argument("--id", nargs="+", dest="ids", metavar="ID", help="실행할 케이스 ID")
    args = parser.parse_args()

    run_eval(api_url=args.api, ks=sorted(args.k), filter_ids=args.ids)


if __name__ == "__main__":
    main()
