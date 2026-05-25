from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class RawNode:
    node_id: str            # 문서 내 고유 식별자 (DB ID 생성에 사용)
    node_type: str          # article | paragraph | item | subitem | provision | ...
    ref: str | None         # "제19조", "①", "1.", "가."
    title: str | None       # 조문제목 등
    content: str | None     # 본문; 컨테이너 노드(조문 등)는 None 가능
    depth: int              # 0=조, 1=항, 2=호, 3=목
    order_no: int
    parent_id: str | None = None   # 다른 RawNode.node_id 를 참조
    metadata: dict = field(default_factory=dict)


@dataclass
class RawVersion:
    version_label: str
    publish_date: date | None
    effective_from: date | None
    effective_to: date | None
    status: str
    raw_text: str
    nodes: list[RawNode] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class RawDocument:
    source_system: str
    source_id: str
    doc_type: str
    authority: str
    title: str
    canonical_url: str | None
    version: RawVersion
