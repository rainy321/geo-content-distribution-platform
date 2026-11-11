"""Source-only repair is hash bound, mocked, and never collects a new answer."""
import asyncio
import copy
import hashlib
import json
import sqlite3
import stat
import threading
import unittest
from types import SimpleNamespace
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_geo_research_probes import ProbeFixture
from services import geo_content_store as store, geo_research_probe_service as probes
from services import geo_probe_source_inspection as inspection
from services.desktop_geo_jobs import DesktopGeoJobs
from services.doubao_source_collector import CollectorConfig
from services.geo_test_runner import write_json


def sha(value):
    return hashlib.sha256(value).hexdigest()


class InspectionFixture(ProbeFixture):
    def setUp(self):
        super().setUp()
        self.probe_id, self.task_id = 'e' * 32, 'd' * 32
        self.url = 'https://www.doubao.com/chat/12345678901234567'
        self.snapshot = {'page_url': self.url, 'question_verified': True, 'user_message_count': 1,
            'assistant_count': 1, 'completion_marker': True, 'busy': False,
            'answer': self.result['response'], 'client_mode': 'work', 'model_label': '测试模型'}
        self.raw_path = self.root / 'geo-content/probes' / self.probe_id / 'evidence/original/result.json'
        snapshot_path = self.raw_path.parent / 'answer.json'
        write_json(snapshot_path, self.snapshot)
        self.raw = {**self.result, 'collection_status': 'partial', 'source_extraction_status': 'incomplete',
            'failure_reason': 'source_ui_not_resolved', 'conversation_url': self.url,
            'environment': {**self.result['environment'], 'session_slot': 'primary'},
            'evidence_manifest': [{'path': 'answer.json', 'sha256': sha(snapshot_path.read_bytes())}]}
        write_json(self.raw_path, self.raw)
        evidence = {'result_path': str(self.raw_path), 'result_sha256': sha(self.raw_path.read_bytes())}
        self.probe = probes.attach_probe(self.database, self.cycle['id'],
            self.native(run_id=self.probe_id, collector_result=self.raw),
            source='native_collector', native_task_id=self.task_id, evidence=evidence)
        self.original_job = {'id': self.task_id, 'kind': 'probe', 'status': 'completed',
            'probe_id': self.probe_id, 'cycle_id': self.cycle['id'], 'session_slot': 'primary',
            'requested_client_mode': 'work', 'result': {'probe': self.probe}}
        write_json(self.root / 'desktop-jobs' / (self.task_id + '.json'), self.original_job)
        self.cycle = store.get_cycle(self.database, self.cycle['id'])
        self.details = inspection.resolve(self.root, self.cycle, self.probe_id)
        self.expected = copy.deepcopy(self.details['expected'])
        self.config = CollectorConfig(question=self.question, headed=True, client_mode='work',
            requested_model_label='测试模型', profile_dir=self.root / 'doubao/browser-profile',
            output_root=self.root / 'geo-content/probe-source-inspections' / self.probe_id)
        self.config.profile_dir.mkdir(parents=True)
        self.calls = []

    @asynccontextmanager
    async def page(self, config, url):
        self.calls.append(('open', config.profile_dir, url))
        yield object()

    async def snapshot_read(self, page, config):
        return copy.deepcopy(self.snapshot)

    async def collect(self, config, original, *, page, progress=None):
        self.calls.append(('reinspect', original))
        sequence = sum(row[0] == 'reinspect' for row in self.calls)
        directory = config.output_root / f'inspect-20300101-000000-{sequence:08x}'
        summary = {'schema_version': 1, 'status': 'verified', 'evidence_file': 'source-context.json',
            'target_question_sha256': sha(self.question.encode()),
            'target_answer_sha256': self.expected['response_sha256'], 'canonical_url_sha256': sha(self.url.encode())}
        write_json(directory / 'source-context.json', {**summary, 'blocked': False,
            'source_phase_completed': True, 'navigation_away_seen': False})
        result = {**copy.deepcopy(self.raw), 'run_id': directory.name, 'mode': 'inspect_existing_answer',
            'timestamp': '2030-01-01T00:00:00+00:00', 'original_answer_timestamp': self.raw['timestamp'],
            'original_result_path': str(self.raw_path), 'original_result_sha256': self.expected['raw_result_sha256'],
            'original_response_sha256': self.expected['response_sha256'], 'question_submission_attempted': False,
            'collection_status': 'success', 'failure_reason': None, 'source_extraction_status': 'captured',
            'displayed_sources': [{'title': '合成公开产品页', 'url': 'https://synthetic.example.org', 'url_status': 'navigation_observed'}],
            'citations': [{'title': '公开第三方资料', 'url': 'https://public.example.org/page', 'url_status': 'navigation_observed'}],
            'source_context_binding': summary,
            'evidence_manifest': [{'path': 'source-context.json', 'sha256': sha((directory / 'source-context.json').read_bytes())}],
            'inspection_provenance': {'kind': 'later_read_only_sources_not_new_answer', 'question_sent': False,
                'original_result_unchanged': True, 'question_sha256': sha(self.question.encode()),
                'navigation_urls': [self.url], 'answer_identity_method': 'exact_complete_response',
                'current_answer_sha256': self.expected['response_sha256']}}
        path = directory / 'result.json'
        write_json(path, result)
        return result, path

    def run_inspection(self, collector=None, config=None):
        with patch.object(inspection, 'open_original_page', self.page), \
             patch.object(inspection, 'read_snapshot', self.snapshot_read), \
             patch.object(inspection, 'reinspect_existing_sources', collector or self.collect), \
             patch('services.desktop_geo_jobs.collect_doubao_sources', side_effect=AssertionError('must not send')):
            return asyncio.run(inspection.inspect(self.root, self.cycle, self.details, config or self.config))


