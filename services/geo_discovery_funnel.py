"""Read-only, question-bound discovery observations, never a success score.

Only the frozen native baseline participates. Later probes, external feedback,
answer sentiment and owner statements cannot upgrade an observed source signal.
"""
from __future__ import annotations

import re

from services.geo_content_research import public_url
from services.geo_entity_identity import entity_measurement_signature
from services.geo_test_reports import project_report_evaluation
from services.geo_test_service import validate_plan


SCHEMA_VERSION = 'geo_discovery_funnel_v1'
STAGES = (
    ('natural_discovery', '自然需求提及', 'discovery', 'brand_mentioned'),
    ('brand_search', '点名检索官网来源', 'brand_identity', 'official_source_present'),
    ('website_guided', '给网址核验官网来源', 'website_guided', 'official_source_present'),
)
NOTE = ('各阶段仅展示本轮原题的可观察信号，不是转化率或优化成功率。'
        '品牌提及可能是否定语境；官网来源出现不证明正文引用、业务正确或推荐。'
        '给网址核验不能替代自然发现，缺失或排除题保留未知。')


def _dict(value):
    return value if isinstance(value, dict) else {}


def _rows(value, maximum=100):
    return [row for row in value[:maximum] if isinstance(row, dict)] if isinstance(value, list) else []


def _id(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) else None


def _unique(rows):
    grouped = {}
    for row in rows:
        identifier = _id(row.get('id'))
        if identifier:
            grouped.setdefault(identifier, []).append(row)
    return {key: rows[0] for key, rows in grouped.items() if len(rows) == 1}


def _conditions(rows):
    if not rows:
        return {'matched': False, 'reasons': ['没有可比较的有效原题回答']}
    reasons = []
    modes = {row.get('client_mode') for row in rows
             if isinstance(row.get('client_mode'), str) and row['client_mode'] in ('consumer', 'work')}
    models = {row.get('model_label') for row in rows
              if isinstance(row.get('model_label'), str) and 1 <= len(row['model_label'].strip()) <= 100}
    if len(modes) != 1 or any(not isinstance(row.get('client_mode'), str) or row['client_mode'] not in modes for row in rows):
        reasons.append('实际对话模式缺失或不同，不能跨题比较')
    if len(models) != 1 or any(not isinstance(row.get('model_label'), str) or row['model_label'] not in models for row in rows):
        reasons.append('实际模型标签缺失或不同，不能跨题比较')
    return {'matched': not reasons, 'reasons': reasons}


def _empty_stages():
    return [{'key': key, 'label': label, 'category': category, 'signal': signal,
             'state': 'unknown', 'planned_cases': 0, 'evaluable_cases': 0, 'known_cases': 0,
             'observed_cases': 0, 'case_ids': [], 'unknown_case_ids': [], 'cases': [],
             'conditions_match': False, 'condition_reasons': []}
            for key, label, category, signal in STAGES]


