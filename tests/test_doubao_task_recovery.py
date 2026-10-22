"""Synthetic task/evidence recovery: no real sessions, credentials or database."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.desktop_geo_jobs import DesktopGeoJobs
from services.doubao_task_recovery import recovery_view
from tests.test_geo_tests import plan


class TaskRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.jobs = DesktopGeoJobs(self.root)
        self.addCleanup(self.jobs.shutdown)

    def evidence(self, name='one', **changes):
        path = self.root / 'geo-content/evidence' / name / 'run/result.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {'timestamp': '2026-10-22T09:16:35+08:00', 'failure_reason': 'session_redirected_to_logout',
                 'finished_at': '2026-10-22T09:16:43+08:00',
                 'collection_status': 'need_action', 'question_submission_attempted': False, 'question_submitted': False,
                 'environment': {'requested_client_mode': 'work', 'requested_model_label': None}, **changes}
        path.write_text(json.dumps(value), encoding='utf-8')
        return value, path

    def legacy(self, **changes):
        return {'id': 'synthetic-old', 'kind': 'draft', 'status': 'failed',
                'created_at': '2026-10-22T01:16:34+00:00', 'updated_at': '2026-10-22T01:16:44+00:00',
                'message': '豆包未返回完整内容（session_redirected_to_logout），原始证据已保存，未自动重试', **changes}

    def test_explicit_legacy_evidence_is_projected_without_rewriting_any_file(self):
        _, evidence = self.evidence()
        old = self.legacy(evidence_path=str(evidence))
        self.jobs.store.save(old)
        task_path = self.root / 'desktop-jobs/synthetic-old.json'
        before = (task_path.read_bytes(), evidence.read_bytes())
        value = self.jobs.get_task(old['id'])
        self.assertEqual(value['status'], 'failed')
        self.assertEqual(value['recovery']['stage'], 'before_submission')
        self.assertEqual(value['recovery']['requested_client_mode'], 'work')
        self.assertEqual(value['recovery']['continuation'], 'new_attempt_required')
        self.assertEqual(value['recovery']['evidence_path'], str(evidence))
        self.assertEqual((task_path.read_bytes(), evidence.read_bytes()), before)

    def test_one_foreign_time_window_candidate_cannot_supply_submission_flags_or_mode(self):
        _, path = self.evidence()
        old = self.legacy()
        before = path.read_bytes()
        value = recovery_view(old, self.root)
        self.assertEqual(value['stage'], 'unknown')
        self.assertIsNone(value['question_submission_attempted'])
        self.assertIsNone(value['question_submitted'])
        self.assertIsNone(value['requested_client_mode'])
        self.assertIsNone(value['evidence_path'])
        self.assertEqual(path.read_bytes(), before)

    def test_ambiguous_or_untyped_old_evidence_never_claims_not_sent(self):
        self.evidence()
        self.evidence('two')
        value = recovery_view(self.legacy(), self.root)
        self.assertIsNone(value['question_submission_attempted'])
        self.assertIsNone(value['question_submitted'])
        _, path = self.evidence('typed', question_submission_attempted='false', question_submitted=0)
        value = recovery_view(self.legacy(evidence_path=str(path)), self.root)
        self.assertEqual(value['stage'], 'unknown')
        self.assertIsNone(value['question_submitted'])

    def test_explicit_unknown_or_invalid_evidence_fields_override_old_false_progress(self):
        for unknown in (None, 'false', 0):
            _, path = self.evidence(question_submission_attempted=unknown, question_submitted=unknown)
            old = self.legacy(evidence_path=str(path), collector_finished=True,
                submission_state={'question_submission_attempted': False, 'question_submitted': False},
                progress={'question_submission_attempted': False, 'question_submitted': False})
            value = recovery_view(old, self.root)
            self.assertEqual(value['stage'], 'unknown')
            self.assertIsNone(value['question_submission_attempted'])
            self.assertIsNone(value['question_submitted'])

    def test_stale_terminal_false_flags_without_final_evidence_are_unknown_but_true_remains_attempted(self):
        old = self.legacy(progress={'question_submission_attempted': False, 'question_submitted': False})
        self.assertEqual(recovery_view(old, self.root)['stage'], 'unknown')
        old['submission_state'] = {'question_submission_attempted': True, 'question_submitted': False}
        value = recovery_view(old, self.root)
        self.assertTrue(value['question_submission_attempted'])
        self.assertIsNone(value['question_submitted'])
        self.assertEqual(value['stage'], 'after_submission')
        old['collector_finished'] = True
        old['submission_state']['question_submission_attempted'] = False
        self.assertEqual(recovery_view(old, self.root)['stage'], 'before_submission')

    def test_explicit_evidence_conflict_or_wrong_root_never_grants_not_sent(self):
        _, path = self.evidence(failure_reason='another_request')
        old = self.legacy(evidence_path=str(path), collector_finished=True,
            submission_state={'question_submission_attempted': False, 'question_submitted': False})
        value = recovery_view(old, self.root)
        self.assertEqual(value['stage'], 'unknown')
        self.assertIsNone(value['requested_client_mode'])
        outside = self.root/'cookies/result.json'
        outside.parent.mkdir()
        outside.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
        self.assertEqual(recovery_view(self.legacy(evidence_path=str(outside)), self.root)['stage'], 'unknown')
        _, second = self.evidence('another-explicit')
        old = self.legacy(evidence_path=str(path), recovery={'evidence_path': str(second)})
        self.assertEqual(recovery_view(old, self.root)['stage'], 'unknown')

    def test_sent_then_closed_is_not_advertised_as_safe_to_retry_or_continue(self):
        _, path = self.evidence(failure_reason='browser_error:TargetClosedError',
                                question_submission_attempted=True, question_submitted=True)
        value = recovery_view(self.legacy(failure_reason='browser_error:TargetClosedError', evidence_path=str(path)), self.root)
        self.assertEqual(value['stage'], 'after_submission')
        self.assertEqual(value['continuation'], 'new_attempt_required')
        self.assertTrue(value['question_submitted'])

    def test_native_plan_auth_failure_becomes_need_action_with_exact_options_and_flags(self):
        result, path = self.evidence()
        path = self.root/'geo-tests/desktop-plans/synthetic/plan-planner-evidence/run/result.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(result), encoding='utf-8')
        result.update(answer_completed=False, question='synthetic request')
        async def collect(*args, **kwargs):
            return result, path
        with patch('services.geo_test_cli.collect_doubao_sources', collect):
            self.jobs.plan_with_doubao(plan()['profile'], 4, {'client_mode': 'work', 'model_label': 'synthetic-choice'})
            self.jobs.worker.join(5)
        self.assertFalse(self.jobs.worker.is_alive())
        value = self.jobs.get_task()
        self.assertEqual(value['status'], 'need_action')
        self.assertEqual(value['recovery']['requested_client_mode'], 'work')
        self.assertEqual(value['recovery']['requested_model_label'], 'synthetic-choice')
        self.assertEqual(value['recovery']['stage'], 'before_submission')
        self.assertNotIn('session_redirected_to_logout', value['message'])

    def test_live_auth_wait_retains_progress_and_only_live_worker_can_continue(self):
        import threading
        entered = threading.Event()
        async def operate():
            self.jobs._collector_progress({'event': 'manual_action_required', 'reason': 'session_redirected_to_logout',
                'question_submission_attempted': True, 'question_submitted': False})
            entered.set()
            await asyncio.Event().wait()
        self.jobs._start('draft', operate)
        self.assertTrue(entered.wait(3))
        value = self.jobs.get_task()
        self.assertEqual(value['wait_reason'], 'session_redirected_to_logout')
        self.assertEqual(value['recovery']['continuation'], 'same_window')
        self.assertEqual(value['recovery']['stage'], 'after_submission')
        self.jobs.stop_task()
        self.jobs.worker.join(5)
        value = self.jobs.get_task()
        self.assertEqual(value['recovery']['continuation'], 'new_attempt_required')
        self.assertTrue(value['recovery']['question_submission_attempted'])


if __name__ == '__main__':
    unittest.main()
