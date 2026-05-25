"""XML 파일을 읽어 태그 트리 구조와 출현 패턴을 요약한다.

사용법:
    uv run python scripts/infer_xml_schema.py law_response.xml
    uv run python scripts/infer_xml_schema.py admrul_response.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def _tag(el: ET.Element) -> str:
    """네임스페이스 제거."""
    tag = el.tag
    return tag.split("}")[-1] if "}" in tag else tag


def collect(
    el: ET.Element,
    parent_path: str,
    stats: dict,
) -> None:
    tag = _tag(el)
    path = f"{parent_path}/{tag}" if parent_path else tag

    node = stats[path]
    node["tag"] = tag
    node["parent"] = parent_path
    node["count"] += 1

    text = (el.text or "").strip()
    if text:
        node["has_text"] = True
        if len(node["samples"]) < 2:
            node["samples"].append(text[:60])

    for attr in el.attrib:
        node["attrs"].add(attr)

    children = [_tag(c) for c in el]
    if children:
        node["children"].update(children)

    for child in el:
        collect(child, path, stats)


def summarize(path: str) -> None:
    tree = ET.parse(path)
    root = tree.getroot()

    stats: dict = defaultdict(
        lambda: {
            "tag": "",
            "parent": "",
            "count": 0,
            "has_text": False,
            "samples": [],
            "attrs": set(),
            "children": set(),
        }
    )

    collect(root, "", stats)

    # 트리 순서로 출력
    printed: set[str] = set()

    def print_node(node_path: str, depth: int) -> None:
        if node_path in printed:
            return
        printed.add(node_path)
        n = stats[node_path]

        indent = "  " * depth
        count_marker = f" [×{n['count']}]" if n["count"] > 1 else ""
        text_marker = " [text]" if n["has_text"] else ""
        attr_marker = f" @{','.join(sorted(n['attrs']))}" if n["attrs"] else ""
        sample = f"  ← 예: {n['samples'][0]!r}" if n["samples"] else ""

        print(f"{indent}{n['tag']}{count_marker}{text_marker}{attr_marker}{sample}")

        # 자식들을 이 노드의 경로 기준으로 재귀
        for child_tag in sorted(n["children"]):
            child_path = f"{node_path}/{child_tag}"
            if child_path in stats:
                print_node(child_path, depth + 1)

    root_path = _tag(root)
    print(f"\n=== {Path(path).name} 구조 ===\n")
    print_node(root_path, 0)
    print(f"\n총 고유 경로: {len(stats)}개")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: uv run python scripts/infer_xml_schema.py <xml파일>")
        sys.exit(1)
    summarize(sys.argv[1])
