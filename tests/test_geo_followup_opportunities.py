"""Synthetic follow-up channels: no network, AI, publishing or customer DB."""
import copy
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

from services import geo_content_store as store, geo_content_research as research, geo_research_probe_service as probes
from services.geo_followup_opportunities import followup_opportunities, followup_projection, source_routes_for_brief
from services.geo_optimization_strategy import optimization_plan
from tests.test_geo_content_loop import ResearchFixture, analysis, draft
from tests.test_geo_optimization_strategy import synthetic_cycle, brief, source_id, DOC_URL


def probe(cycle, *, identifier='followup-01', case_id='q01', sources=None, **extra):
    case = next(item for item in cycle['test_plan']['cases'] if item['id'] == case_id)
    response = '本次合成检索暂未找到该对象的官方入口，来源仅用于研究公开渠道。'
    digest = hashlib.sha256(response.encode('utf-8')).hexdigest()
    return {'id': identifier, 'cycle_id': cycle['id'], 'case_id': case_id, 'category': case['category'],
            'question': case['question'], 'observed_question': case['question'],
            'plan_sha256': cycle['test_plan']['plan_sha256'], 'source': 'native_collector',
            'native_verified': True, 'response_sha256': digest, 'created_at': '2026-01-02T00:00:00Z',
            'conditions': {'same_question': True},
            'after': {'response': response, 'answer_evaluable': True, 'client_mode': 'consumer',
                      'citations': sources or [{'title': '合成渠道资料', 'url': 'https://channels.example.org/catalog'}],
                      'displayed_sources': []}, **extra}


