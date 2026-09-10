"""Deterministic keyword-density checks shared by GEO scoring paths."""

from __future__ import annotations

import re
from collections.abc import Sequence

from services.content_hygiene_service import authoring_visible_length


_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_COMMON_SEPARATORS = re.compile(
    r"[\s,，。.!！?？;；:：、|/\\_\-—–·•()（）\[\]【】{}<>《》]+"
)


def has_no_keyword_stuffing(content: str, keywords: Sequence[str]) -> bool:
    """Reject repetition tricks without exempting short copy.

    Zero-width characters and common punctuation are ignored for matching, so
    casing, line breaks or separators cannot hide repeated terms.  Short copy
    gets a slightly wider total-density allowance, but fewer repetitions.
    """

    normalized_content = _compact(content)
    if not normalized_content or not keywords:
        return True
    visible_length = max(authoring_visible_length(_ZERO_WIDTH.sub("", content)), 1)
    if visible_length <= 120:
        density_limit = 0.30
        repetition_limit = 2
    elif visible_length <= 400:
        density_limit = 0.25
        repetition_limit = 2
    elif visible_length <= 800:
        density_limit = 0.16
        repetition_limit = 3
    else:
        density_limit = 0.08
        repetition_limit = max(3, visible_length // 500 + 2)

    matched_intervals: list[tuple[int, int]] = []
    seen: set[str] = set()
    maximum_occurrences = 0
    for keyword in keywords:
        token = _compact(str(keyword))
        if not token or token in seen:
            continue
        seen.add(token)
        occurrences = normalized_content.count(token)
        maximum_occurrences = max(maximum_occurrences, occurrences)
        matched_intervals.extend(
            (match.start(), match.end())
            for match in re.finditer(re.escape(token), normalized_content)
        )
        if occurrences > repetition_limit:
            return False
        if re.search(rf"(?:{re.escape(token)}){{3,}}", normalized_content):
            return False
    if maximum_occurrences <= 1:
        return True
    matched_intervals.sort()
    merged: list[list[int]] = []
    for start, end in matched_intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    matched_characters = sum(end - start for start, end in merged)
    return matched_characters / visible_length <= density_limit


def _compact(value: str) -> str:
    without_hidden = _ZERO_WIDTH.sub("", str(value or "")).casefold()
    return _COMMON_SEPARATORS.sub("", without_hidden)


__all__ = ["has_no_keyword_stuffing"]
