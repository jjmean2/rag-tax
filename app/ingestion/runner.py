"""법제처 법령 수집 CLI.

사용법:
    uv run python -m app.ingestion.runner
    uv run python -m app.ingestion.runner --dry-run
    uv run python -m app.ingestion.runner --skip-embed

환경변수:
    LAW_API_KEY   법제처 Open API OC 키 (필수)
    DATABASE_URL  PostgreSQL 연결 문자열 (기본: localhost)
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap

from app.ingestion.connectors.law_go_kr import LawGoKrConnector
from app.ingestion.writers import IngestWriter

# (법령명, API target)
# target="law"    → 법률/시행령/시행규칙
# target="admrul" → 행정규칙 (기본통칙, 예규 등)
TARGETS: list[tuple[str, str]] = [
    ("법인세법", "law"),
    ("법인세법 시행령", "law"),
    ("법인세법 시행규칙", "law"),
    ("조세특례제한법", "law"),
    ("국세기본법", "law"),
    ("국세기본법 시행령", "law"),
    ("법인세법 기본통칙", "admrul"),
]


def run(api_key: str, dry_run: bool, skip_embed: bool, debug: bool = False) -> None:
    connector = LawGoKrConnector(api_key, debug=debug)
    writer = IngestWriter()
    total_inserted = total_updated = 0

    for query, target in TARGETS:
        print(f"\n▸ [{query}] 검색 중 (target={target})...")

        try:
            law_id = connector.search_law_id(query, target)
        except Exception as exc:
            print(f"  ✗ 검색 실패: {exc}")
            continue

        if law_id is None:
            print("  ✗ 검색 결과 없음, 건너뜀")
            continue

        print(f"  → ID {law_id} 전문 조회 중...")

        try:
            doc = connector.fetch_document(law_id, target)
        except Exception as exc:
            print(f"  ✗ 전문 조회 실패: {exc}")
            continue

        if doc is None:
            print("  ✗ XML 파싱 실패, 건너뜀")
            continue

        node_count = len(doc.version.nodes)
        print(f"  → 『{doc.title}』 노드 {node_count}개 파싱 완료")

        if dry_run:
            print("  → dry-run: DB 반영 생략")
            articles = [n for n in doc.version.nodes if n.depth == 0]
            if articles:
                sample = articles[0]
                preview = textwrap.shorten(sample.content or "", width=80)
                print(f"     첫 조문: {sample.ref} {sample.title or ''} — {preview}")
            continue

        try:
            inserted, updated = writer.upsert(doc)
        except Exception as exc:
            print(f"  ✗ DB 저장 실패: {exc}")
            continue

        total_inserted += inserted
        total_updated += updated
        print(f"  ✓ 저장 (신규 {inserted}개, 갱신 {updated}개)")

    if dry_run:
        print("\ndry-run 완료. DB 변경 없음.")
        return

    print(f"\n수집 완료 — 신규 {total_inserted}개, 갱신 {total_updated}개 섹션")

    if skip_embed:
        print("--skip-embed: 임베딩 생략")
        print("나중에 임베딩하려면: uv run python -m app.ingestion.runner --embed-only")
        return

    print("\n임베딩 생성 중 (로컬 모델 첫 실행 시 수십 초 소요)...")
    try:
        embedded = writer.embed_pending()
    except Exception as exc:
        print(f"✗ 임베딩 실패: {exc}")
        print("나중에 재시도: uv run python -m app.ingestion.runner --embed-only")
        return

    print(f"임베딩 완료 — {embedded}개 섹션")


def run_verify_embed() -> None:
    writer = IngestWriter()
    print("임베딩 커버리지 검증 중...")
    result = writer.verify_embed_coverage()
    s = result["stats"]
    print(
        f"\n  전체 노드      : {s['total']}"
        f"\n  청크 루트      : {s['chunk_roots']}  (embedding 있음)"
        f"\n  커버된 노드    : {s['covered']}  (parent-chunk)"
        f"\n  미처리 노드    : {s['unprocessed']}  (embedding/model 모두 NULL)"
        f"\n  고아 노드      : {s['orphaned']}  (content 있으나 미처리)"
    )
    if result["orphaned_nodes"]:
        print("\n[!] 고아 노드 (처음 10개):")
        for n in result["orphaned_nodes"]:
            print(f"    {n['id']}  depth={n['depth']}  ref={n['ref']}  '{n['content_preview']}'")
    if result["invalid_parent_chunks"]:
        print("\n[!] 잘못된 parent-chunk (처음 10개):")
        for nid in result["invalid_parent_chunks"]:
            print(f"    {nid}")
    if result["valid"]:
        print("\n✓ 검증 통과: 고아 노드 없음, parent-chunk 마킹 정상")
    else:
        print("\n✗ 검증 실패: 위 항목 확인 필요")


def run_embed_only(limit: int | None = None, dry_run: bool = False) -> None:
    writer = IngestWriter()
    if dry_run:
        print("청킹 계획 분석 중 (API 호출 없음)...")
        writer.embed_pending(limit=limit, dry_run=True)
        return
    label = f"처음 {limit}개 " if limit else ""
    print(f"임베딩 생성 중... ({label}노드 대상)")
    embedded = writer.embed_pending(limit=limit)
    print(f"완료 — {embedded}개 노드")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="법제처 법령 수집기",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="API 파싱만 수행하고 DB에 반영하지 않음",
    )
    parser.add_argument(
        "--skip-embed",
        action="store_true",
        help="DB 저장 후 임베딩 생성을 건너뜀",
    )
    parser.add_argument(
        "--embed-only",
        action="store_true",
        help="수집 없이 미완료 임베딩만 생성",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="API 요청 URL과 응답 원문 앞부분을 출력",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="임베딩 대상 청크 수 제한 (--embed-only 와 함께 사용, 테스트용)",
    )
    parser.add_argument(
        "--chunk-dry",
        action="store_true",
        help="청킹 계획만 출력하고 임베딩 API 호출 없이 종료 (--embed-only 와 함께 사용)",
    )
    parser.add_argument(
        "--verify-embed",
        action="store_true",
        help="임베딩 커버리지 검증 (고아 노드 및 잘못된 parent-chunk 탐색)",
    )
    args = parser.parse_args()

    if args.verify_embed:
        run_verify_embed()
        return

    if args.embed_only:
        run_embed_only(limit=args.limit, dry_run=args.chunk_dry)
        return

    api_key = os.getenv("LAW_API_KEY")
    if not api_key:
        print("오류: LAW_API_KEY 환경변수가 설정되지 않았습니다.")
        print("  export LAW_API_KEY=<발급받은 OC 키>")
        sys.exit(1)

    run(api_key, dry_run=args.dry_run, skip_embed=args.skip_embed, debug=args.debug)


if __name__ == "__main__":
    main()
