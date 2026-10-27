"""Owner text entry through the real Flask routes, with only temporary fixture DBs."""
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
from services.geo_test_service import plan_fingerprint
from tests.test_geo_research_probes import ProbeFixture


class ExternalObservationAPITests(ProbeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with patch.dict(os.environ, DATABASE_PATH=str(self.database), DEMO_MODE='false',
                        SEED_DEMO_DATA='false', MEDIA_ROOT=str(self.root / 'media'),
                        COOKIES_DIRECTORY=str(self.root / 'cookies')):
            from sau_backend import app
        self.configuration = patch.dict(app.config, DATABASE_PATH=self.database, TESTING=True,
            ACCESS_CONTROL_ENABLED=False, MAX_CONTENT_LENGTH=160 * 1024 * 1024)
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
        self.app, self.client = app, app.test_client()
        self.endpoint = '/api/geo-content/cycles/' + self.cycle['id'] + '/external-observations'
        self.raw = '  合成树科技的公开AI业务尚未查到。\r\n只有工商记录；资料名称不等于官网引用。\n\n'

    def scope(self):
        return {'project_id': self.cycle['project_id'], 'case_id': 'q02',
                'expected_plan_sha256': self.cycle['test_plan']['plan_sha256'],
                'expected_revision': self.cycle['revision']}

    def payload(self, **changes):
        return {**self.scope(), 'raw_response_text': self.raw, **changes}

    def save(self, payload=None, **kwargs):
        response = self.client.post(self.endpoint, json=self.payload() if payload is None else payload, **kwargs)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['data']

    def context_payload(self, record, sequence=0, **changes):
        return {**self.scope(), 'expected_response_sha256': record['response_sha256'],
                'expected_context_sequence': sequence,
                'attestation': {'question': self.question, 'new_conversation': True, 'first_message': True,
                    'confirmation_text': ' 用户明确声明：新建空白会话，第一条只问原题。\r\n'}, **changes}

    def context_endpoint(self, record):
        return self.endpoint + '/' + record['id'] + '/context'

    def other_tables(self):
        with sqlite3.connect(self.database) as connection:
            names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {name: connection.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall()
                    for name in names if name != 'geo_research_probes'}

    def assert_no_external(self):
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['probes'], [])
        self.assert_original_unchanged()

    def test_text_entry_preserves_exact_raw_bytes_unknowns_and_all_public_baseline_tables(self):
        before_tables = self.other_tables()
        before = store.get_cycle(self.database, self.cycle['id'])
        data = self.save()
        record, cycle = data['probe'], data['cycle']
        self.assertEqual(record['after']['response'], self.raw)
        self.assertEqual(record['response_sha256'], hashlib.sha256(self.raw.encode('utf-8')).hexdigest())
        self.assertEqual(record['source'], 'user_reported_external')
        self.assertFalse(record['native_verified'])
        self.assertEqual(record['question_role'], 'cycle_anchor_only')
        self.assertIsNone(record['observed_question'])
        self.assertTrue(all(value is None for value in record['conditions'].values()))
        for field in ('answer_evaluable', 'client_mode', 'model_label', 'collected_at', 'citations',
                      'displayed_sources', 'official_source_present', 'official_citation_present'):
            self.assertIsNone(record['after'][field], field)
        for field in ('evidence', 'native_task_id', 'execution', 'business_relationship_review', 'causal_effect'):
            self.assertIsNone(record[field], field)
        self.assertEqual(record['external_submission'], {'channel': 'software_json_text_entry', 'basis': 'user_reported',
            'project_id': self.cycle['project_id'], 'cycle_revision': self.cycle['revision'],
            'plan_sha256': self.cycle['test_plan']['plan_sha256'], 'independent_verification': False})
        self.assertEqual(record['external_source_links'], [])
        self.assertEqual(cycle['probes'], [record])
        self.assertEqual({key: value for key, value in before.items() if key not in ('probes', 'feedback_diagnostics')},
                         {key: value for key, value in cycle.items() if key not in ('probes', 'feedback_diagnostics')})
        self.assertEqual(self.other_tables(), before_tables)
        self.assert_original_unchanged()

    def test_repeat_click_and_retry_after_context_update_return_the_same_record(self):
        first = self.save()['probe']
        original_db = self.database.read_bytes()
        self.assertEqual(self.save()['probe'], first)
        self.assertEqual(self.database.read_bytes(), original_db)
        response = self.client.post(self.context_endpoint(first), json=self.context_payload(first))
        self.assertEqual(response.status_code, 200, response.get_json())
        amended = response.get_json()['data']['probe']
        self.assertEqual(self.save()['probe'], amended)
        self.assertEqual(len(store.get_cycle(self.database, self.cycle['id'])['probes']), 1)

    def test_source_links_are_user_reported_only_and_never_fetched_or_counted_as_citations(self):
        sources = [{'title': ' 用户提供的来源标题 ', 'url': 'https://sources.example.org/page?x=1#part'},
                   {'url': 'https://synthetic.example.org/about'}]
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No source HTTP request')):
            record = self.save(self.payload(metadata={'source_links': sources}))['probe']
        self.assertEqual(record['external_source_links'], [
            {'title': item.get('title'), 'url': item['url'], 'basis': 'user_reported',
             'verified': False, 'native_verified': False} for item in sources])
        self.assertIsNone(record['after']['citations'])
        self.assertIsNone(record['after']['official_citation_present'])
        self.assertIsNone(record['after']['official_source_present'])
        self.assertIsNone(record['after']['displayed_sources'])
        self.assertFalse(record['native_verified'])
        self.assert_original_unchanged()

    def test_source_links_refuse_paths_credentials_internal_urls_and_forged_evidence(self):
        invalid = [None, {}, ['https://example.org'], [{'title': '只有平台名'}],
            [{'url': 'https://example.org', 'native_verified': True}],
            [{'url': 'https://example.org', 'verified': True}],
            [{'url': 'https://example.org', 'title': 't' * 301}],
            [{'url': 'https://example.org/' + 'x' * 2000}],
            [{'url': 'https://example.org'}] * 21]
        invalid.extend([{'url': value}] for value in (
            'file:///C:/private.json', 'C:\\private.json', 'javascript:alert(1)',
            'https://user:secret@example.org/path', 'http://127.0.0.1/x', 'http://127.0.0.1./x',
            'http://127.1/x', 'http://localhost./x', 'http://node.local/x', 'http://node.internal/x',
            'http://[::1]/', 'https://example.org:99999/x', 'https://example.org/white space',
            'https://example.org/back\\slash'))
        for source_links in invalid:
            with self.subTest(source_links=source_links):
                response = self.client.post(self.endpoint, json=self.payload(metadata={'source_links': source_links}))
                self.assertEqual(response.status_code, 400, response.get_json())
        self.assert_no_external()

    def test_web_json_cannot_forge_assistant_business_review_native_evidence_or_GEO_success(self):
        for field, value in (('native_verified', True), ('native_task_id', 'fake'), ('evidence', {}),
                             ('business_relationship_review', {'by': 'assistant', 'status': 'acknowledged'}),
                             ('business_relationship_review', {'by': 'operator', 'status': 'acknowledged'}),
                             ('causal_effect', 'success'), ('public_metrics', {'improved': True}),
                             ('path', 'C:\\private.json'), ('file_path', '/private.json')):
            for location in ('top', 'metadata'):
                with self.subTest(field=field, location=location):
                    payload = self.payload(**({field: value} if location == 'top' else {'metadata': {field: value}}))
                    self.assertEqual(self.client.post(self.endpoint, json=payload).status_code, 400)
        self.assert_no_external()

    def test_standalone_file_paths_are_not_read_or_saved_as_answers(self):
        sentinel = self.root / 'private.txt'
        sentinel.write_text('must remain private', encoding='utf-8')
        for value in (str(sentinel), 'file:///' + sentinel.as_posix(), '/private/answer.txt',
                      '../answer.json', './answer.json', '\\\\host\\share\\answer.txt'):
            with self.subTest(value=value):
                self.assertEqual(self.client.post(self.endpoint, json=self.payload(raw_response_text=value)).status_code, 400)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'must remain private')
        self.assert_no_external()

    def test_project_plan_revision_and_case_guards_reject_stale_or_foreign_entry(self):
        for changes in ({'project_id': self.cycle['project_id'] + 1}, {'project_id': True},
                        {'expected_revision': self.cycle['revision'] + 1}, {'expected_revision': True},
                        {'expected_plan_sha256': '0' * 64}, {'expected_plan_sha256': 'not-a-plan'},
                        {'case_id': 'q99'}, {'case_id': []}):
            with self.subTest(changes=changes):
                response = self.client.post(self.endpoint, json=self.payload(**changes))
                self.assertEqual(response.status_code, 400, response.get_json())
        self.assert_no_external()

    def test_facts_change_after_form_load_invalidates_submission_in_the_write_transaction(self):
        stale = self.payload()
        updated = store.save_facts(self.database, self.cycle['id'], [
            {'text': '这是合成客户确认的公开资料。', 'confirmed': True, 'source_url': ''}], self.cycle['revision'])
        response = self.client.post(self.endpoint, json=stale)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['revision'], updated['revision'])
        self.assert_no_external()

    def test_other_project_cycle_rejects_a_form_from_the_first_project(self):
        report = copy.deepcopy(self.baseline)
        report['profile']['name'] = report['profile']['company'] = '另一家合成客户有限公司'
        report['profile']['aliases'] = ['另一家合成客户']
        report['profile']['website'] = 'https://other-customer.example.org'
        report['run_id'] = 'other-customer-original-run'
        plan = copy.deepcopy(self.plan)
        plan['profile'] = report['profile']
        for planned, answered in zip(plan['cases'], report['cases']):
            question = planned['question'].replace('合成树科技', '另一家合成客户').replace(
                'synthetic.example.org', 'other-customer.example.org')
            planned['question'] = answered['question'] = question
        report['plan_sha256'] = plan_fingerprint(plan)
        other = store.create_cycle(self.database, report, 'other-customer-report')
        endpoint = self.endpoint.replace(self.cycle['id'], other['id'])
        self.assertNotEqual(other['project_id'], self.cycle['project_id'])
        self.assertEqual(self.client.post(endpoint, json=self.payload()).status_code, 400)
        self.assertEqual(store.get_cycle(self.database, other['id'])['probes'], [])
        self.assert_no_external()

    def test_missing_cycle_returns_404_without_creating_any_record(self):
        endpoint = self.endpoint.replace(self.cycle['id'], 'missing-cycle')
        self.assertEqual(self.client.post(endpoint, json=self.payload()).status_code, 404)
        self.assert_no_external()

    def test_request_requires_json_and_rejects_malformed_duplicate_nonfinite_and_nonobject_input(self):
        for body in ('{', '[]', 'null', '"text"', '{"raw_response_text":"first","raw_response_text":"second"}',
                     '{"metadata":{"note":"first","note":"second"}}', '{"project_id":NaN}',
                     '{"project_id":Infinity}'):
            with self.subTest(body=body):
                self.assertEqual(self.client.post(self.endpoint, data=body, content_type='application/json').status_code, 400)
        for content_type in ('text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data'):
            self.assertEqual(self.client.post(self.endpoint, data='{}', content_type=content_type).status_code, 415)
        self.assert_no_external()

    def test_raw_text_and_body_have_separate_bounded_sizes(self):
        self.assertEqual(self.client.post(self.endpoint, json=self.payload(raw_response_text='x' * (1024 * 1024 + 1))).status_code, 400)
        response = self.client.post(self.endpoint, data=b' ' * (1100 * 1024 + 1), content_type='application/json')
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()['code'], 413)
        response = self.client.open(self.endpoint, method='POST', input_stream=io.BytesIO(b'x' * (1100 * 1024 + 1)),
            content_type='application/json', environ_overrides={'CONTENT_LENGTH': '', 'wsgi.input_terminated': True})
        self.assertEqual(response.status_code, 413)
        self.assert_no_external()

    def test_exact_one_MiB_text_is_accepted_without_truncation(self):
        raw = 'x' * (1024 * 1024)
        record = self.save(self.payload(raw_response_text=raw))['probe']
        self.assertEqual(record['after']['response'], raw)
        self.assertEqual(record['response_sha256'], hashlib.sha256(raw.encode()).hexdigest())
        self.assertIsNone(record['after']['answer_evaluable'])
        self.assert_original_unchanged()

    def test_operator_auth_runs_before_new_write_routes(self):
        with patch.dict(self.app.config, ACCESS_CONTROL_ENABLED=True):
            self.assertEqual(self.client.post(self.endpoint, json=self.payload()).status_code, 401)
            with self.client.session_transaction() as session:
                session['operator_authenticated'] = True
            self.assertEqual(self.client.post(self.endpoint, json=self.payload()).status_code, 200)

    def test_json_csrf_guard_rejects_foreign_opaque_and_missing_cross_site_origins(self):
        for headers in ({'Origin': 'https://foreign.example.org'}, {'Origin': 'null'},
                        {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'},
                        {'Origin': 'http://localhost.evil.example.org'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.post(self.endpoint, json=self.payload(), headers=headers).status_code, 403)
        self.assert_no_external()
        for origin in ('http://localhost', 'http://127.0.0.1:5173'):
            with self.subTest(origin=origin):
                self.assertEqual(self.client.post(self.endpoint, json=self.payload(), headers={'Origin': origin}).status_code, 200)

    def test_metadata_types_lengths_and_unknown_values_are_not_inferred(self):
        for metadata in (None, [], {'client_mode': 'mobile'}, {'client_mode': True}, {'answer_complete': 'true'},
                         {'answer_complete': 1}, {'question': ''}, {'question': 'x' * 4001},
                         {'model_label': 'x' * 101}, {'collected_at': 'x' * 101}, {'note': 'x' * 1001}):
            with self.subTest(metadata=metadata):
                self.assertEqual(self.client.post(self.endpoint, json=self.payload(metadata=metadata)).status_code, 400)
        self.assert_no_external()

    def test_explicit_incomplete_or_private_answers_remain_excluded_after_user_entry(self):
        for text, metadata in ((self.raw, {'answer_complete': False}),
                               ('根据内部资料，合成树科技提供AI服务。', {})):
            with self.subTest(metadata=metadata):
                record = self.save(self.payload(raw_response_text=text, metadata=metadata))['probe']
                self.assertFalse(record['after']['answer_evaluable'])
                self.assertIsNone(record['after']['website_presence'])
                self.assertIsNone(record['business_relationship_review'])
                response = self.client.post(self.context_endpoint(record), json=self.context_payload(record))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()['data']['probe']['after'], record['after'])

    def test_context_entry_appends_literal_owner_statement_without_promoting_answer_or_public_metrics(self):
        first = self.save()['probe']
        before_tables = self.other_tables()
        payload = self.context_payload(first)
        response = self.client.post(self.context_endpoint(first), json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        record = response.get_json()['data']['probe']
        self.assertEqual(record['id'], first['id'])
        self.assertEqual(record['after'], first['after'])
        self.assertEqual(record['before'], first['before'])
        self.assertEqual(record['observed_question'], self.question)
        event = record['context_attestations'][0]
        self.assertEqual(event['confirmation_text'], payload['attestation']['confirmation_text'])
        self.assertEqual(event['submission_channel'], 'software_json_text_entry')
        self.assertEqual(event['basis'], 'user_statement')
        self.assertFalse(event['independent_verification'])
        self.assertFalse(event['native_verified'])
        self.assertEqual(self.other_tables(), before_tables)
        self.assert_original_unchanged()

    def test_context_retry_is_idempotent_and_stale_correction_cannot_rewind_history(self):
        first = self.save()['probe']
        endpoint, initial = self.context_endpoint(first), self.context_payload(first)
        response = self.client.post(endpoint, json=initial)
        self.assertEqual(response.status_code, 200)
        record = response.get_json()['data']['probe']
        db_bytes = self.database.read_bytes()
        self.assertEqual(self.client.post(endpoint, json=initial).get_json()['data']['probe'], record)
        self.assertEqual(self.database.read_bytes(), db_bytes)
        correction = self.context_payload(first, sequence=1)
        correction['attestation'].update(first_message=False, confirmation_text='用户补充：之前已有一条消息。')
        response = self.client.post(endpoint, json=correction)
        self.assertEqual(response.status_code, 200)
        corrected = response.get_json()['data']['probe']
        self.assertEqual(len(corrected['context_attestations']), 2)
        self.assertEqual(self.client.post(endpoint, json=initial).status_code, 400)
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['probes'], [corrected])

    def test_context_rejects_foreign_record_stale_scope_hash_and_fabricated_fields(self):
        first = self.save()['probe']
        endpoint, base = self.context_endpoint(first), self.context_payload(first)
        invalid = [None, {}, {**base, 'project_id': self.cycle['project_id'] + 1},
            {**base, 'expected_plan_sha256': '0' * 64}, {**base, 'expected_revision': self.cycle['revision'] + 1},
            {**base, 'case_id': 'q03'}, {**base, 'expected_response_sha256': '0' * 64},
            {**base, 'expected_context_sequence': 3}, {**base, 'expected_context_sequence': True},
            {**base, 'business_relationship_review': {'by': 'assistant'}}]
        for changes in ({'new_conversation': None}, {'first_message': 'true'}, {'client_mode': 'consumer'},
                        {'answer_complete': True}, {'native_verified': True}, {'confirmation_text': ''}):
            invalid.append({**base, 'attestation': {**base['attestation'], **changes}})
        for payload in invalid:
            with self.subTest(payload=payload):
                body = 'null' if payload is None else json.dumps(payload)
                self.assertEqual(self.client.post(endpoint, data=body, content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(endpoint.replace(first['id'], 'f' * 32), json=base).status_code, 400)
        native = probes.attach_probe(self.database, self.cycle['id'], self.native(), source='native_collector')
        native_payload = {**base, 'expected_response_sha256': hashlib.sha256(native['after']['response'].encode()).hexdigest()}
        self.assertEqual(self.client.post(self.context_endpoint(native), json=native_payload).status_code, 400)
        saved = store.get_cycle(self.database, self.cycle['id'])['probes']
        self.assertEqual(next(row for row in saved if row['id'] == first['id']), first)

    def test_context_endpoint_inherits_auth_json_origin_and_body_limits(self):
        record = self.save()['probe']
        endpoint, payload = self.context_endpoint(record), self.context_payload(record)
        with patch.dict(self.app.config, ACCESS_CONTROL_ENABLED=True):
            self.assertEqual(self.client.post(endpoint, json=payload).status_code, 401)
        self.assertEqual(self.client.post(endpoint, data='{}', content_type='text/plain').status_code, 415)
        self.assertEqual(self.client.post(endpoint, json=payload, headers={'Origin': 'https://foreign.example.org'}).status_code, 403)
        self.assertEqual(self.client.post(endpoint, data=b' ' * (1100 * 1024 + 1), content_type='application/json').status_code, 413)
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['probes'], [record])

    def test_simultaneous_identical_domain_submissions_create_only_one_external_record(self):
        payload = self.payload(metadata={'question': self.question})
        with ThreadPoolExecutor(max_workers=4) as workers:
            records = list(workers.map(lambda _: probes.submit_external_observation(
                self.database, self.cycle['id'], copy.deepcopy(payload)), range(4)))
        self.assertTrue(all(record == records[0] for record in records))
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['probes'], [records[0]])
        self.assert_original_unchanged()

    def test_simultaneous_context_retries_create_one_statement_and_conflicting_edits_do_not_overwrite(self):
        first = self.save()['probe']
        payload = self.context_payload(first)
        with ThreadPoolExecutor(max_workers=4) as workers:
            records = list(workers.map(lambda _: probes.submit_external_observation_context(
                self.database, self.cycle['id'], first['id'], copy.deepcopy(payload)), range(4)))
        self.assertTrue(all(record == records[0] for record in records))
        self.assertEqual(len(records[0]['context_attestations']), 1)
        corrections = []
        for first_message in (True, False):
            changed = self.context_payload(first, sequence=1)
            changed['attestation'].update(first_message=first_message,
                confirmation_text='并发修正声明：首条消息为' + str(first_message))
            corrections.append(changed)

        def submit(changed):
            try:
                return probes.submit_external_observation_context(self.database, self.cycle['id'], first['id'], changed)
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(submit, corrections))
        self.assertEqual(sum(result is not None for result in results), 1)
        saved = store.get_cycle(self.database, self.cycle['id'])['probes'][0]
        self.assertEqual(len(saved['context_attestations']), 2)
        self.assertEqual(saved['context_attestations'][0], records[0]['context_attestations'][0])
        self.assertEqual(saved['after'], first['after'])
        self.assert_original_unchanged()


if __name__ == '__main__':
    unittest.main()
