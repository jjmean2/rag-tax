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
from app.ingestion.models import RawDocument, RawSection, RawVersion

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

        API의 exact=1은 포함(contains) 검색이므로 타이틀을 직접 비교한다.
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
                # 상세 조회에는 법령일련번호를 MST 파라미터로 사용
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
        sections, raw_text = self._sections_from_law(law)

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
                sections=sections,
                metadata={"MST": mst, "법령ID": info.법령ID, "소관부처명": info.소관부처},
            ),
        )

    @staticmethod
    def _sections_from_law(law: 법령XML) -> tuple[list[RawSection], str]:
        """typed 법령 dataclass → RawSection 목록과 전체 원문 텍스트."""
        sections: list[RawSection] = []
        raw_lines: list[str] = []
        order = 0

        for unit in law.조문목록:
            if unit.is_deleted or not unit.번호:
                continue

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
            if not content:
                continue

            section_ref = unit.section_ref
            full_text = (
                f"{section_ref}({unit.제목})\n{content}"
                if unit.제목 else
                f"{section_ref}\n{content}"
            )
            raw_lines.append(full_text)

            sections.append(RawSection(
                section_ref=section_ref,
                heading=unit.제목,
                content=content,
                order_no=order,
                section_type="article",
                metadata={"조문번호": unit.번호, "조문가지번호": unit.가지번호 or ""},
            ))
            order += 1

        return sections, "\n\n".join(raw_lines)

    def _parse_admrul(self, root: ET.Element, law_id: str) -> RawDocument | None:
        # 행정규칙은 루트가 <행정규칙> 또는 <법령> 둘 다 가능
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

        sections, raw_text = self._extract_sections(root)

        return RawDocument(
            source_system="law_go_kr",
            source_id=f"admrul:{law_id}",
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
                sections=sections,
                metadata={"행정규칙ID": law_id, "소관부처명": authority_name},
            ),
        )

    @staticmethod
    def _extract_sections(
        root: ET.Element,
    ) -> tuple[list[RawSection], str]:
        """조문단위 → RawSection 목록과 전체 원문 텍스트를 반환한다."""
        sections: list[RawSection] = []
        raw_lines: list[str] = []
        order = 0

        조문_el = root.find("조문")
        if 조문_el is None:
            return sections, ""

        for unit in 조문_el.findall("조문단위"):
            # XSD: 조문번호 (필수), 조문가지번호 (선택, 예: "2" → 제19조의2)
            조문번호 = (unit.findtext("조문번호") or "").strip()
            조문가지번호 = (unit.findtext("조문가지번호") or "").strip()
            조문제목 = (unit.findtext("조문제목") or "").strip()
            조문여부 = (unit.findtext("조문여부") or "").strip()

            if 조문여부 == "삭제" or not 조문번호:
                continue

            if 조문가지번호:
                section_ref = f"제{조문번호}조의{조문가지번호}"
            else:
                section_ref = f"제{조문번호}조"

            heading = 조문제목 or None

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

            full_text = f"{section_ref}({조문제목})\n{content}" if 조문제목 else f"{section_ref}\n{content}"
            raw_lines.append(full_text)

            sections.append(RawSection(
                section_ref=section_ref,
                heading=heading,
                content=content,
                order_no=order,
                section_type="article",
                metadata={"조문번호": 조문번호, "조문가지번호": 조문가지번호},
            ))
            order += 1

        return sections, "\n\n".join(raw_lines)
