"""Local publication intent and operator review, never platform verification.

A source URL is useful research evidence. It does not authorize opening an
account session, preparing a target article, or writing to an editor.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from services.geo_source_url import trusted_source_url


PUBLICATION_USES = frozenset(('research_only', 'editorial', 'commercial'))
REVIEW_STATES = frozenset(('unknown', 'suitable', 'unsuitable'))
INACTIVE_STATES = frozenset(('disabled', 'archived', 'deleted'))

# Product restrictions for technical communities, matched by actual route.
# A client-supplied name, channel, style or review cannot grant an exception.
COMMUNITY_POLICIES = (
    ({'key': 'tencent_community', 'name': '腾讯云开发者社区',
      'rules_url': 'https://cloud.tencent.com/developer/article/1006434',
      'commercial_restricted': True, 'automation_restricted': False,
      'note': '开发者社区普通技术投稿不用于广告推广或站外引流；AI 内容要求仍需按当前规则逐篇核对。'},
     ((frozenset(('cloud.tencent.com',)), '/developer'),
      (frozenset(('developer.cloud.tencent.com',)), '/'))),
    ({'key': 'aliyun_community', 'name': '阿里云开发者社区',
      'rules_url': 'https://developer.aliyun.com/article/1639804',
      'commercial_restricted': True, 'automation_restricted': False,
      'note': '开发者社区普通博文不用于软文推广或单纯站外引流；这不是对所有 AI 使用的统一禁令。'},
     ((frozenset(('developer.aliyun.com',)), '/'),)),
    ({'key': 'csdn_blog', 'name': 'CSDN 普通博客投稿',
      'rules_url': 'https://blog.csdn.net/blogdevteam/article/details/113122012',
      'commercial_restricted': True, 'automation_restricted': False,
      'note': '当前普通博客与 Markdown 编辑路径不用于商业推广；不涵盖平台另行提供的商业合作或推广产品。'},
     ((frozenset(('blog.csdn.net',)), '/'),
      (frozenset(('editor.csdn.net',)), '/md'))),
    ({'key': 'cnblogs_automation', 'name': '博客园',
      'rules_url': 'https://about.cnblogs.com/guidelines',
      'commercial_restricted': True, 'automation_restricted': True,
      'note': '园规限制批量注册、登录、发布及机器生成内容；本软件 AI 自动化目标流程仅保留来源研究与导出，不启动登录或投递。'},
     ((frozenset(('cnblogs.com', 'www.cnblogs.com', 'i.cnblogs.com', 'account.cnblogs.com')), '/'),)),
)


def normalize_publication_intent(config):
    """Normalize explicit configuration writes; never mutate legacy reads."""
    use = config.get('publication_use', 'research_only')
    if not isinstance(use, str) or use not in PUBLICATION_USES:
        raise ValueError('平台用途应为仅研究、技术分享或商业推广')
    review = config.get('rule_review', {})
    if not isinstance(review, dict):
        raise ValueError('请核对平台用途与内容规则')
    state = review.get('state', 'unknown')
    confirmed = review.get('operator_confirmed', False)
    note = review.get('note', '')
    if not isinstance(state, str) or state not in REVIEW_STATES:
        raise ValueError('平台规则核对结果应为未知、适合或不适合')
    if type(confirmed) is not bool:
        raise ValueError('平台规则核对确认必须为布尔值')
    if not isinstance(note, str) or len(note) > 400:
        raise ValueError('平台用途核对说明不能超过 400 字')
    return {'publication_use': use, 'rule_review': {
        'state': state, 'operator_confirmed': confirmed, 'note': note.strip()}}


def publication_policy(config):
    """Fixed display metadata, selected from real routes rather than input labels."""
    if not isinstance(config, dict):
        return None
    matches = []
    for field in ('home_url', 'login_url', 'editor_url', 'source_scope', 'publication_scope'):
        trusted = trusted_source_url(config.get(field))
        if trusted is None:
            continue
        route = urlsplit(trusted)
        host, path = route.hostname, route.path
        for policy, routes in COMMUNITY_POLICIES:
            if any(host in hosts and (prefix == '/' or path == prefix or path.startswith(prefix + '/'))
                   for hosts, prefix in routes):
                matches.append(policy)
    if not matches:
        return None
    # A mixed configuration must not hide a stricter editor behind another
    # community's source URL. Returned copies never expose mutable policy data.
    return dict(max(matches, key=lambda policy: policy['automation_restricted']))


def publication_gate(config, state=None):
    """Return a conservative view, including for unreviewed legacy configs."""
    if not isinstance(config, dict):
        config = {}

    def result(code, reason):
        return {'allowed': code == 'allowed', 'code': code, 'reason': reason}

    if config.get('enabled', True) is not True or state in INACTIVE_STATES:
        return result('inactive', '平台已停用，记录仍保留；请在编辑接入配置中明确启用后再操作')
    use = config.get('publication_use', 'research_only')
    if use == 'research_only':
        return result('research_only', '此平台仅用于来源研究，暂不写稿、登录或发布；请先核对用途与平台规则')
    if use not in ('editorial', 'commercial'):
        return result('review_unknown', '平台用途与规则尚未核对，暂不写稿、登录或发布；历史稿件与记录仍保留')
    policy = publication_policy(config)
    if policy and (policy['automation_restricted'] or use == 'commercial' and policy['commercial_restricted']):
        return {**result('platform_restricted', policy['note']),
                'matched_policy': policy['key'], 'rules_url': policy['rules_url']}
    routes = [config[field] for field in ('home_url', 'login_url', 'editor_url', 'source_scope', 'publication_scope')
              if config.get(field)]
    if not routes or any(trusted_source_url(route) is None for route in routes):
        return result('review_unknown', '平台入口网址身份尚未核对，暂不写稿、登录或发布；原始研究资料仍保留')
    review = config.get('rule_review')
    if not isinstance(review, dict):
        review = {}
    if review.get('state') == 'unsuitable':
        return result('review_unsuitable', '已记录此平台不适合当前用途，暂不写稿、登录或发布；历史稿件与记录仍保留')
    if review.get('state') != 'suitable' or review.get('operator_confirmed') is not True:
        return result('review_unknown', '平台用途与规则尚未核对，暂不写稿、登录或发布；历史稿件与记录仍保留')
    return result('allowed', '运营方已核对当前用途与平台规则；这不是平台自动核验，账号权限与单篇审核仍需另行确认')


def assert_publication_allowed(target):
    gate = publication_gate(target.get('config'), target.get('state'))
    if not gate['allowed']:
        raise ValueError(gate['reason'])
