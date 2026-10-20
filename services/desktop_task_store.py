"""Durable native task history and UI drafts. Never stores AI credentials."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from services.geo_test_runner import write_json


def now():
    return datetime.now(timezone.utc).isoformat()


class DesktopTaskStore:
    def __init__(self, data: Path):
        self.data = data
        self.root = data / "desktop-jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.recover_legacy_planner_results()
        self.recover_saved_plans_and_reports()

    def save(self, job):
        write_json(self.root / (job["id"] + ".json"), job)

    def records(self):
        result = []
        for path in self.root.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(record, dict) and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", record.get("id", "")):
                    result.append(record)
            except (OSError, ValueError, TypeError):
                continue
        def timestamp(item):
            try:
                return datetime.fromisoformat(str(item.get("created_at") or "").replace("Z", "+00:00")).timestamp()
            except ValueError:
                return 0
        return sorted(result, key=timestamp, reverse=True)

    def read(self, identifier):
        return next((item for item in self.records() if item["id"] == identifier), None)

    def recover_legacy_planner_results(self):
        for path in (self.data / "geo-tests/desktop-plans").glob("*/plan-planner-evidence/*/result.json"):
            identifier = "recovered-" + hashlib.sha256(str(path.relative_to(self.data)).encode()).hexdigest()[:24]
            if (self.root / (identifier + ".json")).exists():
                continue
            try:
                evidence = json.loads(path.read_text(encoding="utf-8"))
                profile = {}
                try:
                    profile = json.loads(evidence.get("question", "").rsplit("\n", 1)[-1])
                except (TypeError, ValueError):
                    pass
                status = "failed"
                result = None
                plan_path = path.parents[2] / "plan.json"
                if plan_path.is_file():
                    result = {"plan": json.loads(plan_path.read_text(encoding="utf-8"))}
                    status = "completed"
                reason = evidence.get("failure_reason")
                message = ("此前出题未完成：浏览器组件未加载，本次没有发送问题。" if reason == "playwright_not_installed"
                           else "此前出题记录已恢复；可查看保存的计划或失败原因。")
                self.save({"id": identifier, "kind": "plan", "status": status,
                           "created_at": evidence.get("timestamp"), "updated_at": evidence.get("finished_at"),
                           "client_name": profile.get("name", "未知客户") if isinstance(profile, dict) else "未知客户",
                           "profile": profile if isinstance(profile, dict) else {},
                           "message": message, "failure_reason": reason,
                           "evidence_path": str(path), "result": result,
                           "recovered_from": "existing_planner_evidence"})
            except (OSError, ValueError, AttributeError):
                continue

    def recover_saved_plans_and_reports(self):
        from services.geo_test_service import validate_plan
        records = self.records()
        known_plans = {(item.get('result') or {}).get('plan', {}).get('plan_sha256') for item in records}
        known_runs = {(item.get('result') or {}).get('report', {}).get('run_id') for item in records}
        for path in (self.data / 'geo-tests').rglob('plan.json'):
            try:
                plan = validate_plan(json.loads(path.read_text(encoding='utf-8')))
                if plan['plan_sha256'] in known_plans:
                    continue
                self.save({'id': 'saved-plan-' + plan['plan_sha256'][:24], 'kind': 'plan', 'status': 'completed',
                           'created_at': plan.get('created_at'), 'client_name': plan['profile']['name'],
                           'message': '原有固定问题计划已恢复，未重新发送请求。', 'result': {'plan': plan},
                           'recovered_from': 'saved_plan_file', 'evidence_path': str(path)})
                known_plans.add(plan['plan_sha256'])
            except (OSError, ValueError, KeyError, TypeError):
                continue
        for path in (self.data / 'geo-tests').rglob('report.json'):
            try:
                report = json.loads(path.read_text(encoding='utf-8'))
                if report.get('kind') != 'geo_search_test_report' or report.get('run_id') in known_runs:
                    continue
                identifier = hashlib.sha256(str(report['run_id']).encode()).hexdigest()[:24]
                collected = next((case.get('collected_at') for case in report['cases'] if case.get('collected_at')), None)
                self.save({'id': 'saved-run-' + identifier, 'kind': 'run', 'status': report['status'],
                           'created_at': collected or report.get('updated_at'), 'client_name': report['profile']['name'],
                           'message': '原有采集报告已恢复，原始文件保留。', 'result': {'report': report},
                           'recovered_from': 'saved_report_file', 'evidence_path': str(path)})
                known_runs.add(report['run_id'])
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue

    def save_workspace(self, state):
        allowed = {"project_id", "company", "audience", "aliases", "official_urls", "case_count", "client_mode", "model_label",
                   "selected_report_id", "plan", "report"}
        if not isinstance(state, dict):
            raise ValueError("工作区状态格式无效")
        state = {key: value for key, value in state.items() if key in allowed}
        if len(json.dumps(state, ensure_ascii=False).encode("utf-8")) > 5 * 1024 * 1024:
            raise ValueError("工作区状态过大")
        write_json(self.data / "workspace-state.json", state)

    def workspace(self):
        try:
            value = json.loads((self.data / "workspace-state.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}
