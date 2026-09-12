import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from db.createTable import initialize_database
from services.article_service import get_article
from services.media_account_risk_service import get_active_media_account_risk
from services.publish_job_executor import (
    PublishConfirmationMismatchError,
    _build_publish_content,
    _enforce_citeability_gate,
    execute_publish_job,
)
from services.publish_job_service import (
    ConcurrentPublishJobUpdateError,
    InvalidPublishJobTransitionError,
    build_publish_confirmation_fingerprint,
    create_publish_job,
    get_publish_job,
    transition_publish_job,
)
from services.publisher_adapter import PublishContent, PublisherAdapter, PublishResult


class StubPublisher(PublisherAdapter):
    def __init__(self, platform, outcome):
        self.platform = platform
        self.outcome = outcome
        self.calls = []

    def login(self):
        return True

    def check_login(self):
        return True

    def publish(self, content):
        self.calls.append(content)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def schedule(self, content, publish_at=None):
        raise AssertionError("执行器不应调用 schedule")


class PublishJobExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        self.article_content = (
            "XX科技说明企业 AI Agent 的适用范围、实施步骤与风险边界。\n\n"
            "## 定义\nXX科技的企业 AI Agent 指南用于梳理任务与权限边界。\n\n"
            "## 实施步骤\n1. 明确目标。\n2. 校验数据。\n3. 保留人工复核。\n\n"
            "## 限制\n实际结果取决于数据质量与业务条件。\n\n"
            "## FAQ\n企业如何开始？\n答：先选择低风险流程。\n\n"
            "是否需要人工复核？\n答：需要。\n\n"
            "## 参考资料\nhttps://example.com/guide\n\n"
            "## 总结\nXX科技建议分阶段验证。"
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name, website, keywords) VALUES (?, ?, ?)",
                    ("XX科技", "https://example.com", '["AI","Agent"]'),
                ).lastrowid
                self.article_id = conn.execute(
                    """
                    INSERT INTO articles (
                        project_id, title, summary, content, tags, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        "企业 AI Agent 指南",
                        "XX科技企业 AI Agent 实施摘要，说明适用范围、步骤和风险边界。",
                        self.article_content,
                        '["AI","Agent"]',
                        "ready",
                    ),
                ).lastrowid

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_job(
        self,
        *,
        demo=False,
        publish_at=None,
        images=None,
        video=None,
        platform="zhihu",
        publish_title=None,
        publish_content=None,
        account_id=None,
    ):
        return create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform=platform,
            images=images,
            video=video,
            publish_at=publish_at,
            demo=demo,
            publish_title=publish_title,
            publish_content=publish_content,
            account_id=account_id,
        )

    def _create_connected_account(self, *, account_type=9, name="主账号"):
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                return conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, 1)
                    """,
                    (account_type, f"account-{name}", name),
                ).lastrowid

    def _article_status(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute(
                "SELECT status FROM articles WHERE id = ?",
                (self.article_id,),
            ).fetchone()[0]

    def test_demo_job_runs_full_flow_without_calling_factory(self):
        job = self._create_job(demo=True)

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: self.fail("Demo 任务不应调用真实工厂"),
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["demo"])
        self.assertIn("未访问真实平台", result["message"])
        self.assertTrue(result["started_at"])
        self.assertTrue(result["finished_at"])
        self.assertEqual(self._article_status(), "published")

    def test_real_job_without_explicit_factory_needs_action(self):
        job = self._create_job()

        result = execute_publish_job(self.db_path, job["id"])

        self.assertEqual(result["status"], "need_action")
        self.assertIn("真实发布器尚未配置", result["message"])

    def test_confirmation_is_rechecked_after_claim_before_factory_is_called(self):
        job = self._create_job()
        confirmation_fingerprint = build_publish_confirmation_fingerprint(
            job=job,
            article=get_article(self.db_path, self.article_id),
        )
        publisher_factory = Mock(name="publisher_factory_must_not_be_called")
        original_get_article = get_article

        def mutate_after_claim_then_read(database_path, article_id):
            with closing(sqlite3.connect(self.db_path)) as conn:
                with conn:
                    conn.execute(
                        "UPDATE articles SET content = ? WHERE id = ?",
                        ("任务领取后发生竞态修改", self.article_id),
                    )
            return original_get_article(database_path, article_id)

        with patch(
            "services.publish_job_executor.get_article",
            side_effect=mutate_after_claim_then_read,
        ), self.assertRaises(PublishConfirmationMismatchError):
            execute_publish_job(
                self.db_path,
                job["id"],
                publisher_factory=publisher_factory,
                expected_state_version=job["state_version"],
                expected_confirmation_fingerprint=confirmation_fingerprint,
            )

        publisher_factory.assert_not_called()
        latest = get_publish_job(self.db_path, job["id"])
        self.assertEqual(latest["status"], "need_action")
        self.assertIn("未访问平台", latest["message"])

    def test_persists_success_url_and_passes_article_content(self):
        job = self._create_job()
        publisher = StubPublisher(
            "zhihu",
            PublishResult(
                success=True,
                platform="zhihu",
                status="success",
                url="https://zhuanlan.zhihu.com/p/123",
                message="发布成功",
            ),
        )

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: publisher,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result_url"], "https://zhuanlan.zhihu.com/p/123")
        self.assertEqual(len(publisher.calls), 1)
        content = publisher.calls[0]
        self.assertIsInstance(content, PublishContent)
        self.assertEqual(content.title, "企业 AI Agent 指南")
        # The Zhihu article adapter does not submit a tag field, so execution
        # must use the same empty platform-selected set validated in preflight.
        self.assertEqual(content.tags, ())
        self.assertEqual(self._article_status(), "published")

    def test_executor_validates_and_sends_the_same_platform_selected_tags(self):
        article = get_article(self.db_path, self.article_id)
        tags = [f"标签{index}" for index in range(1, 9)]
        article["tags"] = tags

        for platform, expected_count in (
            ("douyin", 5),
            ("tiktok", 5),
            ("bilibili", 6),
        ):
            with self.subTest(platform=platform), patch(
                "services.publish_job_executor.validate_platform_publish_variant"
            ) as validate:
                content = _build_publish_content(
                    {
                        "platform": platform,
                        "publish_title": "测试标题",
                        "publish_content": "正文",
                        "publish_at": None,
                        "images": [],
                        "video": "",
                    },
                    article,
                    media_root=self.temp_dir.name,
                )

                self.assertEqual(content.tags, tuple(tags[:expected_count]))
                self.assertEqual(validate.call_args.kwargs["tags"], content.tags)

    def test_executor_sends_saved_platform_copy_instead_of_mutating_original(self):
        # A manually saved outbound copy must already satisfy the platform's
        # plain-text gate; the test is about snapshot fidelity, not Markdown
        # cleanup (automatic copies exercise that path separately).
        platform_copy = self.article_content.replace("## ", "")
        platform_title = "快手短标题"
        job = self._create_job(
            platform="kuaishou",
            publish_title=platform_title,
            publish_content=platform_copy,
        )
        publisher = StubPublisher(
            "kuaishou",
            PublishResult(True, "kuaishou", "success", message="发布成功"),
        )

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: publisher,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(publisher.calls[0].title, platform_title)
        self.assertEqual(publisher.calls[0].content, platform_copy)
        self.assertEqual(
            get_article(self.db_path, self.article_id)["content"],
            self.article_content,
        )

    def test_real_executor_blocks_legacy_low_citeability_before_loading_publisher(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE articles SET content = ?, geo_analysis = '{}' WHERE id = ?",
                    ("缺少标准实体和结构的旧文章正文", self.article_id),
                )
        job = self._create_job()
        publisher_factory = Mock(name="publisher_factory_must_not_be_called")

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=publisher_factory,
        )

        publisher_factory.assert_not_called()
        self.assertEqual(result["status"], "need_action")
        self.assertIn("AI 可引用性发布门禁未通过", result["message"])
        self.assertIn("P0 硬阻断", result["message"])
        self.assertIn("entity.canonical_name_absent", result["message"])
        self.assertIn("标准品牌名称“XX科技”", result["message"])
        self.assertIn("质量分", result["message"])
        self.assertIn("不是本次拦截原因", result["message"])
        self.assertNotIn("要求至少", result["message"])
        self.assertIn("处理建议", result["message"])
        self.assertIn("未加载发布器、未访问平台", result["message"])

    def test_p0_gate_message_names_blocker_even_when_quality_score_is_high(self):
        job = self._create_job()
        publisher_factory = Mock(name="publisher_factory_must_not_be_called")
        p0_issue = {
            "code": "risk.unsupported_quantified_claim",
            "priority": "P0",
            "message": "量化主张缺少关联证据。",
            "action": "补充可核验来源并在正文保留来源 URL。",
        }
        with patch(
            "services.publish_job_executor.evaluate_publish_citeability",
            return_value={
                "score": 92,
                "pass_score": 75,
                "passed": False,
                "publish_gate_passed": False,
                "blocking_issues": [p0_issue],
                "warnings": [],
                "suggestions": [],
            },
        ):
            result = execute_publish_job(
                self.db_path,
                job["id"],
                publisher_factory=publisher_factory,
            )

        publisher_factory.assert_not_called()
        self.assertEqual(result["status"], "need_action")
        self.assertIn("1 个 P0 硬阻断", result["message"])
        self.assertIn("risk.unsupported_quantified_claim", result["message"])
        self.assertIn("量化主张缺少关联证据", result["message"])
        self.assertIn("质量分 92/100 仅供优化参考", result["message"])
        self.assertIn("不是本次拦截原因", result["message"])
        self.assertNotIn("要求至少", result["message"])

    def test_real_executor_allows_score_71_without_p0_after_existing_confirmation(self):
        job = self._create_job()
        publisher = StubPublisher(
            "zhihu",
            PublishResult(True, "zhihu", "success", "https://zhuanlan.zhihu.com/p/71"),
        )
        quality_warning = {
            "code": "quality.score_below_target",
            "priority": "P1",
            "message": "AI 可引用性质量分为 71/100，低于建议目标 75/100。",
            "action": "建议继续优化，但不硬阻断发布。",
        }
        with patch(
            "services.publish_job_executor.evaluate_publish_citeability",
            return_value={
                "score": 71,
                "pass_score": 75,
                "passed": False,
                "publish_gate_passed": True,
                "blocking_issues": [],
                "warnings": [quality_warning],
                "suggestions": [],
            },
        ):
            result = execute_publish_job(
                self.db_path,
                job["id"],
                publisher_factory=lambda _job: publisher,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(publisher.calls), 1)

    def test_executor_reuses_only_visible_immutable_generation_source_claims(self):
        source = {
            "title": "公开测试报告",
            "url": "https://example.com/report",
            "claim": "XX科技在公开测试中将处理效率提升 40%",
        }
        article = get_article(self.db_path, self.article_id)
        article["generation"] = {"sources": [source]}
        content = (
            "XX科技在公开测试中将处理效率提升 40%，适用范围仅限报告所述环境。"
            "来源：https://example.com/report"
        )

        result = _enforce_citeability_gate(
            self.db_path,
            article=article,
            title="XX科技如何提高处理效率？",
            content=content,
        )

        self.assertTrue(result["publish_gate_passed"])
        self.assertEqual(result["outbound_source_filter"]["matched_count"], 1)

    def test_real_executor_scores_exact_platform_override_before_publishing(self):
        job = self._create_job(
            platform="kuaishou",
            publish_title="平台短标题",
            publish_content="不含标准品牌、结构或来源的短版",
        )
        publisher_factory = Mock(name="publisher_factory_must_not_be_called")

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=publisher_factory,
        )

        publisher_factory.assert_not_called()
        self.assertEqual(result["status"], "need_action")
        self.assertIn("AI 可引用性发布门禁未通过", result["message"])

    def test_executor_blocks_tampered_over_limit_copy_before_loading_publisher(self):
        job = self._create_job(platform="kuaishou")
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE publish_jobs SET publish_content = ? WHERE id = ?",
                    ("字" * 470, job["id"]),
                )
        publisher_factory = Mock(name="publisher_factory_must_not_be_called")

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=publisher_factory,
        )

        publisher_factory.assert_not_called()
        self.assertEqual(result["status"], "need_action")
        self.assertIn("前置校验未通过", result["message"])
        self.assertIn("未访问平台", result["message"])

    def test_executor_blocks_tampered_title_before_loading_publisher(self):
        job = self._create_job(platform="douyin")
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE publish_jobs SET publish_title = ? WHERE id = ?",
                    ("字" * 31, job["id"]),
                )
        publisher_factory = Mock(name="publisher_factory_must_not_be_called")

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=publisher_factory,
        )

        publisher_factory.assert_not_called()
        self.assertEqual(result["status"], "need_action")
        self.assertIn("发布标题当前 31 字", result["message"])
        self.assertIn("未访问平台", result["message"])

    def test_resolves_job_images_from_media_root_before_publishing(self):
        media_root = Path(self.temp_dir.name) / "videoFile"
        media_root.mkdir()
        image_path = media_root / "cover.png"
        image_path.write_bytes(b"test-image")
        job = self._create_job(images=["cover.png"])
        publisher = StubPublisher(
            "zhihu",
            PublishResult(True, "zhihu", "success", message="发布成功"),
        )

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: publisher,
            media_root=media_root,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(publisher.calls[0].images, (str(image_path.resolve()),))

    def test_resolves_job_video_from_media_root_before_publishing(self):
        media_root = Path(self.temp_dir.name) / "videoFile"
        media_root.mkdir()
        video_path = media_root / "demo.mp4"
        video_path.write_bytes(b"video")
        job = self._create_job(platform="bilibili", video="demo.mp4")
        publisher = StubPublisher(
            "bilibili",
            PublishResult(False, "bilibili", "processing", message="待核验"),
        )

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: publisher,
            media_root=media_root,
        )

        self.assertEqual(result["status"], "need_action")
        self.assertTrue(result["reconciliation_required"])
        self.assertIsNotNone(result["finished_at"])
        self.assertEqual(publisher.calls[0].video, str(video_path.resolve()))
        self.assertEqual(self._article_status(), "ready")

    def test_persists_need_action_result(self):
        job = self._create_job()
        publisher = StubPublisher(
            "zhihu",
            PublishResult(
                success=False,
                platform="zhihu",
                status="need_action",
                message="需要人工确认登录",
            ),
        )

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: publisher,
        )

        self.assertEqual(result["status"], "need_action")
        self.assertEqual(result["message"], "需要人工确认登录")
        self.assertEqual(self._article_status(), "ready")

    def test_finishes_unconfirmed_platform_result_as_reconciliation_required(self):
        job = self._create_job()
        publisher = StubPublisher(
            "zhihu",
            PublishResult(
                success=False,
                platform="zhihu",
                status="processing",
                message="已点击发布，等待平台确认",
            ),
        )

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: publisher,
        )

        self.assertEqual(result["status"], "need_action")
        self.assertTrue(result["reconciliation_required"])
        self.assertEqual(result["message"], "已点击发布，等待平台确认")
        self.assertIsNotNone(result["finished_at"])
        self.assertEqual(self._article_status(), "ready")

    def test_need_action_result_records_and_freezes_selected_account(self):
        account_id = self._create_connected_account()
        job = self._create_job(account_id=account_id)
        publisher = StubPublisher(
            "zhihu",
            PublishResult(
                success=False,
                platform="zhihu",
                status="need_action",
                message="平台要求接收短信验证码后继续",
            ),
        )

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: publisher,
        )

        self.assertEqual(result["status"], "need_action")
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_status = conn.execute(
                "SELECT status FROM user_info WHERE id = ?", (account_id,)
            ).fetchone()[0]
            risk_category = conn.execute(
                """
                SELECT category FROM media_account_risk_events
                WHERE account_id = ? AND active = 1
                """,
                (account_id,),
            ).fetchone()[0]
        self.assertEqual(account_status, 0)
        self.assertEqual(risk_category, "verification_required")

    def test_processing_result_with_visible_ban_becomes_action_required(self):
        account_id = self._create_connected_account()
        job = self._create_job(account_id=account_id)
        publisher = StubPublisher(
            "zhihu",
            PublishResult(
                success=False,
                platform="zhihu",
                status="processing",
                message="平台页面显示账号已被永久封禁",
            ),
        )

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: publisher,
        )

        self.assertEqual(result["status"], "need_action")
        self.assertFalse(result["reconciliation_required"])
        self.assertIn("permanent_ban", result["message"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_status = conn.execute(
                "SELECT status FROM user_info WHERE id = ?", (account_id,)
            ).fetchone()[0]
        self.assertEqual(account_status, 0)

    def test_failed_result_with_network_error_does_not_freeze_account(self):
        account_id = self._create_connected_account()
        job = self._create_job(account_id=account_id)
        publisher = StubPublisher(
            "zhihu",
            PublishResult(
                success=False,
                platform="zhihu",
                status="failed",
                message="网络异常：连接被重置",
            ),
        )

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: publisher,
        )

        self.assertEqual(result["status"], "failed")
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_status = conn.execute(
                "SELECT status FROM user_info WHERE id = ?", (account_id,)
            ).fetchone()[0]
            risk_category = conn.execute(
                """
                SELECT category FROM media_account_risk_events
                WHERE account_id = ? AND active = 1
                """,
                (account_id,),
            ).fetchone()[0]
        self.assertEqual(account_status, 1)
        self.assertEqual(risk_category, "network_error")

    def test_risk_journal_failure_never_leaves_publish_job_processing(self):
        job = self._create_job()
        publisher = StubPublisher(
            "zhihu",
            PublishResult(
                success=False,
                platform="zhihu",
                status="need_action",
                message="需要本人验证",
            ),
        )

        with patch(
            "services.publish_job_executor.record_publish_job_account_risk",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            result = execute_publish_job(
                self.db_path,
                job["id"],
                publisher_factory=lambda _job: publisher,
            )

        self.assertEqual(result["status"], "need_action")
        self.assertEqual(get_publish_job(self.db_path, job["id"]), result)

    def test_manual_intervention_exception_needs_action(self):
        job = self._create_job()
        publisher = StubPublisher("zhihu", RuntimeError("页面结构变化"))

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: publisher,
        )

        self.assertEqual(result["status"], "need_action")
        self.assertIn("页面结构变化", result["message"])
        self.assertEqual(get_publish_job(self.db_path, job["id"]), result)

    def test_ordinary_publisher_exception_becomes_failed_job(self):
        job = self._create_job()
        publisher = StubPublisher("zhihu", RuntimeError("网络连接被重置"))

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: publisher,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("网络连接被重置", result["message"])
        self.assertEqual(self._article_status(), "ready")

    def test_worker_result_cannot_overwrite_concurrent_manual_terminal_state(self):
        job = self._create_job()
        test_case = self

        class InterveningPublisher(PublisherAdapter):
            platform = "zhihu"

            def login(self):
                return True

            def check_login(self):
                return True

            def publish(self, content):
                transition_publish_job(
                    test_case.db_path,
                    job["id"],
                    "need_action",
                    message="人工已接管当前任务",
                )
                return PublishResult(
                    True,
                    self.platform,
                    "success",
                    message="过期执行器返回成功",
                )

            def schedule(self, content, publish_at=None):
                raise AssertionError("执行器不应调用 schedule")

        with self.assertRaises(ConcurrentPublishJobUpdateError):
            execute_publish_job(
                self.db_path,
                job["id"],
                publisher_factory=lambda _job: InterveningPublisher(),
            )

        latest = get_publish_job(self.db_path, job["id"])
        self.assertEqual(latest["status"], "need_action")
        self.assertEqual(latest["message"], "人工已接管当前任务")

    def test_stale_risk_result_cannot_freeze_account_after_job_version_changes(self):
        account_id = self._create_connected_account()
        job = self._create_job(account_id=account_id)
        test_case = self

        class InterveningRiskResultPublisher(PublisherAdapter):
            platform = "zhihu"

            def login(self):
                return True

            def check_login(self):
                return True

            def publish(self, content):
                transition_publish_job(
                    test_case.db_path,
                    job["id"],
                    "need_action",
                    message="新版发布尝试已由人工接管",
                )
                return PublishResult(
                    False,
                    self.platform,
                    "need_action",
                    message="旧发布页面提示账号已被永久封禁",
                )

            def schedule(self, content, publish_at=None):
                raise AssertionError("执行器不应调用 schedule")

        with self.assertRaises(ConcurrentPublishJobUpdateError):
            execute_publish_job(
                self.db_path,
                job["id"],
                publisher_factory=lambda _job: InterveningRiskResultPublisher(),
            )

        latest = get_publish_job(self.db_path, job["id"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_status = conn.execute(
                "SELECT status FROM user_info WHERE id = ?",
                (account_id,),
            ).fetchone()[0]
        self.assertEqual(latest["status"], "need_action")
        self.assertEqual(latest["message"], "新版发布尝试已由人工接管")
        self.assertEqual(account_status, 1)
        self.assertIsNone(get_active_media_account_risk(self.db_path, account_id))

    def test_stale_risk_exception_cannot_freeze_account_after_job_version_changes(self):
        account_id = self._create_connected_account()
        job = self._create_job(account_id=account_id)
        test_case = self

        class InterveningRiskExceptionPublisher(PublisherAdapter):
            platform = "zhihu"

            def login(self):
                return True

            def check_login(self):
                return True

            def publish(self, content):
                transition_publish_job(
                    test_case.db_path,
                    job["id"],
                    "need_action",
                    message="新版异常处理已接管",
                )
                raise RuntimeError("旧发布页面提示账号已被永久封禁")

            def schedule(self, content, publish_at=None):
                raise AssertionError("执行器不应调用 schedule")

        with self.assertRaises(ConcurrentPublishJobUpdateError):
            execute_publish_job(
                self.db_path,
                job["id"],
                publisher_factory=lambda _job: InterveningRiskExceptionPublisher(),
            )

        latest = get_publish_job(self.db_path, job["id"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_status = conn.execute(
                "SELECT status FROM user_info WHERE id = ?",
                (account_id,),
            ).fetchone()[0]
        self.assertEqual(latest["status"], "need_action")
        self.assertEqual(latest["message"], "新版异常处理已接管")
        self.assertEqual(account_status, 1)
        self.assertIsNone(get_active_media_account_risk(self.db_path, account_id))

    def test_risk_result_is_bound_to_claimed_account_credential_epoch(self):
        account_id = self._create_connected_account()
        job = self._create_job(account_id=account_id)
        test_case = self

        class ReloginDuringPublishPublisher(PublisherAdapter):
            platform = "zhihu"

            def login(self):
                return True

            def check_login(self):
                return True

            def publish(self, content):
                with closing(sqlite3.connect(test_case.db_path)) as conn:
                    with conn:
                        conn.execute(
                            """
                            UPDATE user_info
                            SET filePath = 'new-session.json', status = 1,
                                credential_epoch = credential_epoch + 1
                            WHERE id = ?
                            """,
                            (account_id,),
                        )
                return PublishResult(
                    False,
                    self.platform,
                    "need_action",
                    message="旧会话页面提示账号已被永久封禁",
                )

            def schedule(self, content, publish_at=None):
                raise AssertionError("执行器不应调用 schedule")

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: ReloginDuringPublishPublisher(),
        )

        with closing(sqlite3.connect(self.db_path)) as conn:
            account_status, credential_epoch = conn.execute(
                "SELECT status, credential_epoch FROM user_info WHERE id = ?",
                (account_id,),
            ).fetchone()
        self.assertEqual(result["status"], "need_action")
        self.assertEqual((account_status, credential_epoch), (1, 2))
        self.assertIsNone(get_active_media_account_risk(self.db_path, account_id))

    def test_rejects_wrong_publisher_without_invoking_it(self):
        job = self._create_job()
        publisher = StubPublisher(
            "toutiao",
            PublishResult(True, "toutiao", "success"),
        )

        result = execute_publish_job(
            self.db_path,
            job["id"],
            publisher_factory=lambda _job: publisher,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("平台", result["message"])
        self.assertEqual(publisher.calls, [])

    def test_scheduled_job_cannot_execute_before_being_queued(self):
        job = self._create_job(publish_at=datetime(2026, 9, 2, 9, 30))

        with self.assertRaisesRegex(
            InvalidPublishJobTransitionError,
            "不能开始执行",
        ):
            execute_publish_job(self.db_path, job["id"])

        self.assertEqual(get_publish_job(self.db_path, job["id"])["status"], "scheduled")


if __name__ == "__main__":
    unittest.main()
