"""Deterministic cleanup for unfinished model-generated publishing copy."""

from __future__ import annotations

import re
from dataclasses import dataclass


CONTENT_HYGIENE_VERSION = "content-hygiene-1.0.0"

_METADATA_PLACEHOLDER_LINE = re.compile(
    r"^\s*[*_]*\s*(?:作者|更新时间|更新日期|发布日期)\s*[:：]\s*"
    r"(?:\[(?:待补充|待填写|作者占位|更新时间占位|日期占位)\]|"
    r"(?:待补充|待填写|作者占位|更新时间占位|日期占位))\s*[*_]*\s*$",
    re.IGNORECASE,
)
_GENERIC_BRACKET_PLACEHOLDER = re.compile(
    r"\[(?:待补充|待填写|待确认|TBD|TODO|[^\]\n]{0,24}占位)\]",
    re.IGNORECASE,
)
_EVIDENCE_PLACEHOLDER = re.compile(
    r"(?:[（(]\s*)?待补充(?:来源|链接|证据)(?:\s*[）)])?",
    re.IGNORECASE,
)
_LIST_PREFIX = re.compile(r"^\s*(?:[-*+]\s+|[•·]\s*)")


@dataclass(frozen=True)
class ContentHygieneResult:
    content: str
    removed_lines: int
    replaced_markers: int

    @property
    def changed(self) -> bool:
        return bool(self.removed_lines or self.replaced_markers)


def sanitize_generated_content(value: str) -> ContentHygieneResult:
    """Remove unfinished placeholders without inventing missing facts.

    Author and update-time metadata belongs to the destination account or
    article record.  A missing evidence marker is converted into an explicit
    evidence boundary in prose, while a fake item in a reference list is
    removed because it is not a source.
    """

    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    output: list[str] = []
    removed_lines = 0
    replaced_markers = 0
    for line in normalized.split("\n"):
        if _METADATA_PLACEHOLDER_LINE.fullmatch(line):
            removed_lines += 1
            continue
        if _GENERIC_BRACKET_PLACEHOLDER.search(line):
            removed_lines += 1
            continue
        if _EVIDENCE_PLACEHOLDER.search(line):
            if _LIST_PREFIX.match(line):
                removed_lines += 1
                continue
            line, count = _EVIDENCE_PLACEHOLDER.subn("暂无可核验公开证据", line)
            replaced_markers += count
        output.append(line)

    content = "\n".join(output)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return ContentHygieneResult(
        content=content,
        removed_lines=removed_lines,
        replaced_markers=replaced_markers,
    )


def has_unresolved_placeholders(value: str) -> bool:
    text = str(value or "")
    return bool(
        _METADATA_PLACEHOLDER_LINE.search(text)
        or _GENERIC_BRACKET_PLACEHOLDER.search(text)
        or _EVIDENCE_PLACEHOLDER.search(text)
    )
