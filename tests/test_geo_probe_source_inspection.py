"""Source-only repair is hash bound, mocked, and never collects a new answer."""
import asyncio
import copy
import hashlib
import json
import os
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


class FakePage:
    def __init__(self, url):
        self.main_frame = SimpleNamespace(url=url)
        self.callbacks = []

    def on(self, event, callback):
        self.callbacks.append(callback)

    def remove_listener(self, event, callback):
        self.callbacks.remove(callback)

    def navigate(self, url):
        self.main_frame.url = url
        for callback in list(self.callbacks):
            callback(self.main_frame)


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
        yield FakePage(url)

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
            'evidence_manifest': [{'path': 'source-context.json', 'sha256': sha((directory / 'source-context.json').read_bytes()),
                                   'bytes': (directory / 'source-context.json').stat().st_size}],
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
    def test_actual_auth_overlay_field_names_block_an_otherwise_complete_target_answer(self):
        for change in ({'challenge': True}, {'login_required': True}, {'rate_limited': True},
                       {'website_temporarily_unavailable': True}, {'service_unavailable': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                inspection.verify_snapshot({**self.snapshot, **change}, self.details)

    def test_corrupted_optional_probe_and_after_do_not_break_read_only_cycle_projection(self):
        for row in (None, [], 'broken', {**self.probe, 'after': ['broken']}, {**self.probe, 'after': 'broken'}):
            with self.subTest(row=row):
                value = {**self.cycle, 'probes': [row]}
                self.assertEqual(inspection.project(self.root, value), [row])

    def test_whole_sidecar_manifest_and_all_source_evidence_references_are_required(self):
        self.run_inspection()
        result_path = next(self.config.output_root.glob('inspect-*/result.json'))
        binding_path = result_path.parent / 'binding.json'
        baseline = json.loads(result_path.read_text(encoding='utf-8'))
        png = result_path.parent / 'source-panel.png'
        png.write_bytes(b'original image evidence')
        baseline['evidence_manifest'][0]['bytes'] = (result_path.parent / 'source-context.json').stat().st_size
        baseline['evidence_manifest'].append({'path': png.name, 'bytes': png.stat().st_size, 'sha256': sha(png.read_bytes())})
        baseline['displayed_sources'][0]['evidence_file'] = png.name
        baseline['displayed_sources'][0]['evidence_files'] = [png.name]
        baseline['screenshots'] = [png.name]
        binding = json.loads(binding_path.read_text(encoding='utf-8'))
        for attack in ('missing', 'changed_same_size', 'duplicate', 'traversal', 'wrong_size', 'missing_reference'):
            current = copy.deepcopy(baseline)
            png.write_bytes(b'original image evidence')
            old_stat = png.stat()
            if attack == 'missing':
                png.unlink()
            elif attack == 'changed_same_size':
                png.write_bytes(b'changed! image evidence')
                os.utime(png, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
            elif attack == 'duplicate':
                current['evidence_manifest'].append(copy.deepcopy(current['evidence_manifest'][-1]))
            elif attack == 'traversal':
                current['evidence_manifest'][-1]['path'] = '../outside.png'
            elif attack == 'wrong_size':
                current['evidence_manifest'][-1]['bytes'] += 1
            else:
                current['citations'][0]['evidence_file'] = 'never-created.json'
            write_json(result_path, current)
            write_json(binding_path, {**binding, 'inspection_result_sha256': sha(result_path.read_bytes())})
            with self.subTest(attack=attack):
                self.assertNotIn('source_reinspection', store.get_cycle(self.database, self.cycle['id'])['probes'][0])

    def test_manifest_limits_bad_entries_and_unlisted_files_fail_closed(self):
        self.run_inspection()
        path = next(self.config.output_root.glob('inspect-*/result.json'))
        baseline = json.loads(path.read_text(encoding='utf-8'))
        binding_path = path.parent / 'binding.json'
        binding = json.loads(binding_path.read_text(encoding='utf-8'))
        entry = baseline['evidence_manifest'][0]
        attacks = [[], [entry] * 129, [{**entry, 'bytes': True}], [{**entry, 'bytes': inspection.MAX_EVIDENCE_FILE_BYTES + 1}],
            [{**entry, 'path': 'C:escape.png'}], [{**entry, 'path': '..\\escape.png'}],
            [entry, {**entry, 'path': entry['path'].upper()}], [{**entry, 'sha256': '0' * 63}],
            [{**entry, 'path': f'file-{i}.png', 'bytes': inspection.MAX_EVIDENCE_FILE_BYTES} for i in range(9)]]
        for manifest in attacks:
            current = {**baseline, 'evidence_manifest': manifest}
            write_json(path, current)
            write_json(binding_path, {**binding, 'inspection_result_sha256': sha(path.read_bytes())})
            with self.subTest(manifest=manifest):
                self.assertNotIn('source_reinspection', store.get_cycle(self.database, self.cycle['id'])['probes'][0])
        write_json(path, baseline)
        write_json(binding_path, {**binding, 'inspection_result_sha256': sha(path.read_bytes())})
        (path.parent / 'not-in-the-manifest.json').write_bytes(b'{}')
        self.assertNotIn('source_reinspection', store.get_cycle(self.database, self.cycle['id'])['probes'][0])

    def test_manifest_nonregular_and_reparse_evidence_are_rejected(self):
        self.run_inspection()
        path = next(self.config.output_root.glob('inspect-*/result.json'))
        result = json.loads(path.read_text(encoding='utf-8'))
        extra = path.parent / 'source-panel.png'
        extra.mkdir()
        result['evidence_manifest'].append({'path': extra.name, 'bytes': 1, 'sha256': '0' * 64})
        with self.assertRaises(ValueError):
            inspection._evidence_manifest(result, path, self.config.output_root)
        extra.rmdir()
        extra.write_bytes(b'X')
        result['evidence_manifest'][-1]['sha256'] = sha(b'X')
        lstat = Path.lstat
        def flagged(value, *args, **kwargs):
            info = lstat(value, *args, **kwargs)
            if value == extra:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return info
        with patch.object(Path, 'lstat', flagged), self.assertRaises(ValueError):
            inspection._evidence_manifest(result, path, self.config.output_root)

    def test_binding_revision_must_be_an_integer_equal_to_the_current_frozen_revision(self):
        self.run_inspection()
        path = next(self.config.output_root.glob('inspect-*/binding.json'))
        original = json.loads(path.read_text(encoding='utf-8'))
        for value in (True, str(self.cycle['revision']), self.cycle['revision'] + 1):
            write_json(path, {**original, 'revision': value})
            self.assertNotIn('source_reinspection', store.get_cycle(self.database, self.cycle['id'])['probes'][0])

    def test_read_only_hydration_wait_requires_two_matching_complete_snapshots(self):
        page, calls = FakePage(self.url), []
        loading = {**self.snapshot, 'question_verified': False, 'user_message_count': 0, 'assistant_count': 0,
                   'answer': '', 'completion_marker': False, 'model_label': None}
        states = [loading, loading, self.snapshot, self.snapshot]
        async def read(*args, **kwargs):
            calls.append('read')
            return states.pop(0)
        with patch.object(inspection, 'read_snapshot', read):
            identity = asyncio.run(inspection.wait_for_original_answer(page, self.config, self.details,
                timeout_seconds=.1, interval_seconds=0))
        self.assertEqual(identity['method'], 'exact_complete_response')
        self.assertEqual(calls, ['read'] * 4)
        self.assertEqual(page.callbacks, [])

    def test_readiness_latches_away_then_back_and_rejects_wrong_question_or_model(self):
        for attack in ('away_back', 'wrong_question', 'wrong_model'):
            page = FakePage(self.url)
            async def read(*args, **kwargs):
                snapshot = copy.deepcopy(self.snapshot)
                if attack == 'away_back':
                    page.navigate('https://www.doubao.com/chat/9999999')
                    page.navigate(self.url)
                elif attack == 'wrong_question':
                    snapshot['question_verified'] = False
                else:
                    snapshot['model_label'] = '另一个模型'
                return snapshot
            with self.subTest(attack=attack), patch.object(inspection, 'read_snapshot', read), self.assertRaises(ValueError):
                asyncio.run(inspection.wait_for_original_answer(page, self.config, self.details,
                    timeout_seconds=.1, interval_seconds=0))
            self.assertEqual(page.callbacks, [])

    def test_actual_captcha_keeps_target_body_but_never_calls_source_reader_and_timeout_is_bounded(self):
        self.snapshot['challenge'] = True
        with self.assertRaises(inspection.SourceInspectionNeedAction) as caught:
            self.run_inspection()
        self.assertEqual(caught.exception.reason, 'human_verification_required')
        self.assertFalse(any(row[0] == 'reinspect' for row in self.calls))
        loading = {**self.snapshot, 'challenge': False, 'user_message_count': 0, 'assistant_count': 0,
                   'question_verified': False, 'answer': '', 'completion_marker': False}
        async def read(*args, **kwargs):
            return loading
        with patch.object(inspection, 'read_snapshot', read), self.assertRaises(inspection.SourceInspectionNeedAction) as caught:
            asyncio.run(inspection.wait_for_original_answer(FakePage(self.url), self.config, self.details,
                timeout_seconds=0, interval_seconds=0))
        self.assertEqual(caught.exception.reason, 'original_answer_not_ready')

    def test_full_inspection_waits_for_history_hydration_then_calls_sources_once(self):
        states = [{**self.snapshot, 'user_message_count': 0, 'assistant_count': 0, 'answer': '',
                   'question_verified': False, 'completion_marker': False}, self.snapshot, self.snapshot, self.snapshot]
        async def read(*args, **kwargs):
            return copy.deepcopy(states.pop(0))
        with patch.object(inspection, 'open_original_page', self.page), \
             patch.object(inspection, 'read_snapshot', read), \
             patch.object(inspection, 'reinspect_existing_sources', self.collect):
            outcome = asyncio.run(inspection.inspect(self.root, self.cycle, self.details, self.config))
        self.assertTrue(outcome['source_reinspection']['source_collection_complete'])
        self.assertEqual(sum(row[0] == 'reinspect' for row in self.calls), 1)

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
    def test_auth_block_returns_native_need_action_without_claiming_a_new_submission(self):
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        async def blocked(*args, **kwargs):
            raise inspection.SourceInspectionNeedAction('human_verification_required')
        with patch.object(inspection, 'inspect', blocked):
            ack = jobs.start_probe_source_inspection(self.cycle['id'], self.probe_id, self.expected)
            jobs.worker.join(timeout=3)
        task = jobs.get_task(ack['id'])
        self.assertEqual(task['status'], 'need_action')
        self.assertFalse(task['question_sent'])
        self.assertEqual(task['submission_state'], {'question_submission_attempted': False, 'question_submitted': False})
        self.assertIn('没有重新提问', task['message'])
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
