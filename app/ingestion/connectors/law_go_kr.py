"""법제처 국가법령정보 Open API 수집기.

API 문서: https://open.law.go.kr/LSO/openApi/openApiInfo.do
rate limit: 요청 간 REQUEST_DELAY 초 대기
"""
from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime

from app.ingestion.connectors.law_go_kr_types import 법령 as 법령XML
from app.ingestion.models import RawDocument, RawNode, RawVersion

BASE_URL = "https://www.law.go.kr/DRF"
REQUEST_DELAY = 1.2  # 초당 1회 이하

AUTHORITY_MAP = {
    "기획재정부": "moef",
    "재정경제부": "moef",  # 기획재정부 구 명칭
    "국세청": "nts",
    "법제처": "klri",
    "행정안전부": "mois",
}


class LawGoKrConnector:
    def __init__(self, api_key: str, debug: bool = False) -> None:
        self.api_key = api_key
        self.debug = debug
        self._last_call_at: float = 0.0

    # ------------------------------------------------------------------
    # 공개 메서드
    # ------------------------------------------------------------------

    def search_law_id(self, query: str, target: str) -> str | None:
        """법령/행정규칙명으로 검색해 법령일련번호(MST)를 반환한다.

        API의 검색은 포함(contains) 방식이므로 타이틀을 직접 비교한다.
        """
        root = self._get("lawSearch.do", {
            "target": target,
            "query": query,
            "display": "20",
            "page": "1",
        })
        for law_el in root.findall("law"):
            name = (law_el.findtext("법령명한글") or "").strip()
            if name == query:
                mst = (
                    law_el.findtext("법령일련번호")
                    or law_el.findtext("행정규칙일련번호")
                )
                if mst:
                    return mst.strip()
        return None

    def fetch_document(self, mst: str, target: str) -> RawDocument | None:
        """법령일련번호(MST)로 전문을 조회해 RawDocument 로 변환한다."""
        root = self._get("lawService.do", {"target": target, "MST": mst})
        if target == "admrul":
            return self._parse_admrul(root, mst)
        return self._parse_law(root, mst)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _get(self, endpoint: str, params: dict[str, str]) -> ET.Element:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)

        full_params = {"OC": self.api_key, "type": "XML", **params}
        url = f"{BASE_URL}/{endpoint}?{urllib.parse.urlencode(full_params)}"

        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} — {url}") from exc
        finally:
            self._last_call_at = time.monotonic()

        if self.debug:
            print(f"  [GET] {url}")
            print(f"  [RAW] {raw[:600].decode(errors='replace')}\n")

        return ET.fromstring(raw)

    # ------------------------------------------------------------------
    # XML 파싱
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(value: str | None) -> date | None:
        if not value or len(value) < 8:
            return None
        try:
            return datetime.strptime(value[:8], "%Y%m%d").date()
        except ValueError:
            return None

    def _parse_law(self, root: ET.Element, mst: str) -> RawDocument | None:
        law = 법령XML.from_el(root)
        if law is None:
            return None

        info = law.기본정보
        nodes, raw_text = self._nodes_from_law(law)

        return RawDocument(
            source_system="law_go_kr",
            source_id=mst,
            doc_type="statute",
            authority=AUTHORITY_MAP.get(info.소관부처, "moef"),
            title=info.법령명_한글,
            canonical_url=(
                f"https://www.law.go.kr/법령/{urllib.parse.quote(info.법령명_한글)}"
                if info.법령명_한글 else None
            ),
            version=RawVersion(
                version_label=info.공포번호 or "",
                publish_date=self._parse_date(info.공포일자),
                effective_from=self._parse_date(info.시행일자),
                effective_to=None,
                status="current",
                raw_text=raw_text,
                nodes=nodes,
                metadata={"MST": mst, "법령ID": info.법령ID, "소관부처명": info.소관부처},
            ),
        )

    def _parse_admrul(self, root: ET.Element, mst: str) -> RawDocument | None:
        info = root.find("기본정보")
        if info is None:
            return None

        title = (
            info.findtext("행정규칙명")
            or info.findtext("법령명_한글")
            or ""
        ).strip()
        authority_el = info.find("소관부처")
        authority_name = (
            (authority_el.text or "").strip() if authority_el is not None
            else (info.findtext("소관부처명") or "").strip()
        )
        publish_date = self._parse_date(
            info.findtext("발령일자") or info.findtext("공포일자")
        )
        effective_from = self._parse_date(info.findtext("시행일자"))
        version_label = (
            info.findtext("발령번호") or info.findtext("공포번호") or ""
        ).strip()

        nodes, raw_text = self._nodes_from_admrul(root)

        return RawDocument(
            source_system="law_go_kr",
            source_id=f"admrul:{mst}",
            doc_type="ruling",
            authority=AUTHORITY_MAP.get(authority_name, "nts"),
            title=title,
            canonical_url=None,
            version=RawVersion(
                version_label=version_label,
                publish_date=publish_date,
                effective_from=effective_from,
                effective_to=None,
                status="current",
                raw_text=raw_text,
                nodes=nodes,
                metadata={"행정규칙ID": mst, "소관부처명": authority_name},
            ),
        )

    # ------------------------------------------------------------------
    # 노드 트리 생성
    # ------------------------------------------------------------------

    @staticmethod
    def _nodes_from_law(law: 법령XML) -> tuple[list[RawNode], str]:
        """typed 법령 dataclass → RawNode 트리(flat list) + 전체 원문."""
        nodes: list[RawNode] = []
        raw_lines: list[str] = []
        article_order = 0

        for unit in law.조문목록:
            if unit.is_deleted or not unit.번호:
                continue

            article_ref = unit.section_ref      # "제19조" | "제19조의2"

            nodes.append(RawNode(
                node_id=article_ref,
                node_type="article",
                ref=article_ref,
                title=unit.제목,
                content=unit.내용,              # 조문내용 (없으면 None)
                depth=0,
                order_no=article_order,
                parent_id=None,
                metadata={"조문번호": unit.번호, "조문가지번호": unit.가지번호 or ""},
            ))

            para_order = 0
            for 항 in unit.항목록:
                para_id = f"{article_ref}:{항.번호}"
                nodes.append(RawNode(
                    node_id=para_id,
                    node_type="paragraph",
                    ref=항.번호,
                    title=None,
                    content=항.내용,
                    depth=1,
                    order_no=para_order,
                    parent_id=article_ref,
                ))

                item_order = 0
                for 호 in 항.호목록:
                    item_id = f"{para_id}:{호.번호}"
                    nodes.append(RawNode(
                        node_id=item_id,
                        node_type="item",
                        ref=호.번호,
                        title=None,
                        content=호.내용,
                        depth=2,
                        order_no=item_order,
                        parent_id=para_id,
                    ))

                    subitem_order = 0
                    for 목 in 호.목목록:
                        nodes.append(RawNode(
                            node_id=f"{item_id}:{목.번호}",
                            node_type="subitem",
                            ref=목.번호,
                            title=None,
                            content=목.내용,
                            depth=3,
                            order_no=subitem_order,
                            parent_id=item_id,
                        ))
                        subitem_order += 1
                    item_order += 1
                para_order += 1

            # 버전 수준 원문용 평문 조립
            body_lines: list[str] = []
            if unit.내용:
                body_lines.append(unit.내용)
            for 항 in unit.항목록:
                if 항.내용:
                    body_lines.append(f"{항.번호} {항.내용}".strip())
                for 호 in 항.호목록:
                    if 호.내용:
                        body_lines.append(f"  {호.번호} {호.내용}".strip())
                    for 목 in 호.목목록:
                        if 목.내용:
                            body_lines.append(f"    {목.번호} {목.내용}".strip())
            content = "\n".join(body_lines).strip()
            if content:
                header = f"{article_ref}({unit.제목})" if unit.제목 else article_ref
                raw_lines.append(f"{header}\n{content}")

            article_order += 1

        return nodes, "\n\n".join(raw_lines)

    @staticmethod
    def _nodes_from_admrul(root: ET.Element) -> tuple[list[RawNode], str]:
        """행정규칙 XML → flat RawNode 목록 (조문 단위로만 저장).

        행정규칙 XML 구조는 아직 상세 분석 전이므로 조문 단위 flat 저장.
        추후 law_go_kr_types 에 행정규칙 타입이 추가되면 트리로 교체한다.
        """
        nodes: list[RawNode] = []
        raw_lines: list[str] = []
        order = 0

        조문_el = root.find("조문")
        if 조문_el is None:
            return nodes, ""

        for unit in 조문_el.findall("조문단위"):
            조문번호 = (unit.findtext("조문번호") or "").strip()
            조문가지번호 = (unit.findtext("조문가지번호") or "").strip()
            조문제목 = (unit.findtext("조문제목") or "").strip()
            조문여부 = (unit.findtext("조문여부") or "").strip()

            if 조문여부 == "삭제" or not 조문번호:
                continue

            ref = f"제{조문번호}조의{조문가지번호}" if 조문가지번호 else f"제{조문번호}조"

            body_lines: list[str] = []
            조문내용 = (unit.findtext("조문내용") or "").strip()
            if 조문내용:
                body_lines.append(조문내용)
            for 항 in unit.findall("항"):
                항번호 = (항.findtext("항번호") or "").strip()
                항내용 = (항.findtext("항내용") or "").strip()
                if 항내용:
                    body_lines.append(f"{항번호} {항내용}".strip())
                for 호 in 항.findall("호"):
                    호번호 = (호.findtext("호번호") or "").strip()
                    호내용 = (호.findtext("호내용") or "").strip()
                    if 호내용:
                        body_lines.append(f"  {호번호} {호내용}".strip())
                    for 목 in 호.findall("목"):
                        목번호 = (목.findtext("목번호") or "").strip()
                        목내용 = (목.findtext("목내용") or "").strip()
                        if 목내용:
                            body_lines.append(f"    {목번호} {목내용}".strip())

            content = "\n".join(body_lines).strip()
            if not content:
                continue

            nodes.append(RawNode(
                node_id=ref,
                node_type="article",
                ref=ref,
                title=조문제목 or None,
                content=content,
                depth=0,
                order_no=order,
                parent_id=None,
                metadata={"조문번호": 조문번호, "조문가지번호": 조문가지번호},
            ))
            header = f"{ref}({조문제목})" if 조문제목 else ref
            raw_lines.append(f"{header}\n{content}")
            order += 1

        return nodes, "\n\n".join(raw_lines)
