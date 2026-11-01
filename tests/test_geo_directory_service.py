"""Isolated directory application state transitions; no external requests."""
import copy
import json
import sqlite3
import unittest
from contextlib import closing

from services import geo_directory_service as directory, geo_content_store as store, geo_destination_service as destinations
from services.geo_content_research import plan_from_report
from services.geo_test_service import plan_fingerprint
from tests.test_geo_content_loop import ResearchFixture
from tests.test_geo_destinations import config


class DirectoryFixture(ResearchFixture):
    def setUp(self):
        super().setUp()
        self.target = destinations.save_target(self.database, self.cycle['project_id'], config(
            name='合成API目录', home_url=directory.LIST_URL, source_scope=directory.LIST_URL,
            editor_url=directory.ENTRY_URL, login_url=directory.ENTRY_URL,
            title_selector='', body_selector='', publication_use='commercial'))
        self.cycle_id = self.cycle['id']
        self.fields = {'name': self.cycle['profile']['name'] + ' API', 'url': self.cycle['profile']['website'],
            'rate': '公开按量计费，请以控制台为准', 'source': '运营方确认公开资料 ' + self.cycle['profile']['website'].rstrip('/') + '/about',
            'models': ['GLM'], 'contact': '', 'notes': ''}

    def save(self, **changes):
        overview = directory.get_overview(self.database, self.cycle_id, self.target['id'])
        return directory.save_application(self.database, self.cycle_id, {
            'target_id': self.target['id'], 'expected_context_sha256': overview['context_sha256'],
            'fields': copy.deepcopy(self.fields), **changes})

    def review(self, record):
        return directory.review_application(self.database, self.cycle_id, record['id'], {
            'expected_revision': record['revision'], 'expected_review_sha256': record['review_sha256'], 'confirmed': True})

    def session(self):
        reviewed = self.review(self.save())
        record = directory.reserve_session(self.database, self.cycle_id, reviewed['id'], reviewed['review_sha256'], reviewed['open_nonce'])
        directory.update_session(self.database, self.cycle_id, record['id'], record['session_nonce'],
                                 stage='awaiting_human_confirmation', window_open=True)
        return record

    def external(self, record, **changes):
        return directory.register_external_receipt(self.database, self.cycle_id, record['id'], {
            'expected_revision': record['revision'], 'expected_review_sha256': record['review_sha256'],
            'confirmed': True, 'status': 'submitted_pending_review', 'intake_uid': 'lead-fixture-01',
            'observed_at': '2026-01-01T09:12+08:00', 'observed_at_precision': 'minute',
            'note': '本人在平台操作并看到待审核回执，未核实公开收录', **changes})

    def other_cycle(self, name=None):
        baseline = copy.deepcopy(self.baseline)
        if name:
            baseline['profile']['name'] = name
            plan = plan_from_report(self.baseline)
            plan['profile'] = baseline['profile']
            baseline['plan_sha256'] = plan_fingerprint(plan)
        baseline['run_id'] = 'another-synthetic-baseline'
        return store.create_cycle(self.database, baseline, 'other-synthetic-report')


