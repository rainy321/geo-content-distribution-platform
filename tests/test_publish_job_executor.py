import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

from db.createTable import initialize_database
from services.publish_job_executor import execute_publish_job
from services.publish_job_service import (
    InvalidPublishJobTransitionError,
    create_publish_job,
    get_publish_job,
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
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES (?)",
                    ("XX科技",),
                ).lastrowid
                self.article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, tags, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        "企业 AI Agent 指南",
                        "这里是文章正文",
                        '["AI","Agent"]',
                        "ready",
                    ),
                ).lastrowid

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_job(self, *, demo=False, publish_at=None, images=None, video=None, platform="zhihu"):
        return create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform=platform,
            images=images,
            video=video,
            publish_at=publish_at,
            demo=demo,
        )

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
        self.assertEqual(content.tags, ("AI", "Agent"))
        self.assertEqual(self._article_status(), "published")

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

        self.assertEqual(result["status"], "processing")
        self.assertEqual(publisher.calls[0].video, str(video_path.resolve()))

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

    def test_keeps_unconfirmed_platform_result_processing(self):
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

        self.assertEqual(result["status"], "processing")
        self.assertEqual(result["message"], "已点击发布，等待平台确认")
        self.assertIsNone(result["finished_at"])
        self.assertEqual(self._article_status(), "publishing")

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
