"""Pure, synthetic GEO strategy contracts: no SQL, provider or browser calls."""
import copy
import json
import unittest

from services import geo_content_research as research
from services import geo_destination_service as destinations
from services.geo_optimization_strategy import action_for_brief, optimization_plan
from services.geo_test_reports import project_report_evaluation
from services.geo_test_service import build_report, parse_ai_plan


PROFILE = {
    'entity_type': 'company', 'name': '合成数据甲有限公司',
    'industry': '合成数据服务', 'description': '仅用于离线策略合同测试',
    'website': 'https://example.org/',
}
FACT_TEXT = '合成资料提供方确认可处理测试样例数据，适用范围需逐项核对。'
DOC_URL = 'https://docs.example.net/guide'
OTHER_URL = 'https://other.example.net/reference'
UNKNOWN_URL = 'https://community.example.net/thread'


def synthetic_cycle(*, first_question=None):
    cases = [
        {'category': 'discovery', 'question': first_question or '团队如何选择数据整理服务？', 'intent': '自然需求发现'},
        {'category': 'discovery', 'question': '数据服务怎样比较处理范围？', 'intent': '自然需求选型'},
        {'category': 'brand_identity', 'question': '合成数据甲有限公司提供什么服务？', 'intent': '品牌业务核对'},
        {'category': 'website_guided', 'question': '请查看 https://example.org/ 核对服务说明。', 'intent': '官网资料核对'},
    ]
    plan = parse_ai_plan(json.dumps({'cases': cases}, ensure_ascii=False), PROFILE, 4,
                         provenance={'method': 'synthetic_offline_fixture'})
    state = {'run_id': 'synthetic-strategy-baseline', 'status': 'completed',
             'execution': {'client_mode': 'consumer', 'requested_model_label': 'synthetic-model'}, 'cases': {}}
    for index, case in enumerate(plan['cases']):
        citations = [{'title': '合成技术说明', 'url': DOC_URL}]
        displayed = []
        if index == 0:
            citations += [{'title': '合成乙数据有限公司资料', 'url': OTHER_URL}]
            displayed = [{'title': '合成讨论线索', 'url': UNKNOWN_URL}]
        elif index >= 2:
            citations = [{'title': '合成数据甲有限公司公开说明', 'url': 'https://example.org/about'}]
        result = {
            'question': case['question'], 'question_submitted': True, 'question_submission_attempted': True,
            'answer_completed': True, 'collection_status': 'success', 'source_extraction_status': 'captured',
            'response': ('合成数据甲有限公司提供合成数据服务，仅供离线软件合同验证。' if index == 2
                         else '这是合成对照回答，没有推荐具体公司，仅供离线软件合同验证。'),
            'citations': citations, 'displayed_sources': displayed,
            'visible_source_count': len(citations) + len(displayed), 'visible_citation_count': len(citations),
            'environment': {'client_mode': 'consumer', 'model_label': 'synthetic-model'},
            'timestamp': '2026-01-01T00:00:00Z',
        }
        state['cases'][case['id']] = {'status': 'completed', 'result': result}
    baseline = build_report(plan, state)
    observations = research.research_observations(baseline)
    return {
        'id': 'synthetic-strategy-cycle', 'baseline_source': 'native_report', 'baseline': baseline,
        'profile': baseline['profile'], 'test_plan': plan, 'observations': observations,
        'analysis': {'method': 'rule_observations_not_ai', 'briefs': copy.deepcopy(observations['briefs'])},
        'facts': [{'text': FACT_TEXT, 'source_url': 'https://example.org/proof', 'confirmed': True}],
        'opportunities': destinations.opportunities(observations, [], baseline['profile']['website']),
    }


def brief(cycle, *, identifier='b01', case_ids=('q01',), source_ids=(), **extra):
    value = {'id': identifier, 'case_ids': list(case_ids), 'source_ids': list(source_ids),
             'topic': '合成数据服务的范围与依据', 'why': '本次合成回答未说明选型所需依据，需先核对。',
             'outline': ['直接回答问题', '核对适用范围'], 'missing_evidence': ['核对公开能力说明', '补充适用边界'],
             'priority': 'high', **extra}
    cycle['analysis'] = {'method': 'synthetic_editorial_plan', 'briefs': [value]}
    return value


def source_id(cycle, case_id, url):
    return next(row['id'] for row in cycle['observations']['sources']
                if row['case_id'] == case_id and row['url'] == url)


def parse_brief(cycle, value):
    return research.parse_research(json.dumps({'diagnosis': '合成编辑诊断，不是效果结论', 'briefs': [value]}, ensure_ascii=False),
                                   cycle, {'method': 'synthetic_editorial_plan'})


