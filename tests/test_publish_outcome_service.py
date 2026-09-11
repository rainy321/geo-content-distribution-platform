import unittest

from services.publish_outcome_service import (
    PUBLISH_OUTCOME_CATEGORIES,
    classify_publish_outcome,
    detect_safe_publish_repair,
)


class PublishOutcomeServiceTests(unittest.TestCase):
    def test_terminal_and_ambiguous_results_are_mutually_exclusive(self):
        scenarios = (
            ({"status": "success", "result_url": "https://example.com/item"}, "published"),
            ({"status": "processing"}, "platform_processing"),
            (
                {
                    "status": "need_action",
                    "reconciliation_required": True,
                    "message": "平台已接受提交，等待后台核验",
                },
                "platform_processing",
            ),
            (
                {"status": "need_action", "message": "请完成短信验证码"},
                "action_required",
            ),
            ({"status": "failed", "message": "素材上传失败"}, "failed"),
        )

        for values, expected in scenarios:
            with self.subTest(expected=expected):
                outcome = classify_publish_outcome(**values)
                self.assertEqual(outcome["category"], expected)
                self.assertIn(outcome["category"], PUBLISH_OUTCOME_CATEGORIES)
                self.assertTrue(outcome["reason"])
                self.assertTrue(outcome["next_action"])

    def test_unknown_reconciliation_requires_action_and_never_offers_retry(self):
        outcome = classify_publish_outcome(
            status="need_action",
            reconciliation_required=True,
            message="提交超时，平台结果未知",
        )

        self.assertEqual(outcome["category"], "action_required")
        self.assertEqual(outcome["action"], "reconcile")
        self.assertTrue(outcome["validation_required"])
        self.assertFalse(outcome["retry_allowed"])
        self.assertIn("禁止重试", outcome["next_action"])

    def test_confirmed_acceptance_is_processing_not_unknown_action(self):
        outcome = classify_publish_outcome(
            status="need_action",
            reconciliation_required=True,
            message="今日头条已接受文章提交，正在等待平台审核或公开链接",
        )

        self.assertEqual(outcome["category"], "platform_processing")
        self.assertEqual(outcome["action"], "reconcile")
        self.assertFalse(outcome["retry_allowed"])

    def test_observed_request_without_receipt_requires_human_reconciliation(self):
        outcome = classify_publish_outcome(
            status="need_action",
            reconciliation_required=True,
            message="已观察到搜狐号文章写入请求，但未取得明确发布结果，请到平台后台核对",
        )

        self.assertEqual(outcome["category"], "action_required")
        self.assertEqual(outcome["action"], "reconcile")
        self.assertFalse(outcome["retry_allowed"])

    def test_verification_and_failure_have_different_retry_controls(self):
        verification = classify_publish_outcome(
            status="need_action",
            message="平台要求扫码和安全验证",
            account_id=7,
        )
        failed = classify_publish_outcome(
            status="failed",
            message="封面格式不支持",
            account_id=7,
        )

        self.assertEqual(verification["action"], "verify_then_retry")
        self.assertTrue(verification["retry_allowed"])
        self.assertEqual(failed["action"], "retry_after_validation")
        self.assertTrue(failed["retry_allowed"])

    def test_queued_jobs_explain_execute_or_rebuild_instead_of_success(self):
        executable = classify_publish_outcome(
            status="queued",
            can_execute_real=True,
            account_id=3,
        )
        missing_account = classify_publish_outcome(
            status="queued",
            can_execute_real=False,
            account_id=None,
        )

        self.assertEqual(executable["category"], "action_required")
        self.assertEqual(executable["action"], "execute")
        self.assertEqual(missing_account["action"], "login_and_rebuild")
        self.assertFalse(missing_account["retry_allowed"])

    def test_unknown_status_requires_validation_and_is_not_called_failed(self):
        outcome = classify_publish_outcome(
            status="request_timeout",
            message="客户端等待响应超时",
        )

        self.assertEqual(outcome["category"], "action_required")
        self.assertTrue(outcome["validation_required"])
        self.assertEqual(outcome["action"], "validate_then_retry")

    def test_explicit_pre_submit_route_failure_is_safe_for_one_auto_repair(self):
        outcome = classify_publish_outcome(
            status="failed",
            message=(
                "抖音在点击发布前失败，尚未提交："
                "抖音页面结构变化：未找到图文发布入口，尚未提交"
            ),
            account_id=7,
        )

        self.assertEqual(outcome["action"], "auto_repair")
        self.assertTrue(outcome["auto_repair_eligible"])
        self.assertTrue(outcome["automatic_retry_allowed"])
        self.assertEqual(outcome["auto_repair_strategy"], "refresh_publish_route")
        self.assertEqual(outcome["auto_repair_max_attempts"], 1)

    def test_unknown_submission_or_human_gate_is_never_auto_repaired(self):
        ambiguous = detect_safe_publish_repair(
            status="failed",
            message="已点击发布但平台最终状态未知，尚未提交信息不可信",
        )
        verification = detect_safe_publish_repair(
            status="failed",
            message="在点击发布前失败，尚未提交：平台要求短信验证码",
        )

        self.assertFalse(ambiguous["eligible"])
        self.assertFalse(ambiguous["automatic_retry_allowed"])
        self.assertFalse(verification["eligible"])
        self.assertIn("账号或本人验证", verification["summary"])


if __name__ == "__main__":
    unittest.main()
