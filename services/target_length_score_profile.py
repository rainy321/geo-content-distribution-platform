"""Shared target-length profiles for deterministic GEO scoring.

The profile only decides whether a presentation check is applicable and how
much real content is needed for that format.  It never awards points merely
because a shorter target was selected.
"""

from __future__ import annotations

from typing import Any


TARGET_LENGTH_SCORE_PROFILE_VERSION = "target-length-score-profile-1.0.0"
MIN_TARGET_LENGTH = 200
MAX_TARGET_LENGTH = 3000
NAMED_TARGET_LENGTHS = frozenset({200, 600, 1000, 1500})


def resolve_effective_length_score_profile(
    target_length: int | None,
    *,
    actual_length: int,
) -> dict[str, Any]:
    """Resolve the declared profile and prevent short-profile score gaming."""

    if isinstance(actual_length, bool) or not isinstance(actual_length, int):
        raise ValueError("actual_length 必须是整数")
    actual_length = max(0, actual_length)
    if target_length is None:
        return {
            **resolve_target_length_score_profile(None),
            "source": "legacy",
            "requested_target_length": None,
            "effective_target_length": None,
            "over_target": False,
        }

    declared = resolve_target_length_score_profile(target_length)
    upgrade_threshold = max(target_length + 80, round(target_length * 1.25))
    over_target = actual_length > target_length
    if actual_length <= upgrade_threshold:
        return {
            **declared,
            "source": "explicit",
            "requested_target_length": target_length,
            "effective_target_length": target_length,
            "over_target": over_target,
        }

    effective_target = max(
        MIN_TARGET_LENGTH,
        min(MAX_TARGET_LENGTH, actual_length),
    )
    return {
        **resolve_target_length_score_profile(effective_target),
        "source": "actual_length_upgrade",
        "requested_target_length": target_length,
        "effective_target_length": effective_target,
        "over_target": True,
    }


def resolve_target_length_score_profile(
    target_length: int | None,
) -> dict[str, Any]:
    """Return a JSON-safe scoring profile for an intended article length.

    ``None`` deliberately preserves the historic 600-character, long-form
    score contract.  Explicit targets use one of four applicability bands;
    values other than the four UI presets are labelled ``custom`` while still
    receiving a deterministic band.
    """

    if target_length is None:
        return _profile(
            requested_length=None,
            preset="legacy",
            band="legacy",
            minimum_content_length=600,
            minimum_paragraphs=3,
            headings=True,
            faq=True,
            summary=True,
            keyword_limit=None,
        )
    if isinstance(target_length, bool) or not isinstance(target_length, int):
        raise ValueError("target_length 必须是整数")
    if not MIN_TARGET_LENGTH <= target_length <= MAX_TARGET_LENGTH:
        raise ValueError(
            f"target_length 必须在 {MIN_TARGET_LENGTH} 到 {MAX_TARGET_LENGTH} 之间"
        )

    preset = str(target_length) if target_length in NAMED_TARGET_LENGTHS else "custom"
    minimum_content_length = min(
        target_length,
        max(120, round(target_length * 0.60)),
    )
    if target_length <= 400:
        return _profile(
            requested_length=target_length,
            preset=preset,
            band="compact",
            minimum_content_length=minimum_content_length,
            minimum_paragraphs=2,
            headings=False,
            faq=False,
            summary=False,
            keyword_limit=2,
        )
    if target_length < 800:
        return _profile(
            requested_length=target_length,
            preset=preset,
            band="short",
            minimum_content_length=minimum_content_length,
            minimum_paragraphs=2,
            headings=False,
            faq=False,
            summary=False,
            keyword_limit=3,
        )
    if target_length < 1200:
        return _profile(
            requested_length=target_length,
            preset=preset,
            band="standard",
            minimum_content_length=max(600, minimum_content_length),
            minimum_paragraphs=3,
            headings=True,
            faq=True,
            summary=True,
            keyword_limit=5,
        )
    return _profile(
        requested_length=target_length,
        preset=preset,
        band="long",
        minimum_content_length=max(720, minimum_content_length),
        minimum_paragraphs=4,
        headings=True,
        faq=True,
        summary=True,
        keyword_limit=None,
    )


def _profile(
    *,
    requested_length: int | None,
    preset: str,
    band: str,
    minimum_content_length: int,
    minimum_paragraphs: int,
    headings: bool,
    faq: bool,
    summary: bool,
    keyword_limit: int | None,
) -> dict[str, Any]:
    applicability = {
        "headings": headings,
        "faq": faq,
        "summary": summary,
    }
    return {
        "version": TARGET_LENGTH_SCORE_PROFILE_VERSION,
        "requested_length": requested_length,
        "preset": preset,
        "band": band,
        "minimum_content_length": minimum_content_length,
        "minimum_paragraphs": minimum_paragraphs,
        "keyword_limit": keyword_limit,
        "applicable_checks": applicability,
        "excluded_checks": [
            key for key, applicable in applicability.items() if not applicable
        ],
        "scoring_policy": (
            "不适用检查的有效权重为 0；只按实际通过的适用检查计分，"
            "选择较短目标本身不会获得分数。"
        ),
    }