class SourceInspectionTests(InspectionFixture, unittest.TestCase):
    def test_capability_requires_real_native_original_job_raw_and_answer_snapshot(self):
        view = self.cycle['probes'][0]
        self.assertEqual(view['source_inspection']['expected'], self.expected)
        self.assertFalse(view['source_inspection']['question_sent'])
        for changes in ({'native_verified': False}, {'source': 'research_prototype_import'},
                        {'native_task_id': '../elsewhere'}, {'evidence': None},
                        {'after': {**self.probe['after'], 'answer_evaluable': False}}):
            value = {**self.cycle, 'probes': [{**self.probe, **changes}]}
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                inspection.resolve(self.root, value, self.probe_id)
            self.assertNotIn('source_inspection', inspection.project(self.root, value)[0])

    def test_exact_four_expected_fields_reject_extra_path_url_stale_hash_and_bool_revision(self):
        for expected in ({**self.expected, 'url': self.url}, {**self.expected, 'path': str(self.raw_path)},
                         {**self.expected, 'revision': True}, {**self.expected, 'revision': self.cycle['revision'] + 1},
                         {**self.expected, 'response_sha256': '0' * 64},
                         {**self.expected, 'raw_result_sha256': 'F' * 64}, {}):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                inspection.resolve(self.root, self.cycle, self.probe_id, expected)
        for value in ('../escape', 'a' * 31, 'A' * 32):
            with self.assertRaises(ValueError):
                inspection.resolve(self.root, self.cycle, value, self.expected)

    def test_raw_and_job_changes_path_escape_and_slot_mismatch_cannot_enable_repair(self):
        original = self.raw_path.read_bytes()
        self.raw_path.write_bytes(original + b' ')
        with self.assertRaises(ValueError):
            inspection.resolve(self.root, self.cycle, self.probe_id, self.expected)
        self.raw_path.write_bytes(original)
        for changes in ({'evidence': {**self.probe['evidence'], 'result_path': str(self.root / 'elsewhere/result.json')}},
                        {'execution': {**self.probe['execution'], 'session_slot': 'secondary'}}):
            with self.assertRaises((ValueError, FileNotFoundError)):
                inspection.resolve(self.root, {**self.cycle, 'probes': [{**self.probe, **changes}]}, self.probe_id)
        path = self.root / 'desktop-jobs' / (self.task_id + '.json')
        write_json(path, {**self.original_job, 'session_slot': 'secondary'})
        with self.assertRaises(ValueError):
            inspection.resolve(self.root, self.cycle, self.probe_id)

    def test_windows_reparse_flag_is_rejected_before_reading_a_result(self):
        lstat = Path.lstat
        def flagged(path, *args, **kwargs):
            value = lstat(path, *args, **kwargs)
            if path == self.raw_path:
                return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return value
        with patch.object(Path, 'lstat', flagged), self.assertRaisesRegex(ValueError, '重解析点'):
            inspection.resolve(self.root, self.cycle, self.probe_id)

    def test_changed_conversation_question_answer_model_and_completion_reject_before_collector(self):
        for changes in ({'page_url': 'https://www.doubao.com/chat/99999999'}, {'question_verified': False},
                        {'user_message_count': 2}, {'answer': 'different complete answer'},
                        {'client_mode': 'consumer'}, {'model_label': '其他模型'}, {'busy': True},
                        {'completion_marker': False}, {'challenge_visible': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                inspection.verify_snapshot({**self.snapshot, **changes}, self.details)
        with self.assertRaises(ValueError):
            self.run_inspection(config=replace(self.config, profile_dir=self.root / 'doubao/other-profile'))
        self.assertEqual(self.calls, [])

    def test_success_adds_separate_later_source_view_and_preserves_db_raw_after_and_business(self):
        raw_before, db_before = self.raw_path.read_bytes(), self.database.read_bytes()
        job_before = (self.root / 'desktop-jobs' / (self.task_id + '.json')).read_bytes()
        result = self.run_inspection()
        self.assertFalse(result['source_reinspection']['new_answer_collected'])
        self.assertTrue(result['source_reinspection']['source_assessment']['official_source_present'])
        self.assertFalse(result['source_reinspection']['source_assessment']['official_citation_present'])
        self.assertTrue(result['source_reinspection']['source_collection_complete'])
        projected = store.get_cycle(self.database, self.cycle['id'])['probes'][0]
        self.assertNotIn('source_inspection', projected)
        self.assertEqual(projected['after'], self.probe['after'])
        for key in ('before', 'collected_at', 'business_relationship_review', 'conditions', 'status', 'evidence'):
            self.assertEqual(projected[key], self.probe[key])
        self.assertEqual(self.raw_path.read_bytes(), raw_before)
        self.assertEqual(self.database.read_bytes(), db_before)
        self.assertEqual((self.root / 'desktop-jobs' / (self.task_id + '.json')).read_bytes(), job_before)
        self.assertEqual([name for name, *_ in self.calls], ['open', 'reinspect'])
        with self.assertRaises(ValueError):
            self.run_inspection()
        self.assertEqual([name for name, *_ in self.calls], ['open', 'reinspect'])
        self.assert_original_unchanged()

    def test_wrong_source_conversation_or_unverified_guard_never_publishes_a_binding(self):
        for change in ('navigation', 'context', 'answer'):
            async def wrong(*args, **kwargs):
                result, path = await self.collect(*args, **kwargs)
                if change == 'navigation':
                    result['inspection_provenance']['navigation_urls'].append('https://www.doubao.com/chat/99999999')
                elif change == 'context':
                    result['source_context_binding']['status'] = 'unverified'
                else:
                    result['source_context_binding']['target_answer_sha256'] = '0' * 64
                write_json(path, result)
                return result, path
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_inspection(wrong)
            self.assertEqual(list(self.config.output_root.glob('inspect-*/binding.json')), [])
            self.assertNotIn('source_reinspection', store.get_cycle(self.database, self.cycle['id'])['probes'][0])

    def test_verified_binding_with_partial_sources_stays_unknown_and_requires_a_new_manual_action(self):
        async def partial(*args, **kwargs):
            result, path = await self.collect(*args, **kwargs)
            result.update(collection_status='partial', source_extraction_status='incomplete',
                          citations=[{'title': '仅引用标记，网址未知', 'url': None}])
            write_json(path, result)
            return result, path
        first = self.run_inspection(partial)
        self.assertFalse(first['source_reinspection']['source_collection_complete'])
        self.assertIsNone(first['source_reinspection']['source_assessment']['official_citation_present'])
        view = store.get_cycle(self.database, self.cycle['id'])['probes'][0]
        self.assertIn('source_inspection', view)
        self.assertEqual(sum(row[0] == 'reinspect' for row in self.calls), 1)
        second = self.run_inspection()
        self.assertTrue(second['source_reinspection']['source_collection_complete'])
        self.assertEqual(sum(row[0] == 'reinspect' for row in self.calls), 2)
        self.assertNotIn('source_inspection', store.get_cycle(self.database, self.cycle['id'])['probes'][0])

    def test_changed_answer_after_capture_and_cancel_leave_no_success_projection(self):
        async def changed(*args, **kwargs):
            result = await self.collect(*args, **kwargs)
            self.snapshot['answer'] = 'changed after source read'
            return result
        with self.assertRaises(ValueError):
            self.run_inspection(changed)
        self.snapshot['answer'] = self.raw['response']
        async def cancelled(*args, **kwargs):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            self.run_inspection(cancelled)
        self.assertEqual(list(self.config.output_root.glob('inspect-*/binding.json')), [])

    def test_tampered_sidecar_or_source_context_manifest_is_not_displayed(self):
        self.run_inspection()
        path = next(self.config.output_root.glob('inspect-*/source-context.json'))
        path.write_bytes(path.read_bytes() + b' ')
        self.assertNotIn('source_reinspection', store.get_cycle(self.database, self.cycle['id'])['probes'][0])

    def test_cycle_revision_changed_during_read_does_not_publish_later_sources(self):
        raw_before = self.raw_path.read_bytes()
        async def edited(*args, **kwargs):
            result = await self.collect(*args, **kwargs)
            with sqlite3.connect(self.database) as connection:
                connection.execute('UPDATE geo_content_cycles SET revision=revision+1 WHERE id=?', (self.cycle['id'],))
            return result
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.run_inspection(edited)
        self.assertEqual(list(self.config.output_root.glob('inspect-*/binding.json')), [])
        self.assertEqual(self.raw_path.read_bytes(), raw_before)


class DesktopSourceInspectionTests(InspectionFixture, unittest.TestCase):
    def test_native_entry_cannot_omit_freeze_or_supply_url_path_or_script(self):
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        for expected in (None, {}, {**self.expected, 'url': self.url}, {**self.expected, 'path': str(self.raw_path)},
                         {**self.expected, 'script': 'untrusted()'}, {**self.expected, 'revision': False}):
            with self.subTest(expected=expected):
                self.assertIn('error', jobs.start_probe_source_inspection(self.cycle['id'], self.probe_id, expected))
        self.assertIsNone(jobs.worker)

    def test_native_cancellation_stops_without_retry_and_keeps_the_original_answer(self):
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        before = self.raw_path.read_bytes()
        async def cancelled(*args, **kwargs):
            raise asyncio.CancelledError()
        with patch.object(inspection, 'inspect', cancelled):
            ack = jobs.start_probe_source_inspection(self.cycle['id'], self.probe_id, self.expected)
            jobs.worker.join(timeout=3)
        task = jobs.get_task(ack['id'])
        self.assertEqual(task['status'], 'stopped')
        self.assertIn('没有重新提问', task['message'])
        self.assertEqual(self.raw_path.read_bytes(), before)
        self.assertEqual(len(store.get_cycle(self.database, self.cycle['id'])['probes']), 1)

    def test_smoke_requires_actual_new_pywebview_method_even_when_other_capabilities_load(self):
        from desktop_app import run_smoke_check
        (self.root / 'logs').mkdir()
        observed = {'heading': '合成品牌', 'steps': ['品牌', '搜索基线', '内容', '发布', '反馈'],
            'primary_action': True, 'native_bridge': True, 'destination_recheck_bridge': True,
            'automatic_web_bridge': True, 'draft_recovery_bridge': True, 'probe_source_inspection_bridge': False}
        window = SimpleNamespace(evaluate_js=Mock(return_value=json.dumps(observed)), destroy=Mock())
        with patch('desktop_app.time.sleep'), self.assertLogs(level='ERROR'):
            run_smoke_check(window, self.root, 'http://127.0.0.1:5418/#/journey?desktop=1')
        result = json.loads((self.root / 'logs/smoke-test.json').read_text(encoding='utf-8'))
        self.assertFalse(result['passed'])
        self.assertFalse(result['last_journey_observed']['probe_source_inspection_bridge'])
        self.assertNotIn('journey_verified', result)

    def test_native_serial_busy_ack_original_slot_and_single_invocation(self):
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        entered, finish = threading.Event(), threading.Event()
        calls = []
        async def inspect(*args, **kwargs):
            calls.append(args)
            entered.set()
            while not finish.is_set():
                await asyncio.sleep(.005)
            return {'message': '来源补采完成，没有重新提问'}
        with patch.object(inspection, 'inspect', inspect), \
             patch('services.desktop_geo_jobs.collect_doubao_sources', side_effect=AssertionError('must never send')):
            ack = jobs.start_probe_source_inspection(self.cycle['id'], self.probe_id, self.expected)
            self.assertTrue(entered.wait(3))
            self.assertEqual((ack['kind'], ack['target_id'], ack['session_slot']),
                             ('probe_source_inspection', self.probe_id, 'primary'))
            self.assertIn('error', jobs.start_probe_source_inspection(self.cycle['id'], self.probe_id, self.expected))
            finish.set()
            jobs.worker.join(timeout=3)
        self.assertEqual(len(calls), 1)
        self.assertEqual(jobs.get_task(ack['id'])['status'], 'completed')
        self.assertEqual(len(store.get_cycle(self.database, self.cycle['id'])['probes']), 1)

    def test_interrupted_source_only_task_is_not_restarted_or_called_a_new_question(self):
        task_id = 'f' * 32
        write_json(self.root / 'desktop-jobs' / (task_id + '.json'), {'id': task_id,
            'kind': 'probe_source_inspection', 'status': 'running', 'cycle_id': self.cycle['id'],
            'target_id': self.probe_id, 'session_slot': 'primary', 'created_at': '2030-01-01T00:00:00Z'})
        with patch.object(inspection, 'inspect', side_effect=AssertionError('must not restart')):
            jobs = DesktopGeoJobs(self.root)
            self.addCleanup(jobs.shutdown)
            restored = jobs.get_task(task_id)
        self.assertEqual(restored['status'], 'needs_review')
        self.assertFalse(restored['question_sent'])
        self.assertIn('不会重新提问或自动重复补采', restored['message'])
        self.assertIsNone(jobs.worker)


if __name__ == '__main__':
    unittest.main()
