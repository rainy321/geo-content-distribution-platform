import unittest

from services.dongclaw_evaluation_service import (
    AUTHORIZED_SUBJECT,
    DongClawEvaluationError,
    build_dongclaw_input_template,
    evaluate_dongclaw_manifest,
    render_dongclaw_markdown,
)


class DongClawEvaluationServiceTests(unittest.TestCase):
    def attempt(
        self,
        *,
        attempt_id,
        phase,
        captured_at,
        response_text,
        replicate=1,
        provider="qwen",
        model="qwen-test-snapshot",
        question_id="cn-discovery",
    ):
        return {
            "id": attempt_id,
            "phase": phase,
            "question_id": question_id,
            "provider": provider,
            "model": model,
            "surface": "api",
            "web_search_enabled": False,
            "replicate": replicate,
            "captured_at": captured_at,
            "status": "success",
            "response_text": response_text,
            "error_code": None,
            "source_reference": f"evidence/{attempt_id}.json",
        }

    def test_empty_template_is_explicitly_not_executed(self):
        report = evaluate_dongclaw_manifest(build_dongclaw_input_template())

        self.assertEqual(report["status"], "NOT_EXECUTED")
        self.assertFalse(report["methodology"]["external_model_called_by_evaluator"])
        self.assertEqual(report["provider_overviews"], [])
        blocker_ids = {item["id"] for item in report["blocking_items"]}
        self.assertIn("REAL_MODEL_OUTPUTS_MISSING", blocker_ids)
        self.assertIn("OFFICIAL_PRODUCT_SOURCE_UNVERIFIED", blocker_ids)
        readiness = {item["id"]: item for item in report["readiness_snapshot"]}
        self.assertEqual(readiness["fixed-question-matrix"]["current"], 7)
        self.assertEqual(readiness["baseline-sample-depth"]["current"], 0)
        self.assertEqual(readiness["baseline-sample-depth"]["target"], 105)
        markdown = render_dongclaw_markdown(report)
        self.assertIn("尚未导入任何 Qwen、DeepSeek 或豆包真实回答", markdown)
        self.assertIn("不是效果证明", markdown)

    def test_paired_short_horizon_result_computes_auditable_delta(self):
        payload = build_dongclaw_input_template()
        payload["study"]["optimized_at"] = "2026-09-10T10:00:00+08:00"
        payload["attempts"] = [
            self.attempt(
                attempt_id="baseline-1",
                phase="baseline",
                captured_at="2026-09-10T09:00:00+08:00",
                response_text="推荐方案：\n1. RanClaw\n2. 其他产品",
            ),
            self.attempt(
                attempt_id="post-1",
                phase="post_optimization",
                captured_at="2026-09-10T14:00:00+08:00",
                response_text=(
                    "推荐方案：\n1. DongClaw 小龙虾\n2. RanClaw\n"
                    "参考 https://example.com/dongclaw"
                ),
            ),
        ]

        report = evaluate_dongclaw_manifest(payload)

        self.assertEqual(report["status"], "SHORT_HORIZON_EXPLORATORY")
        comparison = report["paired_comparison"]
        self.assertEqual(comparison["matched_pair_count"], 1)
        self.assertEqual(comparison["matched_success_pair_count"], 1)
        self.assertEqual(comparison["deltas"]["mention_rate_success"], 1.0)
        self.assertEqual(comparison["deltas"]["first_position_rate_success"], 1.0)
        self.assertEqual(comparison["deltas"]["citation_rate_success"], 1.0)
        unbranded = next(
            item for item in comparison["by_cohort"] if item["cohort"] == "unbranded"
        )
        self.assertEqual(unbranded["matched_success_pair_count"], 1)
        self.assertEqual(len(report["measurement_records"]), 2)
        self.assertIn(
            "OBSERVATION_WINDOW_TOO_SHORT",
            {item["id"] for item in report["blocking_items"]},
        )
        self.assertNotIn("response_text", report["measurement_records"][0])
        self.assertRegex(
            report["measurement_records"][0]["response_sha256"], r"^[0-9a-f]{64}$"
        )
        markdown = render_dongclaw_markdown(report)
        self.assertIn("+100.0 个百分点", markdown)
        self.assertNotIn("其他产品", markdown)

    def test_failed_attempt_is_kept_in_attempt_denominator(self):
        payload = build_dongclaw_input_template()
        success = self.attempt(
            attempt_id="success-1",
            phase="baseline",
            captured_at="2026-09-10T09:00:00+08:00",
            response_text="DongClaw 小龙虾是一个产品。",
        )
        failed = {
            **success,
            "id": "failed-2",
            "replicate": 2,
            "status": "failed",
            "response_text": None,
            "error_code": "PROVIDER_TIMEOUT",
            "source_reference": "evidence/failed-2.json",
        }
        payload["attempts"] = [success, failed]

        report = evaluate_dongclaw_manifest(payload)
        metrics = report["provider_overviews"][0]["metrics"]

        self.assertEqual(report["provider_overviews"][0]["cohort"], "unbranded")
        self.assertEqual(metrics["success_rate"], 0.5)
        self.assertEqual(metrics["mention_rate_success"], 1.0)
        self.assertEqual(metrics["mention_rate_all_attempts"], 0.5)
        self.assertEqual(metrics["error_counts"], {"PROVIDER_TIMEOUT": 1})

    def test_changed_subject_question_and_credential_fields_are_rejected(self):
        changed_subject = build_dongclaw_input_template()
        changed_subject["subject"] = {**AUTHORIZED_SUBJECT, "product": "Other"}
        with self.assertRaisesRegex(DongClawEvaluationError, "subject"):
            evaluate_dongclaw_manifest(changed_subject)

        changed_question = build_dongclaw_input_template()
        changed_question["questions"][0]["text"] = "另一个客户的问题"
        with self.assertRaisesRegex(DongClawEvaluationError, "questions"):
            evaluate_dongclaw_manifest(changed_question)

        credential = build_dongclaw_input_template()
        credential["study"]["api_key"] = "should-never-be-accepted"
        with self.assertRaisesRegex(DongClawEvaluationError, "凭据或令牌"):
            evaluate_dongclaw_manifest(credential)

    def test_model_or_surface_drift_does_not_create_false_pair(self):
        payload = build_dongclaw_input_template()
        payload["study"]["optimized_at"] = "2026-09-10T10:00:00+08:00"
        baseline = self.attempt(
            attempt_id="baseline",
            phase="baseline",
            captured_at="2026-09-10T09:00:00+08:00",
            response_text="DongClaw",
        )
        post = self.attempt(
            attempt_id="post",
            phase="post_optimization",
            captured_at="2026-09-13T12:00:00+08:00",
            response_text="DongClaw",
            model="qwen-new-snapshot",
        )
        payload["attempts"] = [baseline, post]

        report = evaluate_dongclaw_manifest(payload)

        self.assertEqual(report["status"], "BASELINE_ONLY")
        self.assertEqual(report["paired_comparison"]["matched_pair_count"], 0)


if __name__ == "__main__":
    unittest.main()
