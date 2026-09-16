import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db.createTable import initialize_database
from services.publish_job_service import (
    create_publish_job,
    mark_publish_job_awaiting_reconciliation,
    reconcile_publish_job_failure,
    transition_publish_job,
)
from services.publish_review_monitor_service import run_publish_review_monitor_tick
from services.sohu_publication_guard_service import (
    SohuPublicationGuardError,
    evaluate_sohu_account_publication_guard,
    evaluate_sohu_batch_similarity,
    evaluate_sohu_similarity,
)


class SohuPublicationGuardServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "sohu-guard.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                self.project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES ('守卫测试品牌')"
                ).lastrowid
                self.account_id = conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (8, 'sohu-guard.json', '搜狐守卫测试账号', 1)
                    """
                ).lastrowid
                self.first_article_id = self._insert_article(
                    conn,
                    "企业模型接口的兼容机制说明",
                    "本文说明接口兼容、错误处理和适用边界，不包含购买引导。",
                )
                self.second_article_id = self._insert_article(
                    conn,
                    "知识库权限隔离的实现说明",
                    "本文说明权限隔离、审计记录和部署限制。",
                )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _insert_article(self, conn, title, content):
        return conn.execute(
            """
            INSERT INTO articles (project_id, title, content, status)
            VALUES (?, ?, ?, 'ready')
            """,
            (self.project_id, title, content),
        ).lastrowid

    def _reject_first_article(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.first_article_id,
            platform="sohu",
            account_id=self.account_id,
        )
        processing = transition_publish_job(
            self.db_path,
            job["id"],
            "processing",
            expected_state_version=job["state_version"],
        )
        mark_publish_job_awaiting_reconciliation(
            self.db_path,
            job["id"],
            message="搜狐已受理，等待审核",
            expected_state_version=processing["state_version"],
        )
        run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda _snapshot: {
                "state": "rejected",
                "message": "文章内容不符合搜狐号发文规范",
            },
            now=datetime.now(timezone.utc),
        )
        return job

    def test_batch_guard_requires_one_article_and_reports_similarity(self):
        report = evaluate_sohu_batch_similarity(
            [
                {
                    "article_id": 1,
                    "title": "个人开发者如何选择 AI API 服务？",
                    "content": "核对接口兼容、计费规则和技术支持。",
                },
                {
                    "article_id": 2,
                    "title": "开发者如何选择 AI API 服务？",
                    "content": "需要核对接口兼容、计费规则和技术支持。",
                },
            ]
        )

        codes = {item["code"] for item in report["issues"]}
        self.assertFalse(report["passed"])
        self.assertIn("sohu_batch_one_at_a_time", codes)
        self.assertIn("sohu_batch_similarity", codes)
        self.assertIn("不是搜狐公开阈值", report["disclaimer"])

    def test_explicit_rejected_job_remains_in_similarity_history(self):
        rejected = self._reject_first_article()
        report = evaluate_sohu_account_publication_guard(
            self.db_path,
            account_id=self.account_id,
            title="企业模型接口兼容机制说明",
            content="本文说明接口兼容、错误处理和适用边界，不包含购买引导。",
            now=datetime.now(timezone.utc) + timedelta(days=2),
        )

        similarity_issues = [
            item for item in report["issues"]
            if item["code"] in {
                "sohu_recent_title_similarity",
                "sohu_recent_content_similarity",
            }
        ]
        self.assertFalse(report["passed"])
        self.assertTrue(similarity_issues)
        self.assertIn(rejected["id"], similarity_issues[0]["related_job_ids"])
        self.assertNotIn(
            "sohu_conservative_cooldown",
            {item["code"] for item in report["issues"]},
        )

    def test_rejection_message_survives_monitor_finish_crash_window(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.first_article_id,
            platform="sohu",
            account_id=self.account_id,
        )
        processing = transition_publish_job(
            self.db_path,
            job["id"],
            "processing",
            expected_state_version=job["state_version"],
        )
        pending = mark_publish_job_awaiting_reconciliation(
            self.db_path,
            job["id"],
            message="搜狐已受理，等待审核",
            expected_state_version=processing["state_version"],
        )
        reconcile_publish_job_failure(
            self.db_path,
            job["id"],
            message="后台只读监测发现平台明确未通过：文章内容不符合搜狐号发文规范",
            expected_state_version=pending["state_version"],
        )

        report = evaluate_sohu_account_publication_guard(
            self.db_path,
            account_id=self.account_id,
            title="企业模型接口兼容机制说明",
            content="本文说明接口兼容、错误处理和适用边界，不包含购买引导。",
            now=datetime.now(timezone.utc) + timedelta(days=2),
        )

        codes = {item["code"] for item in report["issues"]}
        self.assertFalse(report["passed"])
        self.assertTrue(
            {"sohu_recent_title_similarity", "sohu_recent_content_similarity"}
            & codes
        )

    def test_explicit_rejection_blocks_same_snapshot_across_accounts_and_article_ids(self):
        rejected = self._reject_first_article()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                second_account_id = conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (8, 'sohu-second.json', '搜狐第二测试账号', 1)
                    """
                ).lastrowid
                copied_article_id = self._insert_article(
                    conn,
                    "企业模型接口的兼容机制说明",
                    "本文说明接口兼容、错误处理和适用边界，不包含购买引导。",
                )
                revised_article_id = self._insert_article(
                    conn,
                    "企业模型接口发生超时时如何定位",
                    "修订稿从超时诊断切入，说明网络、限流和上游状态的核验步骤。",
                )

        with self.assertRaises(SohuPublicationGuardError) as blocked:
            create_publish_job(
                self.db_path,
                article_id=self.first_article_id,
                platform="sohu",
                account_id=second_account_id,
            )

        with self.assertRaises(SohuPublicationGuardError) as copied:
            create_publish_job(
                self.db_path,
                article_id=copied_article_id,
                platform="sohu",
                account_id=second_account_id,
            )

        self.assertIn(
            "sohu_explicit_rejection_same_article_version",
            {item["code"] for item in copied.exception.report["issues"]},
        )

        self.assertIn(
            "sohu_explicit_rejection_same_article_version",
            {item["code"] for item in blocked.exception.report["issues"]},
        )
        self.assertIn(
            rejected["id"],
            next(
                item["related_job_ids"]
                for item in blocked.exception.report["issues"]
                if item["code"] == "sohu_explicit_rejection_same_article_version"
            ),
        )

        revised = create_publish_job(
            self.db_path,
            article_id=revised_article_id,
            platform="sohu",
            account_id=second_account_id,
        )
        self.assertEqual(revised["article_id"], revised_article_id)
        self.assertEqual(revised["account_id"], second_account_id)

    def test_dissimilar_copy_passes_after_cooldown(self):
        self._reject_first_article()
        report = evaluate_sohu_account_publication_guard(
            self.db_path,
            account_id=self.account_id,
            title="权限审计日志如何保存",
            content="审计日志应记录主体、时间和操作结果，并按法规设定保留期。",
            now=datetime.now(timezone.utc) + timedelta(days=2),
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["issues"], [])

    def test_atomic_guard_allows_only_one_concurrent_sohu_job(self):
        article_ids = [self.first_article_id, self.second_article_id]

        def create(article_id):
            try:
                return create_publish_job(
                    self.db_path,
                    article_id=article_id,
                    platform="sohu",
                    account_id=self.account_id,
                )
            except SohuPublicationGuardError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(create, article_ids))

        jobs = [item for item in outcomes if isinstance(item, dict)]
        blocked = [item for item in outcomes if isinstance(item, SohuPublicationGuardError)]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(len(blocked), 1)
        self.assertIn(
            "sohu_unresolved_prior_submission",
            {item["code"] for item in blocked[0].report["issues"]},
        )

    def test_direct_job_service_rejects_fractional_article_and_account_ids(self):
        with self.assertRaisesRegex(ValueError, "article_id 必须是正整数"):
            create_publish_job(
                self.db_path,
                article_id=1.5,
                platform="sohu",
                account_id=self.account_id,
            )
        with self.assertRaisesRegex(ValueError, "account_id 必须是正整数"):
            create_publish_job(
                self.db_path,
                article_id=self.first_article_id,
                platform="sohu",
                account_id=1.5,
            )

    def test_pure_similarity_helper_keeps_different_topics_separate(self):
        matches = evaluate_sohu_similarity(
            title="权限审计日志如何保存",
            content="记录主体、时间、操作和保留期。",
            references=[
                {
                    "job_id": 9,
                    "title": "模型接口兼容机制说明",
                    "content": "说明接口错误处理与网络超时边界。",
                }
            ],
        )

        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
