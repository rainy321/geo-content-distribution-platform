"""Native reinspection stays bound to the current task and fails closed after restart."""
import asyncio
import inspect
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop_app import run_smoke_check
from services.desktop_geo_jobs import DesktopGeoJobs
from services.desktop_task_store import DesktopTaskStore


class DesktopDestinationBridgeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.jobs = DesktopGeoJobs(self.root)
        self.addCleanup(self.jobs.shutdown)

    def test_actual_jobs_instance_keeps_old_entry_points_and_accepts_only_a_task_id(self):
        for name in ('open_login', 'plan_with_doubao', 'start_run', 'generate_research', 'get_task',
                     'open_destination', 'list_tasks', 'stop_task'):
            self.assertTrue(callable(getattr(self.jobs, name)), name)
        signature = inspect.signature(self.jobs.check_destination_session)
        self.assertEqual(list(signature.parameters), ['task_id'])
        self.assertEqual(signature.parameters['task_id'].kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        with self.assertRaises(TypeError):
            self.jobs.check_destination_session('current-task', {'url':'https://unrelated.example/', 'script':'arbitrary code'})

    def test_invalid_or_stale_input_cannot_start_a_browser_or_create_a_successful_task(self):
        before = self.jobs.get_task()
        for value in (None, '', 7, True, {'url':'https://unrelated.example/'},
                      {'script':'arbitrary code'}, 'https://unrelated.example/', '../task', 'stale-task', 'b'*32):
            with self.subTest(value=value):
                result = self.jobs.check_destination_session(value)
                self.assertIn('error', result)
                self.assertNotIn('check_requested', result)
                self.assertNotIn('editor_ready', result)
                self.assertEqual(self.jobs.get_task(), before)
                self.assertEqual(self.jobs.list_tasks(), [])

    def test_another_active_task_is_not_treated_as_a_destination_session(self):
        entered = threading.Event()

        async def pending_login():
            entered.set()
            await asyncio.Event().wait()

        self.jobs._start('login', pending_login)
        self.assertTrue(entered.wait(timeout=3))
        before = self.jobs.get_task()
        result = self.jobs.check_destination_session(before['id'])
        self.assertIn('error', result)
        self.assertNotIn('check_requested', result)
        self.assertEqual(self.jobs.get_task(), before)
        self.assertEqual(len(self.jobs.list_tasks()), 1)

    def test_restarted_destination_record_cannot_resume_through_the_new_method(self):
        state = DesktopTaskStore(self.root)
        state.save({'id':'a'*32,'kind':'destination_login',
                    'status':'running','created_at':'2026-10-21T00:00:00Z','target_id':'b'*32,
                    'progress':{'destination_session':{'state':'binding_required','can_check':True,
                                                       'window_open':True,'content_write_attempted':False}}})
        restored = DesktopGeoJobs(self.root)
        self.addCleanup(restored.shutdown)
        before = restored.get_task()
        self.assertEqual(before['status'], 'needs_review')
        result = restored.check_destination_session(before['id'])
        self.assertIn('error', result)
        self.assertNotIn('check_requested', result)
        self.assertEqual(restored.get_task(), before)
        self.assertEqual(len(restored.list_tasks()), 1)

    def test_smoke_rejects_an_old_bridge_even_when_the_five_step_ui_and_old_methods_load(self):
        (self.root/'logs').mkdir()
        observed = {'heading':'合成品牌','steps':['品牌','搜索基线','内容','发布','反馈'],
                    'primary_action':True,'native_bridge':True,'destination_recheck_bridge':False}
        window = SimpleNamespace(evaluate_js=Mock(return_value=json.dumps(observed, ensure_ascii=False)),
                                 destroy=Mock())
        with patch('desktop_app.time.sleep'), self.assertLogs(level='ERROR'):
            run_smoke_check(window, self.root, 'http://127.0.0.1:5418/#/journey?desktop=1')
        status = json.loads((self.root/'logs/smoke-test.json').read_text(encoding='utf-8'))
        self.assertFalse(status['passed'])
        self.assertNotIn('journey_verified', status)
        self.assertTrue(status['last_journey_observed']['native_bridge'])
        self.assertFalse(status['last_journey_observed']['destination_recheck_bridge'])
        window.destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
