"""Derive question-bound editorial actions; never infer facts or GEO success.

This is a deterministic, read-only projection of existing frozen questions,
usable observations, briefs and source-route references. It has no SQL, network,
AI call or publishing side effect. New drafts persist their exact action snapshot.
"""
from __future__ import annotations

import re

from services.geo_test_reports import project_report_evaluation


SCHEMA_VERSION = 'geo_optimization_plan_v1'
GOALS = {
    'discovery': ('natural_discovery', '自然需求发现',
                  '用同一道不点名原题核对回答是否主动提出本对象及适用理由；仅出现品牌名、点名核验或官网引导不等于自然推荐。'),
    'brand_identity': ('identity_accuracy', '品牌与业务识别',
                       '对照已确认事实核对同题回答中的主体、业务和适用范围；仅出现品牌名不等于业务识别正确。'),
    'website_guided': ('official_source_visibility', '官网资料采用',
                       '分别核对同题回答中的官网正文引用和公开资料采用；网址文字出现、页面可访问或站长提交不能替代引用或采用证据。'),
}
BASE_REQUIREMENTS = (
    '先直接回应关联原题，再用清楚的小标题和可单独引用的简短答案展开，不堆砌关键词。',
    '在同一段明确本次公司或产品主体、已确认能力与对应使用情景，不混淆近名主体、产品和运营公司。',
    '每项主张只用已确认 facts 支撑，公开依据紧邻相应主张；无网址的资料明确归于运营方说明。',
    '写清适用对象、限制与不适用情形；依据缺失时保留待补，不编造能力、价格、案例或第三方背书。',
    'other、unknown 来源及来源路线仅作行业与渠道线索，不成为本公司事实，也不证明投稿权限或未来 AI 引用。',
)
GOAL_REQUIREMENTS = {
    'natural_discovery': '围绕不点名的需求解释可核对的选择标准与适配场景；不要用点名品牌核验代替自然需求发现。',
    'identity_accuracy': '清楚区分主体身份、已确认业务、产品与运营关系；未核验的工商或处罚说法不能写成事实。',
    'official_source_visibility': '以可核对的官网公开资料回答具体问题，区分页面内容、网址出现与实际正文引用。',
}
NOTE = '这是待执行方案，实际效果以同题复测原文为准；有行动覆盖不等于优化成功。'


def _dict(value):
    return value if isinstance(value, dict) else {}


def _rows(value, maximum):
    return [item for item in value[:maximum] if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value, maximum):
    return value.strip()[:maximum] if isinstance(value, str) else ''


def _id(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) else None


def _ids(value, maximum):
    if not isinstance(value, list) or len(value) > maximum or any(not _id(item) for item in value):
        return None
    return list(value) if len(set(value)) == len(value) else None


def _unique_rows(rows):
    grouped = {}
    for row in rows:
        identifier = _id(row.get('id'))
        if identifier:
            grouped.setdefault(identifier, []).append(row)
    return {key: values[0] for key, values in grouped.items() if len(values) == 1}


def _optimization(brief):
    value = brief.get('optimization')
    if (isinstance(value, dict) and set(value) == {'gap', 'change'}
            and all(isinstance(value.get(key), str) and 1 <= len(value[key].strip()) <= 1500 for key in ('gap', 'change'))):
        return value
    return None


def _source_routes(cycle, source_ids):
    selected = set(source_ids)
    routes, seen = [], set()
    for route in _rows(cycle.get('opportunities'), 500):
        identifier = route.get('id')
        references = route.get('source_ids')
        if (not isinstance(identifier, str) or not identifier.strip() or identifier != identifier.strip()
                or len(identifier) > 300 or identifier in seen or not isinstance(references, list)):
            continue
        matching = list(dict.fromkeys(item for item in references if isinstance(item, str) and item in selected))
        if not matching:
            continue
        candidate = _dict(route.get('candidate'))
        label = _text(candidate.get('name'), 200) or _text(route.get('domain'), 200) or '来源网址待核对'
        surface = route.get('surface')
        if surface not in ('owned_site', 'community_candidate', 'vendor_documentation', 'unknown', 'url_unknown'):
            surface = 'unknown'
        routes.append({'id': identifier, 'label': label, 'surface': surface, 'source_ids': matching})
        seen.add(identifier)
    return routes


