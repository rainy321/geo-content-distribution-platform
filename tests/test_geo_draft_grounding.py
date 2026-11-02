"""Public observed failure excerpts and synthetic source/fact boundaries."""
import copy
import hashlib
import json
import unittest

from services import geo_content_research as research
from tests.test_geo_content_loop import ResearchFixture, facts as fixture_facts


def facts():
    return [
        {'id': 'f01', 'confirmed': True, 'text': '懂AI中转站由北京同于野科技有限公司运营，面向开发者与团队。',
         'source_url': 'https://tongyuye.com/products/dongai/'},
        {'id': 'f02', 'confirmed': True, 'text': '介绍页 https://tongyuye.com/products/dongai/，服务入口 https://api.tongyuye.com/，文档 https://api.tongyuye.com/docs；用途不同。',
         'source_url': 'https://tongyuye.com/products/dongai/'},
        {'id': 'f03', 'confirmed': True, 'text': '提供统一模型API接入，以及API密钥、账户余额、调用用量和已配置服务路由的管理。',
         'source_url': 'https://api.tongyuye.com/docs'},
    ]


def payload(content=None):
    return {'title': '开发团队的接入核对清单', 'summary': '依据资料提供方确认的公开自述整理的接入核对方法。',
            'content': content or '据公开资料自述，产品提供API密钥与账户用量管理。开发团队可按当前文档核对接口要求，实际使用范围以服务说明为准。' * 4,
            'tags': ['开发者接入'], 'used_fact_ids': ['f01', 'f02', 'f03']}


# Exact public excerpts of the completed native reply that failed URL validation.
# No task paths, customer records, prices, account identifiers or raw evidence.
OBSERVED_FAILURE = {
    'title': '懂 AI 中转站核心功能模块解析：密钥、余额、用量与路由管理',
    'summary': '本文基于官方公开页面，梳理懂 AI 中转站的运营主体、服务对象、四大核心管理模块，明确服务边界，同时说明产品定价、资质等信息暂未对外披露。',
    'content': '据官方产品页自述，懂 AI 中转站（DongAI，简称懂 AI）由北京同于野科技有限公司运营，面向开发者与团队。\n'
               '平台支持独立密钥创建与权限管控，可对调用凭证进行统一维护，实现调用身份隔离，保障接口调用安全。\n'
               '底层模型能力归属上游模型供应商，平台仅提供中转接入与运维管理服务。具体可用模型与接入要求，以服务入口页面https://api.tongyuye.com/的实时信息为准。\n'
               '根据现有公开页面内容，以下信息暂无对外公示，后续有待运营方补充：模型调用定价明细、完整计费规则。',
    'tags': ['多模型 API 管理'], 'used_fact_ids': ['f01', 'f02', 'f03'],
}


