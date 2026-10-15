import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from services.ai_service import AIServiceError, AISettings
from services.doubao_source_collector import CollectorConfig
from services.geo_test_runner import RunConfig, exclusive_lock, run_test_plan, write_json
from services.geo_test_service import (
    assess_answer, build_planning_prompt, build_report, generate_test_plan,
    normalize_profile, parse_ai_plan, validate_plan,
)


def profile():
    return {"name": "懂AI中转站", "company": "北京同于野科技有限公司", "industry": "模型接入",
            "description": "模型接口", "website": "https://tongyuye.com/", "aliases": ["董艾网关"]}


def payload():
    return {"cases": [
        {"category": "discovery", "question": "开发团队如何选择国内模型的统一接口服务？", "intent": "需求发现"},
        {"category": "discovery", "question": "模型接入服务如何比较用量管理和容灾能力？", "intent": "选型"},
        {"category": "brand_identity", "question": "懂AI中转站由谁运营，公开资料是否充分？", "intent": "品牌核验"},
        {"category": "website_guided", "question": "请查看 https://tongyuye.com/ 核验功能和运营主体。", "intent": "官网核验"},
    ]}


def plan():
    return parse_ai_plan(json.dumps(payload(), ensure_ascii=False), profile(), 4,
                         provenance={"method": "fixture_not_real_ai"})


def answer(submitted_question, **overrides):
    return {"question": submitted_question, "question_submitted": True, "question_submission_attempted": True,
            "answer_completed": True, "response": "未找到懂AI中转站的权威信息。",
            "collection_status": "success", "source_extraction_status": "captured",
            "citations": [], "displayed_sources": [{"url": "https://example.org/page"}],
            "visible_source_count": 1, "visible_citation_count": 0, **overrides}


class TestPlanning(unittest.TestCase):
    def test_ai_prompt_adapts_to_different_industries_without_fabrication(self):
        for industry, description in [("工业制造", "数控设备维护"), ("教育", "成人日语学习"), ("养老", "居家陪护服务")]:
            data = {"name": "示例客户", "industry": industry, "description": description}
            prompt = build_planning_prompt(data, 3)
            self.assertIn(industry, prompt)
            self.assertIn(description, prompt)
            self.assertIn('"website_guided": 0', prompt)
            self.assertIn("不可编造", prompt)

    def test_real_provider_contract_and_no_key_in_plan(self):
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": json.dumps(payload(), ensure_ascii=False)}}]}
        post = Mock(return_value=response)
        settings = AISettings("https://api.example.org/v1", "secret-test-token", "model-test")
        result = generate_test_plan(profile=profile(), settings=settings, http_post=post)
        request = post.call_args.kwargs
        self.assertIn("问题设计员", request["json"]["messages"][0]["content"])
        self.assertEqual(request["json"]["temperature"], 0.3)
        self.assertEqual(result["planner"]["method"], "ai_api")
        self.assertNotIn("secret-test-token", json.dumps(result))
        self.assertEqual(validate_plan(result), result)

    def test_discovery_brand_alias_and_domain_leaks_rejected(self):
        for term in ["懂 ai 中转站", "北京同于野科技有限公司", "董艾网关", "TONGYUYE.COM"]:
            data = payload()
            data["cases"][0]["question"] = f"请推荐 {term} 相关的服务"
            with self.assertRaisesRegex(ValueError, "泄漏"):
                parse_ai_plan(json.dumps(data), profile(), 4, provenance={})

    def test_counts_invalid_json_duplicate_and_changed_plan_rejected(self):
        for raw in ["not json", '{"cases":[]}', json.dumps({"cases": payload()["cases"], "extra": True})]:
            with self.assertRaises(ValueError):
                parse_ai_plan(raw, profile(), 4, provenance={})
        data = payload()
        data["cases"][1] = copy.deepcopy(data["cases"][0])
        with self.assertRaisesRegex(ValueError, "重复"):
            parse_ai_plan(json.dumps(data), profile(), 4, provenance={})
        changed = plan()
        changed["cases"][0]["question"] = "企业如何选择国内模型接口服务？"
        with self.assertRaisesRegex(ValueError, "变更"):
            validate_plan(changed)
        changed["cases"] = 42
        with self.assertRaisesRegex(ValueError, "列表"):
            validate_plan(changed)

    def test_missing_context_and_invalid_types_fail_before_ai_call(self):
        with self.assertRaises(ValueError):
            normalize_profile({"name": "空白客户"})
        with self.assertRaises(ValueError):
            build_planning_prompt(profile(), True)
        with self.assertRaises(ValueError):
            normalize_profile({**profile(), "keywords": "模型"})


