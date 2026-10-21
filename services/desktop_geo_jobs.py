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
from dataclasses import replace

from services.doubao_source_collector import CollectorConfig, open_doubao_session
from services.geo_test_cli import _plan_with_doubao
from services.geo_test_runner import RunConfig, exclusive_lock, run_test_plan, write_json
from services.geo_test_service import build_report, normalize_profile, validate_plan
from services.desktop_task_store import DesktopTaskStore, now
from services import geo_content_store, geo_content_research, geo_destination_service
from services.doubao_source_collector import collect_doubao_sources


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
        if isinstance(value, dict) and isinstance(value.get('result'), dict):
            self._project_report_view(value['result'])
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
                 'question_sent', 'case_started', 'case_finished', 'background_task_waiting', 'background_task_cleared',
                 'answer_completed', 'source_progress'}
        if not isinstance(event, dict) or event.get('event') not in names:
            return
        name = event['event']
        reason = event.get('reason') if event.get('reason') in {'human_verification_required', 'login_required'} else None
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
            'question_sent': '问题已发送，正在等待豆包当前回答；无需重复发送。',
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
            self.job['waiting_for_user'] = name == 'manual_action_required'
            if name == 'manual_action_required' and reason:
                self.job['wait_reason'] = reason
            else:
                self.job.pop('wait_reason', None)
            self.job.update(progress=safe, message=messages[name])
            self._persist()

    def _start(self, kind, operation, *, client_name="", cycle_id=None, target_id=None):
        with self.lock:
            if self.job["status"] == "running":
                return {"error": "已有任务正在执行，请先等待或停止"}
            self.cancel_requested = False
            self.job = {"id": uuid.uuid4().hex, "kind": kind, "status": "running",
                        "created_at": now(), "client_name": client_name}
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
                    if isinstance(result, dict) and result.get('message'):
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
                logging.exception("Native GEO task failed")
                with self.lock:
                    message = str(error) if isinstance(error, ValueError) else f"任务失败（{type(error).__name__}），请检查软件日志"
                    if isinstance(error, ImportError):
                        message = f"软件采集组件加载失败（{getattr(error, 'name', None) or '运行库'}），本次未发送问题。请重新打开更新后的软件。"
                    self.job.update(status="failed", waiting_for_user=False, message=message)
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
        return self._start("plan", plan, client_name=profile["name"])

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
        return self._start("run", run, client_name=plan["profile"]["name"], cycle_id=cycle_id)

    def start_research_retest(self, cycle_id):
        try:
            cycle = geo_content_store.get_cycle(self.data / 'database.db', cycle_id)
            execution = cycle['baseline'].get('execution') or {}
            options = {'client_mode': execution.get('client_mode') or next((case.get('client_mode') for case in cycle['baseline']['cases'] if case.get('client_mode')), 'consumer'),
                       'model_label': execution.get('requested_model_label') or ''}
            return self.start_run(cycle['test_plan'], options, cycle_id=cycle_id)
        except (ValueError, geo_content_store.ResearchNotFoundError) as error:
            return {'error': str(error)}

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
            if not result.get('answer_completed') or not result.get('question_submitted') or result.get('question') != prompt:
                if (result.get('failure_reason') in {'editor_readback_mismatch_not_submitted', 'submission_fresh_user_context_not_confirmed'}
                        and result.get('question_submission_attempted') is False):
                    raise ValueError('发送前核对未通过，问题尚未发送，证据已保存；请检查当前窗口后再手动开始。')
                raise ValueError(f"豆包未返回完整内容（{result.get('failure_reason') or 'answer_incomplete'}），原始证据已保存，未自动重试")
            provenance = {'method': 'doubao_web_ai', 'model_label': (result.get('environment') or {}).get('model_label'),
                          'client_mode': (result.get('environment') or {}).get('client_mode'), 'evidence_path': str(path),
                          'role': 'content_editing_not_exposure_test', 'prompt_sha256': geo_content_research.fingerprint(prompt)}
            if kind == 'draft':
                provenance['input_sha256'] = geo_content_research.fingerprint(geo_content_research.draft_input(cycle, brief_id, platform))
            if kind == 'analysis':
                geo_content_store.save_analysis(database, cycle_id, result['response'], provenance, cycle['revision'])
            else:
                geo_content_store.save_draft(database, cycle_id, brief_id, platform, result['response'], provenance, cycle['revision'])
            return {'cycle_id': cycle_id}
        return self._start('research' if kind == 'analysis' else 'draft', generate, client_name=cycle['profile']['name'], cycle_id=cycle_id)

    def open_destination(self, cycle_id, target_id, article_id=None):
        database = self.data / 'database.db'
        try:
            cycle = geo_content_store.get_cycle(database, cycle_id)
            target = geo_destination_service.get_target(database, cycle['project_id'], target_id)
            geo_destination_service.assert_target_enabled(target)
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
            geo_destination_service.assert_target_enabled(current)
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
                geo_destination_service.assert_target_enabled(target)
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
