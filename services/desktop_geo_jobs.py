"""Native-window bridge to the existing serial collector; one task at a time."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace

from services.doubao_source_collector import CollectorConfig, open_doubao_session
from services.geo_test_cli import _plan_with_doubao
from services.geo_test_runner import RunConfig, exclusive_lock, run_test_plan, write_json
from services.geo_test_service import build_report, normalize_profile, validate_plan
from services.desktop_task_store import DesktopTaskStore, now
from services import geo_content_store, geo_content_research, geo_destination_service
from services.doubao_source_collector import collect_doubao_sources, MANUAL_AUTH_REASONS
from services.doubao_task_recovery import recovery_view, safe_reason, action_required, recovery_message
from services.geo_retest_resume import resume_source, source_manifest, copy_resume_source, file_digest


def edge_executable() -> str | None:
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.getenv(variable)
        candidate = Path(root) / "Microsoft/Edge/Application/msedge.exe" if root else None
        if candidate and candidate.is_file():
            return str(candidate)
    return None


def chrome_executable() -> str | None:
    for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        root = os.getenv(variable)
        candidate = Path(root) / 'Google/Chrome/Application/chrome.exe' if root else None
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
        # Private controls stay off pywebview's recursive public API surface.
        self._destination_check = self._destination_check_loop = None
        self._destination_check_task_id = None
        self._destination_check_pending = False
        self._source_check = self._source_check_loop = self._source_check_task_id = None
        for record in records:
            if record.get("status") == "running":
                record.update(status="needs_review", updated_at=now(),
                              message="软件上次关闭时任务未完成，记录已保留；提交状态需检查，不会自动重发。")
                session = (record.get('progress') or {}).get('destination_session')
                if isinstance(session, dict):
                    session.update(can_check=False, window_open=False)
                if record.get("kind") == "run":
                    report = self._save_interrupted_run(record, reason="application_closed_submission_unknown")
                    if report:
                        record["result"] = {"report": report}
                if record.get('kind') == 'source_maintenance' and record.get('source_task_id'):
                    from services import source_maintenance_service as maintenance
                    try:
                        source = maintenance.get_task(data / 'database.db', record.get('cycle_id'), record['source_task_id'])
                        maintenance.record_state(data / 'database.db', record['cycle_id'], source['id'],
                            stage='submission_unknown' if source['read_only'] else 'window_closed',
                            message='软件上次关闭时来源维护未结束，原申请保留；只能检查，不会自动提交。', window_open=False)
                    except (ValueError, sqlite3.Error, geo_content_store.ResearchNotFoundError):
                        logging.warning('Interrupted source maintenance requires manual record review')
                if record.get('target_id') and record.get('kind') == 'destination_fill':
                    geo_destination_service.record_event(data / 'database.db', record['target_id'], 'interrupted', 'need_action',
                        '上次填写时软件关闭，编辑器是否自动保存或提交未知；请检查页面，不会自动重试')
                self.store.save(record)
        if records:
            self.job = self.store.read(records[0]["id"])
            self.job["restored_from_disk"] = True

    def get_task(self, identifier=None):
        with self.lock:
            value = copy.deepcopy(self.store.read(identifier) if identifier else self.job)
            live_window = bool(value and value.get('id') == self.job.get('id') and value.get('status') == 'running'
                               and value.get('waiting_for_user') is True and self.worker and self.worker.is_alive()
                               and self.task is not None and not self.task.done())
        if isinstance(value, dict) and isinstance(value.get('result'), dict):
            self._project_report_view(value['result'])
        if isinstance(value, dict):
            recovery = recovery_view(value, self.data, live_window=live_window)
            if recovery:
                value['recovery'] = recovery
            if value.get('kind') == 'run' and value.get('cycle_id') and value.get('status') != 'running':
                try:
                    details = self._resume_research_source(value['id'], value['cycle_id'])
                    value['resume_research_retest'] = {'can_resume': True, 'pending_case_ids': details['case_ids'],
                        'message': '继续未发送题，已发送题不会重发'}
                except (ValueError, OSError, KeyError, TypeError, geo_content_store.ResearchNotFoundError):
                    value['resume_research_retest'] = {'can_resume': False, 'pending_case_ids': [],
                        'message': '当前任务不满足安全续采条件，请查看原记录'}
        return value

    @staticmethod
    def _project_report_view(container):
        report = container.get('report')
        if isinstance(report, dict) and report.get('kind') == 'geo_search_test_report':
            from services.geo_test_reports import project_report_evaluation
            container['report'] = project_report_evaluation(report)

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
                self._project_report_view(saved)
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
        # Keep public chat separate from a work account's enterprise connectors.
        # The existing profile remains the work profile; no sessions are copied.
        client_mode = options.get("client_mode", "consumer")
        profile = "consumer-browser-profile" if client_mode == "consumer" else "browser-profile"
        return CollectorConfig(profile_dir=self.data / "doubao" / profile, headed=True,
                               manual_wait_seconds=None, client_mode=client_mode,
                               requested_model_label=options.get("model_label") or None,
                               executable_path=edge_executable(), fallback_executable_path=chrome_executable())

    def _collector_progress(self, event):
        """Persist only known phase fields; raw DOM, URLs and prompts stay in evidence."""
        names = {'manual_action_required', 'manual_action_cleared', 'manual_action_ended',
                 'question_submission_started', 'question_sent', 'case_started', 'case_finished', 'background_task_waiting', 'background_task_cleared',
                 'answer_completed', 'source_progress', 'collector_finished'}
        if not isinstance(event, dict) or event.get('event') not in names:
            return
        name = event['event']
        reason = event.get('reason') if event.get('reason') in MANUAL_AUTH_REASONS else None
        if name == 'collector_finished':
            with self.lock:
                if self.job.get('status') != 'running':
                    return
                for field in ('question_submission_attempted', 'question_submitted'):
                    if type(event.get(field)) is bool:
                        self.job.setdefault('submission_state', {})[field] = event[field]
                failure = safe_reason(event.get('failure_reason'))
                if failure:
                    self.job['failure_reason'] = failure
                stage = event.get('failure_stage')
                if isinstance(stage, str) and 0 < len(stage) <= 80 and all(c.islower() or c == '_' for c in stage):
                    self.job['failure_stage'] = stage
                path = event.get('evidence_path')
                if isinstance(path, str):
                    try:
                        Path(path).resolve().relative_to(self.data.resolve())
                        self.job['evidence_path'] = path
                    except ValueError:
                        pass
                self.job['collector_need_action'] = action_required(failure, event.get('collection_status'))
                self.job['collector_finished'] = True
                self._persist()
            return
        safe = {'event': name}
        if name == 'source_progress':
            phase, processed, total = event.get('phase'), event.get('processed'), event.get('total')
            if (phase not in {'source_panels', 'inline_citations'} or type(processed) is not int or type(total) is not int
                    or not 0 <= processed <= total <= 30):
                return
            safe.update(phase=phase, processed=processed, total=total)
        case_id = event.get('case_id') or event.get('id')
        if isinstance(case_id, str) and 0 < len(case_id) <= 100 and all(char.isalnum() or char in '_-' for char in case_id):
            safe['case_id'] = case_id
        else:
            case_id = None
        for field in ('question_submission_attempted', 'question_submitted'):
            if type(event.get(field)) is bool:
                safe[field] = event[field]
        if reason:
            safe['reason'] = reason
        messages = {
            'manual_action_required': '等待你在当前豆包窗口完成正常验证或登录；保持窗口打开，本轮会原地继续，请勿重新发送。',
            'manual_action_cleared': '正常验证或登录已解除，继续当前请求；不会重新发送问题。',
            'manual_action_ended': '当前验证等待已结束，请核对窗口与提交状态；不会自动重发。',
            'question_sent': '已执行本次发送操作，正在核对豆包当前请求的回执；请勿重复发送。',
            'question_submission_started': '已保留本次发送尝试，正在提交当前问题；请勿重复开始。',
            'background_task_waiting': '豆包已委托后台调研，尚未返回最终回答；保留当前请求等待，不作为曝光结果，无需重新发送。',
            'background_task_cleared': '豆包后台调研已返回当前请求的最终回答，继续保存证据。',
            'answer_completed': '回答已完成，正在检查可见引用和网址；每条来源需要单独核对，请保持窗口打开。',
            'case_started': f'正在采集 {case_id or "当前问题"}，每题只发送一次。',
            'case_finished': f'{case_id or "当前问题"} 的结果已保存，继续本轮采集。',
        }
        if name == 'source_progress':
            if phase == 'source_panels':
                messages[name] = (f'{case_id or "当前问题"} 回答已完成，正在检查来源面板：已检查 {processed}/{total}（本批最多 30 个）。'
                                  if total else f'{case_id or "当前问题"} 回答已完成，继续核对当前回答的可见引用。')
            else:
                messages[name] = (f'{case_id or "当前问题"} 正在逐条核对可见引用：已检查 {processed}/{total}（本批最多 30 条），请保持窗口打开。'
                                  if total else f'{case_id or "当前问题"} 暂未发现需逐条核对的可见引用，正在保存现有来源证据。')
        with self.lock:
            if self.job.get('status') != 'running':
                return
            if name == 'case_started':
                for field in ('submission_state', 'collector_finished', 'collector_need_action',
                              'failure_reason', 'failure_stage', 'evidence_path'):
                    self.job.pop(field, None)
            for field in ('question_submission_attempted', 'question_submitted'):
                if field in safe:
                    self.job.setdefault('submission_state', {})[field] = safe[field]
            if name == 'question_submission_started' and isinstance(event.get('evidence_path'), str):
                try:
                    Path(event['evidence_path']).resolve().relative_to(self.data.resolve())
                    self.job['evidence_path'] = event['evidence_path']
                except ValueError:
                    pass
            self.job['waiting_for_user'] = name == 'manual_action_required'
            if name == 'manual_action_required' and reason:
                self.job['wait_reason'] = reason
            else:
                self.job.pop('wait_reason', None)
            self.job.update(progress=safe, message=messages[name])
            self._persist()

    def _start(self, kind, operation, *, client_name="", cycle_id=None, target_id=None, collector_config=None):
        with self.lock:
            if self.job["status"] == "running":
                return {"error": "已有任务正在执行，请先等待或停止"}
            self.cancel_requested = False
            self.job = {"id": uuid.uuid4().hex, "kind": kind, "status": "running",
                        "created_at": now(), "client_name": client_name}
            if collector_config is not None:
                self.job.update(requested_client_mode=collector_config.client_mode,
                                requested_model_label=collector_config.requested_model_label)
            if cycle_id:
                self.job['cycle_id'] = cycle_id
            if target_id:
                self.job['target_id'] = target_id
            self._destination_check = self._destination_check_loop = None
            self._destination_check_task_id = None
            self._destination_check_pending = False
            self._persist()
            task_identifier = self.job['id']

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
                    self.job.update(status="completed", result=result, waiting_for_user=False)
                    self.job.pop('wait_reason', None)
                    report = result.get('report') if kind == 'run' and isinstance(result, dict) else None
                    if isinstance(report, dict) and report.get('status') in ('stopped', 'needs_review'):
                        self._apply_incomplete_run(report)
                    elif isinstance(result, dict) and result.get('message'):
                        self.job['message'] = result['message']
                    elif self.job.get('progress'):
                        self.job['message'] = '本轮任务已结束，结果和未完成原因已保存，请查看对应记录。'
                    self._persist()
            except asyncio.CancelledError:
                try:
                    report = self._save_interrupted_run()
                    if report:
                        self.job["result"] = {"report": report}
                except Exception:
                    logging.exception("Could not save interrupted native run; original running state remains")
                with self.lock:
                    self.job.update(status="stopped", waiting_for_user=False, message="已停止；提交状态不明确的问题保留待检查，不会自动重发")
                    self.job.pop('wait_reason', None)
                    self._persist()
            except Exception as error:
                if self.job.get('collector_need_action') is True:
                    logging.warning('Native GEO task needs user action: %s', self.job.get('failure_reason') or 'unknown')
                else:
                    logging.exception("Native GEO task failed")
                with self.lock:
                    message = str(error) if isinstance(error, ValueError) else f"任务失败（{type(error).__name__}），请检查软件日志"
                    if isinstance(error, ImportError):
                        message = f"软件采集组件加载失败（{getattr(error, 'name', None) or '运行库'}），本次未发送问题。请重新打开更新后的软件。"
                    needs_action = self.job.get('collector_need_action') is True
                    if needs_action:
                        state = self.job.get('submission_state') or {}
                        message = recovery_message(self.job.get('failure_reason'),
                            attempted=state.get('question_submission_attempted'), submitted=state.get('question_submitted'),
                            failure_stage=self.job.get('failure_stage'))
                    self.job.update(status="need_action" if needs_action else "failed", waiting_for_user=False, message=message)
                    self.job.pop('wait_reason', None)
                    self._persist()
            finally:
                with self.lock:
                    if self._destination_check_task_id == task_identifier:
                        self._destination_check = self._destination_check_loop = None
                        self._destination_check_task_id = None
                        self._destination_check_pending = False
                    if self.job.get('id') == task_identifier:
                        session = (self.job.get('progress') or {}).get('destination_session')
                        if isinstance(session, dict):
                            session.update(can_check=False, window_open=False)
                            self._persist()
                        self.task = self.loop = None
        self.worker = threading.Thread(target=run, daemon=True, name="GEO native collector")
        self.worker.start()
        return {"id": self.job["id"], "status": "running"}

    def _apply_incomplete_run(self, report):
        """A completed worker is not a successfully completed collection run."""
        rows = report.get('cases') if isinstance(report.get('cases'), list) else []
        reason = safe_reason(report.get('stop_reason'))
        # A resumed report can contain an earlier failed question. Bind the
        # task's submission state to the failure which stopped this attempt,
        # never to an unrelated earlier row.
        row = next((item for item in reversed(rows) if isinstance(item, dict)
                    and reason and item.get('failure_reason') == reason
                    and item.get('collection_status') not in (None, 'success', 'partial')), {})
        state = {field: row[field] for field in ('question_submission_attempted', 'question_submitted')
                 if type(row.get(field)) is bool}
        if state:
            self.job.update(submission_state=state, collector_finished=True)
        if reason:
            self.job['failure_reason'] = reason
        stage = row.get('failure_stage')
        if isinstance(stage, str):
            self.job['failure_stage'] = stage
        evidence = row.get('result_path')
        if isinstance(evidence, str):
            try:
                Path(evidence).resolve().relative_to(self.data.resolve())
                self.job['evidence_path'] = evidence
            except (OSError, ValueError):
                pass
        needs_action = action_required(reason, row.get('collection_status'))
        self.job['status'] = ('needs_review' if report.get('status') == 'needs_review'
                              else 'need_action' if needs_action else 'failed')
        self.job['message'] = recovery_message(reason, attempted=state.get('question_submission_attempted'),
            submitted=state.get('question_submitted'), failure_stage=stage)

    def open_login(self, options):
        try:
            config = self._config(options)
        except (ValueError, TypeError) as error:
            return {"error": str(error)}
        async def login():
            with exclusive_lock(config.profile_dir / ".geo-test.lock"):
                result = await open_doubao_session(config, None)
            result = result if isinstance(result, dict) else {}
            mode = result.get('client_mode') if result.get('client_mode') in ('consumer', 'work') else None
            observation = {'client_mode': mode,
                           'login_required': result.get('login_required') if type(result.get('login_required')) is bool else None,
                           'challenge': result.get('challenge') if type(result.get('challenge')) is bool else None}
            labels = {'consumer': '豆包对话', 'work': '豆包工作'}
            reason = result.get('close_reason') if result.get('close_reason') in (
                'window_closed', 'wait_elapsed', 'page_crashed', 'browser_disconnected') else None
            message = ('豆包页面异常退出。' if reason == 'page_crashed' else
                       '豆包浏览器连接已结束。' if reason == 'browser_disconnected' else '登录窗口已结束。')
            if mode:
                message += f'所选{labels[config.client_mode]}，最后观察到{labels[mode]}。'
                if mode != config.client_mode:
                    message += '界面不匹配，请确认采集界面选项；软件不会自动切换。'
            else:
                message += '实际界面未知。'
            message += ('最后仍显示登录提示。' if observation['login_required'] is True else
                        '最后未见登录提示，登录状态尚未核验。' if observation['login_required'] is False else '登录状态未知。')
            if observation['challenge'] is True:
                message += '最后仍显示安全验证。'
            elif observation['challenge'] is None:
                message += '验证状态未知。'
            message += '会话保存状态未确认；再次采集前会检查界面、登录与验证状态。'
            return {'message': message, 'login_observation': observation,
                    'requested_client_mode': config.client_mode, 'close_reason': reason,
                    'session_saved': None}
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
            value = await _plan_with_doubao(profile, args, config, progress=self._collector_progress)
            write_json(output, value)
            return {"plan": value}
        return self._start("plan", plan, client_name=profile["name"], collector_config=config)

    def start_run(self, plan, options, cycle_id=None):
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
            report = await run_test_plan(plan, RunConfig(run_dir, config), progress=self._collector_progress)
            if cycle_id:
                geo_content_store.attach_retest(self.data / 'database.db', cycle_id, report)
            return {"report": report}
        return self._start("run", run, client_name=plan["profile"]["name"], cycle_id=cycle_id, collector_config=config)

    def start_research_retest(self, cycle_id):
        try:
            cycle = geo_content_store.get_cycle(self.data / 'database.db', cycle_id)
            execution = cycle['baseline'].get('execution') or {}
            options = {'client_mode': execution.get('client_mode') or next((case.get('client_mode') for case in cycle['baseline']['cases'] if case.get('client_mode')), 'consumer'),
                       'model_label': execution.get('requested_model_label') or ''}
            return self.start_run(cycle['test_plan'], options, cycle_id=cycle_id)
        except (ValueError, geo_content_store.ResearchNotFoundError) as error:
            return {'error': str(error)}

    def start_research_probe(self, cycle_id, case_id):
        """Collect one unchanged cycle question once; never create a full report."""
        from services.geo_research_probe_service import context, attach_probe
        database = self.data / 'database.db'
        try:
            if not isinstance(cycle_id, str) or not cycle_id:
                raise ValueError('请选择本轮实验')
            cycle = geo_content_store.get_cycle(database, cycle_id)
            details = context(cycle, case_id)
            execution = details['execution']
            config = replace(self._config({'client_mode': execution['client_mode'],
                'model_label': execution['requested_model_label']}),
                question=details['case']['question'], timeout_seconds=execution['timeout_seconds'])
        except (ValueError, TypeError, KeyError, sqlite3.Error, geo_content_store.ResearchNotFoundError) as error:
            return {'error': str(error)}
        probe_id = uuid.uuid4().hex
        directory = self.data / 'geo-content/probes' / probe_id
        config = replace(config, output_root=directory / 'evidence')

        async def probe():
            import hashlib
            current = geo_content_store.get_cycle(database, cycle_id)
            fresh = context(current, case_id)
            if (current['test_plan']['plan_sha256'] != cycle['test_plan']['plan_sha256']
                    or current['profile'] != cycle['profile'] or fresh != details):
                raise ValueError('原实验问题或资料已变化，未发送，请重新核对')
            with self.lock:
                task_id = self.job['id']
                self.job.update(probe_id=probe_id, case_id=case_id, run_dir=str(directory),
                    message='独立复测这道原问题；只发送一次，其他问题不会重发。')
                self._persist()
            result = None
            evidence = None
            cancelled = False
            with exclusive_lock(config.profile_dir / '.geo-test.lock'):
                write_json(directory / 'single_probe_intent.json', {'probe_id': probe_id,
                    'cycle_id': cycle_id, 'case_id': case_id, 'question': config.question,
                    'plan_sha256': cycle['test_plan']['plan_sha256'], 'created_at': now(),
                    'collector_invocation_limit': 1, 'automatic_retry': False})
                try:
                    # Do not retry any call, including exceptions after sending.
                    result, path = await collect_doubao_sources(config, progress=self._collector_progress)
                    path = Path(path).resolve()
                    path.relative_to(directory.resolve())
                    if path.is_symlink() or path.stat().st_size > 5 * 1024 * 1024:
                        raise ValueError('单题采集结果文件无法核对')
                    raw = path.read_bytes()
                    if json.loads(raw.decode('utf-8')) != result or result.get('question') != config.question:
                        raise ValueError('单题采集问题或持久结果不一致')
                    evidence = {'result_path': str(path), 'result_sha256': hashlib.sha256(raw).hexdigest()}
                except asyncio.CancelledError:
                    cancelled = True
                    result = None
                except Exception:
                    logging.exception('Single-question collector failed; no second call will be made')
                    result = None
                if result is None:
                    result = {'question': config.question, 'response': '', 'collection_status': 'failed',
                        'failure_reason': 'collector_exception_submission_unknown',
                        'timestamp': now(), 'environment': {'client_mode': execution['client_mode']}}
                self._collector_progress({'event': 'collector_finished',
                    'failure_reason': result.get('failure_reason'), 'failure_stage': result.get('failure_stage'),
                    'collection_status': result.get('collection_status'),
                    'evidence_path': (evidence or {}).get('result_path'),
                    'question_submission_attempted': result.get('question_submission_attempted'),
                    'question_submitted': result.get('question_submitted')})
                record = attach_probe(database, cycle_id,
                    {'case_id': case_id, 'run_id': probe_id, 'collector_result': result, 'created_at': now()},
                    source='native_collector', native_task_id=task_id, evidence=evidence)
                write_json(directory / 'single_case_probe.json', record)
            outcome = {'probe': record, 'cycle_id': cycle_id,
                'message': ('单题回答及对照已保存；官网地址出现不代表引用、业务正确或优化因果。'
                            if record['after']['answer_evaluable'] else
                            '本次没有可评估的完整回答，地址出现情况未知；证据已保存，未自动重发。')}
            if cancelled:
                with self.lock:
                    self.job['result'] = outcome
                    self._persist()
                raise asyncio.CancelledError()
            return outcome
        return self._start('probe', probe, client_name=cycle['profile']['name'], cycle_id=cycle_id,
                           collector_config=config)

    def _resume_research_source(self, source_task_id, cycle_id):
        if not isinstance(source_task_id, str) or not isinstance(cycle_id, str) or not source_task_id or not cycle_id:
            raise ValueError('请选择本轮原采集任务')
        with self.lock:
            if self.job.get('status') == 'running':
                raise ValueError('已有任务正在执行，请先等待或停止')
            source = self.store.read(source_task_id)
            if any(item.get('resumed_from_task_id') == source_task_id for item in self.store.records()):
                raise ValueError('该原任务已有续采记录，请从最近的续采任务继续')
        database = self.data / 'database.db'
        if not database.is_file():
            raise ValueError('本机实验记录尚未就绪，请先打开对应实验')
        try:
            cycle = geo_content_store.get_cycle(database, cycle_id)
        except sqlite3.Error as error:
            raise ValueError('本机实验记录无法读取，请先核对原记录') from error
        details = resume_source(self.data, source, cycle)
        details['task'] = source
        return details

    def resume_research_retest(self, source_task_id, cycle_id):
        try:
            details = self._resume_research_source(source_task_id, cycle_id)
            execution = details['execution']
            config = self._config({'client_mode': execution['client_mode'],
                                   'model_label': execution.get('requested_model_label')})
            config = replace(config, timeout_seconds=execution.get('timeout_seconds', config.timeout_seconds))
            cooldown = execution.get('cooldown_seconds', 15)
            source = details['run_dir']
            manifest = source_manifest(source)
            task_path = self.data / 'desktop-jobs' / (source_task_id + '.json')
            task_hash = file_digest(task_path)
            destination = self.data / 'geo-tests/desktop-runs' / uuid.uuid4().hex
            RunConfig(destination, config, cooldown)
        except (ValueError, OSError, KeyError, TypeError, geo_content_store.ResearchNotFoundError) as error:
            return {'error': str(error)}

        async def resume():
            with self.lock:
                self.job.update(run_dir=str(destination), resumed_from_task_id=source_task_id,
                    resumed_from_run_dir=str(source), resumed_from_manifest_sha256=manifest['sha256'],
                    resumed_from_task_sha256=task_hash, resumed_case_ids=details['case_ids'],
                    logical_run_id=details['state']['run_id'], message='继续未发送题，已发送题不会重发；这是原实验的续采。')
                self._persist()
            if file_digest(task_path) != task_hash:
                raise ValueError('原任务记录已变化，已停止；不会发送问题')
            copy_resume_source(source, destination, manifest)
            write_json(destination / 'resume-source-manifest.json', {'source_task_id': source_task_id,
                'source_run_dir': str(source), 'source_task_sha256': task_hash, **manifest})
            # The original plan and logical run stay fixed. build_report in
            # the existing runner recomputes every assessment and cohort from
            # saved case results; stale cached cohort rates are never reused.
            report = await run_test_plan(details['plan'], RunConfig(destination, config, cooldown),
                                         resume=True, progress=self._collector_progress)
            geo_content_store.attach_retest(self.data / 'database.db', cycle_id, report)
            return {'report': report, 'message': '未发送问题已继续采集；已发送问题保留原结果，没有重发。'}
        return self._start('run', resume, client_name=details['plan']['profile']['name'],
                           cycle_id=cycle_id, collector_config=config)

    def generate_research(self, cycle_id, kind, brief_id, platform, options):
        database = self.data / 'database.db'
        try:
            cycle = geo_content_store.get_cycle(database, cycle_id)
            config = self._config(options)
            if kind == 'analysis':
                prompt = geo_content_research.research_prompt(cycle)
            elif kind == 'draft':
                prompt = geo_content_research.draft_prompt(cycle, brief_id, platform)
            else:
                raise ValueError('生成类型无效')
        except (ValueError, geo_content_store.ResearchNotFoundError) as error:
            return {'error': str(error)}

        async def generate():
            if kind == 'draft' and geo_content_store.existing_draft(database, cycle, brief_id, platform):
                return {'cycle_id': cycle_id, 'message': '相同资料已有草稿，未重复提问'}
            with exclusive_lock(config.profile_dir / '.geo-test.lock'):
                result, path = await collect_doubao_sources(replace(config, question=prompt, submission_format='verbatim_fenced',
                    output_root=self.data / 'geo-content/evidence' / uuid.uuid4().hex), progress=self._collector_progress)
            self._collector_progress({'event': 'collector_finished', 'failure_reason': result.get('failure_reason'),
                'failure_stage': result.get('failure_stage'),
                'collection_status': result.get('collection_status'), 'evidence_path': str(path),
                'question_submission_attempted': result.get('question_submission_attempted'),
                'question_submitted': result.get('question_submitted')})
            if not result.get('answer_completed') or not result.get('question_submitted') or result.get('question') != prompt:
                if (result.get('failure_reason') in {'editor_readback_mismatch_not_submitted', 'submission_fresh_user_context_not_confirmed'}
                        and result.get('question_submission_attempted') is False):
                    raise ValueError('发送前核对未通过，问题尚未发送，证据已保存；请检查当前窗口后再手动开始。')
                raise ValueError(f"豆包未返回完整内容（{result.get('failure_reason') or 'answer_incomplete'}），原始证据已保存，未自动重试")
            provenance = {'method': 'doubao_web_ai', 'model_label': (result.get('environment') or {}).get('model_label'),
                          'client_mode': (result.get('environment') or {}).get('client_mode'), 'evidence_path': str(path),
                          'role': 'content_editing_not_exposure_test', 'prompt_sha256': geo_content_research.fingerprint(prompt)}
            if kind == 'analysis':
                provenance['feedback_inputs_sha256'] = geo_content_research.fingerprint(geo_content_research.planning_feedback(cycle))
            if kind == 'draft':
                provenance['input_sha256'] = geo_content_research.fingerprint(geo_content_research.draft_input(cycle, brief_id, platform))
            if kind == 'analysis':
                geo_content_store.save_analysis(database, cycle_id, result['response'], provenance, cycle['revision'])
            else:
                geo_content_store.save_draft(database, cycle_id, brief_id, platform, result['response'], provenance, cycle['revision'])
            return {'cycle_id': cycle_id}
        return self._start('research' if kind == 'analysis' else 'draft', generate, client_name=cycle['profile']['name'],
                           cycle_id=cycle_id, collector_config=config)

    def open_destination(self, cycle_id, target_id, article_id=None):
        database = self.data / 'database.db'
        try:
            cycle = geo_content_store.get_cycle(database, cycle_id)
            target = geo_destination_service.get_target(database, cycle['project_id'], target_id)
            geo_destination_service.assert_publication_allowed(target)
            if article_id is not None and (type(article_id) is not int or article_id <= 0):
                raise ValueError('文章编号无效')
        except (ValueError, geo_content_store.ResearchNotFoundError) as error:
            return {'error': str(error)}

        async def operate():
            from services.geo_destination_browser import open_session
            event_id, article = None, None
            kind = 'fill' if article_id is not None else 'login'
            if article_id is not None:
                current, article, event_id = geo_destination_service.reserve_fill(database, cycle['project_id'], target_id, article_id)
            else:
                current = geo_destination_service.get_target(database, cycle['project_id'], target_id)
            geo_destination_service.assert_publication_allowed(current)
            check_requested = asyncio.Event()
            with self.lock:
                self._destination_check = check_requested
                self._destination_check_loop = asyncio.get_running_loop()
                self._destination_check_task_id = self.job['id']
            def progress(value):
                if kind == 'fill':
                    geo_destination_service.record_event(database, target_id, kind, value['state'], value['message'], article_id, event_id)
                with self.lock:
                    self.job['message'] = value['message']
                    session = value.get('destination_session')
                    if isinstance(session, dict):
                        # The browser emits fixed status/messages. Do not save
                        # account fields, page HTML, URLs or arbitrary extras.
                        names = {'state', 'message', 'can_check', 'window_open', 'content_write_attempted',
                                 'editor_ready', 'login_required', 'challenge', 'binding_required', 'binding_kind', 'body_state'}
                        safe = {key: copy.deepcopy(item) for key, item in session.items() if key in names}
                        self.job['progress'] = {'destination_session': safe}
                        self.job['waiting_for_user'] = bool(safe.get('can_check'))
                        if safe.get('state') not in ('opening', 'checking'):
                            self._destination_check_pending = False
                    self._persist()
            try:
                result = await open_session(current, self.data / 'platform-profiles' / str(cycle['project_id']) / target_id,
                    edge_executable(), fallback_executable_path=chrome_executable(), article=article, progress=progress,
                    check_requested=check_requested)
                geo_destination_service.record_event(database, target_id, kind, result['state'], result['message'], article_id, event_id)
                return {'target_id': target_id, **result}
            except BaseException:
                geo_destination_service.record_event(database, target_id, kind, 'need_action',
                    '窗口关闭、任务停止或页面异常，请检查页面内容和账号状态；不会自动重复填写', article_id, event_id)
                raise
        return self._start('destination_fill' if article_id is not None else 'destination_login', operate,
                           client_name=cycle['profile']['name'], cycle_id=cycle_id, target_id=target_id)

    def check_destination_session(self, task_id):
        """Signal a single explicit, read-only check in the existing window."""
        with self.lock:
            if (not isinstance(task_id, str) or not task_id or len(task_id) > 128
                    or self.job.get('id') != task_id or self.job.get('status') != 'running'
                    or self.job.get('kind') not in ('destination_login', 'destination_fill')
                    or self._destination_check_task_id != task_id):
                return {'error': '仅能检查当前正在运行的平台接入任务；请保留原窗口。'}
            try:
                cycle = geo_content_store.get_cycle(self.data / 'database.db', self.job.get('cycle_id'))
                target = geo_destination_service.get_target(self.data / 'database.db', cycle['project_id'], self.job.get('target_id'))
                geo_destination_service.assert_publication_allowed(target)
            except (ValueError, geo_content_store.ResearchNotFoundError) as error:
                return {'error': str(error)}
            loop, signal = self._destination_check_loop, self._destination_check
            session = (self.job.get('progress') or {}).get('destination_session') or {}
            if loop is None or signal is None or loop.is_closed() or not session.get('window_open'):
                return {'error': '当前平台窗口尚未就绪或已结束，请查看任务提示。'}
            if self._destination_check_pending:
                return {'id': task_id, 'status': 'running', 'check_requested': True}
            if session.get('can_check') is not True:
                return {'error': '平台状态正在检查，请等待当前检查完成。'}
            self._destination_check_pending = True
            session.update(state='checking', can_check=False,
                           message='正在检查当前平台会话和编辑器，不会提交文章或重复填写。')
            self.job['message'] = session['message']
            self._persist()
            try:
                loop.call_soon_threadsafe(signal.set)
            except RuntimeError:
                self._destination_check_pending = False
                return {'error': '当前平台窗口已结束，请查看任务记录；不会自动重开。'}
            return {'id': task_id, 'status': 'running', 'check_requested': True}

    def open_source_maintenance(self, cycle_id, source_task_id):
        from services import source_maintenance_service as maintenance
        database = self.data / 'database.db'
        try:
            record = maintenance.get_task(database, cycle_id, source_task_id)
            if not record['read_only'] and maintenance.get_overview(database, cycle_id)['context_sha256'] != record['context_sha256']:
                raise ValueError('确认资料已变化，请刷新公开说明，未打开辅助申请')
        except (ValueError, TypeError, sqlite3.Error, geo_content_store.ResearchNotFoundError) as error:
            return {'error': str(error)}

        async def operate():
            from services.qcc_source_maintenance_browser import open_session
            signal = asyncio.Event()
            with self.lock:
                native_id = self.job['id']
                self.job['source_task_id'] = source_task_id
                self._source_check, self._source_check_loop, self._source_check_task_id = signal, asyncio.get_running_loop(), native_id
                self._persist()
            latest = record

            def progress(value):
                nonlocal latest
                if value.get('write_attempted') and not latest.get('write_attempted'):
                    if maintenance.get_overview(database, cycle_id)['context_sha256'] != record['context_sha256']:
                        raise ValueError('确认资料已变化，公开说明尚未写入')
                latest = maintenance.record_state(database, cycle_id, source_task_id,
                    stage=value['stage'], message=value['message'], native_task_id=native_id,
                    write_attempted=value.get('write_attempted', False),
                    confirmation_seen=value.get('confirmation_seen', False), window_open=value.get('window_open', False),
                    submission_success_observed=value.get('submission_success_observed', False),
                    inspection_only=value.get('inspection_only', False))
                with self.lock:
                    self.job.update(message=latest['message'], waiting_for_user=latest['window_open'],
                        progress={'source_maintenance': {'id': source_task_id, 'stage': latest['stage'],
                            'window_open': latest['window_open'], 'read_only': latest['read_only']}})
                    self._persist()
            try:
                progress({'stage': 'opening', 'message': '正在打开固定企查查官方入口；最终提交始终由本人操作。', 'window_open': False})
                # Stable per-user/platform profile reuses normal authorized QCC
                # sessions, including the existing browser-selection marker.
                await open_session(record, self.data / 'source-research/qcc-browser-profile', edge_executable(),
                    fallback_executable_path=chrome_executable(), progress=progress, check_requested=signal)
                return {'source_maintenance': latest, 'message': latest['message']}
            finally:
                if latest.get('window_open') or latest['stage'] == 'opening':
                    progress({'stage': 'submission_unknown' if latest.get('read_only') else 'window_closed',
                        'message': '来源维护窗口已结束，原申请保留；不会自动重新填写或提交。',
                        'write_attempted': latest.get('write_attempted'), 'window_open': False})
                with self.lock:
                    self._source_check = self._source_check_loop = self._source_check_task_id = None
        return self._start('source_maintenance', operate, client_name=record['company_name'], cycle_id=cycle_id)

    def check_source_maintenance_session(self, native_task_id):
        with self.lock:
            if (not isinstance(native_task_id, str) or self.job.get('id') != native_task_id
                    or self.job.get('kind') != 'source_maintenance' or self.job.get('status') != 'running'
                    or self._source_check_task_id != native_task_id or self._source_check is None
                    or self._source_check_loop is None or self._source_check_loop.is_closed()):
                return {'error': '只能检查当前来源维护窗口；请先打开原任务。'}
            try:
                self._source_check_loop.call_soon_threadsafe(self._source_check.set)
            except RuntimeError:
                return {'error': '来源维护窗口已结束，请查看原任务；不会自动重新打开或提交。'}
            return {'id': native_task_id, 'status': 'running', 'check_requested': True}

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
        cycle_id = (job or self.job).get('cycle_id')
        if cycle_id:
            geo_content_store.attach_retest(self.data / 'database.db', cycle_id, report)
        return report

    def shutdown(self):
        if self.get_task()["status"] == "running":
            self.stop_task()
        if self.worker:
            self.worker.join(timeout=15)
