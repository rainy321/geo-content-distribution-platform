"""Serial local browser runner. Saves intent before submission and never retries it."""
from __future__ import annotations

import asyncio
import json
import inspect
import math
import os
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from services.doubao_source_collector import CollectorConfig, collect_doubao_sources
from services.geo_test_service import build_report, validate_plan


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def exclusive_lock(path: Path):
    """OS releases the lock after a crash; no stale ownership files to delete."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("另一个测试正在使用本轮任务或浏览器会话，请等其结束") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class RunConfig:
    run_dir: Path
    collector: CollectorConfig
    cooldown_seconds: float = 15

    def __post_init__(self):
        if not math.isfinite(self.cooldown_seconds) or not 10 <= self.cooldown_seconds <= 300:
            raise ValueError("连续问题的间隔必须是 10 到 300 秒")


async def run_test_plan(plan: dict, config: RunConfig, *, resume: bool = False,
                        collect=collect_doubao_sources, sleep=asyncio.sleep, progress=None) -> dict:
    validate_plan(plan)
    run_dir = config.run_dir.resolve()
    with exclusive_lock(run_dir / ".run.lock"), exclusive_lock(config.collector.profile_dir / ".geo-test.lock"):
        state_path = run_dir / "state.json"
        if resume:
            if not state_path.is_file():
                raise ValueError("没有可恢复的任务")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("plan_sha256") != plan["plan_sha256"]:
                raise ValueError("任务与测试计划不一致")
            if (state.get("execution") or {}).get("client_mode", "consumer") != config.collector.client_mode:
                raise ValueError("恢复时必须使用原界面类型；更换界面请开启新的一轮")
            if (state.get("execution") or {}).get("requested_model_label") != config.collector.requested_model_label:
                raise ValueError("恢复时必须使用原模型选项；更换模型请开启新的一轮")
            if set(state.get("cases", {})) != {case["id"] for case in plan["cases"]}:
                raise ValueError("任务问题清单不一致")
            for entry in state["cases"].values():
                if entry["status"] == "running":
                    state.update(status="needs_review", stop_reason="interrupted_submission_unknown")
                    _checkpoint(run_dir, plan, state)
                    return build_report(plan, state)
                result = entry.get("result") or {}
                if entry["status"] in {"failed", "need_action"} and result.get("question_submission_attempted") is False:
                    entry["status"] = "pending"  # Only resume a positively known unsubmitted case.
        else:
            if state_path.exists():
                raise ValueError("此目录已有测试，请用 resume 或指定新的运行目录")
            state = {"run_id": uuid.uuid4().hex, "plan_sha256": plan["plan_sha256"],
                     "created_at": datetime.now(timezone.utc).isoformat(), "status": "running",
                     "execution": {"platform": "doubao_web", "cooldown_seconds": config.cooldown_seconds,
                                   "headed": config.collector.headed, "timeout_seconds": config.collector.timeout_seconds,
                                   "client_mode": config.collector.client_mode,
                                   "requested_model_label": config.collector.requested_model_label},
                     "cases": {case["id"]: {"status": "pending"} for case in plan["cases"]}}
            write_json(run_dir / "plan.json", plan)
        state.update(status="running", stop_reason=None)
        _checkpoint(run_dir, plan, state)
        attempted_this_run = False
        for case in plan["cases"]:
            entry = state["cases"][case["id"]]
            if entry["status"] != "pending":
                continue
            if attempted_this_run:
                await sleep(config.cooldown_seconds)
            entry.update(status="running", started_at=datetime.now(timezone.utc).isoformat())
            _checkpoint(run_dir, plan, state)
            if progress:
                progress({"event": "case_started", "id": case["id"], "category": case["category"]})
            try:
                def collector_progress(event):
                    if progress:
                        progress({**event, 'case_id': case['id']})
                # Keep existing injected config-only collectors compatible.
                # Inspect before calling: never retry a call after TypeError,
                # because it may already have submitted the current question.
                try:
                    parameters = inspect.signature(collect).parameters.values()
                except (ValueError, TypeError):
                    parameters = ()
                supports_progress = any(item.name == 'progress' or item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters)
                callback = {'progress': collector_progress} if progress and supports_progress else {}
                result, path = await collect(replace(config.collector, question=case["question"],
                                                     output_root=run_dir / "evidence" / case["id"]), **callback)
            except Exception as exc:
                # No automatic re-submission after exceptions: browser input may
                # have happened before the collector could write its result.
                entry["error_type"] = type(exc).__name__
                state.update(status="needs_review", stop_reason="collector_exception_submission_unknown")
                _checkpoint(run_dir, plan, state)
                return build_report(plan, state)
            attempted_this_run = True
            entry.update(status=result["collection_status"], result=result, result_path=str(Path(path).resolve()),
                         finished_at=datetime.now(timezone.utc).isoformat())
            if result.get("question") != case["question"]:
                state.update(status="needs_review", stop_reason="collector_question_mismatch")
                _checkpoint(run_dir, plan, state)
                return build_report(plan, state)
            _checkpoint(run_dir, plan, state)
            if progress:
                progress({"event": "case_finished", "id": case["id"], "status": entry["status"],
                          "sources": result.get("visible_source_count"), "result_path": entry["result_path"]})
            if entry["status"] not in {"success", "partial"}:
                state.update(status="stopped", stop_reason=result.get("failure_reason") or entry["status"])
                _checkpoint(run_dir, plan, state)
                return build_report(plan, state)
        state["status"] = "completed" if all(entry["status"] == "success" for entry in state["cases"].values()) else "completed_with_gaps"
        _checkpoint(run_dir, plan, state)
        return build_report(plan, state)


def _checkpoint(run_dir: Path, plan: dict, state: dict):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(run_dir / "state.json", state)
    write_json(run_dir / "report.json", build_report(plan, state))