class TestAssessment(unittest.TestCase):
    def test_negative_mention_is_not_classified_as_recommendation(self):
        result = assess_answer(profile(), answer("测试问题"), "测试问题")
        self.assertTrue(result["brand_mentioned"])
        self.assertIn("未找到", result["mention_evidence"][0]["quote"])
        self.assertIsNone(result["recommended"])
        self.assertIsNone(result["fact_accuracy"])

    def test_service_capacity_text_excluded_even_from_legacy_success_result(self):
        result = assess_answer(profile(), answer('测试问题', response='算力紧张，办公能力得晚点才能用了。开通订阅，享高峰期优先使用。'), '测试问题')
        self.assertFalse(result['answer_evaluable'])
        self.assertIsNone(result['brand_mentioned'])
        self.assertEqual(result['answer_error'], 'service_capacity_unavailable')

    def test_partial_zero_and_unverified_absence_remain_unknown(self):
        for extra in [{"collection_status": "partial"}, {"source_extraction_status": "none_detected"}]:
            result = assess_answer(profile(), answer("测试问题", displayed_sources=[], **extra), "测试问题")
            self.assertIsNone(result["official_source_present"])
        result = assess_answer(profile(), answer("测试问题", collection_status="partial",
                                                 displayed_sources=[{"url": "https://docs.tongyuye.com/a"}]), "测试问题")
        self.assertTrue(result["official_source_present"])

    def test_domain_matching_ignores_spoofed_suffix_query_and_credentials(self):
        invalid = ["https://tongyuye.com.evil.org/x", "https://evil.org/?url=https://tongyuye.com",
                   "https://tongyuye.com@evil.org", "https://user@tongyuye.com/x"]
        for url in invalid:
            result = assess_answer(profile(), answer("测试问题", displayed_sources=[{"url": url}]), "测试问题")
            self.assertIsNot(result["official_source_present"], True)
        result = assess_answer(profile(), answer("测试问题", citations=[{"url": "https://www.tongyuye.com/a"}]), "测试问题")
        self.assertTrue(result["official_citation_present"])
        self.assertFalse(result["official_source_present"])

    def test_unresolved_citation_urls_cannot_prove_official_site_absent(self):
        result = assess_answer(profile(), answer('测试问题', displayed_sources=[], citations=[{'title': '来源', 'url': None}]), '测试问题')
        self.assertIsNone(result['official_source_present'])
        self.assertIsNone(result['official_citation_present'])

    def test_wrong_question_incomplete_and_similar_company_not_scored(self):
        for extra in [{"answer_completed": False}, {"question": "另一题"}, {"question_submitted": False}]:
            result = assess_answer(profile(), answer("测试问题", **extra), "测试问题")
            self.assertIsNone(result["brand_mentioned"])
        result = assess_answer(profile(), answer("测试问题", response="北京同人于野科技有限公司"), "测试问题")
        self.assertFalse(result["brand_mentioned"])


