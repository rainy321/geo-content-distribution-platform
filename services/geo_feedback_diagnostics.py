"""Read-only next steps from structured evidence; never score an effect or judge facts.

No answer-text interpretation, AI, network, SQL, or publishing belongs here.
Actions are fixed application navigation keys, never caller-provided URLs/code.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit

from services.geo_content_research import public_url
from services.geo_destination_service import CATALOG, scope_matches
from services.geo_source_identity import classify_source_identity, unconfirmed_source_url_status
from services.geo_source_url import trusted_source_url
from services.geo_planning_feedback import planning_feedback


ACTION_KEYS = frozenset(('review_baseline', 'review_external_feedback', 'review_facts', 'review_sources',
    'review_source_maintenance', 'resume_source_maintenance', 'review_destinations', 'review_destination',
    'review_drafts', 'review_publication', 'review_webmaster', 'retest_same_question'))
_REGISTRIES = ('qcc.com', 'qichacha.com', 'tianyancha.com', 'qixin.com', 'aiqicha.baidu.com')
_TARGET_CAPABILITIES = ('platform_assisted_fill', 'configured_fill', 'login_and_export')


def _dict(value):
    return value if isinstance(value, dict) else {}


def _rows(value, maximum=200):
    return [item for item in value[:maximum] if isinstance(item, dict)] if isinstance(value, list) else []


def _id(value):
    if type(value) is int and value > 0:
        return str(value)
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) else None


class _View:
    def __init__(self, cycle_id):
        self.cycle_id = cycle_id
        self.evidence = {}
        self.issues = {}
        self.actions = {}

    def reference(self, kind, identifier, label, **details):
        identifier = _id(identifier)
        if not identifier:
            return None
        key = kind + ':' + identifier
        self.evidence.setdefault(key, {'id': key, 'kind': kind, 'label': label, **details})
        return key

    def issue(self, kind, message, evidence_ids):
        item = self.issues.setdefault(kind, {'id': kind, 'kind': kind, 'message': message, 'evidence_ids': []})
        item['evidence_ids'] = list(dict.fromkeys(item['evidence_ids'] + [key for key in evidence_ids if key]))[:8]

    def action(self, key, label, reason, evidence_ids, *, priority=50, **identifiers):
        if not self.cycle_id or key not in ACTION_KEYS:
            return
        target = {'cycle_id': self.cycle_id}
        for field, value in identifiers.items():
            if field not in ('cycle_id', 'case_id', 'target_id', 'task_id', 'article_id', 'job_id') or not _id(value):
                return
            target[field] = value
        identity = json.dumps([key, target], sort_keys=True)
        item = self.actions.setdefault(identity, {'id': hashlib.sha256(identity.encode()).hexdigest()[:16],
            'key': key, 'label': label, 'reason': reason, 'target': target, 'evidence_ids': [], '_priority': priority})
        item['_priority'] = min(item['_priority'], priority)
        item['evidence_ids'] = list(dict.fromkeys(item['evidence_ids'] + [ref for ref in evidence_ids if ref]))[:8]

    def result(self, *, has_baseline):
        issues = list(self.issues.values())[:8]
        selected = sorted(self.actions.values(), key=lambda item: item['_priority'])[:6]
        actions = [{key: value for key, value in item.items() if key != '_priority'} for item in selected]
        used = {key for item in issues + actions for key in item['evidence_ids']}
        return {'schema_version': 'geo_feedback_diagnostics_v1', 'cycle_id': self.cycle_id, 'read_only': True,
            'summary': {'state': 'needs_review' if has_baseline else 'insufficient_evidence',
                        'message': '按已保存证据列出下一步核对，不能据此判断优化成功。' if has_baseline
                                   else '先核对本轮原始问题与完整回答；缺少有效回答不作零曝光。'},
            'attention_items': issues, 'actions': actions,
            'evidence': [item for key, item in self.evidence.items() if key in used],
            'limitations': ['这是诊断提示，不是事实核实、法律判断、评分或GEO效果结论。',
                '用户外部反馈、原生采集和不同会话条件分别保留，不能互相覆盖或合并成成功率。',
                '已发文章、可访问、索引和AI引用分别核对；任何入口都不会自动发帖、提交或重放原任务。']}


def _baseline(view, cycle):
    if cycle.get('baseline_source') == 'user_reported_external':
        records = _rows(_dict(cycle.get('external_baseline')).get('observations'))
        valid = {}
        for row in records:
            case_id = _id(row.get('case_id'))
            if not case_id:
                continue
            ref = view.reference('probe', row['id'], '用户提供的外部基线原文 ' + case_id,
                                 case_id=case_id, native_verified=False, independent_verification=False)
            if _dict(row.get('after')).get('response') and not _dict(row.get('after')).get('answer_error'):
                valid[case_id] = ref
        ref = view.reference('cycle', view.cycle_id, '外部用户基线，原生指标不可用')
        view.issue('external_baseline_unverified', '本轮来自用户提供的外部原文，采样条件、完整性和内容未独立核验；不进入原生曝光指标，也不能从原文生成客户事实。', [ref])
        return valid
    rows = _rows(_dict(cycle.get('baseline')).get('cases'))
    valid, incomplete = {}, []
    for row in rows:
        identifier = _id(row.get('id'))
        if not identifier:
            continue
        usable = _dict(row.get('assessment')).get('answer_evaluable') is True
        ref = view.reference('baseline', identifier, '原始采集题 ' + identifier,
                             case_id=identifier, answer_evaluable=usable)
        if usable:
            valid[identifier] = ref
        else:
            incomplete.append((identifier, ref))
            view.issue('baseline_incomplete', '存在未完成、排除或不可评价的原回答；先检查原任务，不能把缺失当作零曝光。', [ref])
    if incomplete:
        view.action('review_baseline', '检查原采集任务', '回到原始任务逐题核对提交及回答状态，不自动重复提问。',
                    [ref for _, ref in incomplete], priority=10, case_id=incomplete[0][0])
    if not rows:
        ref = view.reference('cycle', view.cycle_id, '本轮内容迭代记录')
        view.issue('baseline_missing', '还没有可核对的原始问题与回答记录。', [ref])
        view.action('review_baseline', '检查本轮原始采集', '先保存原问题和采集结果，再确定后续动作。', [ref], priority=10)
    return valid


def _feedback(view, cycle, fact_refs):
    probes = [row for row in _rows(cycle.get('probes'), 50) if row.get('cycle_id') == view.cycle_id
              and row.get('observation_role') != 'baseline']
    external = [row for row in probes if row.get('source') == 'user_reported_external' and row.get('native_verified') is False]
    native = [row for row in probes if row.get('source') == 'native_collector' and row.get('native_verified') is True]
    for row in external:
        ref = view.reference('probe', row.get('id'), '用户提供的外部回答', case_id=_id(row.get('case_id')),
                             source='user_reported_external', independent_verification=False)
        conditions = _dict(row.get('conditions'))
        statements = _rows(row.get('context_attestations'), 1000)
        latest = statements[-1] if statements else {}
        missing = (any(conditions.get(key) is not True for key in ('same_question', 'client_mode_match', 'model_label_match'))
                   or latest.get('new_conversation') is not True or latest.get('first_message') is not True)
        if missing:
            view.issue('external_conditions_unconfirmed', '外部原文已保留；同题、模式、模型或新会话条件仍有未确认项，不与原生结果合并。', [ref])
        else:
            view.issue('external_user_statement', '外部采样条件来自用户声明，仍不是软件原生采集或独立条件核验。', [ref])
        related = [item for item in native if item.get('case_id') == row.get('case_id')]
        native_refs = [view.reference('probe', item.get('id'), '原生单题采集记录', case_id=_id(item.get('case_id')),
                                      source='native_collector') for item in related[:3]]
        if related:
            view.issue('separate_feedback_conditions', '外部反馈与原生单题记录应并列核对；工作模式或另一份回答不能覆盖外部原文。', [ref, *native_refs])
        reviews = []
        for item in [row, *related]:
            current = item.get('business_relationship_review')
            history = item.get('business_relationship_reviews')
            if history is None:
                history = [current] if isinstance(current, dict) else []
            elif (not isinstance(history, list) or any(not isinstance(entry, dict) for entry in history)
                    or (history[-1] if history else None) != current):
                history = []
            reviews.extend(entry for entry in history
                if entry.get('status') in ('acknowledged', 'not_acknowledged')
                and entry.get('by') in ('assistant', 'operator')
                and entry.get('scope') == 'manual_or_assistant_business_material_acknowledgment_not_automated_fact_accuracy')
        if len({item['status'] for item in reviews}) > 1:
            view.issue('attributed_review_disagreement', '已有明确署名的业务材料核对意见存在差异或历史变化；回到原文与当时确认资料，不能按名称出现判为成功。', [ref, *native_refs, *fact_refs])
        identifiers = {'case_id': row['case_id']} if _id(row.get('case_id')) else {}
        view.action('review_external_feedback', '核对外部反馈原文与条件', '保留本次原文，补充确知条件；未知项保持未知，不以另一模式回答替代。',
                    [ref, *native_refs], priority=15, **identifiers)


def _maintenance(view, cycle):
    profile, overview = _dict(cycle.get('profile')), _dict(cycle.get('source_maintenance'))
    if profile.get('entity_type') != 'company' or not isinstance(profile.get('name'), str) or not profile['name'].strip():
        return False
    tasks = [task for task in _rows(overview.get('tasks'), 50)
             if task.get('company_name') == profile.get('name') and _id(task.get('id')) and _id(task.get('cycle_id'))]
    for task in tasks[:3]:
        ref = view.reference('maintenance', task['id'], '已有公司来源资料维护请求',
                             cycle_id=task['cycle_id'], stage=task.get('stage'))
        uncertain = (task.get('read_only') is True or task.get('write_attempted') is True
                     or task.get('stage') in ('submitted_pending_review', 'submission_unknown', 'opening',
                                              'awaiting_human_verification', 'waiting_verification', 'needs_manual_review'))
        view.issue('maintenance_existing', '已有公司资料维护请求；待审核、验证或提交结果未知时回原请求核对，不能另开申请或据此认定公开资料已更新。', [ref])
        view.action('resume_source_maintenance', '检查原资料维护请求' if uncertain else '继续原资料维护草稿',
                    '打开已保存的原请求；提交、公开更新和搜索采用分别记录，不自动重发。', [ref], priority=5,
                    cycle_id=task['cycle_id'], task_id=task['id'])
    return bool(tasks)


def _sources(view, cycle, valid_cases, has_maintenance, fact_refs):
    profile = _dict(cycle.get('profile'))
    maintenance = _dict(cycle.get('source_maintenance'))
    targets = [target for target in _rows(cycle.get('destinations'), 50)
               if _id(target.get('id')) and target.get('project_id') == cycle.get('project_id')]
    for source in _rows(_dict(cycle.get('observations')).get('sources')):
        if source.get('case_id') not in valid_cases or not _id(source.get('id')):
            continue
        classification = classify_source_identity(profile, source)
        ref = view.reference('source', source['id'], str(source.get('title') or '来源记录')[:120],
            case_id=source['case_id'], subject_scope=classification['classification'], identity_basis=classification['basis'])
        if classification['classification'] == 'other':
            view.issue('different_source_subject', '部分来源标题指向其他主体或近名公司，不能作为本客户的公司事实或业务采用证据。', [ref])
            view.action('review_sources', '核对来源主体与原页', '先检查完整主体、原网址和页面范围；不把近名条目用于客户事实。', [ref], priority=25)
            continue
        url = None if unconfirmed_source_url_status(source) else trusted_source_url(source.get('url'))
        if not url:
            view.issue('source_url_unknown', '存在只有标题或网址尚未核实的来源；平台名称、标题和名称出现均不证明页面内容或官网被引用。', [ref])
            view.action('review_sources', '核对原回答的来源网址', '检查同一原回答中的来源；只看到标题时继续保留网址和引用状态未知。', [ref], priority=25)
            continue
        if classification['basis'] in ('company_title_not_product', 'official_authority_or_scope_mismatch',
                                        'unconfirmed_relationship', 'ambiguous_legal_name_title', 'multiple_legal_names_title'):
            view.issue('source_scope_unconfirmed', '来源主体、关联关系或产品页面范围未确认；公司条目和同域其他产品页面不能替代本产品事实。', [ref])
            view.action('review_sources', '核对来源范围', '核对当前公司或产品的明确页面范围，不推断运营、股权或法律关系。', [ref], priority=25)
            continue
        host = urlsplit(url).hostname
        registry = next((name for name in _REGISTRIES if host == name or host.endswith('.' + name)), None)
        if registry:
            view.issue('registry_material', '工商资料平台来源应先核对主体资料；它不是广告投稿平台，维护申请也不等于工商变更或AI采用。', [ref])
            if (not has_maintenance and registry in ('qcc.com', 'qichacha.com')
                    and profile.get('entity_type') == 'company' and classification['classification'] == 'target'
                    and maintenance.get('supported') is True):
                view.action('review_source_maintenance', '核对公司资料维护', '依据已确认公开资料准备站内官网或简介补正；先检查是否已有原申请。',
                            [ref, *fact_refs], priority=30)
            else:
                view.action('review_sources', '核对工商来源与维护状态', '先核对正确主体及平台支持的资料维护流程，不向该页面投放广告。', [ref], priority=35)
            continue
        if classification['classification'] == 'target' and classification['basis'] == 'exact_official_host':
            view.issue('official_scope_not_fact_verification', '官网范围已匹配，只说明地址或页面范围；业务事实、索引和本次AI采用仍需分别核对。', [ref])
            view.action('review_facts', '核对官网与公开业务资料', '检查已确认资料及其公开依据，不把官网地址出现当作业务正确或优化成功。', [ref], priority=40)
            continue
        platform = next((key for key, hosts, path, _ in CATALOG
                         if host in hosts and (path == '/' or urlsplit(url).path.startswith(path))), None)
        if platform:
            matched = [target for target in targets
                       if target.get('capability') in _TARGET_CAPABILITIES
                       and scope_matches(url, trusted_source_url(_dict(target.get('config')).get('source_scope')))]
            if matched:
                target = matched[0]
                target_ref = view.reference('destination', target['id'], '本项目已配置的内容平台', capability=target['capability'])
                view.action('review_destination', '检查匹配平台与稿件', '来源匹配本项目已接入目标；先核对规则、正常登录和人工审稿，未完成投递回原任务处理。',
                            [ref, target_ref], priority=45, target_id=target['id'])
            else:
                view.issue('platform_not_connected', '来源网址匹配内容平台，但本项目尚无匹配且受支持的接入目标；不能据引用推断投稿权限。', [ref])
                view.action('review_destinations', '检查内容平台接入', '在软件中核对该平台的实际支持能力、当前规则和账号权限，再决定是否准备内容。', [ref], priority=45)
        else:
            view.issue('external_site_not_publication_target', '第三方官网、文档或未知来源不能当作可投稿平台；先核对引用关系与资料适用范围。', [ref])
            view.action('review_sources', '核对第三方来源用途', '查看原来源范围与依据；不建议去第三方公司官网发广告，也不推断合作关系。', [ref], priority=45)


def _content_and_publications(view, cycle, fact_refs):
    published = False
    blocked_articles = set()
    for blocked in _rows(cycle.get('blocked_publications'), 100):
        article_id = _id(blocked.get('article_id'))
        if not article_id or blocked.get('moderation_state') not in ('pending', 'rejected'):
            continue
        blocked_articles.add(article_id)
        ref = view.reference('article', article_id, '需要核对原审核状态的文章')
        view.issue('publication_moderation_pending', '已有稿件待审核或被拒绝；保留原稿件、版本和原任务，不能当作公开文章或自动另发。', [ref])
        view.action('review_publication', '检查原稿件审核状态', '查看同一稿件的既有审核记录和平台要求，不重复提交来代替核对。',
                    [ref], priority=8, article_id=blocked['article_id'])
    for draft in _rows(cycle.get('drafts'), 50):
        if (draft.get('cycle_id', view.cycle_id) != view.cycle_id
                or draft.get('project_id', cycle.get('project_id')) != cycle.get('project_id')):
            continue
        ref = view.reference('article', draft.get('article_id'), '本轮已有文章或草稿', status=draft.get('status'))
        jobs = [job for job in _rows(draft.get('jobs'), 50) if not job.get('demo')]
        if _id(draft.get('article_id')) in blocked_articles:
            continue
        for job in jobs:
            job_ref = view.reference('publish_job', job.get('id'), '已有真实发布任务', status=job.get('status'))
            if job.get('status') == 'success' and public_url(job.get('result_url')):
                published = True
            elif job.get('status') not in ('cancelled', 'canceled'):
                view.issue('publication_unresolved', '已有发布尝试尚需核对；处理原任务的登录、验证或结果未知，不另开投递来替代。', [ref, job_ref])
                identifiers = {'article_id': draft['article_id']} if _id(draft.get('article_id')) else {}
                if _id(job.get('id')):
                    identifiers['job_id'] = job['id']
                view.action('review_publication', '检查原发布任务', '回到同一文章和原发布任务核对状态，不自动重复提交。', [ref, job_ref], priority=8, **identifiers)
        if not jobs and _id(draft.get('article_id')):
            view.action('review_drafts', '审查已有稿件', '检查稿件与已确认事实及目标规则的一致性；草稿或就绪状态不代表已发布。',
                        [ref, *fact_refs], priority=50, article_id=draft['article_id'])
    urls = cycle.get('publications') if isinstance(cycle.get('publications'), list) else []
    reported = _rows(cycle.get('reported_publications'))
    if any(public_url(url) for url in urls) or any(public_url(row.get('url')) for row in reported) or published:
        ref = view.reference('publication', view.cycle_id, '本轮已有发布记录', basis='separate_publication_evidence')
        view.issue('publication_index_adoption_separate', '已有发布记录不等于搜索索引或AI引用；发布、索引及同题采用状态需分别保留，未核验的引用继续未知。', [ref])
        view.action('review_publication', '核对发布与采用证据', '分别检查发布回执、公开状态和后续同题引用；不因发文完成记为GEO成功。', [ref], priority=40)


def _webmaster(view, cycle):
    overview = _dict(cycle.get('webmaster'))
    if overview.get('supported') is not True:
        return
    record = _dict(overview.get('record'))
    if not record:
        return
    ref = view.reference('webmaster', view.cycle_id, '已保存的本站站长记录')
    sitemap = _dict(record.get('sitemap_submission'))
    inspections = _rows(record.get('inspections'), 50)
    if sitemap.get('status') in ('pending', 'unknown', 'submitted'):
        view.issue('webmaster_request_pending', '已有站长提交或处理中记录；回原站点记录检查，Processing和发现URL数量不表示已索引，不重复提交。', [ref])
        view.action('review_webmaster', '检查原站长提交记录', '核对已有站点验证、站点地图及URL检查状态，保留未知结果。', [ref], priority=20)
    elif (record.get('ownership') or record.get('public_check') or inspections):
        view.issue('webmaster_index_separate', '站点验证、HTTP文件匹配和URL索引记录与AI采用分开；用户报告已索引也不证明AI已经引用。', [ref])
        view.action('review_webmaster', '核对站点与索引证据', '分别检查验证、索引和采用；不把站点地图数量或验证完成当作效果指标。', [ref], priority=55)


def diagnose_cycle(value):
    """Project existing cycle evidence without modifying it or its public metrics."""
    cycle = _dict(value)
    view = _View(_id(cycle.get('id')))
    valid = _baseline(view, cycle)
    facts = [fact for fact in _rows(cycle.get('facts'), 30) if fact.get('confirmed') is True and _id(fact.get('id'))]
    fact_refs = [view.reference('fact', fact['id'], '本轮用户确认的事实资料 ' + str(fact['id']),
                                basis='owner_confirmed_not_independent_fact_check') for fact in facts[:3]]
    if not facts:
        ref = view.reference('cycle', view.cycle_id, '本轮资料核对状态')
        view.issue('confirmed_facts_missing', '尚无本轮已确认事实资料；先核对公开依据，不能从AI原文或近名来源自动生成客户事实。', [ref])
        view.action('review_facts', '核对本轮事实资料', '确认客户公开资料后再用于内容准备，保留资料来源与适用范围。', [ref], priority=20)
    _feedback(view, cycle, fact_refs)
    has_maintenance = _maintenance(view, cycle)
    _sources(view, cycle, valid, has_maintenance, fact_refs)
    _content_and_publications(view, cycle, fact_refs)
    _webmaster(view, cycle)
    if valid and facts and not cycle.get('drafts') and not cycle.get('publications') and not cycle.get('reported_publications'):
        view.action('review_drafts', '准备有依据的内容草稿', '围绕原问题与已确认资料准备人工审稿；不从AI回答直接生成公司事实，也不自动发布。',
                    [next(iter(valid.values())), *fact_refs], priority=60)
    feedback=planning_feedback(cycle)
    feedback_digest=hashlib.sha256(json.dumps(feedback,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    prior=_dict(_dict(cycle.get('analysis')).get('provenance')).get('feedback_inputs_sha256')
    needs_update=bool(feedback and _dict(cycle.get('analysis')).get('method') not in (None,'rule_observations_not_ai') and prior!=feedback_digest)
    if needs_update:
        refs=[view.reference('probe',item['probe_id'],'后来收到的单题或外部反馈',case_id=item['case_id']) for item in feedback[:3]]
        view.action('review_drafts','用后续反馈更新选题','原选题尚未使用当前后续反馈；可重新策划，既有稿件与发布记录保留，不会自动重发。',refs,priority=25)
    result=view.result(has_baseline=bool(valid))
    result['planning_feedback']={'available_count':len(feedback),'context_sha256':feedback_digest,'needs_update':needs_update,
                                 'role':'editorial_signal_only_not_writing_fact_or_public_metric'}
    return result