def discovery_funnel(value):
    """Project strict entity-scoped native signals, without SQL or mutations."""
    cycle = _dict(value)
    result = {'schema_version': SCHEMA_VERSION, 'basis': 'native_baseline_only',
              'supported': False, 'run_id': None, 'plan_sha256': None,
              'comparability': {'matched': False, 'reasons': []},
              'stages': _empty_stages(), 'priorities': [], 'note': NOTE}

    def unavailable(reason):
        result['comparability']['reasons'] = [reason]
        return result

    if cycle.get('baseline_source') == 'user_reported_external':
        return unavailable('用户提供的外部基线单独保留，不进入原生跨题诊断')
    baseline, plan = _dict(cycle.get('baseline')), _dict(cycle.get('test_plan'))
    if baseline.get('kind') != 'geo_search_test_report':
        return unavailable('缺少可核对的原生报告')
    if (not isinstance(plan.get('plan_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', plan['plan_sha256'])
            or baseline.get('plan_sha256') != plan['plan_sha256']):
        return unavailable('原生报告与冻结问题计划不匹配')
    profile = _dict(cycle.get('profile'))
    baseline_profile = _dict(baseline.get('profile'))
    if profile.get('entity_type') not in ('company', 'product') or baseline_profile.get('entity_type') not in ('company', 'product'):
        return unavailable('历史混合主体口径不能用于当前对象的发现诊断')
    try:
        validate_plan(plan)
        if entity_measurement_signature(profile) != entity_measurement_signature(baseline_profile):
            return unavailable('报告与当前测试对象或官网范围不一致')
        report = project_report_evaluation(baseline)
    except (KeyError, TypeError, ValueError, AttributeError):
        return unavailable('原生报告结构不完整，保留未知')
    original_rows = _rows(plan.get('cases'))
    originals = _unique(original_rows)
    if not originals or len(originals) != len(original_rows):
        return unavailable('冻结问题缺失或编号存在歧义')
    observed = _unique(_rows(report.get('cases')))
    result.update(supported=True, run_id=baseline.get('run_id'), plan_sha256=plan['plan_sha256'])
    valid_rows = []
    for stage in result['stages']:
        for identifier, original in originals.items():
            if original.get('category') != stage['category']:
                continue
            question = original.get('question')
            observed_row = observed.get(identifier, {})
            assessment = _dict(observed_row.get('assessment'))
            exact = (isinstance(question, str) and bool(question.strip()) and len(question) <= 800
                     and observed_row.get('question') == question and observed_row.get('category') == stage['category'])
            evaluable = exact and assessment.get('answer_evaluable') is True
            signal = assessment.get(stage['signal']) if evaluable else None
            if type(signal) is not bool:
                signal = None
            reason = (None if signal is not None else 'source_or_signal_unknown' if evaluable
                      else 'missing_or_mismatched_case' if not exact else 'answer_excluded_or_incomplete')
            row = {'case_id': identifier, 'evaluable': evaluable, 'signal_value': signal,
                   'official_citation_present': assessment.get('official_citation_present')
                       if evaluable and type(assessment.get('official_citation_present')) is bool else None,
                   'client_mode': observed_row.get('client_mode') if observed_row.get('client_mode') in ('consumer', 'work') else None,
                   'model_label': observed_row.get('model_label') if isinstance(observed_row.get('model_label'), str)
                                  and 1 <= len(observed_row['model_label'].strip()) <= 100 else None,
                   'unknown_reason': reason}
            stage['cases'].append(row)
            stage['case_ids'].append(identifier)
            if evaluable:
                valid_rows.append(observed_row)
        stage['planned_cases'] = len(stage['cases'])
        stage['evaluable_cases'] = sum(row['evaluable'] for row in stage['cases'])
        stage['known_cases'] = sum(type(row['signal_value']) is bool for row in stage['cases'])
        stage['observed_cases'] = sum(row['signal_value'] is True for row in stage['cases'])
        stage['unknown_case_ids'] = [row['case_id'] for row in stage['cases'] if row['signal_value'] is None]
        # Keep per-case signals visible, but never aggregate different or unknown modes/models.
        comparable = _conditions([row for row in stage['cases'] if row['evaluable']])
        stage['conditions_match'] = comparable['matched']
        stage['condition_reasons'] = comparable['reasons']
        if comparable['matched']:
            if stage['observed_cases']:
                stage['state'] = 'observed'
            elif stage['planned_cases'] and stage['known_cases'] == stage['planned_cases']:
                stage['state'] = 'not_observed'
    result['comparability'] = _conditions(valid_rows)
    if not result['comparability']['matched']:
        return result
    natural, brand, guided = result['stages']
    public_facts = [fact for fact in _rows(cycle.get('facts'), 30)
                    if fact.get('confirmed') is True and public_url(fact.get('source_url'))]
    if (brand['state'] == 'not_observed'
            and all(row['official_citation_present'] is False for row in brand['cases'])):
        if not public_facts:
            result['priorities'].append({'key': 'identity_evidence', 'label': '先补可核对的身份依据',
                'reason': '点名题未观察到官网来源，且本轮尚无带公开链接的已确认事实。先核对产品名称、运营主体与对应官方页面，再用于写作。',
                'case_ids': brand['case_ids'], 'action_key': 'review_facts'})
        guided_note = ('给网址题观察到官网来源，仍不能替代不带网址的品牌检索。' if guided['state'] == 'observed'
                       else '给网址题来源尚未确认，先核对同一原回答的来源记录。' if guided['state'] == 'unknown'
                       else '给网址题也未观察到官网来源，需同时核对网页内容与可抓取性。')
        result['priorities'].append({'key': 'retrieval_discovery', 'label': '优先补品牌检索发现路径',
            'reason': '点名题未观察到官网来源。' + guided_note + '核对名称与运营关系、索引及对应公开渠道，完成后沿用原题复测。',
            'case_ids': brand['case_ids'] + guided['case_ids'], 'action_key': 'review_webmaster'})
    if natural['state'] == 'not_observed':
        result['priorities'].append({'key': 'scenario_coverage', 'label': '补自然需求的场景内容',
            'reason': '本轮不点名的自然需求题未提及本对象。围绕原题补有依据的适用场景和选型内容；点名题或给网址题不能代替这一步验收。',
            'case_ids': natural['case_ids'], 'action_key': 'review_drafts'})
    return result
