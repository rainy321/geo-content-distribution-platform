"""Operator-only material acknowledgment through real Flask routes and fixture DBs."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
import sqlite3
import unittest
from unittest.mock import patch

from services import geo_content_store as store
from services import geo_research_probe_service as probes
from tests.test_geo_research_probes import ProbeFixture


class OperatorBusinessReviewTests(ProbeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.cycle = store.save_facts(self.database, self.cycle['id'], [
            {'text': '用户确认：官网公开介绍软件服务。', 'source_url': 'https://synthetic.example.org/about', 'confirmed': True},
            {'text': '用户确认：产品文档公开说明接口用途与边界。', 'source_url': 'https://synthetic.example.org/docs', 'confirmed': True},
        ], self.cycle['revision'])
        self.raw = '  已见工商资料。\r\n未找到公开AI业务材料。\n另有软件服务介绍，需核对。\r\n'
        self.record = probes.attach_external_observation(self.database, self.cycle['id'], 'q02', self.raw, {})
        with patch.dict(os.environ, DATABASE_PATH=str(self.database), DEMO_MODE='false',
                        SEED_DEMO_DATA='false', MEDIA_ROOT=str(self.root / 'media'), COOKIES_DIRECTORY=str(self.root / 'cookies')):
            from sau_backend import app
        self.configuration = patch.dict(app.config, DATABASE_PATH=self.database, TESTING=True, ACCESS_CONTROL_ENABLED=False)
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
        self.app, self.client = app, app.test_client()
        self.endpoint = self.endpoint_for(self.record)

    def endpoint_for(self, record):
        return '/api/geo-content/cycles/' + self.cycle['id'] + '/probes/' + record['id'] + '/business-reviews'

    def context(self, record=None):
        response = self.client.get(self.endpoint_for(record or self.record))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['data']

    def payload(self, *, record=None, sequence=0, status='not_acknowledged', **changes):
        record = record or self.record
        return {'project_id': self.cycle['project_id'], 'case_id': record['case_id'],
            'expected_plan_sha256': self.cycle['test_plan']['plan_sha256'], 'expected_revision': self.cycle['revision'],
            'expected_response_sha256': hashlib.sha256(record['after']['response'].encode()).hexdigest(),
            'expected_review_sequence': sequence,
            'review': {'status': status, 'quote': record['after']['response'], 'reference_fact_ids': ['f01'], 'note': ' 用户逐字核对的材料意见。\r\n'},
            **changes}

    def save(self, payload=None, *, record=None):
        response = self.client.post(self.endpoint_for(record or self.record), json=payload or self.payload(record=record))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['data']

    def saved_record(self, identifier=None):
        identifier = identifier or self.record['id']
        with sqlite3.connect(self.database) as connection:
            return json.loads(connection.execute('SELECT record FROM geo_research_probes WHERE id=?', (identifier,)).fetchone()[0])

    def replace_record(self, record):
        with sqlite3.connect(self.database) as connection:
            connection.execute('UPDATE geo_research_probes SET record=? WHERE id=?',
                               (json.dumps(record, ensure_ascii=False), record['id']))

    def other_tables(self):
        with sqlite3.connect(self.database) as connection:
            names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {name: connection.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall()
                    for name in names if name != 'geo_research_probes'}

    def test_GET_is_read_only_no_store_and_supplies_current_exact_source_and_confirmed_facts(self):
        before = self.database.read_bytes()
        response = self.client.get(self.endpoint)
        context = response.get_json()['data']
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(context['response_sha256'], hashlib.sha256(self.raw.encode()).hexdigest())
        self.assertEqual(context['review_sequence'], 0)
        self.assertEqual(context['reviews'], [])
        self.assertEqual(context['confirmed_facts'], [
            {key: fact[key] for key in ('id', 'text', 'source_url')} for fact in self.cycle['facts']])
        self.assertEqual(context['project_id'], self.cycle['project_id'])
        self.assertEqual(context['plan_sha256'], self.cycle['test_plan']['plan_sha256'])
        self.assertEqual(context['revision'], self.cycle['revision'])
        self.assertEqual(self.database.read_bytes(), before)

    def test_operator_review_preserves_exact_quote_and_note_all_original_fields_and_other_tables(self):
        before = self.other_tables()
        payload = self.payload()
        data = self.save(payload)
        record, review = data['probe'], data['probe']['business_relationship_review']
        self.assertEqual(review['by'], 'operator')
        self.assertEqual(review['basis'], 'operator_statement')
        self.assertEqual(review['submission_channel'], 'software_json_text_entry')
        self.assertFalse(review['independent_verification'])
        self.assertEqual(review['quote'], payload['review']['quote'])
        self.assertEqual(review['note'], payload['review']['note'])
        self.assertEqual(review['response_sha256'], payload['expected_response_sha256'])
        self.assertEqual(review['delivery_capability'], 'unknown')
        self.assertEqual(review['scope'], 'manual_or_assistant_business_material_acknowledgment_not_automated_fact_accuracy')
        self.assertEqual(review['reference_facts'][0]['text'], self.cycle['facts'][0]['text'])
        self.assertEqual(record['business_relationship_reviews'], [review])
        self.assertEqual(record['business_review_sequence'], 1)
        excluded = {'business_relationship_review', 'business_relationship_reviews', 'business_review_sequence'}
        self.assertEqual({key: value for key, value in record.items() if key not in excluded},
                         {key: value for key, value in self.record.items() if key not in excluded})
        self.assertEqual(self.other_tables(), before)
        self.assertEqual(data['cycle']['probes'], [record])
        self.assert_original_unchanged()

    def test_acknowledged_negative_and_unknown_are_append_only_separate_opinions(self):
        history = []
        for sequence, status in enumerate(('not_acknowledged', 'acknowledged', 'unknown')):
            record = self.save(self.payload(sequence=sequence, status=status))['probe']
            self.assertEqual(record['business_relationship_reviews'][:sequence], history)
            history = copy.deepcopy(record['business_relationship_reviews'])
            self.assertEqual(record['business_relationship_review']['status'], status)
            self.assertEqual(record['business_review_sequence'], sequence + 1)
        self.assertEqual([entry['status'] for entry in history], ['not_acknowledged', 'acknowledged', 'unknown'])
        self.assertEqual(len({entry['id'] for entry in history}), 3)
        self.assertEqual([entry['sequence'] for entry in history], [1, 2, 3])
        self.assertIsNone(record['causal_effect'])
        self.assertIsNone(record['after']['official_citation_present'])

    def test_identical_retry_is_byte_idempotent_and_old_sequence_cannot_rewind_later_opinion(self):
        initial = self.payload()
        first = self.save(initial)['probe']
        before = self.database.read_bytes()
        self.assertEqual(self.save(initial)['probe'], first)
        self.assertEqual(self.database.read_bytes(), before)
        second = self.save(self.payload(sequence=1, status='unknown'))['probe']
        self.assertEqual(self.client.post(self.endpoint, json=initial).status_code, 400)
        self.assertEqual(self.saved_record(), second)
        self.assertEqual(len(second['business_relationship_reviews']), 2)

    def test_legacy_assistant_singleton_is_retained_exactly_before_first_operator_append(self):
        legacy = copy.deepcopy(self.record)
        legacy_review = probes._business_review(self.cycle, legacy, {**self.payload()['review'], 'by': 'assistant'})
        legacy['business_relationship_review'] = legacy_review
        self.replace_record(legacy)
        before = self.database.read_bytes()
        context = self.context()
        self.assertEqual(context['review_sequence'], 1)
        self.assertEqual(context['reviews'], [legacy_review])
        self.assertEqual(self.database.read_bytes(), before, 'Read context must not migrate or rewrite legacy storage')
        record = self.save(self.payload(sequence=context['review_sequence'], status='unknown'))['probe']
        self.assertEqual(record['business_relationship_reviews'][0], legacy_review)
        self.assertEqual(record['business_relationship_reviews'][1]['by'], 'operator')
        self.assertEqual(record['business_relationship_reviews'][1]['sequence'], 2)
        self.assertEqual(record['business_relationship_review']['by'], 'operator')
        self.assertEqual(self.context()['reviews'], record['business_relationship_reviews'])

    def test_native_and_prototype_records_use_real_SHA_without_changing_native_attribution(self):
        native = probes.attach_probe(self.database, self.cycle['id'], self.native(), source='native_collector')
        prototype = probes.attach_probe(self.database, self.cycle['id'], json.dumps(self.prototype()).encode(),
                                        source='research_prototype_import')
        for record in (native, prototype):
            with self.subTest(source=record['source']):
                context = self.context(record)
                self.assertEqual(context['response_sha256'], hashlib.sha256(record['after']['response'].encode()).hexdigest())
                after = self.save(self.payload(record=record, status='acknowledged'), record=record)['probe']
                self.assertEqual(after['source'], record['source'])
                self.assertEqual(after['native_verified'], record['native_verified'])
                self.assertEqual(after['after'], record['after'])
                self.assertEqual(after['business_relationship_review']['by'], 'operator')
                self.assertFalse(after['business_relationship_review']['independent_verification'])

    def test_client_cannot_choose_operator_or_assistant_identity_native_proof_capability_or_causal_success(self):
        for key, value in (('by', 'assistant'), ('by', 'operator'), ('native_verified', True),
                           ('independent_verification', True), ('delivery_capability', 'proven'),
                           ('causal_effect', 'success'), ('geo_success', True), ('reference_facts', []),
                           ('reviewed_at', '2026-01-01'), ('scope', 'fact_accuracy')):
            for place in ('top', 'review'):
                with self.subTest(key=key, place=place):
                    payload = self.payload()
                    (payload if place == 'top' else payload['review'])[key] = value
                    self.assertEqual(self.client.post(self.endpoint, json=payload).status_code, 400)
        self.assertEqual(self.saved_record(), self.record)

    def test_quote_must_be_an_exact_nonblank_raw_substring_without_normalization(self):
        for quote in ('', '  \r\n', self.raw.replace('\r\n', '\n'), self.raw + '额外文本',
                      '模型从未说过的结论', 'x' * 3001):
            with self.subTest(quote=quote):
                payload = self.payload()
                payload['review']['quote'] = quote
                self.assertEqual(self.client.post(self.endpoint, json=payload).status_code, 400)
        self.assertEqual(self.saved_record(), self.record)

    def test_references_require_distinct_current_confirmed_fact_IDs_even_for_unknown(self):
        for references in (None, [], ['f99'], ['f01', 'f01'], [True], ['f01'] * 31, 'f01'):
            with self.subTest(references=references):
                payload = self.payload(status='unknown')
                payload['review']['reference_fact_ids'] = references
                self.assertEqual(self.client.post(self.endpoint, json=payload).status_code, 400)
        self.assertEqual(self.saved_record(), self.record)

    def test_no_confirmed_facts_does_not_turn_review_into_an_unconstrained_success_control(self):
        with sqlite3.connect(self.database) as connection:
            unconfirmed = [{**fact, 'confirmed': False} for fact in self.cycle['facts']]
            connection.execute('UPDATE geo_content_cycles SET facts=? WHERE id=?', (json.dumps(unconfirmed), self.cycle['id']))
        self.assertEqual(self.context()['confirmed_facts'], [])
        for status in ('acknowledged', 'not_acknowledged', 'unknown'):
            self.assertEqual(self.client.post(self.endpoint, json=self.payload(status=status)).status_code, 400)
        self.assertEqual(self.saved_record(), self.record)

    def test_project_case_plan_revision_and_raw_SHA_are_required_and_match_in_the_write_transaction(self):
        invalid = ({'project_id': None}, {'project_id': True}, {'project_id': self.cycle['project_id'] + 1},
            {'case_id': 'q01'}, {'case_id': None}, {'expected_plan_sha256': '0' * 64},
            {'expected_revision': True}, {'expected_revision': self.cycle['revision'] + 1},
            {'expected_response_sha256': '0' * 64}, {'expected_response_sha256': 'not-a-hash'},
            {'expected_review_sequence': True}, {'expected_review_sequence': -1}, {'expected_review_sequence': 2})
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post(self.endpoint, json=self.payload(**changes)).status_code, 400)
        self.assertEqual(self.saved_record(), self.record)

    def test_nullable_legacy_project_scope_matches_only_the_exact_unassigned_cycle(self):
        unassigned = {**self.cycle, 'project_id': None}
        expected = {'project_id': None, 'expected_plan_sha256': self.cycle['test_plan']['plan_sha256'],
                    'expected_revision': self.cycle['revision']}
        probes._check_external_scope(unassigned, expected, allow_unassigned_project=True)
        with self.assertRaises(ValueError):
            probes._check_external_scope(self.cycle, expected, allow_unassigned_project=True)
        with self.assertRaises(ValueError):
            probes._check_external_scope(unassigned, {**expected, 'project_id': self.cycle['project_id']}, allow_unassigned_project=True)
        with self.assertRaises(ValueError):
            probes._check_external_scope(unassigned, expected)

    def test_changed_fact_revision_rejects_stale_form_and_retains_each_review_fact_snapshot(self):
        first = self.save()['probe']
        stale = self.payload(sequence=1, status='unknown')
        original_fact = self.cycle['facts'][0]['text']
        self.cycle = store.save_facts(self.database, self.cycle['id'], [
            {'text': '用户重新确认：公开资料已作当前修订。', 'source_url': 'https://synthetic.example.org/about', 'confirmed': True}],
            self.cycle['revision'])
        self.assertEqual(self.client.post(self.endpoint, json=stale).status_code, 400)
        second = self.save(self.payload(sequence=1, status='unknown'))['probe']
        self.assertEqual(second['business_relationship_reviews'][0], first['business_relationship_review'])
        self.assertEqual(second['business_relationship_reviews'][0]['reference_facts'][0]['text'], original_fact)
        self.assertEqual(second['business_relationship_reviews'][1]['reference_facts'][0]['text'], self.cycle['facts'][0]['text'])

    def test_incomplete_or_private_response_only_allows_unknown_material_review(self):
        for metadata, text in (({'answer_complete': False}, self.raw + '不完整。'),
                               ({}, '根据内部资料，合成树科技提供软件服务。')):
            with self.subTest(metadata=metadata):
                record = probes.attach_external_observation(self.database, self.cycle['id'], 'q02', text, metadata)
                for status in ('acknowledged', 'not_acknowledged'):
                    self.assertEqual(self.client.post(self.endpoint_for(record), json=self.payload(record=record, status=status)).status_code, 400)
                after = self.save(self.payload(record=record, status='unknown'), record=record)['probe']
                self.assertFalse(after['after']['answer_evaluable'])
                self.assertEqual(after['after'], record['after'])
                self.assertEqual(after['business_relationship_review']['status'], 'unknown')

    def test_legacy_and_latest_history_corruption_is_refused_without_overwrite(self):
        first = self.save()['probe']
        corruptions = []
        changed = copy.deepcopy(first)
        changed['business_relationship_review']['quote'] = '与历史不一致'
        corruptions.append(changed)
        changed = copy.deepcopy(first)
        changed['business_review_sequence'] = 2
        corruptions.append(changed)
        changed = copy.deepcopy(first)
        changed['business_relationship_reviews'][0]['sequence'] = True
        corruptions.append(changed)
        changed = copy.deepcopy(first)
        changed['business_relationship_reviews'] = 'invalid-history'
        corruptions.append(changed)
        for changed in corruptions:
            with self.subTest(changed=changed):
                self.replace_record(changed)
                before = self.database.read_bytes()
                self.assertEqual(self.client.get(self.endpoint).status_code, 400)
                self.assertEqual(self.client.post(self.endpoint, json=self.payload(sequence=1, status='unknown')).status_code, 400)
                self.assertEqual(self.database.read_bytes(), before)

    def test_response_tampering_and_wrong_cycle_or_probe_never_read_as_current_review_context(self):
        changed = copy.deepcopy(self.record)
        changed['after']['response'] += '被修改的原文'
        self.replace_record(changed)
        self.assertEqual(self.client.get(self.endpoint).status_code, 400)
        self.assertEqual(self.client.post(self.endpoint, json=self.payload()).status_code, 400)
        self.assertEqual(self.saved_record(), changed)
        missing = self.endpoint.replace(self.record['id'], 'f' * 32)
        self.assertEqual(self.client.get(missing).status_code, 400)
        self.assertEqual(self.client.post(missing, json=self.payload()).status_code, 400)
        foreign = self.endpoint.replace(self.cycle['id'], 'missing-cycle')
        self.assertEqual(self.client.get(foreign).status_code, 404)

    def test_operator_auth_and_same_origin_protect_GET_POST_and_all_replies_are_no_store(self):
        with patch.dict(self.app.config, ACCESS_CONTROL_ENABLED=True):
            for method in (self.client.get, self.client.post):
                response = method(self.endpoint)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with self.client.session_transaction() as session:
                session['operator_authenticated'] = True
            self.assertEqual(self.client.get(self.endpoint).status_code, 200)
        for method in (self.client.get, self.client.post):
            for headers in ({'Origin': 'https://foreign.example.org'}, {'Origin': 'null'}, {'Sec-Fetch-Site': 'cross-site'}):
                with self.subTest(method=method, headers=headers):
                    kwargs = {'json': self.payload()} if method == self.client.post else {}
                    response = method(self.endpoint, headers=headers, **kwargs)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.client.get(self.endpoint, headers={'Origin': 'http://localhost'}).status_code, 200)

    def test_finite_JSON_body_limits_and_GET_cannot_override_scope(self):
        for body in ('{}', '[]', 'null', '{"review":NaN}', '{"review":{},"review":{}}'):
            self.assertEqual(self.client.post(self.endpoint, data=body, content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(self.endpoint, data='{}', content_type='text/plain').status_code, 415)
        response = self.client.post(self.endpoint, data=b' ' * (64 * 1024 + 1), content_type='application/json')
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        response = self.client.open(self.endpoint, method='POST', input_stream=io.BytesIO(b'x' * (64 * 1024 + 1)),
            content_type='application/json', environ_overrides={'CONTENT_LENGTH': '', 'wsgi.input_terminated': True})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.get(self.endpoint + '?by=assistant').status_code, 400)
        self.assertEqual(self.client.get(self.endpoint, data='{}', content_type='application/json').status_code, 400)
        self.assertEqual(self.saved_record(), self.record)

    def test_simultaneous_retries_append_once_and_conflicting_edits_preserve_the_winner(self):
        payload = self.payload()
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _: probes.submit_operator_business_review(
                self.database, self.cycle['id'], self.record['id'], copy.deepcopy(payload)), range(4)))
        self.assertTrue(all(record == results[0] for record in results))
        self.assertEqual(results[0]['business_review_sequence'], 1)

        def submit(status):
            try:
                return probes.submit_operator_business_review(self.database, self.cycle['id'], self.record['id'],
                    self.payload(sequence=1, status=status))
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as workers:
            corrected = list(workers.map(submit, ('acknowledged', 'unknown')))
        self.assertEqual(sum(record is not None for record in corrected), 1)
        after = self.saved_record()
        self.assertEqual(after['business_review_sequence'], 2)
        self.assertEqual(after['business_relationship_reviews'][0], results[0]['business_relationship_review'])
        self.assertEqual(after['after'], self.record['after'])


if __name__ == '__main__':
    unittest.main()
