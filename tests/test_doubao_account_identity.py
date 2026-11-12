"""Synthetic official-visible-handle evidence only; no live browser or data."""
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone

from services.doubao_account_identity import (
    HASH_PREFIX, AccountObservedResult, bind_probe_handle, bound_result_handle,
    compare_visible_handles, observe_visible_handle, result_handle_changed,
)


class DoubaoAccountIdentityTests(unittest.TestCase):
    def observe(self, label='合成昵称 豆包号：user_AbC123', **changes):
        options = dict(page_url='https://www.doubao.com/chat/123?from_login=1',
            settings_group_name='设置分类', control_role='button', visible=True,
            session_slot='secondary', context_id='a' * 32, profile_scope_sha256='b' * 64,
            observed_at=datetime(2026, 11, 12, 4, tzinfo=timezone.utc))
        options.update(changes)
        return observe_visible_handle(label, **options)

    def pair(self, **changes):
        options = dict(probe_id='c' * 32)
        options.update(changes)
        return (self.observe(stage='before_submission', **options),
                self.observe(stage='after_answer', **options))

    def bind(self, before, after, **changes):
        options = dict(probe_id='c' * 32, session_slot='secondary', context_id='a' * 32,
                       profile_scope_sha256='b' * 64, source='native_collector')
        options.update(changes)
        return bind_probe_handle(before, after, **options)

    def test_visible_labeled_handle_uses_exact_domain_separated_case_sensitive_hash(self):
        value = self.observe().safe_record()
        expected = hashlib.sha256(b'doubao-visible-handle:v1:user_AbC123').hexdigest()
        self.assertEqual(HASH_PREFIX, 'doubao-visible-handle:v1:')
        self.assertEqual(value['handle_sha256'], expected)
        self.assertEqual(value['identifier_kind'], 'official_visible_handle')
        self.assertEqual(value['identifier_stability'], 'not_established')
        self.assertEqual(value['official_origin'], 'https://www.doubao.com')
        self.assertEqual(self.observe('另一个昵称\n豆包号: user_AbC123 \n').handle_sha256, expected)
        self.assertNotEqual(self.observe('合成昵称 豆包号：user_abc123').handle_sha256, expected)

    def test_nickname_avatar_masked_phone_and_ambiguous_labels_are_not_identity(self):
        for label in ('合成昵称', '头像', '138****1234', '用户ID：123456', 'user_AbC123',
                      '昵称user_AbC123', '豆包号：138****1234', '豆包号：user_a 豆包号：user_b',
                      '豆包号：user_AbC123 其他内容', '豆包号：user_AbC123/path',
                      '豆包号：user_ＡbC123', '豆包号：USER_AbC123', '豆包号：user_', None):
            with self.subTest(label=label):
                self.assertIsNone(self.observe(label))

    def test_only_visible_button_in_exact_official_settings_group_is_eligible(self):
        for changes in ({'visible': False}, {'visible': 1}, {'settings_group_name': '聊天'},
                        {'settings_group_name': '个人资料'}, {'control_role': 'text'},
                        {'control_role': 'Button'}, {'settings_group_name': None}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.observe(**changes))

    def test_official_https_chat_origin_rejects_spoofs_credentials_and_other_pages(self):
        for url in ('http://www.doubao.com/chat/', 'https://www.doubao.com.evil.test/chat/',
                    'https://evil.test/?next=https://www.doubao.com/chat/',
                    'https://person:secret@www.doubao.com/chat/', 'https://www.doubao.com:444/chat/',
                    'https://www.doubao.com/chat-fake/', 'https://www.doubao.com/signin',
                    'file:///www.doubao.com/chat/', 'https://www.doubao.com:bad/chat/',
                    'https://www.doubao.com/\nchat/', None):
            with self.subTest(url=url):
                self.assertIsNone(self.observe(page_url=url))
        self.assertIsNotNone(self.observe(page_url='https://doubao.com:443/chat'))

    def test_observation_repr_and_safe_json_never_retain_label_handle_phone_or_chat_path(self):
        label = '合成昵称 138****1234 豆包号：user_AbC123'
        observation = self.observe(label)
        payload = json.dumps(observation.safe_record(), ensure_ascii=False) + repr(observation)
        for secret in (label, '合成昵称', '138****1234', 'user_AbC123', '/chat/123', 'from_login'):
            self.assertNotIn(secret, payload)

    def test_only_paired_new_native_probe_observations_produce_bound_evidence(self):
        before, after = self.pair()
        evidence = self.bind(before, after)
        self.assertEqual(evidence['account_identity_status'], 'official_visible_handle_observed')
        self.assertEqual(evidence['probe_id'], 'c' * 32)
        self.assertEqual([item['stage'] for item in evidence['observations']],
                         ['before_submission', 'after_answer'])
        self.assertEqual(evidence['handle_sha256'], before.handle_sha256)
        self.assertNotIn('account_independence_verified', evidence)
        self.assertNotIn('geo_success', evidence)

    def test_frontend_import_dicts_missing_observations_and_login_evidence_cannot_bind_probe(self):
        before, after = self.pair()
        for left, right, changes in ((before.safe_record(), after.safe_record(), {}),
                                    (None, after, {}), (self.observe(), after, {}),
                                    (before, after, {'source': 'research_prototype_import'}),
                                    (before, after, {'source': 'user_reported_external'})):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.bind(left, right, **changes)

    def test_changed_identity_context_profile_slot_probe_or_order_is_not_bound(self):
        before, after = self.pair()
        changed = [self.observe(stage='after_answer', probe_id='c' * 32, **options) for options in (
            {'context_id': 'd' * 32}, {'profile_scope_sha256': 'e' * 64}, {'session_slot': 'primary'},
            {'observed_at': datetime(2026, 11, 12, 3, tzinfo=timezone.utc)})]
        changed.append(self.observe('合成昵称 豆包号：user_different', stage='after_answer', probe_id='c' * 32))
        changed.append(self.observe(stage='after_answer', probe_id='f' * 32))
        for observation in changed:
            with self.subTest(observation=observation):
                with self.assertRaises(ValueError):
                    self.bind(before, observation)
        with self.assertRaises(ValueError):
            self.bind(after, before)
        with self.assertRaises(ValueError):
            self.bind(before, after, probe_id='f' * 32)

    def test_compare_describes_visible_handles_without_promoting_historical_or_geo_claims(self):
        first = self.observe(session_slot='primary')
        second = self.observe(session_slot='secondary')
        other = self.observe('同一昵称 豆包号：user_Other123')
        self.assertEqual(compare_visible_handles(first, second)['relation'], 'same')
        self.assertEqual(compare_visible_handles(first, other)['relation'], 'different')
        self.assertEqual(compare_visible_handles(first.safe_record(), other.safe_record())['relation'], 'unverified')
        self.assertEqual(compare_visible_handles(None, other)['relation'], 'unverified')
        self.assertEqual(set(compare_visible_handles(first, other)),
                         {'identifier_kind', 'relation', 'identifier_stability'})

    def test_scope_and_timestamp_validation_is_closed_and_never_echoes_raw_text(self):
        for changes in ({'session_slot': 'work'}, {'session_slot': {}}, {'context_id': 'bad'},
                        {'profile_scope_sha256': '../private'}, {'stage': 'imported'},
                        {'stage': []}, {'probe_id': 'c' * 32}, {'observed_at': datetime(2026, 11, 12)}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError) as caught:
                    self.observe(**changes)
                self.assertNotIn('user_AbC123', str(caught.exception))

    def test_observation_time_is_normalized_to_utc_for_cross_timezone_order_comparison(self):
        time = datetime(2026, 11, 12, 12, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(self.observe(observed_at=time).observed_at, '2026-11-12T04:00:00+00:00')
        before = self.observe(stage='before_submission', probe_id='c' * 32, observed_at=time)
        after = self.observe(stage='after_answer', probe_id='c' * 32,
                             observed_at=datetime(2026, 11, 12, 4, 1, tzinfo=timezone.utc))
        self.assertEqual(self.bind(before, after)['observations'][1]['observed_at'],
                         '2026-11-12T04:01:00+00:00')

    def observed_result(self):
        before, after = self.pair()
        return AccountObservedResult({
            'environment': {'client_mode': 'consumer', 'session_slot': 'secondary',
                            'account_identity_status': 'official_visible_handle_observed'},
            'account_identity_evidence': self.bind(before, after),
        }, account_observations=(before, after))

    def test_json_roundtrip_drops_native_observation_capability(self):
        result = self.observed_result()
        bound = bound_result_handle(result, probe_id='c' * 32, session_slot='secondary')
        self.assertEqual(bound, result['account_identity_evidence'])
        self.assertIsNot(bound, result['account_identity_evidence'])
        raw = json.dumps(result)
        self.assertNotIn('account_observations', raw)
        self.assertNotIn('_native', raw)
        self.assertIsNone(bound_result_handle(json.loads(raw), probe_id='c' * 32, session_slot='secondary'))
        self.assertIsNone(bound_result_handle(AccountObservedResult(json.loads(raw)),
                                             probe_id='c' * 32, session_slot='secondary'))

    def test_result_proof_requires_exact_safe_binding_and_current_consumer_scope(self):
        for mutate in (
            lambda value: value['account_identity_evidence'].update(handle_sha256='f' * 64),
            lambda value: value['account_identity_evidence'].update(extra_claim=True),
            lambda value: value['environment'].update(account_identity_status='unverified'),
            lambda value: value['environment'].update(client_mode='work'),
            lambda value: value['environment'].update(session_slot='primary'),
            lambda value: setattr(value, 'account_observations', tuple(
                item.safe_record() for item in value.account_observations)),
        ):
            value = self.observed_result()
            mutate(value)
            self.assertIsNone(bound_result_handle(value, probe_id='c' * 32, session_slot='secondary'))
        self.assertIsNone(bound_result_handle(self.observed_result(), probe_id='d' * 32, session_slot='secondary'))
        class OtherResult(AccountObservedResult):
            pass
        value = self.observed_result()
        self.assertIsNone(bound_result_handle(OtherResult(value, account_observations=value.account_observations),
                                             probe_id='c' * 32, session_slot='secondary'))

    def test_only_typed_same_context_changed_handles_explain_an_unverified_result(self):
        before, _ = self.pair()
        after = self.observe('另一昵称 豆包号：user_Changed', stage='after_answer', probe_id='c' * 32)
        value = AccountObservedResult({'environment': {'client_mode': 'consumer', 'session_slot': 'secondary',
            'account_identity_status': 'unverified'}}, account_observations=(before, after))
        self.assertTrue(result_handle_changed(value, probe_id='c' * 32, session_slot='secondary'))
        self.assertIsNone(bound_result_handle(value, probe_id='c' * 32, session_slot='secondary'))
        self.assertFalse(result_handle_changed(dict(value), probe_id='c' * 32, session_slot='secondary'))
        self.assertFalse(result_handle_changed(value, probe_id='d' * 32, session_slot='secondary'))
        value['account_identity_evidence'] = {'forged': True}
        self.assertFalse(result_handle_changed(value, probe_id='c' * 32, session_slot='secondary'))


if __name__ == '__main__':
    unittest.main()
