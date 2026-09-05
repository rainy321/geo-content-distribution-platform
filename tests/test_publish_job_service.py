import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from db.createTable import initialize_database
from services.article_service import ArticleNotFoundError
from services.publish_job_service import (
    InvalidPublishJobTransitionError,
    PublishJobNotFoundError,
    UnsupportedPublishPlatformError,
    claim_publish_job,
    create_publish_job,
    get_publish_job,
    list_publish_jobs,
    publish_authorization_matches,
    reconcile_publish_job_failure,
    reconcile_publish_job_success,
    retry_publish_job,
    transition_publish_job,
    update_publish_job_progress,
)


class PublishJobServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES (?)",
                    ("XX科技",),
                ).lastrowid
                self.article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project_id, "企业 AI Agent 指南", "正文", "ready"),
                ).lastrowid

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_creates_queued_and_scheduled_jobs(self):
        queued = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform=" ZHIHU ",
        )
        scheduled = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="toutiao",
            publish_at=datetime(2026, 9, 2, 9, 30, tzinfo=timezone.utc),
            demo=True,
        )

        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["platform"], "zhihu")
        self.assertFalse(queued["demo"])
        self.assertEqual(queued["article_title"], "企业 AI Agent 指南")
        self.assertEqual(scheduled["status"], "scheduled")
        self.assertEqual(scheduled["publish_at"], "2026-09-02 09:30:00+00:00")
        self.assertTrue(scheduled["demo"])
        self.assertFalse(scheduled["auto_execute"])
        self.assertFalse(scheduled["authorization_bound"])
        self.assertEqual(get_publish_job(self.db_path, scheduled["id"]), scheduled)

    def test_persists_real_scheduled_auto_execute_authorization(self):
        scheduled = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
            publish_at="2026-09-02T18:00:00+08:00",
            auto_execute=True,
        )

        self.assertEqual(scheduled["status"], "scheduled")
        self.assertTrue(scheduled["auto_execute"])
        self.assertTrue(scheduled["authorization_bound"])
        self.assertEqual(len(scheduled["authorization_fingerprint"]), 64)
        self.assertTrue(get_publish_job(self.db_path, scheduled["id"])["auto_execute"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            article = dict(
                conn.execute(
                    "SELECT id, title, content, tags FROM articles WHERE id = ?",
                    (self.article_id,),
                ).fetchone()
            )
        self.assertTrue(publish_authorization_matches(scheduled, article))
        article["content"] = f"{article['content']}（授权后修改）"
        self.assertFalse(publish_authorization_matches(scheduled, article))

        with self.assertRaisesRegex(ValueError, "仅适用于定时"):
            create_publish_job(
                self.db_path,
                article_id=self.article_id,
                platform="zhihu",
                auto_execute=True,
            )
        with self.assertRaisesRegex(ValueError, "必须是布尔值"):
            create_publish_job(
                self.db_path,
                article_id=self.article_id,
                platform="zhihu",
                publish_at="2026-09-02T18:00:00+08:00",
                auto_execute=1,
            )

    def test_persists_portable_image_filenames_across_reads_and_retries(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="baijiahao",
            images=["cover.png", "cover.png", "detail.jpg"],
        )
        transition_publish_job(self.db_path, job["id"], "processing")
        transition_publish_job(self.db_path, job["id"], "failed")
        retried = retry_publish_job(self.db_path, job["id"])

        self.assertEqual(job["images"], ["cover.png", "detail.jpg"])
        self.assertEqual(retried["images"], ["cover.png", "detail.jpg"])
        self.assertEqual(get_publish_job(self.db_path, job["id"])["images"], job["images"])

    def test_rejects_nonportable_or_malformed_image_lists(self):
        for images in ("cover.png", ["../cover.png"], ["folder/cover.png"], [1]):
            with self.subTest(images=images), self.assertRaises(ValueError):
                create_publish_job(
                    self.db_path,
                    article_id=self.article_id,
                    platform="baijiahao",
                    images=images,
                )

    def test_persists_portable_video_and_binds_it_to_scheduled_authorization(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="bilibili",
            video="launch.mp4",
            publish_at="2026-09-04T18:00:00+08:00",
            auto_execute=True,
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            article = dict(
                conn.execute(
                    "SELECT id, title, content, tags FROM articles WHERE id = ?",
                    (self.article_id,),
                ).fetchone()
            )

        self.assertEqual(job["video"], "launch.mp4")
        self.assertTrue(publish_authorization_matches(job, article))
        changed_job = {**job, "video": "other.mp4"}
        self.assertFalse(publish_authorization_matches(changed_job, article))
        with self.assertRaisesRegex(ValueError, "素材库"):
            create_publish_job(
                self.db_path,
                article_id=self.article_id,
                platform="bilibili",
                video="../launch.mp4",
            )

    def test_lists_and_filters_jobs_with_pagination(self):
        first = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
        )
        second = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="toutiao",
        )
        transition_publish_job(self.db_path, first["id"], "processing")
        transition_publish_job(self.db_path, first["id"], "failed", message="网络异常")

        all_jobs = list_publish_jobs(self.db_path, page=1, page_size=1)
        failed_jobs = list_publish_jobs(
            self.db_path,
            platform="zhihu",
            status="failed",
        )

        self.assertEqual(all_jobs["pagination"]["total"], 2)
        self.assertEqual(all_jobs["items"][0]["id"], second["id"])
        self.assertEqual(failed_jobs["pagination"]["total"], 1)
        self.assertEqual(failed_jobs["items"][0]["message"], "网络异常")

    def test_transitions_through_processing_to_success(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
        )

        processing = transition_publish_job(
            self.db_path,
            job["id"],
            "processing",
            message="正在打开知乎编辑器",
        )
        success = transition_publish_job(
            self.db_path,
            job["id"],
            "success",
            message="发布成功",
            result_url="https://www.zhihu.com/p/123",
        )

        self.assertEqual(processing["status"], "processing")
        self.assertTrue(processing["started_at"])
        self.assertIsNone(processing["finished_at"])
        self.assertEqual(success["status"], "success")
        self.assertEqual(success["result_url"], "https://www.zhihu.com/p/123")
        self.assertTrue(success["finished_at"])

    def test_claims_a_queued_job_only_once(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
        )

        claimed = claim_publish_job(self.db_path, job["id"])

        self.assertEqual(claimed["status"], "processing")
        self.assertTrue(claimed["started_at"])
        with self.assertRaisesRegex(
            InvalidPublishJobTransitionError,
            "不能开始执行",
        ):
            claim_publish_job(self.db_path, job["id"])

    def test_updates_processing_message_without_finishing_job(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
        )
        claim_publish_job(self.db_path, job["id"])

        updated = update_publish_job_progress(
            self.db_path,
            job["id"],
            message="已点击发布，等待平台确认",
        )

        self.assertEqual(updated["status"], "processing")
        self.assertEqual(updated["message"], "已点击发布，等待平台确认")
        self.assertIsNone(updated["finished_at"])

    def test_retries_failed_and_need_action_jobs(self):
        failed_job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
        )
        transition_publish_job(self.db_path, failed_job["id"], "processing")
        transition_publish_job(
            self.db_path,
            failed_job["id"],
            "failed",
            message="页面结构变化",
        )

        retried = retry_publish_job(self.db_path, failed_job["id"])

        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["message"], "")
        self.assertIsNone(retried["started_at"])
        self.assertIsNone(retried["finished_at"])

        action_job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="toutiao",
        )
        transition_publish_job(
            self.db_path,
            action_job["id"],
            "need_action",
            message="需要人工确认",
        )
        self.assertEqual(
            retry_publish_job(self.db_path, action_job["id"])["status"],
            "queued",
        )

    def test_manual_retry_revokes_prior_scheduled_auto_execute_authorization(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
            publish_at="2026-09-02T18:00:00+08:00",
            auto_execute=True,
        )
        transition_publish_job(self.db_path, job["id"], "queued")
        transition_publish_job(
            self.db_path,
            job["id"],
            "need_action",
            message="平台状态未知",
        )

        retried = retry_publish_job(self.db_path, job["id"])

        self.assertEqual(retried["status"], "queued")
        self.assertFalse(retried["auto_execute"])
        self.assertFalse(retried["authorization_bound"])
        self.assertEqual(retried["authorization_fingerprint"], "")

    def test_reconciles_failed_job_with_platform_article_proof(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
        )
        transition_publish_job(self.db_path, job["id"], "processing")
        transition_publish_job(
            self.db_path,
            job["id"],
            "failed",
            message="发布后页面导航导致本地状态未知",
        )

        reconciled = reconcile_publish_job_success(
            self.db_path,
            job["id"],
            result_url="https://zhuanlan.zhihu.com/p/123",
        )

        self.assertEqual(reconciled["status"], "success")
        self.assertEqual(
            reconciled["result_url"],
            "https://zhuanlan.zhihu.com/p/123",
        )
        self.assertTrue(reconciled["finished_at"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            article_status = conn.execute(
                "SELECT status FROM articles WHERE id = ?",
                (self.article_id,),
            ).fetchone()[0]
        self.assertEqual(article_status, "published")

    def test_reconciles_need_action_after_human_verification(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="baijiahao",
        )
        transition_publish_job(
            self.db_path,
            job["id"],
            "need_action",
            message="平台要求账号本人完成人机验证",
        )

        reconciled = reconcile_publish_job_success(
            self.db_path,
            job["id"],
            result_url="https://baijiahao.baidu.com/s?id=123456789",
            message="人工验证后，平台已发布列表与公开链接均已核验",
        )

        self.assertEqual(reconciled["status"], "success")
        self.assertEqual(
            reconciled["result_url"],
            "https://baijiahao.baidu.com/s?id=123456789",
        )
        self.assertTrue(reconciled["finished_at"])

    def test_reconciles_need_action_as_failed_with_no_submit_evidence(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="channels",
        )
        transition_publish_job(
            self.db_path,
            job["id"],
            "need_action",
            message="平台状态未知",
        )

        reconciled = reconcile_publish_job_failure(
            self.db_path,
            job["id"],
            message="发表按钮未启用，五个内容状态均无记录",
        )

        self.assertEqual(reconciled["status"], "failed")
        self.assertEqual(reconciled["result_url"], "")
        self.assertTrue(reconciled["finished_at"])

    def test_failure_reconciliation_rejects_queued_job(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="channels",
        )

        with self.assertRaises(InvalidPublishJobTransitionError):
            reconcile_publish_job_failure(
                self.db_path,
                job["id"],
                message="无提交证据",
            )

    def test_article_stays_published_when_another_platform_fails(self):
        zhihu_job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
        )
        toutiao_job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="toutiao",
        )
        transition_publish_job(self.db_path, zhihu_job["id"], "processing")
        transition_publish_job(self.db_path, toutiao_job["id"], "processing")
        transition_publish_job(self.db_path, zhihu_job["id"], "success")
        transition_publish_job(self.db_path, toutiao_job["id"], "failed")

        with closing(sqlite3.connect(self.db_path)) as conn:
            article_status = conn.execute(
                "SELECT status FROM articles WHERE id = ?",
                (self.article_id,),
            ).fetchone()[0]
        self.assertEqual(article_status, "published")

    def test_reconciliation_requires_proof_and_ambiguous_status(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
        )
        with self.assertRaisesRegex(ValueError, "文章链接"):
            reconcile_publish_job_success(self.db_path, job["id"], result_url="")
        with self.assertRaises(InvalidPublishJobTransitionError):
            reconcile_publish_job_success(
                self.db_path,
                job["id"],
                result_url="https://zhuanlan.zhihu.com/p/123",
            )

    def test_rejects_invalid_transitions_and_non_retryable_jobs(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
        )

        with self.assertRaisesRegex(
            InvalidPublishJobTransitionError,
            "queued.*success",
        ):
            transition_publish_job(self.db_path, job["id"], "success")
        with self.assertRaises(InvalidPublishJobTransitionError):
            retry_publish_job(self.db_path, job["id"])
        with self.assertRaises(PublishJobNotFoundError):
            get_publish_job(self.db_path, 999999)

    def test_rejects_unknown_article_platform_and_publish_time(self):
        with self.assertRaises(ArticleNotFoundError):
            create_publish_job(
                self.db_path,
                article_id=999999,
                platform="zhihu",
            )
        with self.assertRaises(UnsupportedPublishPlatformError):
            create_publish_job(
                self.db_path,
                article_id=self.article_id,
                platform="unknown",
            )
        with self.assertRaisesRegex(ValueError, "ISO 8601"):
            create_publish_job(
                self.db_path,
                article_id=self.article_id,
                platform="zhihu",
                publish_at="tomorrow morning",
            )

    def test_article_delete_cascades_to_publish_jobs(self):
        create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                conn.execute("DELETE FROM articles WHERE id = ?", (self.article_id,))
            count = conn.execute("SELECT COUNT(*) FROM publish_jobs").fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
