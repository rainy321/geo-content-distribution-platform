"""Deterministic content summaries for a user's specific confirmation view.

These hashes detect stale views. They are neither credentials nor permission
to publish, and never contain session data or expose authorization internals.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def _digest(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _string_list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            value = []
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def article_review_fingerprint(article: Mapping[str, Any]) -> str:
    value = dict(article)
    return _digest({
        "schema": 1,
        "id": value["id"],
        "project_id": value["project_id"],
        "title": str(value.get("title") or ""),
        "content": str(value.get("content") or ""),
        "tags": _string_list(value.get("tags")),
        "status": value.get("status"),
    })


def publish_confirmation_snapshot(job: Mapping[str, Any]) -> str:
    value = dict(job)
    return _digest({
        "schema": 1,
        **{key: value.get(key) for key in (
            "id", "article_id", "account_id", "platform", "status", "video",
            "publish_at", "authorization_fingerprint", "started_at", "finished_at",
            "article_title", "account_name", "result_url", "message",
        )},
        "images": _string_list(value.get("images")),
        "demo": bool(value.get("demo")),
        "auto_execute": bool(value.get("auto_execute")),
    })
