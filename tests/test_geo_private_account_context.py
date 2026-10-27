"""Synthetic account-context claims must never measure public GEO exposure."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from services.geo_test_reports import list_saved_reports, project_report_evaluation, read_saved_report
from services.geo_test_service import assess_answer, build_report
from tests.test_geo_tests import answer, plan, profile


class PrivateAccountContextTests(unittest.TestCase):
    def assess(self, response, mode='work'):
        raw = answer('合成问题', response=response, environment={'client_mode': mode},
                     citations=[{'url': 'https://tongyuye.com/'}],
                     displayed_sources=[{'url': 'https://tongyuye.com/'}])
        before = copy.deepcopy(raw)
        assessed = assess_answer(profile(), raw, '合成问题')
        self.assertEqual(raw, before)
        return assessed

    def test_explicit_internal_record_claims_exclude_mentions_and_public_source_hits(self):
        claims = (
            '内部资料已显示示例甲在做接口服务。',
            '内部工作记录中出现过样例硬件研发项目。',
            '内部周报中出现过样例工具的开发测试。',
            '内部飞书工作记录可作为其产品迭代的旁证。',
            '我已查阅了企业内部知识库，找到相关业务说明。',
            '根据你们的飞书文档，样例服务近期已有更新。',
            '我查询了飞书内部文档，补充了业务信息。',
            '我先在企业内部资料中检索样例产品。',
            '我已在企业内部知识库中检索到样例产品的信息。',
            '内部资料中检索到了样例产品的说明。',
            '企业内部资料中已检索到样例产品。',
            '基于企业内部官方资料检索，下面整理样例产品的信息。',
            '我从团队内部官方文档里查询了样例产品的使用范围。',
        )
        for claim in claims:
            with self.subTest(claim=claim):
                result = self.assess(claim + '懂AI中转站提供相关产品。')
                self.assertFalse(result['answer_evaluable'])
                self.assertEqual(result['answer_error'], 'private_account_context')
                for field in ('brand_mentioned', 'official_source_present', 'official_citation_present',
                              'company_mentioned', 'related_company_mentioned'):
                    self.assertIsNone(result[field])
                self.assertEqual(result['matched_terms'], [])
                self.assertEqual(result['mention_evidence'], [])
                self.assertIn('内部资料', result['evaluation_note'])

    def test_product_descriptions_denials_and_proposed_actions_remain_evaluable(self):
        public = (
            '飞书支持企业内部知识库搜索，官网有公开的产品介绍。',
            '企业可使用飞书管理内部文档，本文依据公开官网。',
            '我未根据企业内部知识库作出判断，仅核对了公开信息。',
            '我没有结合内部资料，仅使用公开官网。',
            '无法访问内部资料，以下仅依据公开信息。',
            '内部资料未显示任何相关记录，不能作为依据。',
            '如果根据内部资料判断，还需要额外核验。',
            '建议结合企业内部知识库确认后再决策。',
            '后续可以根据内部资料再进行内部评审。',
            '公开新闻提到飞书升级了企业搜索功能。',
            '仅使用公开信息，不在企业内部资料中检索。',
            '我不能在企业内部资料中检索，仅核对公开官网。',
            '我未在企业内部资料中检索任何文档，仅使用公开来源。',
            '我在企业内部资料中未检索到相关内容，以下仅依据公开官网。',
            '我在企业内部资料中检索不到相关内容，以下仅依据公开官网。',
            '请你在企业内部资料中检索样例产品，再核对公开来源。',
            '请你根据企业内部官方资料检索结果作出内部判断。',
            '如果我先在企业内部资料中检索，再核对官网是否更好？',
            '如果使用企业内部官方资料检索，需要获得额外权限。',
            '产品支持基于企业内部官方资料检索，本文只介绍公开功能。',
            '产品具备基于企业内部官方资料检索的能力。',
            '产品能在企业内部资料中检索到相关文档，官网介绍的是这项功能。',
            '后续将在企业内部资料中检索，当前尚未访问。',
        )
        for response in public:
            with self.subTest(response=response):
                result = self.assess(response)
                self.assertTrue(result['answer_evaluable'])
                self.assertIsNone(result['answer_error'])
        for mode in ('consumer', None):
            result = self.assess('内部资料已显示样例产品正在开发。', mode)
            self.assertTrue(result['answer_evaluable'])

    def test_personal_history_source_claims_are_excluded_in_every_mode(self):
        claims = (
            '补充：从你之前内部工作记录（非公开企业宣传材料），内部业务面向样例客户，但这不属于对外公开可核验的信息。',
            '根据你此前的聊天记录，合成团队在进行样例产品测试。',
            '依据你历史的内部工作记录，合成团队有相关内部计划。',
            '我读取了你的历史聊天记录，补充合成业务信息。',
            '从你过往的内部工作记录中可以看出，样例项目仍在准备中。',
            '你先前的内部工作记录中已显示样例项目的信息。',
            '结合你们之前的内部资料，下面整理一份样例介绍。',
        )
        for mode in ('work', 'consumer', None):
            for claim in claims:
                with self.subTest(mode=mode, claim=claim):
                    result = self.assess(claim, mode)
                    self.assertFalse(result['answer_evaluable'])
                    self.assertEqual(result['answer_error'], 'private_account_context')
                    for field in ('brand_mentioned', 'official_source_present', 'official_citation_present'):
                        self.assertIsNone(result[field])
                    self.assertEqual(result['matched_terms'], [])
                    self.assertEqual(result['mention_evidence'], [])

    def test_personal_history_denials_hypotheses_and_capabilities_are_not_current_use(self):
        public = (
            '我没有使用你之前的内部工作记录，以下仅核对公开官网。',
            '我并未根据你此前的聊天记录判断，仅使用公开来源。',
            '如果从你之前内部工作记录作出判断，还需要额外授权。',
            '假设根据你历史的内部工作记录进行核验，需要先确认权限。',
            '请从你此前的聊天记录中核对，本文只说明操作步骤。',
            '建议参考你之前的内部工作记录，再由你补充事实。',
            '我会根据你此前的聊天记录协助整理，当前尚未访问。',
            '我从你之前的内部工作记录中未检索到相关内容，以下仅依公开信息。',
            '从你之前的内部工作记录中无法确认，本文只引用公开官网。',
            '我从你之前的聊天记录中检索不到相关内容。',
            '该产品能够从你之前的内部工作记录中检索信息，这是公开介绍的产品功能。',
            '该工具会根据你此前的聊天记录生成摘要，本文介绍的是功能。',
            '产品具备从你此前的内部工作记录中检索的能力。',
            '根据你此前公开的工作记录，下面只引用公开材料。',
        )
        for mode in ('work', 'consumer', None):
            for response in public:
                with self.subTest(mode=mode, response=response):
                    result = self.assess(response, mode)
                    self.assertTrue(result['answer_evaluable'])
                    self.assertIsNone(result['answer_error'])

    def test_personal_history_supplement_contaminates_an_otherwise_public_answer(self):
        response = ('以下信息根据公开官网整理，样例产品提供公开介绍的功能。\n'
                    '补充：从你之前内部工作记录（非公开企业宣传材料），样例团队另有内部计划。')
        for mode in ('work', 'consumer', None):
            with self.subTest(mode=mode):
                self.assertEqual(self.assess(response, mode)['answer_error'], 'private_account_context')

    def test_unrelated_negation_and_contrast_do_not_hide_personal_history_claims(self):
        claims = (
            '公开信息不足但我已根据你此前的聊天记录补充了样例业务信息。',
            '这不是公开来源而是从你此前的聊天记录提取的样例业务信息。',
            '公开记录不多然而我已参考你之前的内部工作记录补充样例介绍。',
            '公开信息不完整，我已根据你此前的聊天记录补充样例内容。',
        )
        for mode in ('work', 'consumer', None):
            for claim in claims:
                with self.subTest(mode=mode, claim=claim):
                    self.assertEqual(self.assess(claim, mode)['answer_error'], 'private_account_context')

    def test_adjacent_negation_still_denies_the_personal_retrieval_claim(self):
        public = (
            '我没有根据你此前的聊天记录补充内容，只引用公开官网。',
            '我并未真正参考你之前的内部工作记录，以下只有公开来源。',
            '我不能直接从你此前的聊天记录获取材料，本文只使用官网。',
            '这不是从你此前的聊天记录提取的信息，而是公开官网内容。',
            '公开信息不足但我没有根据你此前的聊天记录作出判断。',
            '这不是公开来源而是示例，但我未使用你之前的内部工作记录。',
        )
        for mode in ('work', 'consumer', None):
            for response in public:
                with self.subTest(mode=mode, response=response):
                    self.assertTrue(self.assess(response, mode)['answer_evaluable'])

    def test_product_summary_feature_without_modal_is_not_current_personal_retrieval(self):
        descriptions = (
            '该产品根据你此前的聊天记录生成摘要，本文介绍公开产品功能。',
            '该工具根据你此前的聊天记录生成摘要，本文未读取个人资料。',
            '这款软件根据你之前的内部工作记录整理摘要，这是公开功能说明。',
        )
        actual_claims = (
            '我已通过该工具根据你此前的聊天记录生成摘要。',
            '该工具已根据你此前的聊天记录生成摘要。',
            '该产品根据你此前的聊天记录生成了样例摘要。',
        )
        for mode in ('work', 'consumer', None):
            for response in descriptions:
                with self.subTest(mode=mode, description=response):
                    self.assertTrue(self.assess(response, mode)['answer_evaluable'])
            for claim in actual_claims:
                with self.subTest(mode=mode, current_claim=claim):
                    self.assertEqual(self.assess(claim, mode)['answer_error'], 'private_account_context')

    def test_personal_capability_match_is_not_reclassified_by_work_generic_records(self):
        descriptions = (
            '该产品根据你的内部工作记录生成摘要，本文介绍公开产品功能。',
            '该工具会根据你的内部工作记录生成摘要，本文介绍公开产品功能。',
        )
        for mode in ('work', 'consumer', None):
            for response in descriptions:
                with self.subTest(mode=mode, response=response):
                    self.assertTrue(self.assess(response, mode)['answer_evaluable'])

    def test_capability_match_suppression_does_not_hide_later_actual_claim(self):
        response = ('该产品根据你的内部工作记录生成摘要'
                    '但我已根据你的内部工作记录补充了样例说明。')
        for mode in ('work', 'consumer', None):
            with self.subTest(mode=mode):
                self.assertEqual(self.assess(response, mode)['answer_error'], 'private_account_context')

    def test_legacy_personal_history_claim_is_projected_for_all_modes_without_mutation(self):
        baseline, _ = self.report()
        for mode in ('work', 'consumer', None):
            value = copy.deepcopy(baseline)
            value['execution']['client_mode'] = mode
            first = value['cases'][0]
            first.update(status='success', client_mode=mode,
                response='补充：从你之前内部工作记录（非公开企业宣传材料），样例计划仅是内部记录。')
            first['assessment'].update(answer_evaluable=True, answer_error=None, brand_mentioned=True)
            before = copy.deepcopy(value)
            projected = project_report_evaluation(value)
            with self.subTest(mode=mode):
                self.assertEqual(value, before)
                self.assertEqual(projected['cases'][0]['status'], 'private_account_context')
                self.assertFalse(projected['cases'][0]['assessment']['answer_evaluable'])
                self.assertIsNone(projected['cases'][0]['assessment']['brand_mentioned'])
                self.assertEqual(projected['cases'][0]['response'], first['response'])
                self.assertFalse(projected['evaluation_projection']['new_collection_performed'])
                self.assertEqual(projected['run_id'], value['run_id'])
                self.assertEqual(projected['plan_sha256'], value['plan_sha256'])

    def report(self):
        current = plan()
        state = {'run_id': 'synthetic-private-context', 'status': 'completed',
                 'execution': {'client_mode': 'work'}, 'cases': {}}
        for case in current['cases']:
            raw = answer(case['question'], response='公开资料介绍了懂AI中转站。',
                         environment={'client_mode': 'work'}, timestamp='2026-10-20T10:00:00+08:00')
            state['cases'][case['id']] = {'status': 'success', 'result': raw}
        private = current['cases'][0]
        state['cases'][private['id']]['result']['response'] = '内部资料已显示懂AI中转站有相关服务。'
        return build_report(current, state), state

    def test_new_report_keeps_raw_answer_but_excludes_only_contaminated_denominator(self):
        report, state = self.report()
        first = report['cases'][0]
        self.assertEqual(first['status'], 'private_account_context')
        self.assertEqual(first['response'], state['cases'][first['id']]['result']['response'])
        self.assertEqual(first['collected_at'], '2026-10-20T10:00:00+08:00')
        self.assertEqual(report['status'], 'completed_with_gaps')
        self.assertEqual(report['cohorts']['discovery']['answers_evaluable'], 1)
        self.assertEqual(report['cohorts']['discovery']['brand_mention_rate'], 1)

    def test_legacy_projection_falls_back_to_work_execution_and_preserves_consumer_override(self):
        report, _ = self.report()
        report['cases'][0]['status'] = 'success'
        report['cases'][0]['assessment'].update(answer_evaluable=True, answer_error=None, brand_mentioned=True)
        report['cases'][0].pop('client_mode')
        report['cases'][1]['response'] = report['cases'][0]['response']
        report['cases'][1]['client_mode'] = 'consumer'
        before = copy.deepcopy(report)
        projected = project_report_evaluation(report)
        self.assertEqual(report, before)
        self.assertEqual(projected['cases'][0]['status'], 'private_account_context')
        self.assertFalse(projected['cases'][0]['assessment']['answer_evaluable'])
        self.assertTrue(projected['cases'][1]['assessment']['answer_evaluable'])
        self.assertEqual(projected['cohorts']['discovery']['answers_evaluable'], 1)
        self.assertEqual(project_report_evaluation(projected)['cases'], projected['cases'])

    def test_saved_legacy_report_is_excluded_without_altering_evidence_bytes_or_mtime(self):
        report, _ = self.report()
        first = report['cases'][0]
        first['status'] = 'success'
        first['assessment'].update(answer_evaluable=True, answer_error=None, brand_mentioned=True)
        report['evaluation_version'] = '1.3'
        report['updated_at'] = '2026-10-20T10:01:00+08:00'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'run-001' / 'report.json'
            path.parent.mkdir()
            path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
            signature = lambda: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            before = signature()
            listing = list_saved_reports(root)[0]
            projected = read_saved_report(root, listing['id'])
            self.assertEqual(signature(), before)
            self.assertEqual(projected['cases'][0]['response'], first['response'])
            self.assertEqual(projected['cases'][0]['collected_at'], first['collected_at'])
            self.assertEqual(projected['updated_at'], report['updated_at'])
            self.assertEqual(projected['cases'][0]['assessment']['answer_error'], 'private_account_context')
            self.assertEqual(projected['evaluation_projection']['changed_case_ids'], ['q01'])
            self.assertFalse(projected['evaluation_projection']['new_collection_performed'])
            self.assertTrue(projected['evaluation_projection']['source_file_unchanged'])

    def test_version_14_retrieval_claim_is_downgraded_read_only_by_15(self):
        report, _ = self.report()
        first = report['cases'][0]
        first.update(status='success', response='基于企业内部官方资料检索，合成结果仅用于回归。')
        first['assessment'].update(answer_evaluable=True, answer_error=None, brand_mentioned=True)
        report['evaluation_version'] = '1.4'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'synthetic-14-report/report.json'
            path.parent.mkdir()
            path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            projected = read_saved_report(root, list_saved_reports(root)[0]['id'])
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            self.assertEqual(projected['evaluation_version'], '1.5')
            self.assertEqual(projected['evaluation_projection']['stored_evaluation_version'], '1.4')
            self.assertFalse(projected['cases'][0]['assessment']['answer_evaluable'])
            self.assertEqual(projected['cases'][0]['assessment']['answer_error'], 'private_account_context')
            self.assertEqual(projected['plan_sha256'], report['plan_sha256'])
            self.assertEqual(projected['run_id'], report['run_id'])
            self.assertEqual(projected['updated_at'], report['updated_at'])
            self.assertEqual(projected['cases'][0]['response'], first['response'])
