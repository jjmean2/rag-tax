"""법제처 law.go.kr XML 응답을 그대로 반영하는 파이썬 데이터 클래스.

schemas/law_go_kr_law.rnc 와 1:1 대응.
각 클래스는 from_el() 로 XML Element 에서 생성한다.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────
# 목 / 호 / 항  (leaf → root 순서로 정의)
# ─────────────────────────────────────────────────────────


@dataclass
class 목:
    번호: str
    내용: str | None = None

    @classmethod
    def from_el(cls, el: ET.Element) -> 목:
        return cls(
            번호=(el.findtext("목번호") or "").strip(),
            내용=(el.findtext("목내용") or "").strip() or None,
        )


@dataclass
class 호:
    번호: str
    가지번호: str | None = None  # 있으면 제X호의Y
    내용: str | None = None
    목목록: list[목] = field(default_factory=list)

    @classmethod
    def from_el(cls, el: ET.Element) -> 호:
        return cls(
            번호=(el.findtext("호번호") or "").strip(),
            가지번호=(el.findtext("호가지번호") or "").strip() or None,
            내용=(el.findtext("호내용") or "").strip() or None,
            목목록=[목.from_el(m) for m in el.findall("목")],
        )


@dataclass
class 항:
    번호: str
    내용: str | None = None
    제개정유형: str | None = None
    제개정일자: str | None = None
    호목록: list[호] = field(default_factory=list)

    @classmethod
    def from_el(cls, el: ET.Element) -> 항:
        return cls(
            번호=(el.findtext("항번호") or "").strip(),
            내용=(el.findtext("항내용") or "").strip() or None,
            제개정유형=(el.findtext("항제개정유형") or "").strip() or None,
            제개정일자=(el.findtext("항제개정일자문자열") or "").strip() or None,
            호목록=[호.from_el(h) for h in el.findall("호")],
        )


# ─────────────────────────────────────────────────────────
# 조문단위
# ─────────────────────────────────────────────────────────


@dataclass
class 조문단위:
    번호: str  # 조문번호
    여부: str  # 조문여부: "전문" | "삭제"
    가지번호: str | None = None  # 조문가지번호 → 제X조의Y
    제목: str | None = None
    내용: str | None = None
    시행일자: str | None = None
    참고자료: str | None = None
    키: str | None = None  # @조문키
    항목록: list[항] = field(default_factory=list)

    @classmethod
    def from_el(cls, el: ET.Element) -> 조문단위:
        return cls(
            번호=(el.findtext("조문번호") or "").strip(),
            여부=(el.findtext("조문여부") or "").strip(),
            가지번호=(el.findtext("조문가지번호") or "").strip() or None,
            제목=(el.findtext("조문제목") or "").strip() or None,
            내용=(el.findtext("조문내용") or "").strip() or None,
            시행일자=(el.findtext("조문시행일자") or "").strip() or None,
            참고자료=(el.findtext("조문참고자료") or "").strip() or None,
            키=el.get("조문키"),
            항목록=[항.from_el(h) for h in el.findall("항")],
        )

    @property
    def section_ref(self) -> str:
        if self.가지번호:
            return f"제{self.번호}조의{self.가지번호}"
        return f"제{self.번호}조"

    @property
    def is_deleted(self) -> bool:
        return self.여부 == "삭제"


# ─────────────────────────────────────────────────────────
# 기본정보
# ─────────────────────────────────────────────────────────


@dataclass
class 기본정보:
    법령ID: str
    법령명_한글: str
    소관부처: str
    소관부처코드: str | None = None
    법령명_한자: str | None = None
    법종구분: str | None = None
    법종구분코드: str | None = None
    공포번호: str | None = None
    공포일자: str | None = None  # YYYYMMDD
    시행일자: str | None = None  # YYYYMMDD
    제개정구분: str | None = None

    @classmethod
    def from_el(cls, el: ET.Element) -> 기본정보:
        소관부처_el = el.find("소관부처")
        법종구분_el = el.find("법종구분")
        return cls(
            법령ID=(el.findtext("법령ID") or "").strip(),
            법령명_한글=(el.findtext("법령명_한글") or "").strip(),
            소관부처=(소관부처_el.text or "").strip() if 소관부처_el is not None else "",
            소관부처코드=소관부처_el.get("소관부처코드") if 소관부처_el is not None else None,
            법령명_한자=(el.findtext("법령명_한자") or "").strip() or None,
            법종구분=(법종구분_el.text or "").strip() if 법종구분_el is not None else None,
            법종구분코드=법종구분_el.get("법종구분코드") if 법종구분_el is not None else None,
            공포번호=(el.findtext("공포번호") or "").strip() or None,
            공포일자=(el.findtext("공포일자") or "").strip() or None,
            시행일자=(el.findtext("시행일자") or "").strip() or None,
            제개정구분=(el.findtext("제개정구분") or "").strip() or None,
        )


# ─────────────────────────────────────────────────────────
# 부칙단위
# ─────────────────────────────────────────────────────────


@dataclass
class 부칙단위:
    공포번호: str | None = None
    공포일자: str | None = None  # YYYYMMDD
    내용: str | None = None
    키: str | None = None  # @부칙키

    @classmethod
    def from_el(cls, el: ET.Element) -> 부칙단위:
        return cls(
            공포번호=(el.findtext("부칙공포번호") or "").strip() or None,
            공포일자=(el.findtext("부칙공포일자") or "").strip() or None,
            내용=(el.findtext("부칙내용") or "").strip() or None,
            키=el.get("부칙키"),
        )


# ─────────────────────────────────────────────────────────
# 법령  (루트)
# ─────────────────────────────────────────────────────────


@dataclass
class 법령:
    기본정보: 기본정보
    조문목록: list[조문단위]
    부칙목록: list[부칙단위] = field(default_factory=list)
    개정문내용: str | None = None
    제개정이유내용: str | None = None
    법령키: str | None = None

    @classmethod
    def from_el(cls, root: ET.Element) -> 법령 | None:
        기본정보_el = root.find("기본정보")
        if 기본정보_el is None:
            return None

        조문_el = root.find("조문")
        부칙_el = root.find("부칙")
        개정문_el = root.find("개정문")
        이유_el = root.find("제개정이유")

        return cls(
            법령키=root.get("법령키"),
            기본정보=기본정보.from_el(기본정보_el),
            조문목록=[
                조문단위.from_el(u)
                for u in (조문_el.findall("조문단위") if 조문_el is not None else [])
            ],
            부칙목록=[
                부칙단위.from_el(u)
                for u in (부칙_el.findall("부칙단위") if 부칙_el is not None else [])
            ],
            개정문내용=(개정문_el.findtext("개정문내용") or "").strip() or None
            if 개정문_el is not None
            else None,
            제개정이유내용=(이유_el.findtext("제개정이유내용") or "").strip() or None
            if 이유_el is not None
            else None,
        )