class TestGeoOptimizationStrategy(unittest.TestCase):
    def test_identity_action_and_brand_name_do_not_cover_natural_discovery(self):
        cycle = synthetic_cycle()
        self.assertTrue(cycle['baseline']['cases'][2]['assessment']['brand_mentioned'])
        brief(cycle, case_ids=('q03',))
        result = optimization_plan(cycle)
        self.assertEqual(result['coverage'], {'planned_questions': 4, 'questions_with_actions': 1,
                                               'uncovered_case_ids': ['q01', 'q02', 'q04']})
        questions = {item['case_id']: item for item in result['questions']}
        self.assertEqual(questions['q01']['goal'], 'natural_discovery')
        self.assertEqual(questions['q01']['action_ids'], [])
        self.assertEqual(result['actions'][0]['goals'], [{'key': 'identity_accuracy', 'label': '品牌与业务识别'}])
        self.assertIn('不等于优化成功', result['note'])
        self.assertNotIn('success_rate', json.dumps(result))

    def test_each_original_question_keeps_its_own_goal_and_acceptance_signal(self):
        cycle = synthetic_cycle()
        brief(cycle, case_ids=('q01', 'q02', 'q03', 'q04'))
        result = optimization_plan(cycle); action = result['actions'][0]
        self.assertEqual([item['key'] for item in action['goals']],
                         ['natural_discovery', 'identity_accuracy', 'official_source_visibility'])
        checks = {item['case_id']: item for item in action['acceptance_checks']}
        self.assertEqual(checks['q01']['goal'], 'natural_discovery')
        self.assertIn('不点名原题', checks['q01']['signal'])
        self.assertEqual(checks['q03']['goal'], 'identity_accuracy')
        self.assertIn('业务识别', checks['q03']['signal'])
        self.assertEqual(checks['q04']['goal'], 'official_source_visibility')
        self.assertIn('正文引用', checks['q04']['signal'])
        self.assertEqual([item['question'] for item in action['acceptance_checks']],
                         [item['question'] for item in cycle['test_plan']['cases']])
        self.assertEqual(result['coverage']['questions_with_actions'], 4)

    def test_legacy_brief_is_explicitly_derived_without_mutating_saved_objects(self):
        cycle = synthetic_cycle(); original = brief(cycle)
        snapshot = copy.deepcopy(cycle)
        result = optimization_plan(cycle); action = result['actions'][0]
        self.assertEqual(action['basis'], 'derived_from_stored_brief')
        self.assertEqual(action['gap'], original['why'])
        self.assertEqual(action['evidence_needed'], original['missing_evidence'])
        self.assertIn(original['missing_evidence'][0], action['change_summary'])
        self.assertEqual(cycle, snapshot)
        action['evidence_needed'].append('调用者自己的临时修改')
        action['case_ids'].append('unrelated')
        result['questions'][0]['action_ids'].clear()
        self.assertEqual(cycle, snapshot)

    def test_optional_optimization_is_normalized_and_preserved_as_editorial_data(self):
        cycle = synthetic_cycle()
        value = brief(cycle, optimization={'gap': '  本题缺少能力边界的公开依据。  ', 'change': '  先补核对资料再调整段落。  '})
        parsed = parse_brief(cycle, value)
        self.assertEqual(parsed['briefs'][0]['optimization'],
                         {'gap': '本题缺少能力边界的公开依据。', 'change': '先补核对资料再调整段落。'})
        cycle['analysis'] = parsed
        action = action_for_brief(cycle, 'b01')
        self.assertEqual(action['basis'], 'stored_optimization')
        self.assertEqual(action['gap'], parsed['briefs'][0]['optimization']['gap'])
        self.assertEqual(action['change_summary'], parsed['briefs'][0]['optimization']['change'])

    def test_older_ai_output_without_optimization_remains_accepted(self):
        cycle = synthetic_cycle(); value = brief(cycle)
        cycle['analysis'] = parse_brief(cycle, value)
        self.assertNotIn('optimization', cycle['analysis']['briefs'][0])
        self.assertEqual(action_for_brief(cycle, 'b01')['basis'], 'derived_from_stored_brief')

    def test_optional_optimization_rejects_invalid_shape_type_and_empty_text(self):
        invalid = [None, [], 'not an object', {}, {'gap': '缺口'}, {'change': '改动'},
                   {'gap': '缺口', 'change': '改动', 'success': True},
                   {'gap': '', 'change': '改动'}, {'gap': '  ', 'change': '改动'},
                   {'gap': True, 'change': '改动'}, {'gap': '缺口', 'change': ['改动']},
                   {'gap': '缺口', 'change': None}]
        for optimization in invalid:
            with self.subTest(optimization=optimization):
                cycle = synthetic_cycle(); value = brief(cycle, optimization=optimization)
                with self.assertRaises(ValueError): parse_brief(cycle, value)

    def test_optional_optimization_accepts_the_limit_and_rejects_one_character_over(self):
        cycle = synthetic_cycle(); value = brief(cycle, optimization={'gap': '甲' * 1500, 'change': '乙' * 1500})
        parsed = parse_brief(cycle, value)
        self.assertEqual(len(parsed['briefs'][0]['optimization']['change']), 1500)
        for field in ('gap', 'change'):
            with self.subTest(field=field):
                value['optimization'][field] += '超'
                with self.assertRaises(ValueError): parse_brief(cycle, value)
                value['optimization'][field] = value['optimization'][field][:-1]

    def test_current_native_exclusions_override_stale_usable_and_reject_a_mixed_brief(self):
        for status, evaluable in [('service_unavailable', True), ('failed', True), ('completed', False)]:
            with self.subTest(status=status, evaluable=evaluable):
                cycle = synthetic_cycle(); brief(cycle, case_ids=('q01', 'q03'))
                cycle['baseline']['cases'][0]['status'] = status
                cycle['baseline']['cases'][0]['assessment']['answer_evaluable'] = evaluable
                self.assertTrue(cycle['observations']['cases'][0]['usable'])
                result = optimization_plan(cycle)
                self.assertEqual(result['actions'], [])
                self.assertEqual(result['coverage']['questions_with_actions'], 0)
                self.assertIn('q03', result['coverage']['uncovered_case_ids'])

    def test_private_account_claim_cannot_enter_actions_through_a_stale_mixed_brief(self):
        cycle = synthetic_cycle(); brief(cycle, case_ids=('q01', 'q03'))
        cycle['baseline']['cases'][0]['response'] = '根据你之前的聊天记录，这里找到了一段合成说明。'
        projected = project_report_evaluation(cycle['baseline'])
        self.assertEqual(projected['cases'][0]['assessment']['answer_error'], 'private_account_context')
        self.assertTrue(cycle['observations']['cases'][0]['usable'])
        result = optimization_plan(cycle)
        self.assertEqual(result['actions'], [])
        self.assertEqual(result['coverage']['planned_questions'], 4)

    def test_external_baseline_without_native_report_keeps_unobserved_questions_uncovered(self):
        cycle = synthetic_cycle(); brief(cycle)
        cycle.update(baseline=None, baseline_source='user_reported_external')
        cycle['observations']['sources'] = []; cycle['opportunities'] = []
        for item in cycle['observations']['cases']:
            item.update(usable=item['id'] == 'q01', native_verified=False)
        result = optimization_plan(cycle)
        self.assertEqual(result['coverage'], {'planned_questions': 4, 'questions_with_actions': 1,
                                               'uncovered_case_ids': ['q02', 'q03', 'q04']})
        self.assertEqual(result['actions'][0]['source_routes'], [])
        self.assertIn('外部原文仅作编辑线索', result['note'])
        inputs = research.draft_input(cycle, 'b01', 'zhihu')
        self.assertEqual(inputs['baseline_basis'], 'user_reported_external_not_independently_verified')
        for key in ('brand_rate', 'success_rate', 'native_metrics'):
            self.assertNotIn(key, json.dumps(result))

    def test_unknown_missing_or_ambiguous_original_questions_never_gain_goals(self):
        for change in ('unknown_category', 'missing_question', 'duplicate_id'):
            with self.subTest(change=change):
                cycle = synthetic_cycle(); brief(cycle)
                if change == 'unknown_category': cycle['test_plan']['cases'][0]['category'] = 'unrecognized'
                elif change == 'missing_question': cycle['test_plan']['cases'][0]['question'] = ''
                else: cycle['test_plan']['cases'].append(copy.deepcopy(cycle['test_plan']['cases'][0]))
                result = optimization_plan(cycle)
                self.assertNotIn('q01', [item['case_id'] for item in result['questions']])
                self.assertEqual(result['actions'], [])

    def test_eight_hundred_character_original_question_is_preserved_for_acceptance(self):
        prefix = '如何选择数据处理服务？'
        question = prefix + '核对依据和适用范围' * ((800 - len(prefix)) // 9) + '甲' * ((800 - len(prefix)) % 9)
        self.assertEqual(len(question), 800)
        cycle = synthetic_cycle(first_question=question); brief(cycle)
        result = optimization_plan(cycle)
        self.assertEqual(result['questions'][0]['question'], question)
        self.assertEqual(result['actions'][0]['acceptance_checks'][0]['question'], question)

    def test_source_routes_use_only_selected_refs_including_docs_and_other_unknown_sources(self):
        cycle = synthetic_cycle()
        selected = [source_id(cycle, 'q01', url) for url in (DOC_URL, OTHER_URL, UNKNOWN_URL)]
        brief(cycle, source_ids=selected)
        self.assertEqual(next(row for row in cycle['observations']['sources'] if row['id'] == selected[1])['source_identity']['classification'], 'other')
        self.assertEqual(next(row for row in cycle['observations']['sources'] if row['id'] == selected[2])['source_identity']['classification'], 'unknown')
        action = action_for_brief(cycle, 'b01')
        docs = next(row for row in action['source_routes'] if row['surface'] == 'vendor_documentation')
        self.assertTrue(docs['id'].endswith(':docs'))
        self.assertEqual(docs['source_ids'], [selected[0]])
        self.assertNotIn(source_id(cycle, 'q02', DOC_URL), docs['source_ids'])
        self.assertEqual({item for row in action['source_routes'] for item in row['source_ids']}, set(selected))
        self.assertTrue(any(row['surface'] == 'unknown' for row in action['source_routes']))
        for row in action['source_routes']:
            self.assertEqual(set(row), {'id', 'label', 'surface', 'source_ids'})
        inputs = research.draft_input(cycle, 'b01', 'zhihu')
        self.assertEqual([item['text'] for item in inputs['facts']], [FACT_TEXT])
        self.assertNotIn('合成乙数据有限公司', json.dumps(inputs['facts'], ensure_ascii=False))
        self.assertIn('不成为本公司事实', '；'.join(action['writing_requirements']))

    def test_source_reference_to_another_question_cannot_cover_the_selected_question(self):
        cycle = synthetic_cycle()
        brief(cycle, source_ids=[source_id(cycle, 'q02', DOC_URL)])
        result = optimization_plan(cycle)
        self.assertEqual(result['actions'], [])
        self.assertEqual(result['coverage']['questions_with_actions'], 0)

    def test_writing_requirements_reach_the_prompt_without_turning_editorial_claims_into_facts(self):
        cycle = synthetic_cycle()
        gap = '本次合成回答声称存在一项未经核验的处罚，仍需查证。'
        brief(cycle, optimization={'gap': gap, 'change': '只补核对依据，不认定该说法为事实。'})
        inputs = research.draft_input(cycle, 'b01', 'zhihu')
        prompt = research.draft_prompt(cycle, 'b01', 'zhihu')
        self.assertEqual([item['text'] for item in inputs['facts']], [FACT_TEXT])
        self.assertNotIn(gap, json.dumps(inputs['facts'], ensure_ascii=False))
        self.assertIn('action_plan 是待执行的编辑方案', prompt)
        self.assertIn('均不是事实或已取得效果', prompt)
        self.assertIn('不能成为本公司事实', prompt)
        for requirement in inputs['action_plan']['writing_requirements']:
            self.assertIn(requirement, prompt)
        for concept in ('不堆砌关键词', '同一段', '紧邻相应主张', '不适用情形'):
            self.assertIn(concept, prompt)

    def test_refresh_projection_never_changes_the_same_brief_draft_fingerprint(self):
        minimal = synthetic_cycle(); brief(minimal)
        full = copy.deepcopy(minimal)
        full.update(probes=[], drafts=[], feedback_diagnostics={'planning_feedback': {'needs_update': True}})
        before = copy.deepcopy(full)
        left = research.draft_input(minimal, 'b01', 'zhihu')
        right = research.draft_input(full, 'b01', 'zhihu')
        self.assertEqual(research.fingerprint(left), research.fingerprint(right))
        self.assertTrue(optimization_plan(full)['needs_refresh'])
        self.assertNotIn('needs_refresh', right['action_plan'])
        self.assertEqual(full, before)
        for value in (False, 1, 'true', None):
            full['feedback_diagnostics']['planning_feedback']['needs_update'] = value
            self.assertFalse(optimization_plan(full)['needs_refresh'])

    def test_missing_original_plan_has_no_invented_goal_or_success_measurement(self):
        cycle = synthetic_cycle(); brief(cycle); cycle['test_plan'] = {}
        result = optimization_plan(cycle)
        self.assertEqual(result['questions'], [])
        self.assertEqual(result['actions'], [])
        self.assertEqual(result['coverage'], {'planned_questions': 0, 'questions_with_actions': 0, 'uncovered_case_ids': []})
        self.assertIn('有行动覆盖不等于优化成功', result['note'])


if __name__ == '__main__':
    unittest.main()