class DirectoryServiceTests(DirectoryFixture, unittest.TestCase):
    def test_get_does_not_migrate_and_only_one_new_table_is_added_on_explicit_save(self):
        with closing(sqlite3.connect(self.database)) as c:
            before = c.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        view = directory.get_overview(self.database, self.cycle_id, self.target['id'])
        self.assertTrue(view['supported'])
        self.assertEqual(view['applications'], [])
        with closing(sqlite3.connect(self.database)) as c:
            self.assertEqual(before, c.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall())
        self.save()
        with closing(sqlite3.connect(self.database)) as c:
            for kind, name, sql in before:
                self.assertEqual(c.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', (kind, name)).fetchone()[0], sql)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM geo_directory_applications').fetchone()[0], 1)
        self.assertFalse(destinations.get_target(self.database, self.cycle['project_id'], self.target['id'])['auto_publish_verified'])

    def test_exact_public_seven_fields_reject_private_material_and_other_customer_urls(self):
        self.assertTrue(self.save()['can_edit'])
        record = directory.get_overview(self.database, self.cycle_id, self.target['id'])['applications'][0]
        for key, value in [('notes', '成本价：0.35元'), ('notes', r'C:\private\sheet.xlsx'), ('notes', '=A1*B2'),
                           ('contact', 'Bearer syntheticsecretvalue'), ('source', 'https://different.example/'),
                           ('source', 'https://example.org/docs?api_key=syntheticsecret'), ('url', 'https://different.example/')]:
            fields = {**self.fields, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.save(application_id=record['id'], revision=record['revision'], fields=fields)
        with self.assertRaises(ValueError):
            self.save(fields={**self.fields, 'cost': 'private'})

    def test_normal_public_api_key_management_and_cost_description_are_not_private_values(self):
        fields = {**self.fields, 'contact': '+86 123456789 或 @publichandle',
                  'notes': '提供API密钥管理、创建API Key、供应商路由，降低接入成本；上游模型提供能力。'}
        self.assertEqual(self.save(fields=fields)['fields'], fields)

    def test_edit_revokes_review_and_old_nonce_cannot_start_or_replay(self):
        record = self.review(self.save())
        edited = self.save(application_id=record['id'], revision=record['revision'], fields={**self.fields, 'notes': '新增已确认公开说明'})
        self.assertFalse(edited['reviewed'])
        self.assertIsNone(edited['open_nonce'])
        with self.assertRaises(ValueError):
            directory.reserve_session(self.database, self.cycle_id, record['id'], record['review_sha256'], record['open_nonce'])
        record = self.review(edited)
        directory.reserve_session(self.database, self.cycle_id, record['id'], record['review_sha256'], record['open_nonce'])
        with self.assertRaises(ValueError):
            directory.reserve_session(self.database, self.cycle_id, record['id'], record['review_sha256'], record['open_nonce'])

    def test_target_revision_and_project_identity_changes_block_send(self):
        record = self.session()
        destinations.save_target(self.database, self.cycle['project_id'], {**self.target['config'], 'name': '改名目录'},
                                 self.target['id'], self.target['revision'])
        with self.assertRaisesRegex(ValueError, '版本已变化'):
            directory.begin_submit(self.database, self.cycle_id, record['id'], record['session_nonce'], directory.expected_transport(record['fields']))
        self.assertFalse(directory.get_application(self.database, self.cycle_id, record['id'])['submit_attempted'])

    def test_normal_positive_zero_post_close_can_review_same_frozen_fields_again(self):
        record = self.session()
        closed = directory.close_session(self.database, self.cycle_id, record['id'], record['session_nonce'],
            no_submission_evidence={'normal_close_confirmed': True, 'route_guard_installed': True, 'allowed_posts': 0, 'blocked_writes': 0})
        self.assertEqual(closed['stage'], 'closed_not_submitted')
        self.assertFalse(closed['can_edit'])
        self.assertTrue(closed['can_review'])
        reviewed = self.review(closed)
        self.assertTrue(reviewed['can_open'])
        self.assertEqual(reviewed['fields'], record['fields'])
        self.assertEqual(reviewed['review_sha256'], record['review_sha256'])

    def test_crash_or_missing_positive_evidence_zero_post_never_unlocks(self):
        record = self.session()
        closed = directory.close_session(self.database, self.cycle_id, record['id'], record['session_nonce'])
        self.assertEqual(closed['stage'], 'submission_unknown')
        with self.assertRaises(ValueError):
            self.review(closed)
        with self.assertRaises(ValueError):
            self.save()
        self.assertTrue(closed['can_inspect'])

    def test_durable_intent_survives_close_and_rejects_second_post_and_modified_payload(self):
        record = self.session()
        body = directory.expected_transport(record['fields'])
        with self.assertRaises(ValueError):
            directory.begin_submit(self.database, self.cycle_id, record['id'], record['session_nonce'], {**body, 'contact': 'changed'})
        pending = directory.begin_submit(self.database, self.cycle_id, record['id'], record['session_nonce'], body)
        self.assertTrue(pending['submit_attempted'])
        self.assertEqual(pending['submit_intent']['transport_sha256'], directory.digest(body))
        with self.assertRaises(ValueError):
            directory.begin_submit(self.database, self.cycle_id, record['id'], record['session_nonce'], body)
        closed = directory.close_session(self.database, self.cycle_id, record['id'], record['session_nonce'],
            no_submission_evidence={'normal_close_confirmed': True, 'route_guard_installed': True, 'allowed_posts': 0, 'blocked_writes': 0})
        self.assertEqual(closed['stage'], 'submission_unknown')
        self.assertFalse(closed['can_review'])

    def test_only_matching_persisted_receipt_is_pending_never_public_or_geo_effect(self):
        record = self.session()
        directory.begin_submit(self.database, self.cycle_id, record['id'], record['session_nonce'], directory.expected_transport(record['fields']))
        for body, status in [({'ok': True, 'intake_uid': 'lead-fixture-02'}, 200),
                             ({'ok': True, 'persisted': True, 'intake_uid': 'lead-fixture-02'}, 500),
                             ({'ok': True, 'persisted': True, 'intake_uid': 2100208460726116757}, 200)]:
            with self.assertRaises(ValueError):
                directory.record_receipt(self.database, self.cycle_id, record['id'], record['session_nonce'], body, status)
        pending = directory.record_receipt(self.database, self.cycle_id, record['id'], record['session_nonce'],
            {'ok': True, 'persisted': True, 'intake_uid': 'lead-fixture-02'}, 200)
        self.assertEqual(pending['stage'], 'submitted_pending_review')
        self.assertEqual(pending['evidence_level'], 'native_observed_receipt')
        self.assertFalse(pending['geo_effect_evidence'])
        self.assertFalse(pending['receipt']['public_listing_verified'])
        closed = directory.close_session(self.database, self.cycle_id, record['id'], record['session_nonce'])
        self.assertEqual(closed['stage'], 'submitted_pending_review')
        with self.assertRaises(ValueError):
            self.review(closed)

    def test_external_owner_receipt_keeps_true_minute_precision_and_never_claims_native(self):
        record = self.save()
        registered = self.external(record)
        self.assertEqual(registered['receipt']['observed_at'], '2026-01-01T09:12+08:00')
        self.assertEqual(registered['receipt']['observed_at_precision'], 'minute')
        self.assertEqual(registered['source'], 'operator_assisted')
        self.assertEqual(registered['evidence_level'], 'owner_reported')
        self.assertFalse(registered['submit_attempted'])
        self.assertNotIn('evidence_sha256', registered['receipt'])
        self.assertFalse(registered['can_open'])
        with self.assertRaises(ValueError):
            self.save()

    def test_http_cannot_inject_close_evidence_or_modify_frozen_scope(self):
        record = self.save()
        with self.assertRaises(ValueError):
            self.external(record, no_submission_evidence={'allowed_posts': 0})
        with self.assertRaises(ValueError):
            directory.review_application(self.database, self.cycle_id, record['id'], {
                'expected_revision': record['revision'], 'expected_review_sha256': record['review_sha256'],
                'confirmed': True, 'stage': 'closed_not_submitted'})

    def test_restart_zero_intent_is_unknown_and_stale_callbacks_cannot_unlock(self):
        record = self.session()
        restored = directory.recover_interrupted(self.database, self.cycle_id, record['id'])
        self.assertEqual(restored['stage'], 'submission_unknown')
        self.assertFalse(restored['can_review'])
        with self.assertRaises(ValueError):
            directory.close_session(self.database, self.cycle_id, record['id'], record['session_nonce'],
                no_submission_evidence={'normal_close_confirmed': True, 'route_guard_installed': True, 'allowed_posts': 0, 'blocked_writes': 0})

    def test_boolean_false_is_not_typed_positive_zero_post_evidence(self):
        record = self.session()
        closed = directory.close_session(self.database, self.cycle_id, record['id'], record['session_nonce'],
            no_submission_evidence={'normal_close_confirmed': True, 'route_guard_installed': True, 'allowed_posts': False, 'blocked_writes': False})
        self.assertEqual(closed['stage'], 'submission_unknown')

    def test_never_opened_stale_target_can_refresh_explicitly_and_preserve_old_snapshot(self):
        record = self.review(self.save())
        self.target = destinations.save_target(self.database, self.cycle['project_id'], {**self.target['config'], 'name': '新版合成目录'},
                                               self.target['id'], self.target['revision'])
        stale = directory.get_application(self.database, self.cycle_id, record['id'])
        self.assertFalse(stale['can_open'])
        self.assertTrue(stale['can_refresh_context'])
        overview = directory.get_overview(self.database, self.cycle_id, self.target['id'])
        fresh = directory.refresh_context(self.database, self.cycle_id, record['id'], {
            'expected_revision': stale['revision'], 'expected_review_sha256': stale['review_sha256'],
            'expected_context_sha256': overview['context_sha256'], 'confirmed': True})
        self.assertEqual(fresh['stage'], 'draft')
        self.assertFalse(fresh['reviewed'])
        self.assertIsNone(fresh['open_nonce'])
        self.assertEqual(fresh['history'][0]['review_sha256'], record['review_sha256'])
        self.assertEqual(fresh['history'][0]['fields'], record['fields'])
        self.assertTrue(self.review(fresh)['can_open'])
        with self.assertRaises(ValueError):
            directory.reserve_session(self.database, self.cycle_id, record['id'], record['review_sha256'], record['open_nonce'])

    def test_unknown_cannot_use_refresh_context_or_new_cycle_to_bypass_lock(self):
        record = self.session()
        unknown = directory.close_session(self.database, self.cycle_id, record['id'], record['session_nonce'])
        with self.assertRaises(ValueError):
            directory.refresh_context(self.database, self.cycle_id, record['id'], {
                'expected_revision': unknown['revision'], 'expected_review_sha256': unknown['review_sha256'],
                'expected_context_sha256': 'changed', 'confirmed': True})
        later = self.other_cycle()
        view = directory.get_application(self.database, later['id'], record['id'])
        self.assertFalse(view['can_review'])
        self.assertFalse(view['can_edit'])
        self.assertFalse(view['can_refresh_context'])
        overview = directory.get_overview(self.database, later['id'], self.target['id'])
        with self.assertRaises(ValueError):
            directory.save_application(self.database, later['id'], {'target_id': self.target['id'],
                'expected_context_sha256': overview['context_sha256'], 'fields': self.fields})

    def test_fixed_model_enum_uses_observed_single_label_transform_and_omits_empty_optional_values(self):
        for model in directory.MODEL_OPTIONS:
            fields = {**self.fields, 'models': [model]}
            self.assertEqual(directory.validate_fields(fields, self.cycle['profile']), fields)
            wire = directory.expected_transport(fields)
            self.assertEqual(wire['product_focus'], model)
            self.assertIn('模型：' + model + '\n来源页：', wire['description'])
            self.assertNotIn('contact', wire)
            self.assertNotIn('\n\n', wire['description'])
        for models in ([], ['GLM', 'GPT'], ['unknown'], 'GLM'):
            with self.assertRaises(ValueError):
                directory.validate_fields({**self.fields, 'models': models}, self.cycle['profile'])

    def test_another_project_cannot_read_or_change_an_existing_application(self):
        record = self.save()
        other = self.other_cycle('另一合成品牌')
        with self.assertRaises(ValueError):
            directory.get_application(self.database, other['id'], record['id'])

    def test_receipt_uid_cannot_be_reused_across_customers_and_bad_precision_does_not_mutate(self):
        record = self.save()
        for changes in ({'observed_at': '2026-01-01T09:12:00+08:00'},
                        {'observed_at': '2026-01-01T09:12', 'observed_at_precision': 'minute'},
                        {'observed_at': '2026-01-01T09:12+08:00', 'observed_at_precision': 'second'},
                        {'observed_at': '2099-01-01T09:12+08:00'}, {'evidence_sha256': 'invented'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.external(record, **changes)
        self.assertEqual(directory.get_application(self.database, self.cycle_id, record['id']), record)
        self.external(record)
        other = self.other_cycle('另一合成品牌')
        target = destinations.save_target(self.database, other['project_id'], self.target['config'])
        view = directory.get_overview(self.database, other['id'], target['id'])
        second = directory.save_application(self.database, other['id'], {'target_id': target['id'],
            'expected_context_sha256': view['context_sha256'], 'fields': {**self.fields, 'name': other['profile']['name'] + ' API'}})
        with self.assertRaisesRegex(ValueError, '另一份申请'):
            directory.register_external_receipt(self.database, other['id'], second['id'], {
                'expected_revision': second['revision'], 'expected_review_sha256': second['review_sha256'],
                'confirmed': True, 'status': 'submitted_pending_review', 'intake_uid': 'lead-fixture-01',
                'observed_at': '2026-01-01T09:12+08:00', 'observed_at_precision': 'minute', 'note': '合成回执'})

    def test_concurrent_submit_callbacks_only_one_can_commit_intent(self):
        from concurrent.futures import ThreadPoolExecutor
        record = self.session()
        def attempt():
            try:
                directory.begin_submit(self.database, self.cycle_id, record['id'], record['session_nonce'], directory.expected_transport(record['fields']))
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            accepted = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(accepted.count(True), 1)
        self.assertTrue(directory.get_application(self.database, self.cycle_id, record['id'])['submit_attempted'])

    def test_disabled_or_stale_context_keeps_owner_receipt_visible_and_read_only(self):
        from unittest.mock import patch
        from services.geo_external_baseline import ExternalBaselineConflict
        registered = self.external(self.save())
        destinations.save_target(self.database, self.cycle['project_id'], {**self.target['config'], 'enabled': False},
                                 self.target['id'], self.target['revision'])
        view = directory.get_overview(self.database, self.cycle_id, self.target['id'])
        self.assertFalse(view['supported'])
        historical = view['applications'][0]
        self.assertEqual(historical['receipt'], registered['receipt'])
        self.assertTrue(historical['can_inspect'])
        self.assertFalse(historical['can_open'])
        self.assertFalse(historical['current_context'])
        self.assertTrue(historical['context_reason'])
        with patch.object(directory, '_context', side_effect=ExternalBaselineConflict('Synthetic stale current project')):
            view = directory.get_overview(self.database, self.cycle_id, self.target['id'])
            self.assertEqual(view['applications'][0]['receipt'], registered['receipt'])
            self.assertFalse(view['supported'])
            self.assertEqual(directory.get_application(self.database, self.cycle_id, registered['id'])['receipt'], registered['receipt'])

    def test_restart_after_receipt_only_clears_owned_window_and_keeps_pending_evidence_and_lock(self):
        record = self.session()
        directory.begin_submit(self.database, self.cycle_id, record['id'], record['session_nonce'], directory.expected_transport(record['fields']))
        pending = directory.record_receipt(self.database, self.cycle_id, record['id'], record['session_nonce'],
            {'ok': True, 'persisted': True, 'intake_uid': 'lead-restart-fixture'}, 200)
        self.assertTrue(pending['window_open'])
        restored = directory.recover_interrupted(self.database, self.cycle_id, record['id'])
        self.assertFalse(restored['window_open'])
        for key in ('stage', 'receipt', 'source', 'evidence_level', 'submit_intent', 'submit_attempted', 'fields', 'review_sha256'):
            self.assertEqual(restored[key], pending[key], key)
        self.assertFalse(restored['can_open'])
        self.assertTrue(restored['can_inspect'])

    def test_an_unopened_draft_cannot_be_rebound_to_another_target_through_save(self):
        record = self.review(self.save())
        second = destinations.save_target(self.database, self.cycle['project_id'], {**self.target['config'], 'name': '另一合成目录目标'})
        view = directory.get_overview(self.database, self.cycle_id, second['id'])
        self.assertEqual(view['applications'][0]['id'], record['id'])
        with self.assertRaisesRegex(ValueError, '绑定原目标'):
            directory.save_application(self.database, self.cycle_id, {'target_id': second['id'],
                'expected_context_sha256': view['context_sha256'], 'fields': self.fields,
                'application_id': record['id'], 'revision': record['revision']})
        self.assertEqual(directory.get_application(self.database, self.cycle_id, record['id']), record)

    def test_existing_research_unsuitable_unknown_or_editorial_use_cannot_be_bypassed(self):
        record = self.save()
        for changes in ({'publication_use': 'research_only'}, {'publication_use': 'editorial'},
                        {'rule_review': {'state': 'unsuitable', 'operator_confirmed': True, 'note': '合成不适合'}},
                        {'rule_review': {'state': 'unknown', 'operator_confirmed': False, 'note': ''}}):
            with self.subTest(changes=changes):
                self.target = destinations.save_target(self.database, self.cycle['project_id'],
                    {**self.target['config'], 'publication_use': 'commercial',
                     'rule_review': {'state': 'suitable', 'operator_confirmed': True, 'note': 'Synthetic review'}, **changes},
                    self.target['id'], self.target['revision'])
                view = directory.get_overview(self.database, self.cycle_id, self.target['id'])
                self.assertFalse(view['supported'])
                self.assertEqual(view['applications'][0]['id'], record['id'])
                self.assertTrue(view['applications'][0]['can_inspect'])
                self.assertFalse(view['applications'][0]['can_review'])
                with self.assertRaises(ValueError):
                    directory.save_application(self.database, self.cycle_id, {'target_id': self.target['id'],
                        'expected_context_sha256': view['context_sha256'], 'fields': self.fields})

    def test_controlled_zero_submit_close_can_refresh_target_revision_with_frozen_fields_and_close_history(self):
        record = self.session()
        closed = directory.close_session(self.database, self.cycle_id, record['id'], record['session_nonce'],
            no_submission_evidence={'normal_close_confirmed': True, 'route_guard_installed': True, 'allowed_posts': 0, 'blocked_writes': 0})
        self.target = destinations.save_target(self.database, self.cycle['project_id'], {**self.target['config'], 'name': '受控关闭后新版目录'},
                                               self.target['id'], self.target['revision'])
        stale = directory.get_application(self.database, self.cycle_id, record['id'])
        view = directory.get_overview(self.database, self.cycle_id, self.target['id'])
        self.assertTrue(stale['can_refresh_context'])
        data = {'expected_revision': stale['revision'], 'expected_review_sha256': stale['review_sha256'],
                'expected_context_sha256': view['context_sha256'], 'confirmed': True}
        with self.assertRaisesRegex(ValueError, '逐字冻结'):
            directory.refresh_context(self.database, self.cycle_id, record['id'], {**data, 'fields': {**self.fields, 'notes': '禁止改写'}})
        self.assertEqual(directory.get_application(self.database, self.cycle_id, record['id']), stale)
        fresh = directory.refresh_context(self.database, self.cycle_id, record['id'], data)
        self.assertEqual(fresh['fields'], closed['fields'])
        self.assertEqual(fresh['session_count'], closed['session_count'])
        self.assertFalse(fresh['can_edit'])
        self.assertIsNone(fresh['open_nonce'])
        self.assertEqual(fresh['history'][-1]['review_sha256'], closed['review_sha256'])
        self.assertEqual(fresh['history'][-1]['controlled_closed_at'], closed['controlled_closed_at'])
        self.assertEqual(fresh['history'][-1]['controlled_close_evidence'],
                         {'normal_close_confirmed': True, 'route_guard_installed': True, 'allowed_posts': 0, 'blocked_writes': 0})
        reviewed = self.review(fresh)
        self.assertTrue(reviewed['can_open'])
        self.assertFalse(reviewed['can_edit'])
        reserved = directory.reserve_session(self.database, self.cycle_id, record['id'], reviewed['review_sha256'], reviewed['open_nonce'])
        self.assertEqual(reserved['session_count'], closed['session_count'] + 1)
        self.assertNotIn('no_submission_evidence', reserved)
        self.assertNotIn('controlled_closed_at', reserved)

    def test_unknown_cannot_refresh_even_after_target_revision_changes(self):
        record = self.session()
        unknown = directory.close_session(self.database, self.cycle_id, record['id'], record['session_nonce'])
        self.target = destinations.save_target(self.database, self.cycle['project_id'], {**self.target['config'], 'name': '未知状态后新版目录'},
                                               self.target['id'], self.target['revision'])
        view = directory.get_overview(self.database, self.cycle_id, self.target['id'])
        self.assertFalse(view['applications'][0]['can_refresh_context'])
        with self.assertRaises(ValueError):
            directory.refresh_context(self.database, self.cycle_id, record['id'], {
                'expected_revision': unknown['revision'], 'expected_review_sha256': unknown['review_sha256'],
                'expected_context_sha256': view['context_sha256'], 'confirmed': True})
        self.assertEqual(directory.get_application(self.database, self.cycle_id, record['id'])['stage'], 'submission_unknown')

    def test_owner_receipt_cannot_refresh_after_target_revision_changes(self):
        pending = self.external(self.save())
        self.target = destinations.save_target(self.database, self.cycle['project_id'], {**self.target['config'], 'name': '既有回执后新版目录'},
                                               self.target['id'], self.target['revision'])
        view = directory.get_overview(self.database, self.cycle_id, self.target['id'])
        self.assertFalse(view['applications'][0]['can_refresh_context'])
        with self.assertRaises(ValueError):
            directory.refresh_context(self.database, self.cycle_id, pending['id'], {
                'expected_revision': pending['revision'], 'expected_review_sha256': pending['review_sha256'],
                'expected_context_sha256': view['context_sha256'], 'confirmed': True})
        after = directory.get_application(self.database, self.cycle_id, pending['id'])
        self.assertEqual(after['receipt'], pending['receipt'])
        self.assertEqual(after['stage'], pending['stage'])


if __name__ == '__main__':
    unittest.main()
