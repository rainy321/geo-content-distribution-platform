"""Native-window bridge to the existing serial collector; one task at a time."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

from services.doubao_source_collector import CollectorConfig, open_doubao_session
from services.geo_test_cli import _plan_with_doubao
from services.geo_test_runner import RunConfig, exclusive_lock, run_test_plan, write_json
from services.geo_test_service import build_report, normalize_profile, validate_plan
from services.desktop_task_store import DesktopTaskStore, now


def edge_executable() -> str | None:
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.getenv(variable)
        candidate = Path(root) / "Microsoft/Edge/Application/msedge.exe" if root else None
        if candidate and candidate.is_file():
            return str(candidate)
    return None


class DesktopGeoJobs:
    def __init__(self, data: Path):
        self.data = data
        self.lock = threading.Lock()
        self.store = DesktopTaskStore(data)
        records = self.store.records()
        self.job = records[0] if records else {"status": "idle"}
        self.worker = self.loop = self.task = None
        self.cancel_requested = False
        for record in records:
            if record.get("status") == "running":
                record.update(status="needs_review", updated_at=now(),
                              message="软件上次关闭时任务未完成，记录已保留；提交状态需检查，不会自动重发。")
                if record.get("kind") == "run":
                    report = self._save_interrupted_run(record, reason="application_closed_submission_unknown")
                    if report:
                        record["result"] = {"report": report}
                self.store.save(record)
        if records:
            self.job = self.store.read(records[0]["id"])
            self.job["restored_from_disk"] = True

    def get_task(self, identifier=None):
        with self.lock:
            return copy.deepcopy(self.store.read(identifier) if identifier else self.job)

    def list_tasks(self):
        with self.lock:
            return [{key: value for key, value in item.items() if key != "result"}
                    for item in self.store.records()]

    def save_workspace_state(self, state):
        with self.lock:
            self.store.save_workspace(state)
        return {"saved": True}

    def get_workspace_state(self):
        with self.lock:
            saved = self.store.workspace()
            if saved:
                return saved
            profile = self.job.get('profile') or {}
            return {'client_name': profile.get('name'), 'company': profile.get('company', ''),
                    'audience': profile.get('audience', '')} if profile else {}

    def _persist(self):
        self.job["updated_at"] = now()
        self.store.save(self.job)

    def _config(self, options):
        if not isinstance(options, dict):
            raise ValueError("采集选项格式无效")
        return CollectorConfig(profile_dir=self.data / "doubao/browser-profile", headed=True,
                               manual_wait_seconds=600, client_mode=options.get("client_mode", "consumer"),
                               requested_model_label=options.get("model_label") or None,
                               executable_path=edge_executable())

    def _start(self, kind, operation, *, client_name=""):
        with self.lock:
            if self.job["status"] == "running":
                return {"error": "已有任务正在执行，请先等待或停止"}
            self.cancel_requested = False
            self.job = {"id": uuid.uuid4().hex, "kind": kind, "status": "running",
                        "created_at": now(), "client_name": client_name}
            self._persist()

        def run():
            async def execute():
                self.loop = asyncio.get_running_loop()
                self.task = asyncio.current_task()
                if self.cancel_requested:
                    raise asyncio.CancelledError()
                return await operation()
            try:
                result = asyncio.run(execute())
                with self.lock:
                    self.job.update(status="completed", result=result)
                    self._persist()
            except asyncio.CancelledError:
                try:
                    report = self._save_interrupted_run()
                    if report:
                        self.job["result"] = {"report": report}
                except Exception:
                    logging.exception("Could not save interrupted native run; original running state remains")
                with self.lock:
                    self.job.update(status="stopped", message="已停止；提交状态不明确的问题保留待检查，不会自动重发")
                    self._persist()
            except Exception as error:
                logging.exception("Native GEO task failed")
                with self.lock:
                    message = str(error) if isinstance(error, ValueError) else f"任务失败（{type(error).__name__}），请检查软件日志"
                    if isinstance(error, ImportError):
                        message = f"软件采集组件加载失败（{getattr(error, 'name', None) or '运行库'}），本次未发送问题。请重新打开更新后的软件。"
                    self.job.update(status="failed", message=message)
                    self._persist()
            finally:
                self.task = self.loop = None
        self.worker = threading.Thread(target=run, daemon=True, name="GEO native collector")
        self.worker.start()
        return {"id": self.job["id"], "status": "running"}

    def open_login(self, options):
        try:
            config = self._config(options)
        except (ValueError, TypeError) as error:
            return {"error": str(error)}
        async def login():
            with exclusive_lock(config.profile_dir / ".geo-test.lock"):
                await open_doubao_session(config, 600)
            return {"message": "登录窗口已关闭，会话已保存"}
        return self._start("login", login)

    def plan_with_doubao(self, profile, case_count, options):
        try:
            profile = normalize_profile(profile)
            config = self._config(options)
        except (ValueError, TypeError) as error:
            return {"error": str(error)}
        output = self.data / "geo-tests/desktop-plans" / uuid.uuid4().hex / "plan.json"
        async def plan():
            args = SimpleNamespace(case_count=case_count, output=output)
            value = await _plan_with_doubao(profile, args, config)
            write_json(output, value)
            return {"plan": value}
        return self._start("plan", plan, client_name=profile["name"])

    def start_run(self, plan, options):
        try:
            plan = copy.deepcopy(validate_plan(plan))
            config = self._config(options)
        except (ValueError, TypeError, KeyError) as error:
            return {"error": str(error)}
        run_dir = self.data / "geo-tests/desktop-runs" / uuid.uuid4().hex
        async def run():
            with self.lock:
                self.job["run_dir"] = str(run_dir)
                self._persist()
            def progress(event):
                with self.lock:
                    self.job["progress"] = event
                    self._persist()
            report = await run_test_plan(plan, RunConfig(run_dir, config), progress=progress)
            return {"report": report}
        return self._start("run", run, client_name=plan["profile"]["name"])

    def stop_task(self):
        self.cancel_requested = True
        loop, task = self.loop, self.task
        if loop and task and not loop.is_closed():
            loop.call_soon_threadsafe(task.cancel)
        return {"status": "stopping"}

    def _save_interrupted_run(self, job=None, *, reason="user_cancelled_submission_unknown"):
        path = (job or self.job).get("run_dir")
        if not path:
            return
        root = Path(path)
        if not (root / "state.json").is_file():
            return
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
        state.update(status="needs_review", stop_reason=reason)
        write_json(root / "state.json", state)
        report = build_report(plan, state)
        write_json(root / "report.json", report)
        return report

    def shutdown(self):
        if self.get_task()["status"] == "running":
            self.stop_task()
        if self.worker:
            self.worker.join(timeout=15)