class TestRunner(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = RunConfig(self.root / "run", CollectorConfig(profile_dir=self.root / "profile"))
        self.plan = plan()
        self.sleep_calls = []

    async def sleep(self, duration):
        self.sleep_calls.append(duration)

    async def collect_ok(self, config):
        state = json.loads((self.config.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertIn("running", [entry["status"] for entry in state["cases"].values()])
        return answer(config.question), config.output_root / "result.json"

    async def test_serial_run_fixed_plan_cohorts_and_safe_completed_resume(self):
        report = await run_test_plan(self.plan, self.config, collect=self.collect_ok, sleep=self.sleep)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(len(self.sleep_calls), 3)
        self.assertEqual(report["cohorts"]["discovery"]["answers_evaluable"], 2)
        self.assertEqual(report["cohorts"]["brand_identity"]["answers_evaluable"], 1)
        self.assertEqual(report["plan_sha256"], self.plan["plan_sha256"])
        async def unexpected(_):
            self.fail("Completed questions must not be submitted again")
        await run_test_plan(self.plan, self.config, resume=True, collect=unexpected)

    async def test_verification_stops_batch_and_only_unsubmitted_case_resumes(self):
        calls = []
        async def blocked(config):
            calls.append(config.question)
            return answer(config.question, collection_status="need_action", answer_completed=False,
                          response="", question_submitted=False, question_submission_attempted=False,
                          failure_reason="human_verification_required"), self.root / "blocked.json"
        report = await run_test_plan(self.plan, self.config, collect=blocked, sleep=self.sleep)
        self.assertEqual(report["status"], "stopped")
        self.assertEqual(len(calls), 1)
        self.assertIsNone(report["cohorts"]["discovery"]["brand_mention_rate"])
        report = await run_test_plan(self.plan, self.config, resume=True, collect=self.collect_ok, sleep=self.sleep)
        self.assertEqual(report["status"], "completed")

    async def test_submitted_timeout_is_not_retried_on_resume(self):
        async def failed(config):
            return answer(config.question, collection_status="failed", answer_completed=False,
                          response="", failure_reason="answer_timeout"), self.root / "failed.json"
        await run_test_plan(self.plan, self.config, collect=failed, sleep=self.sleep)
        questions = []
        async def followup(config):
            questions.append(config.question)
            return await self.collect_ok(config)
        report = await run_test_plan(self.plan, self.config, resume=True, collect=followup, sleep=self.sleep)
        self.assertEqual(len(questions), 3)
        self.assertNotIn(self.plan["cases"][0]["question"], questions)
        self.assertEqual(report["status"], "completed_with_gaps")

    async def test_resume_cannot_mix_consumer_and_work_interfaces(self):
        await run_test_plan(self.plan, self.config, collect=self.collect_ok, sleep=self.sleep)
        other = RunConfig(self.config.run_dir, CollectorConfig(profile_dir=self.root / "profile", client_mode="work"))
        with self.assertRaisesRegex(ValueError, "原界面类型"):
            await run_test_plan(self.plan, other, resume=True, collect=self.collect_ok)

    async def test_exception_or_interrupted_submission_never_replays(self):
        async def interrupted(config):
            raise RuntimeError("browser died after input")
        report = await run_test_plan(self.plan, self.config, collect=interrupted, sleep=self.sleep)
        self.assertEqual(report["status"], "needs_review")
        report = await run_test_plan(self.plan, self.config, resume=True, collect=self.collect_ok, sleep=self.sleep)
        self.assertEqual(report["stop_reason"], "interrupted_submission_unknown")

    async def test_lock_conflicting_runner_cannot_submit(self):
        with exclusive_lock(self.config.collector.profile_dir / ".geo-test.lock"):
            with self.assertRaisesRegex(ValueError, "另一个测试"):
                await run_test_plan(self.plan, self.config, collect=self.collect_ok)

    async def test_cohort_denominators_exclude_failed_answers(self):
        state = {"run_id": "fixture", "status": "stopped", "cases": {case["id"]: {"status": "pending"} for case in self.plan["cases"]}}
        first, brand = self.plan["cases"][0], self.plan["cases"][2]
        state["cases"][first["id"]] = {"status": "success", "result": answer(first["question"], response="其他服务")}
        state["cases"][brand["id"]] = {"status": "success", "result": answer(brand["question"])}
        report = build_report(self.plan, state)
        self.assertEqual(report["cohorts"]["discovery"]["brand_mention_rate"], 0)
        self.assertEqual(report["cohorts"]["discovery"]["answers_evaluable"], 1)
        self.assertEqual(report["cohorts"]["brand_identity"]["brand_mention_rate"], 1)


class TestPlanningApi(unittest.TestCase):
    def setUp(self):
        from sau_backend import app
        from db.createTable import initialize_database
        from services.project_service import create_project
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        path = Path(self.temporary.name) / "test.db"
        initialize_database(path)
        self.project = create_project(path, name="懂AI中转站", product="懂AI中转站", industry="模型接入",
                                      description="模型接口", website="https://tongyuye.com/", keywords=[], competitors=[])
        self.app = app
        self.config_patch = patch.dict(app.config, DATABASE_PATH=path, ACCESS_CONTROL_ENABLED=False, AI_RATE_LIMIT_PER_MINUTE=0, TESTING=True)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.client = app.test_client()

    def test_project_context_and_request_scoped_key_not_persisted(self):
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": json.dumps(payload())}}]}
        with patch('services.geo_test_service.requests.post', return_value=response):
            result = self.client.post('/api/geo-tests/plan', json={
                "project_id": self.project["id"], "company": "北京同于野科技有限公司", "case_count": 4,
                "ai_config": {"base_url": "https://api.example.org", "api_key": "secret-api-key", "model": "test-model"}})
        self.assertEqual(result.status_code, 200)
        data = result.get_json()["data"]
        self.assertEqual(data["profile"]["company"], "北京同于野科技有限公司")
        self.assertNotIn("secret-api-key", json.dumps(data))
        validate_plan(data)

    def test_invalid_input_missing_project_and_bad_model_output(self):
        for body, expected in [(None, 400), ({"project_id": True}, 400), ({"project_id": 999999}, 404),
                               ({"project_id": self.project["id"], "case_count": 999}, 400)]:
            result = self.client.post('/api/geo-tests/plan', json=body)
            self.assertEqual(result.status_code, expected)
        with patch('sau_backend.generate_test_plan', side_effect=AIServiceError("计划格式无效")):
            result = self.client.post('/api/geo-tests/plan', json={"project_id": self.project["id"]})
            self.assertEqual(result.status_code, 502)


if __name__ == '__main__':
    unittest.main()
