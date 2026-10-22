"""Local generic GEO tests: client profile -> AI plan -> consumer-web evidence."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from services.ai_service import AIServiceError
from services.doubao_source_collector import CollectorConfig, collect_doubao_sources
from services.geo_test_runner import RunConfig, exclusive_lock, run_test_plan, write_json
from services.geo_test_service import build_planning_prompt, generate_test_plan, normalize_profile, parse_ai_plan
from services.project_service import get_project


def build_parser():
    parser = argparse.ArgumentParser(description="通用客户 AI 搜索测试：先生成固定计划，再串行采集")
    actions = parser.add_subparsers(dest="action", required=True)
    planner = actions.add_parser("plan", help="AI 按客户资料设计问题并保存可审阅计划")
    profiles = planner.add_mutually_exclusive_group(required=True)
    profiles.add_argument("--profile", type=Path, help="客户资料 JSON")
    profiles.add_argument("--project-id", type=int, help="复用本地品牌项目资料")
    planner.add_argument("--database", type=Path, default=Path("db/database.db"))
    planner.add_argument("--planner", choices=["api", "doubao-web"], default="api")
    planner.add_argument("--case-count", type=int, default=4)
    planner.add_argument("--output", type=Path, required=True)
    runner = actions.add_parser("run", help="启动新一轮测试")
    runner.add_argument("--plan", type=Path, required=True)
    resumer = actions.add_parser("resume", help="仅继续未提交的问题，已提交的问题不重发")
    for action in (planner, runner, resumer):
        action.add_argument("--profile-dir", type=Path, default=Path("runtime/doubao/browser-profile"))
        action.add_argument("--headed", action="store_true")
        action.add_argument("--manual-wait", type=float, default=0)
        action.add_argument("--timeout", type=float, default=180)
        action.add_argument("--executable-path")
        action.add_argument("--client-mode", choices=["consumer", "work"], default="consumer")
        action.add_argument("--model-label", help="用正常菜单选择确切可用模型；不能点击升级或不可用选项")
        if action is not planner:
            action.add_argument("--run-dir", type=Path, required=True)
            action.add_argument("--cooldown", type=float, default=15)
    return parser


async def _plan_with_doubao(profile, args, config, *, progress=None):
    prompt = build_planning_prompt(profile, args.case_count)
    from dataclasses import replace
    with exclusive_lock(config.profile_dir / ".geo-test.lock"):
        result, path = await collect_doubao_sources(replace(
            config, question=prompt, submission_format='verbatim_fenced',
            output_root=args.output.parent / (args.output.stem + "-planner-evidence")),
            **({'progress': progress} if progress is not None else {}))
    _print({"event": "planner_collected", "status": result["collection_status"], "result_path": str(path)})
    if progress:
        progress({'event': 'collector_finished', 'failure_reason': result.get('failure_reason'),
                  'collection_status': result.get('collection_status'), 'evidence_path': str(path),
                  'question_submission_attempted': result.get('question_submission_attempted'),
                  'question_submitted': result.get('question_submitted')})
    if not result.get("answer_completed") or not result.get("question_submitted") or result.get("question") != prompt:
        reason = result.get("failure_reason") or "answer_incomplete"
        messages = {"playwright_not_installed": "浏览器采集组件未加载，本次没有发送问题。请重新打开更新后的软件。",
                    "login_required": "豆包尚未登录，请先点击“登录豆包”完成正常登录。",
                    "client_mode_mismatch": "豆包当前界面与所选类型不符，请确认“豆包对话”或“豆包工作”选项。",
                    "human_verification_required": "豆包需要正常安全验证，当前未取得完整出题结果。",
                    "manual_verification_window_closed": "正常验证窗口已关闭，本轮提交状态需要核对；软件不会自动重发。",
                    "service_capacity_unavailable": "豆包当前算力繁忙，本次未取得有效出题结果。",
                    "background_task_timeout": "豆包仅确认已委托后台调研，等待期间未返回最终结果；本次未取得有效出题结果，不会自动重发。"}
        if reason.startswith("browser_runtime_dependency_missing:"):
            message = "软件浏览器运行库未加载成功，本次没有发送问题。请重新打开更新后的软件。"
        else:
            message = messages.get(reason, f"本次未取得完整测试计划（{reason}），失败记录已保存。")
        raise ValueError(message)
    return parse_ai_plan(result["response"], profile, args.case_count, provenance={
        "method": "doubao_web_ai", "model_label": (result.get("environment") or {}).get("model_label"),
        "model_version": "unknown", "evidence_path": str(path.resolve()),
        "client_mode": (result.get("environment") or {}).get("client_mode"),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "role": "question_planning_not_exposure_test",
    })


def _print(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = CollectorConfig(profile_dir=args.profile_dir, headed=args.headed, timeout_seconds=args.timeout,
                                 manual_wait_seconds=args.manual_wait, executable_path=args.executable_path,
                                 client_mode=args.client_mode, requested_model_label=args.model_label)
        if args.action == "plan":
            if args.output.exists():
                raise ValueError("计划文件已存在，请指定新文件；固定问题用于复测对比")
            profile = (json.loads(args.profile.read_text(encoding="utf-8-sig")) if args.profile
                       else get_project(args.database, args.project_id))
            profile = normalize_profile(profile)
            plan = (generate_test_plan(profile=profile, case_count=args.case_count) if args.planner == "api"
                    else asyncio.run(_plan_with_doubao(profile, args, config)))
            write_json(args.output, plan)
            _print({"status": "planned", "plan_path": str(args.output.resolve()), "plan_sha256": plan["plan_sha256"],
                    "cases": len(plan["cases"]), "planner": plan["planner"]["method"]})
            return 0
        resume = args.action == "resume"
        plan_path = args.run_dir / "plan.json" if resume else args.plan
        plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
        report = asyncio.run(run_test_plan(plan, RunConfig(args.run_dir, config, args.cooldown),
                                          resume=resume, progress=_print))
        _print({"status": report["status"], "stop_reason": report["stop_reason"],
                "report_path": str((args.run_dir / "report.json").resolve()), "cohorts": report["cohorts"]})
        return 0 if report["status"] == "completed" else 2
    except (ValueError, OSError, AIServiceError, LookupError) as exc:
        _print({"status": "failed", "reason": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
