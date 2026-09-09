import builtins
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from db.createTable import initialize_database
from sau_backend import app
from services.publish_job_service import create_publish_job, get_publish_job


class RealPublishApiTests(unittest.TestCase):
    LEGACY_PUBLISHER_NAMES = (
        "post_article_baijiahao",
        "post_article_sohu",
        "post_article_toutiao",
        "post_article_zhihu",
        "post_video_DouYin",
        "post_video_baijiahao",
        "post_video_bilibili",
        "post_video_ks",
        "post_video_tencent",
        "post_video_toutiao",
        "post_video_xhs",
    )

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "real-publish.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES ('真实发布测试')"
                ).lastrowid
                self.article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, '待发布文章', '正文', 'ready')
                    """,
                    (project_id,),
                ).lastrowid

        self.original_config = {
            key: app.config.get(key)
            for key in (
                "DATABASE_PATH",
                "DEMO_MODE",
                "ALLOW_REAL_PUBLISHING",
                "ENABLE_BILIBILI_RUNTIME",
            )
        }
        app.config.update(
            DATABASE_PATH=self.db_path,
            DEMO_MODE=False,
            ALLOW_REAL_PUBLISHING=True,
            ENABLE_BILIBILI_RUNTIME=False,
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temp_dir.cleanup()

    def _create_job(self, platform="zhihu", *, demo=False):
        return create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform=platform,
            demo=demo,
        )

    def _legacy_publisher_module(self):
        module = types.ModuleType("myUtils.postVideo")
        publishers = {
            name: Mock(name=name) for name in self.LEGACY_PUBLISHER_NAMES
        }
        for name, publisher in publishers.items():
            setattr(module, name, publisher)
        return module, publishers

    def _assert_legacy_route_blocked_before_publishers(self, route, payload):
        module, publishers = self._legacy_publisher_module()
        original_import = builtins.__import__

        app.config["ALLOW_REAL_PUBLISHING"] = False
        with patch.dict(sys.modules, {"myUtils.postVideo": module}), patch(
            "builtins.__import__", wraps=original_import
        ) as import_mock, patch("sau_backend.threading.Thread") as thread_class:
            response = self.client.post(route, json=payload)

        self.assertEqual(response.status_code, 403)
        self.assertIn("ALLOW_REAL_PUBLISHING", response.get_json()["msg"])
        self.assertFalse(
            any(
                call.args and call.args[0] == "myUtils.postVideo"
                for call in import_mock.call_args_list
            ),
            "publisher module must not be imported while the global gate is closed",
        )
        thread_class.assert_not_called()
        for publisher in publishers.values():
            publisher.assert_not_called()

    def test_legacy_single_publish_is_blocked_before_import_or_thread(self):
        self._assert_legacy_route_blocked_before_publishers(
            "/postVideo",
            {
                "type": 9,
                "title": "不会发布的测试文章",
                "articleBody": "仅用于验证总闸门，不会发送到任何平台。",
                "accountList": ["synthetic-account"],
                "dryRun": False,
            },
        )

    def test_legacy_batch_publish_is_blocked_before_import_or_publishers(self):
        self._assert_legacy_route_blocked_before_publishers(
            "/postVideoBatch",
            [
                {
                    "type": 9,
                    "title": "不会发布的批量测试文章",
                    "articleBody": "仅用于验证总闸门，不会发送到任何平台。",
                    "accountList": ["synthetic-account"],
                    "dryRun": False,
                }
            ],
        )

    def test_legacy_single_publish_keeps_explicitly_authorized_path(self):
        module, publishers = self._legacy_publisher_module()
        app.config["ALLOW_REAL_PUBLISHING"] = True

        with patch.dict(sys.modules, {"myUtils.postVideo": module}), patch(
            "sau_backend.threading.Thread"
        ) as thread_class, patch("builtins.print"):
            response = self.client.post(
                "/postVideo",
                json={
                    "type": 9,
                    "title": "授权路径合成测试文章",
                    "articleBody": "只调用 mock 发布器，绝不连接真实平台。",
                    "accountList": ["synthetic-account"],
                    "dryRun": False,
                },
            )

            thread_class.return_value.start.assert_called_once_with()
            publish_target = thread_class.call_args.kwargs["target"]
            publish_target()

        self.assertEqual(response.status_code, 200)
        publishers["post_article_zhihu"].assert_called_once()
        for name, publisher in publishers.items():
            if name != "post_article_zhihu":
                publisher.assert_not_called()

    def test_legacy_batch_publish_keeps_explicitly_authorized_path(self):
        module, publishers = self._legacy_publisher_module()
        app.config["ALLOW_REAL_PUBLISHING"] = True

        with patch.dict(sys.modules, {"myUtils.postVideo": module}), patch(
            "builtins.print"
        ):
            response = self.client.post(
                "/postVideoBatch",
                json=[
                    {
                        "type": 9,
                        "title": "授权批量路径合成测试文章",
                        "articleBody": "只调用 mock 发布器，绝不连接真实平台。",
                        "accountList": ["synthetic-account"],
                        "dryRun": False,
                    }
                ],
            )

        self.assertEqual(response.status_code, 200)
        publishers["post_article_zhihu"].assert_called_once()
        for name, publisher in publishers.items():
            if name != "post_article_zhihu":
                publisher.assert_not_called()

    def test_requires_all_feature_and_request_confirmation_gates(self):
        job = self._create_job()

        app.config["ALLOW_REAL_PUBLISHING"] = False
        disabled = self.client.post(
            f"/api/publish/jobs/{job['id']}/execute-real",
            json={"confirm": True},
        )
        app.config["ALLOW_REAL_PUBLISHING"] = True
        app.config["DEMO_MODE"] = True
        demo_mode = self.client.post(
            f"/api/publish/jobs/{job['id']}/execute-real",
            json={"confirm": True},
        )
        app.config["DEMO_MODE"] = False
        unconfirmed = self.client.post(
            f"/api/publish/jobs/{job['id']}/execute-real",
            json={},
        )

        self.assertEqual(disabled.status_code, 403)
        self.assertEqual(demo_mode.status_code, 403)
        self.assertEqual(unconfirmed.status_code, 400)
        self.assertEqual(get_publish_job(self.db_path, job["id"])["status"], "queued")

    def test_rejects_demo_real_job_without_execution(self):
        demo_job = self._create_job(demo=True)

        with patch("sau_backend.execute_publish_job") as execute:
            demo_response = self.client.post(
                f"/api/publish/jobs/{demo_job['id']}/execute-real",
                json={"confirm": True},
            )

        self.assertEqual(demo_response.status_code, 409)
        execute.assert_not_called()

    def test_executes_confirmed_zhihu_job_with_explicit_factory(self):
        job = self._create_job()
        completed = {
            **job,
            "status": "success",
            "message": "知乎发布成功",
            "result_url": "https://www.zhihu.com/p/123",
            "started_at": "2026-08-31 14:00:00",
            "finished_at": "2026-08-31 14:01:00",
        }
        factory = Mock(name="real_publisher_factory")

        with patch(
            "sau_backend.create_real_publisher_factory",
            return_value=factory,
        ) as create_factory, patch(
            "sau_backend.execute_publish_job",
            return_value=completed,
        ) as execute:
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/execute-real",
                json={"confirm": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "success")
        create_factory.assert_called_once()
        execute.assert_called_once_with(
            self.db_path,
            job["id"],
            publisher_factory=factory,
            media_root=app.config["MEDIA_ROOT"],
        )

    def test_executes_confirmed_toutiao_job_with_explicit_factory(self):
        job = self._create_job(platform="toutiao")
        processing = {
            **job,
            "status": "processing",
            "message": "今日头条已接受文章提交，等待平台审核",
            "started_at": "2026-09-01 10:00:00",
        }
        factory = Mock(name="real_publisher_factory")

        with patch(
            "sau_backend.create_real_publisher_factory",
            return_value=factory,
        ), patch(
            "sau_backend.execute_publish_job",
            return_value=processing,
        ) as execute:
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/execute-real",
                json={"confirm": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "processing")
        execute.assert_called_once_with(
            self.db_path,
            job["id"],
            publisher_factory=factory,
            media_root=app.config["MEDIA_ROOT"],
        )

    def test_bilibili_real_execution_is_blocked_before_factory_by_default(self):
        job = self._create_job(platform="bilibili")

        with patch("sau_backend.create_real_publisher_factory") as create_factory:
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/execute-real",
                json={"confirm": True},
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("ENABLE_BILIBILI_RUNTIME", response.get_json()["msg"])
        create_factory.assert_not_called()
        self.assertEqual(
            get_publish_job(self.db_path, job["id"])["status"],
            "queued",
        )

    def test_job_payload_exposes_real_action_only_when_all_server_gates_allow_it(self):
        job = self._create_job()

        allowed = self.client.get(f"/api/publish/jobs/{job['id']}").get_json()["data"]
        app.config["ALLOW_REAL_PUBLISHING"] = False
        blocked = self.client.get(f"/api/publish/jobs/{job['id']}").get_json()["data"]

        self.assertTrue(allowed["can_execute_real"])
        self.assertFalse(blocked["can_execute_real"])

    def test_toutiao_job_payload_exposes_real_action(self):
        job = self._create_job(platform="toutiao")

        payload = self.client.get(
            f"/api/publish/jobs/{job['id']}"
        ).get_json()["data"]

        self.assertTrue(payload["can_execute_real"])

    def test_creates_pre_authorized_real_scheduled_job_only_behind_gate(self):
        payload = {
            "article_id": self.article_id,
            "platform": "zhihu",
            "publish_at": "2026-09-03T09:30:00+08:00",
            "auto_execute": True,
        }
        app.config["ALLOW_REAL_PUBLISHING"] = False
        blocked = self.client.post("/api/publish", json=payload)
        app.config["ALLOW_REAL_PUBLISHING"] = True
        created = self.client.post("/api/publish", json=payload)
        immediate = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "auto_execute": True,
            },
        )
        malformed = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "publish_at": "2026-09-03T09:30:00+08:00",
                "auto_execute": "yes",
            },
        )

        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["data"]["status"], "scheduled")
        self.assertTrue(created.get_json()["data"]["auto_execute"])
        self.assertTrue(created.get_json()["data"]["authorization_bound"])
        self.assertNotIn("authorization_fingerprint", created.get_json()["data"])
        self.assertEqual(immediate.status_code, 400)
        self.assertEqual(malformed.status_code, 400)

    def test_sohu_job_payload_exposes_real_action(self):
        job = self._create_job(platform="sohu")

        payload = self.client.get(
            f"/api/publish/jobs/{job['id']}"
        ).get_json()["data"]

        self.assertTrue(payload["can_execute_real"])

    def test_baijiahao_and_xiaohongshu_payloads_expose_real_action(self):
        for platform in ("baijiahao", "xiaohongshu"):
            with self.subTest(platform=platform):
                job = self._create_job(platform=platform)
                payload = self.client.get(
                    f"/api/publish/jobs/{job['id']}"
                ).get_json()["data"]
                self.assertTrue(payload["can_execute_real"])


if __name__ == "__main__":
    unittest.main()