def optimization_plan(cycle):
    """Build intentions and plan coverage, never an observed-success metric.

    Frozen questions remain visible when an answer is missing/unusable. Such
    questions cannot acquire an action from a stale or excluded brief. Unknown
    question categories or absent original questions never gain invented goals.
    """
    cycle = _dict(cycle)
    original = _unique_rows(_rows(_dict(cycle.get('test_plan')).get('cases'), 100))
    observed = _unique_rows(_rows(_dict(cycle.get('observations')).get('cases'), 100))
    external = cycle.get('baseline_source') == 'user_reported_external'
    if external:
        native = None
    else:
        try:
            native = _unique_rows(_rows(project_report_evaluation(cycle['baseline']).get('cases'), 100))
        except (KeyError, TypeError, ValueError, AttributeError):
            native = {}
    questions, eligible = [], set()
    for identifier, item in original.items():
        category, question = item.get('category'), item.get('question')
        if category not in GOALS or not isinstance(question, str) or not question.strip() or len(question) > 800:
            continue
        goal, label, _ = GOALS[category]
        questions.append({'case_id': identifier, 'category': category, 'question': question,
                          'goal': goal, 'label': label, 'action_ids': []})
        row = observed.get(identifier, {})
        if row.get('usable') is not True or row.get('question') != question or row.get('category') != category:
            continue
        if native is not None:
            baseline = native.get(identifier, {})
            if (baseline.get('question') != question or baseline.get('category') != category
                    or _dict(baseline.get('assessment')).get('answer_evaluable') is not True):
                continue
        eligible.add(identifier)
    by_question = {item['case_id']: item for item in questions}
    sources = _unique_rows(_rows(_dict(cycle.get('observations')).get('sources'), 6000))
    briefs = _unique_rows(_rows(_dict(cycle.get('analysis')).get('briefs'), 100))
    actions = []
    for identifier, brief in briefs.items():
        case_ids = _ids(brief.get('case_ids'), 12)
        source_ids = _ids(brief.get('source_ids', []), 40)
        topic = brief.get('topic')
        if (not isinstance(topic, str) or not topic.strip() or len(topic) > 300
                or not case_ids or not set(case_ids) <= eligible or source_ids is None
                or any(source not in sources or sources[source].get('case_id') not in case_ids for source in source_ids)):
            continue
        missing = [_text(value, 1500) for value in brief.get('missing_evidence', [])[:12]
                   if isinstance(value, str) and value.strip()] if isinstance(brief.get('missing_evidence', []), list) else []
        goals = list(dict.fromkeys(by_question[case]['goal'] for case in case_ids))
        optimization = _optimization(brief)
        gap = optimization['gap'].strip() if optimization else _text(brief.get('why'), 1500)
        change = optimization['change'].strip() if optimization else (
            '围绕原题补齐并核对依据：' + '；'.join(missing[:3]) if missing else
            '围绕“' + topic + '”直接回答原题，明确已确认业务、公开依据与适用边界。')
        action = {'id': identifier, 'topic': topic, 'case_ids': case_ids,
                  'goals': [{'key': goal, 'label': next(item['label'] for item in questions if item['goal'] == goal)} for goal in goals],
                  'gap': gap or '已存选题尚未说明具体缺口，先核对本题回答与已确认资料。',
                  'change_summary': change[:1500],
                  'writing_requirements': [*BASE_REQUIREMENTS, *(GOAL_REQUIREMENTS[goal] for goal in goals)],
                  'evidence_needed': missing, 'source_routes': _source_routes(cycle, source_ids),
                  'acceptance_checks': [{'case_id': case, 'question': by_question[case]['question'],
                      'goal': by_question[case]['goal'], 'label': by_question[case]['label'],
                      'signal': GOALS[by_question[case]['category']][2]} for case in case_ids],
                  'basis': 'stored_optimization' if optimization else 'derived_from_stored_brief'}
        actions.append(action)
        for case in case_ids:
            by_question[case]['action_ids'].append(identifier)
    refresh = _dict(_dict(cycle.get('feedback_diagnostics')).get('planning_feedback')).get('needs_update') is True
    return {'schema_version': SCHEMA_VERSION, 'questions': questions, 'actions': actions,
            'coverage': {'planned_questions': len(questions),
                         'questions_with_actions': sum(bool(item['action_ids']) for item in questions),
                         'uncovered_case_ids': [item['case_id'] for item in questions if not item['action_ids']]},
            'needs_refresh': refresh,
            'note': NOTE + (' 用户提供的外部原文仅作编辑线索，不生成原生指标。' if external else '')}


def action_for_brief(cycle, brief_id):
    return next((item for item in optimization_plan(cycle)['actions'] if item['id'] == brief_id), None)
