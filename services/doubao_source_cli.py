"""Run with ``python -m services.doubao_source_cli collect`` or ``geo-doubao``."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import fields
from pathlib import Path

from services.doubao_source_collector import (
    DEFAULT_QUESTION, CollectorConfig, DoubaoSelectors,
    collect_doubao_sources, open_doubao_session,
)
from services.geo_test_runner import exclusive_lock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="豆包消费者网页版来源采集（单次提问与证据保存）")
    parser.add_argument("action", choices=["collect", "login"])
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--output-dir", type=Path, default=Path("runtime/doubao/evidence"))
    parser.add_argument("--profile-dir", type=Path, default=Path("runtime/doubao/browser-profile"))
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--login-wait", type=float, default=300)
    parser.add_argument("--manual-wait", type=float, default=0, help="采集过程中保留窗口等待人工验证的总秒数（需要 --headed，最多 600）")
    parser.add_argument("--executable-path")
    parser.add_argument("--client-mode", choices=["consumer", "work"], default="consumer")
    parser.add_argument("--model-label")
    parser.add_argument("--selectors", type=Path, help="已核验的 CSS 选择器 JSON；允许只覆盖部分字段")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        overrides = json.loads(args.selectors.read_text(encoding="utf-8")) if args.selectors else {}
        names = {field.name for field in fields(DoubaoSelectors)}
        if not isinstance(overrides, dict) or set(overrides) - names:
            raise ValueError("选择器配置必须为对象，且只能包含 DoubaoSelectors 支持的字段")
        if any(not isinstance(value, str) or not value.strip() for value in overrides.values()):
            raise ValueError("选择器必须为非空 CSS 字符串")
        if not 0 < args.login_wait <= 600:
            raise ValueError("登录等待时间必须在 0 到 600 秒之间")
        config = CollectorConfig(
            question=args.question, output_root=args.output_dir, profile_dir=args.profile_dir,
            headed=args.headed, timeout_seconds=args.timeout, executable_path=args.executable_path,
            manual_wait_seconds=args.manual_wait,
            client_mode=args.client_mode,
            requested_model_label=args.model_label,
            selectors=DoubaoSelectors(**overrides),
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.action == "login":
        print("请在专用浏览器完成正常登录/验证；关闭页面或到时会结束等待，登录与会话保存状态仍需核验。")
        try:
            with exclusive_lock(config.profile_dir / ".geo-test.lock"):
                asyncio.run(open_doubao_session(config, args.login_wait))
            return 0
        except Exception as exc:
            print(json.dumps({"status": "failed", "reason": f"session_error:{type(exc).__name__}"}, ensure_ascii=False))
            return 1
    try:
        with exclusive_lock(config.profile_dir / ".geo-test.lock"):
            result, path = asyncio.run(collect_doubao_sources(config))
    except ValueError as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": result["collection_status"], "reason": result["failure_reason"],
                      "answer_completed": result["answer_completed"], "visible_citation_count": result["visible_citation_count"],
                      "visible_source_count": result["visible_source_count"],
                      "result_path": str(path)}, ensure_ascii=False, indent=2))
    return {"success": 0, "need_action": 2, "partial": 3}.get(result["collection_status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