class FollowupOpportunitiesTests(unittest.TestCase):
    def test_seventeen_sources_include_later_directories_with_separate_attributed_refs(self):
        cycle = synthetic_cycle()
        sources = [{'title': f'合成来源 {index}', 'url': f'https://source{index:02d}.example.org/reference'}
                   for index in range(1, 18)]
        cycle['probes'] = [probe(cycle, sources=sources)]
        before = copy.deepcopy(cycle)
        result = followup_opportunities(cycle)
        self.assertEqual(len(result), 17)
        for index in (9, 10):
            route = next(row for row in result if row['domain'] == f'source{index:02d}.example.org')
            self.assertEqual(route['source_ids'], [])
            self.assertEqual(route['surface'], 'unknown')
            self.assertIsNone(route['candidate'])
            self.assertEqual(route['feedback_sources'][0]['title'], f'合成来源 {index}')
            self.assertEqual(route['feedback_source_refs'][0]['probe_id'], 'followup-01')
            self.assertEqual(route['feedback_source_refs'][0]['case_id'], 'q01')
            self.assertTrue(route['feedback_source_refs'][0]['source_ref'].startswith('fs_'))
            self.assertEqual(route['evidence_role'], 'followup_channel_research_only')
            for key in ('facts_verified', 'publication_authorized', 'geo_effect_verified'):
                self.assertIs(route[key], False)
            self.assertIn('均未确认', route['note'])
        self.assertEqual(cycle, before)
        self.assertEqual(followup_opportunities(cycle), result)
        result[0]['feedback_sources'][0]['title'] = '调用者的修改'
        self.assertEqual(cycle, before)

    def test_channel_matching_reuses_exact_existing_surface_scope_without_changing_target_history(self):
        cycle = synthetic_cycle()
        cycle['destinations'] = [{'id': 'target-01', 'key': 'target_target-01',
            'config': {'name': '合成已登记渠道', 'source_scope': 'https://blog.csdn.net/example/'},
            'state': 'disabled', 'events': [{'kind': 'fill', 'state': 'need_action'}]}]
        cycle['probes'] = [probe(cycle, sources=[{'title': '合成技术文章',
            'url': 'https://blog.csdn.net/example/article/details/123'},
            {'title': '域名伪装', 'url': 'https://blog.csdn.net.attacker.org/example/article/details/123'}])]
        before = copy.deepcopy(cycle)
        routes = followup_opportunities(cycle)
        match = next(row for row in routes if row['surface'] == 'community_candidate')
        self.assertEqual(match['id'], 'feedback:csdn')
        self.assertEqual(match['matches'][0]['id'], 'target-01')
        self.assertIs(match['publication_authorized'], False)
        self.assertEqual(next(row for row in routes if row['domain'].endswith('attacker.org'))['matches'], [])
        self.assertEqual(cycle, before)

    def test_foreign_private_incomplete_unverified_and_question_mismatches_never_enter_channels(self):
        cycle = synthetic_cycle(); normal = probe(cycle)
        mutations = [dict(cycle_id='foreign-cycle'), dict(plan_sha256='0' * 64),
                     dict(response_sha256='0' * 64), dict(native_verified=False),
                     dict(question='另一道题'), dict(observed_question='添加了品牌或网址的另一道题'),
                     dict(category='brand_identity'), dict(conditions={'same_question': False}),
                     dict(case_id='missing-case'), dict(observation_role='baseline'),
                     dict(after={**normal['after'], 'answer_evaluable': False}),
                     dict(after={**normal['after'], 'response': '根据你之前内部工作记录，这是一条私密回答。'})]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                cycle['probes'] = [{**normal, **mutation}]
                self.assertEqual(followup_opportunities(cycle), [])
        cycle['probes'] = [normal, copy.deepcopy(normal)]
        self.assertEqual(followup_opportunities(cycle), [])
        cycle['probes'] = [normal]
        cycle['test_plan']['cases'].append(copy.deepcopy(cycle['test_plan']['cases'][0]))
        self.assertEqual(followup_opportunities(cycle), [])

    def test_unconfirmed_private_and_spoofed_urls_are_not_recovered_from_raw_probe_or_cache(self):
        cycle = synthetic_cycle()
        cycle['probes'] = [probe(cycle, sources=[
            {'title': '仅可见标题', 'url': 'https://hidden.example.org/secret', 'url_status': 'not_exposed'},
            {'title': '私网来源', 'url': 'https://127.0.0.1/private'},
            {'title': '账号地址', 'url': 'https://user:secret@example.org/private'},
            {'title': '合成普通来源', 'url': 'https://ordinary.example.org/public'}])]
        cycle['followup_opportunities'] = [{'id': 'feedback:forged', 'source_ids': ['s001'],
            'feedback_source_refs': [{'probe_id': 'followup-01', 'case_id': 'q01', 'source_ref': 'forged'}]}]
        projected = followup_opportunities(cycle)
        text = json.dumps(projected)
        for secret in ('hidden.example.org', '127.0.0.1', 'user:secret', 'forged'):
            self.assertNotIn(secret, text)
        unknown = next(row for row in projected if row['surface'] == 'url_unknown')
        self.assertTrue(all(source['url'] is None for source in unknown['feedback_sources']))
        self.assertTrue(any(row['domain'] == 'ordinary.example.org' for row in projected))

    def test_routes_require_valid_explicit_feedback_ids_and_same_original_case(self):
        cycle = synthetic_cycle()
        cycle['probes'] = [probe(cycle), probe(cycle, identifier='followup-02', case_id='q02')]
        projection = followup_projection(cycle)
        for ids in (None, [], ['missing'], ['followup-02'], ['followup-01', 'missing'],
                    ['followup-01', 'followup-01'], ['javascript:bad']):
            with self.subTest(ids=ids):
                self.assertEqual(source_routes_for_brief(projection, {'case_ids': ['q01'], 'feedback_probe_ids': ids}), [])
        routes = source_routes_for_brief(projection, {'case_ids': ['q01'], 'feedback_probe_ids': ['followup-01']})
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]['source_ids'], [])
        self.assertEqual([ref['probe_id'] for ref in routes[0]['feedback_source_refs']], ['followup-01'])
        self.assertEqual([ref['case_id'] for ref in routes[0]['feedback_source_refs']], ['q01'])

    def test_legacy_strategy_routes_are_unchanged_until_brief_explicitly_references_feedback(self):
        cycle = synthetic_cycle()
        brief(cycle, source_ids=[source_id(cycle, 'q01', DOC_URL)])
        legacy = optimization_plan(cycle)
        cycle['probes'] = [probe(cycle)]
        self.assertEqual(optimization_plan(cycle), legacy)
        current = cycle['analysis']['briefs'][0]
        current['feedback_probe_ids'] = ['followup-01']
        before = copy.deepcopy(cycle)
        action = optimization_plan(cycle)['actions'][0]
        self.assertEqual(action['source_routes'][:1], legacy['actions'][0]['source_routes'])
        self.assertEqual(len(action['source_routes']), 2)
        self.assertEqual(action['source_routes'][1]['source_ids'], [])
        self.assertEqual(cycle, before)
        self.assertNotIn('success_rate', json.dumps(action))
        self.assertEqual(cycle['facts'], before['facts'])

    def test_source_refs_bind_exact_response_and_public_source_not_order_or_baseline_ids(self):
        cycle = synthetic_cycle()
        sources = [{'title': '合成甲', 'url': 'https://alpha.example.org/public'},
                   {'title': '合成乙', 'url': 'https://beta.example.org/public'}]
        original = probe(cycle, sources=sources)
        cycle['probes'] = [original]
        initial = {row['domain']: row['feedback_source_refs'] for row in followup_opportunities(cycle)}
        original['after']['citations'].reverse()
        reordered = {row['domain']: row['feedback_source_refs'] for row in followup_opportunities(cycle)}
        self.assertEqual(initial, reordered)
        original['after']['response'] += '另一轮回答内容。'
        original['response_sha256'] = hashlib.sha256(original['after']['response'].encode()).hexdigest()
        changed = {row['domain']: row['feedback_source_refs'] for row in followup_opportunities(cycle)}
        self.assertNotEqual(initial, changed)
        self.assertFalse(set(ref['source_ref'] for refs in initial.values() for ref in refs)
                         & set(source['id'] for source in cycle['observations']['sources']))

    def test_full_eight_hundred_character_question_is_compared_without_excerpt_false_mismatch(self):
        question = '如何选择服务？' + '甲' * 793
        cycle = synthetic_cycle(first_question=question)
        cycle['probes'] = [probe(cycle)]
        self.assertEqual(len(followup_opportunities(cycle)), 1)
        cycle['probes'][0]['observed_question'] = question[:-1] + '乙'
        self.assertEqual(followup_opportunities(cycle), [])

    def test_user_provided_links_remain_separate_from_observed_sources_and_malformed_context_is_quiet(self):
        cycle = synthetic_cycle()
        external = probe(cycle, source='user_reported_external', native_verified=False,
                         external_source_links=[{'url': 'https://unobserved.example.org/catalog',
                             'basis': 'user_reported', 'verified': False, 'native_verified': False}])
        external['after']['citations'] = None
        cycle['probes'] = [external]
        self.assertEqual(followup_opportunities(cycle), [])
        for malformed in (None, {}, {**cycle, 'test_plan': []}, {**cycle, 'baseline': []}):
            with self.subTest(malformed=type(malformed).__name__):
                self.assertEqual(followup_opportunities(malformed), [])


