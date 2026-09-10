"""Deterministic cleanup for unfinished model-generated publishing copy."""

from __future__ import annotations

import re
from dataclasses import dataclass


CONTENT_HYGIENE_VERSION = "content-hygiene-1.2.0"

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


def authoring_visible_length(value: str) -> int:
    """Count non-whitespace authoring characters.

    This is a generation/scoring length, not a publishing platform limit.
    Markdown markers and every other non-whitespace Unicode code point remain
    countable; platform policies deliberately keep separate raw counters.
    """

    return sum(1 for character in str(value or "") if not character.isspace())


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
    """Keep model output inside the requested visible-character budget.

    A generation target is treated as a hard product-side ceiling.  The helper
    prefers complete leading sentences or natural clauses and, when space
    permits, retains one short evidence/boundary sentence from the tail.  An
    explicit ellipsis is used only when complete units cannot reach a
    reasonable 80% budget floor.  It never calls another model or invents copy.
    """

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("内容长度上限必须是正整数")
    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    original_length = authoring_visible_length(normalized)
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

    tail_index: int | None = None
    tail_budget = min(120, max(0, limit // 4))
    for index in range(len(pieces) - 1, 1, -1):
        piece = pieces[index]
        if (
            _EVIDENCE_OR_BOUNDARY.search(piece)
            and authoring_visible_length(piece) <= tail_budget
        ):
            tail_index = index
            break

    tail = pieces[tail_index] if tail_index is not None else ""
    tail_length = authoring_visible_length(tail)
    separator = "\n" if tail else ""
    head_budget = limit - tail_length
    minimum_output_length = (limit * 4 + 4) // 5
    selected: list[str] = []
    used = 0
    for index, piece in enumerate(pieces):
        if index == tail_index:
            continue
        piece_length = authoring_visible_length(piece)
        if used + piece_length <= head_budget:
            selected.append(piece)
            used += piece_length
            continue
        if used + tail_length < minimum_output_length:
            fragment = _natural_prefix(piece, head_budget - used)
            if fragment:
                selected.append(fragment)
                used += authoring_visible_length(fragment)
        break

    head = "\n".join(selected).rstrip()
    if not head:
        head = _natural_prefix(pieces[0], head_budget)

    shortened = f"{head}{separator}{tail}".strip()
    if authoring_visible_length(shortened) > limit:  # defensive invariant
        shortened = _natural_prefix(shortened, limit)
    return ContentLengthResult(
        shortened,
        original_length,
        authoring_visible_length(shortened),
        limit,
    )


def _natural_prefix(value: str, visible_budget: int) -> str:
    """Return a readable prefix that fits one visible-character budget."""

    if visible_budget <= 0:
        return ""
    normalized = str(value or "").strip()
    if authoring_visible_length(normalized) <= visible_budget:
        return normalized

    candidate = _prefix_with_visible_budget(normalized, visible_budget)
    minimum_natural_fill = max(1, (visible_budget * 4 + 4) // 5)
    for index in range(len(candidate) - 1, -1, -1):
        if candidate[index] not in "。！？!?；;，,：:":
            continue
        natural = candidate[: index + 1].rstrip()
        if authoring_visible_length(natural) >= minimum_natural_fill:
            return natural

    if visible_budget == 1:
        return "…"
    prefix = _prefix_with_visible_budget(normalized, visible_budget - 1)
    prefix = prefix.rstrip(" ，,；;：:")
    if re.search(r"[。！？!?；;]$", prefix):
        return prefix
    return f"{prefix}…" if prefix else "…"


def _prefix_with_visible_budget(value: str, visible_budget: int) -> str:
    visible_count = 0
    end = 0
    for index, character in enumerate(value):
        if not character.isspace():
            if visible_count >= visible_budget:
                break
            visible_count += 1
        end = index + 1
    return value[:end].rstrip()


def has_unresolved_placeholders(value: str) -> bool:
    text = str(value or "")
    return bool(
        _METADATA_PLACEHOLDER_LINE.search(text)
        or _GENERIC_BRACKET_PLACEHOLDER.search(text)
        or _EVIDENCE_PLACEHOLDER.search(text)
    )
