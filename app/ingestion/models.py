from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class RawSection:
    section_ref: str | None
    heading: str | None
    content: str
    order_no: int
    section_type: str = "article"
    metadata: dict = field(default_factory=dict)


@dataclass
class RawVersion:
    version_label: str
    publish_date: date | None
    effective_from: date | None
    effective_to: date | None
    status: str
    raw_text: str
    sections: list[RawSection] = field(default_factory=list)
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