class FollowupDetailTests(ResearchFixture, unittest.TestCase):
    def prepared_feedback_cycle(self):
        self.prepare_facts()
        current = store.get_cycle(self.database, self.cycle['id'])
        item = probe(current)
        with sqlite3.connect(self.database) as connection:
            connection.execute('INSERT INTO geo_research_probes VALUES(?,?,?,?,?,?)',
                (item['id'], item['cycle_id'], item['case_id'], item['source'], json.dumps(item), item['created_at']))
        current = store.get_cycle(self.database, current['id'])
        payload = json.loads(analysis())
        payload['briefs'][0]['feedback_probe_ids'] = [item['id']]
        current = store.save_analysis(self.database, current['id'], json.dumps(payload),
            {'method': 'synthetic_offline_fixture'}, current['revision'])
        return current, item

    def test_detail_projects_after_probes_are_loaded_and_does_not_write_synthetic_database(self):
        current = store.get_cycle(self.database, self.cycle['id'])
        item = probe(current)
        def rows():
            with sqlite3.connect(self.database) as connection:
                names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                return {name: connection.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in names}
        before = rows()
        with patch('services.geo_research_probe_service.list_probes', return_value=[item]):
            detail = store.get_cycle(self.database, current['id'])
        self.assertEqual(rows(), before)
        self.assertTrue(detail['followup_opportunities'])
        self.assertEqual(detail['opportunities'], current['opportunities'])
        self.assertEqual(detail['analysis'], current['analysis'])
        self.assertEqual(detail['baseline'], current['baseline'])
        self.assertEqual(detail['observations'], current['observations'])
        self.assertEqual(detail['drafts'], current['drafts'])
        self.assertEqual(detail['facts'], current['facts'])

    def test_generation_input_saves_deduplicates_and_retains_exact_followup_route_lineage(self):
        current, item = self.prepared_feedback_cycle()
        inputs = research.draft_input(current, 'b01', 'zhihu')
        followup = [row for row in inputs['action_plan']['source_routes'] if row.get('feedback_source_refs')]
        self.assertEqual(len(followup), 1)
        self.assertIsNone(store.existing_draft(self.database, current, 'b01', 'zhihu'))
        provenance = {'method': 'synthetic_offline_fixture', 'input_sha256': research.fingerprint(inputs)}
        saved = store.save_draft(self.database, current['id'], 'b01', 'zhihu', draft(), provenance, current['revision'])
        self.assertIs(saved['already_saved'], False)
        recorded = store.article_research(self.database, saved['article_id'])['provenance']['optimization_action']
        self.assertEqual(recorded, inputs['action_plan'])
        self.assertEqual(recorded['source_routes'][-1]['feedback_source_refs'][0]['probe_id'], item['id'])
        repeated = store.save_draft(self.database, current['id'], 'b01', 'zhihu', draft(), provenance, current['revision'])
        self.assertIs(repeated['already_saved'], True)
        self.assertEqual(saved['article_id'], repeated['article_id'])
        latest = store.get_cycle(self.database, current['id'])
        self.assertEqual(store.existing_draft(self.database, latest, 'b01', 'zhihu')['article_id'], saved['article_id'])
        with patch('services.geo_research_probe_service.list_probes', wraps=probes.list_probes) as loader:
            store.get_cycle(self.database, current['id'])
            self.assertEqual(loader.call_count, 1)
        # The internal transactional reader used by builtin publication CAS
        # also returns the same current action/input, without a second detail GET.
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual(research.fingerprint(research.draft_input(store._cycle(connection, current['id']), 'b01', 'zhihu')),
                             provenance['input_sha256'])

    def test_saved_probe_source_change_rejects_old_generation_even_without_cycle_revision_change(self):
        current, item = self.prepared_feedback_cycle()
        provenance = {'method': 'synthetic_offline_fixture',
                      'input_sha256': research.fingerprint(research.draft_input(current, 'b01', 'zhihu'))}
        item['after']['citations'][0]['url'] = 'https://changed.example.org/catalog'
        with sqlite3.connect(self.database) as connection:
            connection.execute('UPDATE geo_research_probes SET record=? WHERE id=?', (json.dumps(item), item['id']))
        with self.assertRaisesRegex(ValueError, '过期稿件'):
            store.save_draft(self.database, current['id'], 'b01', 'zhihu', draft(), provenance, current['revision'])
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM geo_content_drafts').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM articles').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT revision FROM geo_content_cycles WHERE id=?', (current['id'],)).fetchone()[0],
                             current['revision'])

    def test_legacy_transactional_read_has_no_probe_query_and_preserves_original_draft_key(self):
        self.prepare_facts()
        current = store.get_cycle(self.database, self.cycle['id'])
        key = research.fingerprint(research.draft_input(current, 'b01', 'zhihu'))
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            with patch('services.geo_research_probe_service.list_probes', wraps=probes.list_probes) as loader:
                legacy = store._cycle(connection, current['id'])
                self.assertEqual(loader.call_count, 0)
                self.assertNotIn('probes', legacy)
                self.assertEqual(research.fingerprint(research.draft_input(legacy, 'b01', 'zhihu')), key)
        saved = store.save_draft(self.database, current['id'], 'b01', 'zhihu', draft(),
            {'method': 'synthetic_offline_fixture', 'input_sha256': key}, current['revision'])
        self.assertEqual(store.existing_draft(self.database, store.get_cycle(self.database, current['id']), 'b01', 'zhihu')['article_id'],
                         saved['article_id'])
