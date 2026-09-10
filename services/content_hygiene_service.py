"""Deterministic cleanup for unfinished model-generated publishing copy."""

from __future__ import annotations

import re
from dataclasses import dataclass


CONTENT_HYGIENE_VERSION = "content-hygiene-1.1.0"

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
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;])|\n+")
_EVIDENCE_OR_BOUNDARY = re.compile(
    r"(?:https?://|参考(?:资料|来源)?|证据|适用(?:条件|范围)|限制|边界|风险)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContentHygieneResult:
    content: str
    removed_lines: int
    replaced_markers: int

    @property
    def changed(self) -> bool:
        return bool(self.removed_lines or self.replaced_markers)


@dataclass(frozen=True)
class ContentLengthResult:
    content: str
    original_length: int
    final_length: int
    limit: int

    @property
    def changed(self) -> bool:
        return self.final_length < self.original_length


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


def constrain_generated_content_length(value: str, limit: int) -> ContentLengthResult:
    """Keep model output inside the requested character budget.

    A generation target is treated as a hard product-side ceiling.  The helper
    selects complete leading sentences and, when space permits, retains one
    short evidence/boundary sentence from the tail.  It never calls another
    model, invents copy, or leaves a half sentence merely to fill the budget.
    """

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("内容长度上限必须是正整数")
    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    original_length = len(normalized)
    if original_length <= limit:
        return ContentLengthResult(normalized, original_length, original_length, limit)

    pieces = [
        piece
        for raw_piece in _SENTENCE_BOUNDARY.split(normalized)
        if (piece := raw_piece.strip())
    ]
    if not pieces:
        shortened = normalized[:limit].rstrip()
        return ContentLengthResult(shortened, original_length, len(shortened), limit)

    tail = ""
    tail_budget = min(120, max(0, limit // 4))
    for piece in reversed(pieces):
        if (
            _EVIDENCE_OR_BOUNDARY.search(piece)
            and len(piece) <= tail_budget
            and piece not in pieces[:2]
        ):
            tail = piece
            break

    separator = "\n" if tail else ""
    head_budget = limit - len(separator) - len(tail)
    selected: list[str] = []
    used = 0
    for piece in pieces:
        if piece == tail:
            continue
        joiner = "\n" if selected else ""
        addition = f"{joiner}{piece}"
        if used + len(addition) <= head_budget:
            selected.append(piece)
            used += len(addition)
            continue
        break

    head = "\n".join(selected).rstrip()
    if not head:
        # A single model sentence can exceed the entire budget. Prefer the
        # last natural clause boundary inside the limit; only then fall back
        # to an explicit ellipsis rather than a misleading complete sentence.
        candidate = pieces[0][: max(1, head_budget)].rstrip()
        boundary = max(
            candidate.rfind(mark)
            for mark in ("。", "！", "？", "；", ".", "!", "?", ";")
        )
        head = candidate[: boundary + 1].rstrip() if boundary >= 20 else candidate.rstrip(" ，,；;：:")
        if head and not re.search(r"[。！？!?；;]$", head):
            head = f"{head[: max(1, head_budget - 1)].rstrip()}…"

    shortened = f"{head}{separator}{tail}".strip()
    if len(shortened) > limit:  # defensive invariant
        shortened = shortened[:limit].rstrip()
    return ContentLengthResult(shortened, original_length, len(shortened), limit)


def has_unresolved_placeholders(value: str) -> bool:
    text = str(value or "")
    return bool(
        _METADATA_PLACEHOLDER_LINE.search(text)
        or _GENERIC_BRACKET_PLACEHOLDER.search(text)
        or _EVIDENCE_PLACEHOLDER.search(text)
    )
