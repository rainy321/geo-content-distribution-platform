"""Pure preparation helpers for explicitly user-provided external baselines.

Preparing questions neither collects an answer nor confirms sampling conditions.
The existing GEO plan validators remain authoritative for every question.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
import hashlib
from itertools import combinations
import json
import re

from services.geo_test_service import (
    LIST_FIELDS, TEXT_FIELDS, case_quotas, normalize_profile, plan_fingerprint, validate_plan,
)


def profile_for_project(rowdict):
    """Normalize one saved project's exact fields, rejecting malformed lists."""
    if not isinstance(rowdict, Mapping):
        raise ValueError('品牌项目资料必须是对象')
    values = {field: copy.deepcopy(rowdict.get(field, '')) for field in TEXT_FIELDS}
    values['entity_type'] = rowdict.get('entity_type', 'product')
    for field in (*LIST_FIELDS, 'official_urls'):
        value = rowdict.get(field, [])
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, RecursionError) as error:
                raise ValueError(f'品牌项目的 {field} 不是有效JSON文本列表') from error
        if not isinstance(value, list):
            raise ValueError(f'品牌项目的 {field} 必须是文本列表')
        values[field] = copy.deepcopy(value)
    return normalize_profile(values)


def profile_sha256(profile):
    normalized = normalize_profile(profile)
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def question_sha256(question):
    """Hash the original question bytes, preserving whitespace and line endings."""
    if not isinstance(question, str):
        raise ValueError('问题必须是UTF8文本')
    try:
        return hashlib.sha256(question.encode('utf-8')).hexdigest()
    except UnicodeError as error:
        raise ValueError('问题包含无效UTF8字符') from error


def validate_request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', value):
        raise ValueError('请求编号必须是16到100位ASCII字母、数字、下划线或连字符')
    return value


def _make_template(profile, discoveries):
    cases = [{'category': 'discovery', 'question': question, 'intent': '核对无品牌提示下的公开方案发现情况'}
             for question in discoveries]
    cases.append({'category': 'brand_identity',
                  'question': f'请根据公开资料介绍{profile["name"]}的业务范围和主体身份；无法确认的内容请明确说明。',
                  'intent': '核对用户指定品牌的公开身份与业务信息'})
    if profile['website']:
        cases.append({'category': 'website_guided',
                      'question': f'请查看 {profile["website"]} ，核对{profile["name"]}的业务、运营主体与适用边界；无法访问或确认时请明确说明。',
                      'intent': '核对用户明确提供的官网范围'})
    cases = [{'id': f'q{index:02d}', **case} for index, case in enumerate(cases, 1)]
    plan = {'schema_version': '1.0', 'platform': 'doubao_web', 'created_at': None,
            'profile': copy.deepcopy(profile), 'profile_provenance': 'client_supplied_not_verified',
            'planner': {'method': 'template_not_ai', 'independent_verification': False,
                        'note': '固定模板问题，未调用AI或采集；请先核对问题是否适合当前业务。'},
            'cases': cases}
    plan['plan_sha256'] = plan_fingerprint(plan)
    return validate_plan(plan)


def _template_plan(profile):
    quotas = case_quotas(profile, 3)
    industry = profile['industry']
    candidates = []
    if industry:
        candidates.extend([
            f'在{industry}领域，有哪些可供比较的方案，选择时应核对哪些公开资料？',
            f'选择{industry}方案时，如何核对适用范围、费用依据和实际交付边界？',
        ])
    candidates.extend([
        '有哪些适合当前业务需求的可选方案，判断其适用性时应核对哪些公开资料？',
        '在做技术选型时，应如何比较候选方案的使用限制、文档与交付范围？',
        '采用外部解决方案前，需要哪些公开依据来判断兼容性、维护要求与使用成本？',
        '怎样核对一个候选方案的功能边界、部署要求以及后续支持方式？',
        '评估同类工具时，有哪些容易忽略的公开信息与适用前提？',
        '为新需求筛选备选方案时，哪些公开信息最值得逐项核对？',
        '对照同类产品进行选型时，应检查哪些范围限制、可用文档与维护要求？',
        '采购一类工具之前，哪些资料可以用来核对功能边界与后续支持？',
    ])
    # The core validator checks the exact target's brand, aliases, company and
    # official domains. Do not drop identity terms or weaken those checks to
    # make a generic template pass for an unusually broad brand/alias.
    for selected in combinations(dict.fromkeys(candidates), quotas['discovery']):
        try:
            return _make_template(profile, selected)
        except ValueError:
            continue
    raise ValueError('模板问题与品牌、别名或官网范围冲突，请提供经核对的原始问题计划')


def prepare_plan(profile, existing_plan=None):
    """Return reviewable questions; freeze and sampling attestation are separate."""
    normalized = normalize_profile(profile)
    if existing_plan is None:
        plan = _template_plan(normalized)
    else:
        if not isinstance(existing_plan, dict):
            raise ValueError('原始问题计划必须是对象')
        plan = copy.deepcopy(existing_plan)
        validate_plan(plan)
        if plan['profile'] != normalized:
            raise ValueError('原始问题计划与当前品牌项目资料不一致，请重新核对')
        plan['profile_provenance'] = 'client_supplied_not_verified'
        plan['planner'] = {'method': 'user_provided_not_verified', 'independent_verification': False,
                           'note': '问题计划由用户提供，未独立核验其生成或采样过程。'}
    return {'profile': copy.deepcopy(normalized), 'profile_sha256': profile_sha256(normalized),
            'plan': plan, 'plan_sha256': plan['plan_sha256'], 'requires_user_confirmation': True}