class DraftGroundingTests(unittest.TestCase):
    def setUp(self):
        self.inputs = {'facts': facts(), 'platform': 'owned_web'}

    def parse(self, value, inputs=None):
        return research.parse_draft(json.dumps(value, ensure_ascii=False), inputs or self.inputs)

    def test_stable_source_first_catalog_deduplicates_only_within_fact(self):
        rows = research.draft_source_references(facts())
        self.assertEqual([(r['token'], r['url']) for r in rows if r['fact_id'] == 'f02'], [
            ('[[ref:f02:u1]]', 'https://tongyuye.com/products/dongai/'),
            ('[[ref:f02:u2]]', 'https://api.tongyuye.com/'),
            ('[[ref:f02:u3]]', 'https://api.tongyuye.com/docs')])
        self.assertEqual(rows, research.draft_source_references(copy.deepcopy(facts())))

    def test_ref_at_eof_and_adjacent_chinese_are_fixed_markdown_links(self):
        item = payload(); item['content'] += '\n入口：[[ref:f02:u2]]，然后核对文档。\n[[ref:f02:u3]]'
        result = self.parse(item)
        self.assertIn('[参考资料](https://api.tongyuye.com/)，然后核对', result['content'])
        self.assertTrue(result['content'].endswith('[参考资料](https://api.tongyuye.com/docs)'))

    def test_plain_platform_and_summary_keep_whitespace_boundaries(self):
        for inputs in ({**self.inputs, 'platform': 'toutiao'},
                       {**self.inputs, 'platform': 'target_custom', 'destination': {'channel': 'toutiao', 'title_limit': 180, 'body_limit': 10000}}):
            with self.subTest(inputs=inputs):
                item = payload(); item['content'] += '\n前[[ref:f02:u2]]后'; item['summary'] = '查阅[[ref:f02:u3]]后核对。'
                result = self.parse(item, inputs)
                self.assertIn('前 https://api.tongyuye.com/ 后', result['content'])
                self.assertEqual(result['summary'], '查阅 https://api.tongyuye.com/docs 后核对。')
                self.assertNotIn('[参考资料]', result['content'])

    def test_unknown_malformed_or_suffixed_refs_are_rejected(self):
        values = ['[[ref:f02:u99]]', '[[ref:f99:u1]]', '[[ref:f02:u0]]', '[[Ref:f02:u1]]',
                  '[[ref: f02:u1]]', '[[ ref:f02:u1]]', '[[ref:f02:u1]', '[[ref',
                  *('[[ref:f02:u2]]' + suffix for suffix in ['/evil', '/未授权', '?x=1', '#frag', '%2Fevil', '@evil.org', '.evil.org', ':8443', '\\evil'])]
        for value in values:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '来源引用|来源编号'):
                item = payload(); item['content'] += '\n' + value; self.parse(item)

    def test_ref_fact_must_be_listed_in_lineage(self):
        item = payload(); item['content'] += '\n[[ref:f02:u2]]'; item['used_fact_ids'] = ['f01']
        with self.assertRaisesRegex(ValueError, 'used_fact_ids'):
            self.parse(item)

    def test_title_and_tags_cannot_hide_refs_or_unknown_urls(self):
        for field in ('title', 'tags'):
            for text in ('[[ref:f02:u2]]', '[[ref:f02:u0]]', 'https://not-confirmed.org/other'):
                item = payload(); item[field] = [text] if field == 'tags' else text
                with self.subTest(field=field, text=text), self.assertRaises(ValueError):
                    self.parse(item)

    def test_rendered_length_is_checked_again(self):
        item = payload(); item['content'] += '\n[[ref:f02:u2]]'
        inputs = {**self.inputs, 'destination': {'title_limit': 180, 'body_limit': len(item['content'])}}
        with self.assertRaisesRegex(ValueError, '长度限制'):
            self.parse(item, inputs)

    def test_unicode_path_and_exact_query_fragment_are_preserved(self):
        for url in ('https://example.org/产品/接入', 'https://example.org/docs?q=value.#anchor.',
                    'https://example.org/docs?q=a-b.c#part-2', 'https://example.org/docs.;'):
            with self.subTest(url=url):
                inputs = {'facts': [{'id': 'f01', 'confirmed': True, 'text': '公开接入说明', 'source_url': url}], 'platform': 'owned_web'}
                item = payload(); item['used_fact_ids'] = ['f01']; item['content'] += '\n[[ref:f01:u1]]'
                catalog = research.draft_source_references(inputs['facts'])
                self.assertEqual(catalog[0]['url'], url)
                self.assertIn('](' + url + ')', self.parse(item, inputs)['content'])

    def test_unsupported_parenthesized_url_is_not_offered_as_truncated_ref(self):
        item = {'id': 'f01', 'confirmed': True, 'text': '文档 https://example.org/docs/(intro)', 'source_url': 'https://example.org/docs/(intro)'}
        self.assertEqual(research.draft_source_references([item]), [])

    def test_independent_source_url_remains_authorized_despite_prose_parenthesis(self):
        for mark in ('(使用说明)', '（使用说明）'):
            item = {'id': 'f01', 'confirmed': True, 'text': '公开文档https://example.org/docs' + mark,
                    'source_url': 'https://example.org/docs'}
            self.assertEqual(research.draft_source_references([item]), [
                {'token': '[[ref:f01:u1]]', 'fact_id': 'f01', 'url': 'https://example.org/docs'}])

    def test_authorized_url_words_are_not_claims_or_capability_evidence(self):
        for url in ('https://example.org/docs/权限管理', 'https://example.org/产品/未公开价格说明'):
            inputs = {'facts': [{'id': 'f01', 'confirmed': True, 'text': '公开接入说明', 'source_url': url}], 'platform': 'owned_web'}
            item = payload(); item['used_fact_ids'] = ['f01']; item['content'] += '\n[[ref:f01:u1]]'
            with self.subTest(url=url):
                self.parse(item, inputs)
        inputs['facts'][0]['text'] = '公开文档位于 https://example.org/docs/权限管理'
        item['content'] += '\n平台支持权限管理。'
        with self.assertRaisesRegex(ValueError, '权限'):
            self.parse(item, inputs)

    def test_refs_do_not_expand_unconfirmed_owner_only_or_private_facts(self):
        for source in ('', 'https://localhost/docs', 'https://127.0.0.1/docs', 'https://example.internal/docs',
                       'https://user:pass@example.org/docs', 'https://example.org/\nprivate'):
            item = {'id': 'f01', 'confirmed': True, 'text': '另有 https://example.org/public', 'source_url': source}
            with self.subTest(source=source):
                self.assertEqual(research.draft_source_references([item]), [])
        self.assertEqual(research.draft_source_references([{**facts()[0], 'confirmed': False}]), [])

    def test_http_source_is_preserved_without_upgrading_rendered_scheme(self):
        item = {'id': 'f01', 'confirmed': True, 'text': '公开说明', 'source_url': 'http://example.org/docs'}
        self.assertEqual(research.draft_source_references([item])[0]['url'], item['source_url'])

    def test_legacy_json_backslashes_and_input_bytes_are_unchanged(self):
        item = payload(); item['content'] += '\n资料：https://api.tongyuye.com/docs\n代码示例 C:\\new\\test 与文字 \\n。'
        raw = json.dumps(item, ensure_ascii=False); before = hashlib.sha256(raw.encode()).hexdigest()
        self.assertEqual(research.parse_draft(raw, self.inputs), item)
        self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(), before)

    def test_ref_rendering_does_not_mutate_original_reply_or_inputs(self):
        item = payload(); item['content'] += '\n[[ref:f02:u2]]'
        raw = json.dumps(item, ensure_ascii=False).encode(); original = bytes(raw); inputs_before = copy.deepcopy(self.inputs)
        research.parse_draft(raw.decode(), self.inputs)
        self.assertEqual(raw, original); self.assertEqual(self.inputs, inputs_before)

    def test_observed_failure_is_not_promoted_by_only_fixing_url_format(self):
        with self.assertRaisesRegex(ValueError, '网址'):
            self.parse(OBSERVED_FAILURE)
        item = copy.deepcopy(OBSERVED_FAILURE)
        item['content'] = item['content'].replace('https://api.tongyuye.com/的', '[[ref:f02:u2]]的')
        with self.assertRaisesRegex(ValueError, '未公开|权限|隔离'):
            self.parse(item)

    def test_permissions_and_isolation_need_specific_positive_facts(self):
        for claim in ('平台支持权限管控。', '平台可实现调用身份隔离。', '平台提供多租户隔离，但具体需要核对文档。'):
            item = payload(); item['content'] += '\n' + claim
            with self.subTest(claim=claim), self.assertRaisesRegex(ValueError, '权限|隔离'):
                self.parse(item)

    def test_permission_management_does_not_authorize_rbac_or_isolation(self):
        inputs = copy.deepcopy(self.inputs); inputs['facts'][2]['text'] += '平台支持权限管理。'
        for claim in ('平台支持RBAC。', '平台支持细粒度权限。', '平台支持权限隔离。', '平台支持租户隔离。'):
            item = payload(); item['content'] += '\n' + claim
            with self.subTest(claim=claim), self.assertRaisesRegex(ValueError, '权限|隔离'):
                self.parse(item, inputs)
        item = payload(); item['content'] += '\n平台支持权限管理。'
        self.parse(item, inputs)

    def test_negative_or_conflicting_facts_cannot_authorize_positive_capability(self):
        inputs = copy.deepcopy(self.inputs); inputs['facts'][2]['text'] += '平台不支持权限管理。'
        item = payload(); item['content'] += '\n平台支持权限管理。'
        with self.assertRaisesRegex(ValueError, '权限'):
            self.parse(item, inputs)
        inputs['facts'][1]['text'] += '平台支持权限管理。'
        with self.assertRaisesRegex(ValueError, '冲突'):
            self.parse(item, inputs)

    def test_normal_conditions_questions_and_boundaries_are_not_capability_claims(self):
        item = payload(); item['content'] += '\n如果需要权限管理，应另行核对实际功能。\n不能仅凭密钥管理推断身份隔离。\n权限隔离是否可用，需要单独确认。'
        self.parse(item)
        item['content'] += '\n不能据此推断角色权限，或租户隔离等高级能力。'
        self.parse(item)
        item['content'] += '\n不能据此推断角色权限，但本产品支持租户隔离。'
        with self.assertRaisesRegex(ValueError, '隔离'):
            self.parse(item)

    def test_page_nondisclosure_cannot_become_denied_capability(self):
        inputs = copy.deepcopy(self.inputs); inputs['facts'][0]['text'] += '产品页未公开权限管理功能。'
        item = payload(); item['content'] += '\n该产品不支持权限管理。'
        with self.assertRaisesRegex(ValueError, '权限'):
            self.parse(item, inputs)
        inputs['facts'][0]['text'] += '本产品页未公开租户隔离。'
        item['content'] = payload()['content'] + '\n该产品不提供租户隔离。'
        with self.assertRaisesRegex(ValueError, '隔离'):
            self.parse(item, inputs)

    def test_missing_brief_information_cannot_prove_global_price_absence(self):
        item = payload(); item['summary'] = '产品定价、资质等信息暂未对外披露。'
        inputs = {**self.inputs, 'brief': {'missing_evidence': ['尚未公开定价'], 'outline': ['全站没有价格']}}
        with self.assertRaisesRegex(ValueError, '未公开|不存在'):
            self.parse(item, inputs)
        item = payload(); item['content'] += '\n该产品未公开定价仍需核对。'
        with self.assertRaisesRegex(ValueError, '未公开|不存在'):
            self.parse(item)

    def test_exact_page_observation_cannot_lose_its_scope(self):
        inputs = copy.deepcopy(self.inputs); inputs['facts'][0]['text'] += '本产品介绍页未披露模型定价明细。'
        item = payload(); item['content'] += '\n本产品介绍页未披露模型定价明细。'
        self.parse(item, inputs)
        item['content'] = item['content'].replace('本产品介绍页未披露模型定价明细。', '未披露模型定价明细。')
        with self.assertRaisesRegex(ValueError, '同范围'):
            self.parse(item, inputs)

    def test_current_public_pricing_blocks_stale_global_absence_even_if_confirmed(self):
        inputs = copy.deepcopy(self.inputs); inputs['facts'][0]['text'] += '产品未公开价格。'
        inputs['facts'][1]['text'] += '官网已提供公开价格页面。'
        item = payload(); item['content'] += '\n产品未公开价格。'
        with self.assertRaisesRegex(ValueError, '公开价格'):
            self.parse(item, inputs)

    def test_known_raw_urls_do_not_authorize_new_paths_queries_or_fragments(self):
        for url in ('https://api.tongyuye.com/另一个路径', 'https://api.tongyuye.com/docs?x=1',
                    'https://api.tongyuye.com/docs#new', 'https://api.tongyuye.com.evil.org/',
                    'https://api.tongyuye.com/，未授权后缀'):
            item = payload(); item['content'] += '\n' + url
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, '网址'):
                self.parse(item)


class DraftPromptGroundingTests(ResearchFixture, unittest.TestCase):
    def test_prompt_supplies_catalog_and_preserves_hypotheses_as_nonfacts(self):
        self.prepare_facts()
        prompt = research.draft_prompt(self.cycle, 'b01', 'owned_web')
        inputs = research.draft_input(self.cycle, 'b01', 'owned_web')
        self.assertIn('[[ref:f01:u1]]', prompt)
        self.assertEqual(inputs['source_references'][0]['url'], fixture_facts()[0]['source_url'])
        self.assertIn('不证明产品或全站未公开', prompt)
        self.assertIn('API密钥管理不能自动推导', prompt)

