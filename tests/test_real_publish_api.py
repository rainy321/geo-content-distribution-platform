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
from sau_backend import _scheduled_real_publishing_allowed, app
from services.article_service import get_article
from services.publish_job_service import (
    build_publish_confirmation_fingerprint,
    create_publish_job,
    get_publish_job,
    transition_publish_job,
)


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
                "REAL_PUBLISHING_AUTHORIZED",
                "ALLOW_LEGACY_DIRECT_PUBLISHING",
                "ENABLE_BILIBILI_RUNTIME",
                "PUBLISHING_RUNTIME_READY",
                "SCHEDULED_REAL_PUBLISHING_ENABLED",
            )
        }
        app.config.update(
            DATABASE_PATH=self.db_path,
            DEMO_MODE=False,
            ALLOW_REAL_PUBLISHING=True,
            ALLOW_LEGACY_DIRECT_PUBLISHING=False,
            ENABLE_BILIBILI_RUNTIME=False,
            PUBLISHING_RUNTIME_READY=True,
            SCHEDULED_REAL_PUBLISHING_ENABLED=False,
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temp_dir.cleanup()

    def _create_account(self, platform="zhihu"):
        account_types = {
            "xiaohongshu": 1,
            "channels": 2,
            "douyin": 3,
            "kuaishou": 4,
            "baijiahao": 5,
            "bilibili": 6,
            "toutiao": 7,
            "sohu": 8,
            "zhihu": 9,
            "tiktok": 10,
        }
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                return conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, 1)
                    """,
                    (
                        account_types[platform],
                        f"{platform}-synthetic.json",
                        f"{platform}-synthetic-account",
                    ),
                ).lastrowid

    def _create_job(self, platform="zhihu", *, demo=False):
        account_id = None if demo else self._create_account(platform)
        return create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform=platform,
            demo=demo,
            account_id=account_id,
        )

    def _confirmation(self, job):
        return {
            "confirm": True,
            "job_id": job["id"],
            "state_version": job["state_version"],
            "account_id": job["account_id"],
            "confirmation_fingerprint": build_publish_confirmation_fingerprint(
                job=job,
                article=get_article(self.db_path, job["article_id"]),
            ),
        }

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
        self.assertIn("旧版直发入口", response.get_json()["msg"])
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
        app.config["ALLOW_LEGACY_DIRECT_PUBLISHING"] = True

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
        app.config["ALLOW_LEGACY_DIRECT_PUBLISHING"] = True

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

    def test_legacy_batch_runtime_gate_precedes_publisher_import(self):
        app.config["ALLOW_REAL_PUBLISHING"] = True
        app.config["ALLOW_LEGACY_DIRECT_PUBLISHING"] = True
        app.config["PUBLISHING_RUNTIME_READY"] = False

        with patch.dict(sys.modules, {"myUtils.postVideo": None}):
            response = self.client.post("/postVideoBatch", json=[])

        self.assertEqual(response.status_code, 503)

    def test_requires_all_feature_and_request_confirmation_gates(self):
        job = self._create_job()

        app.config["ALLOW_REAL_PUBLISHING"] = False
        disabled = self.client.post(
            f"/api/publish/jobs/{job['id']}/execute-real",
            json=self._confirmation(job),
        )
        app.config["ALLOW_REAL_PUBLISHING"] = True
        app.config["DEMO_MODE"] = True
        demo_mode = self.client.post(
            f"/api/publish/jobs/{job['id']}/execute-real",
            json=self._confirmation(job),
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

    def test_runtime_gate_rejects_before_factory_or_execution(self):
        job = self._create_job()
        app.config["REAL_PUBLISHING_AUTHORIZED"] = True
        app.config["PUBLISHING_RUNTIME_READY"] = False
        app.config["ALLOW_REAL_PUBLISHING"] = False

        with patch("sau_backend.create_real_publisher_factory") as create_factory, patch(
            "sau_backend.execute_publish_job"
        ) as execute:
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/execute-real",
                json=self._confirmation(job),
            )

        self.assertEqual(response.status_code, 503)
        create_factory.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(get_publish_job(self.db_path, job["id"])["status"], "queued")

    def test_unbound_real_job_is_rejected_before_factory(self):
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
        )

        with patch("sau_backend.create_real_publisher_factory") as create_factory:
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/execute-real",
                json={
                    "confirm": True,
                    "job_id": job["id"],
                    "state_version": job["state_version"],
                    "account_id": None,
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("未绑定明确账号", response.get_json()["msg"])
        create_factory.assert_not_called()

    def test_strong_confirmation_rejects_changed_identifiers_and_fingerprint(self):
        job = self._create_job()
        base = self._confirmation(job)
        cases = (
            ("job_id", job["id"] + 1, 409),
            ("state_version", job["state_version"] + 1, 409),
            ("account_id", job["account_id"] + 1, 409),
            ("state_version", True, 400),
            ("confirmation_fingerprint", "0" * 64, 409),
        )

        for field, value, expected_status in cases:
            with self.subTest(field=field, value=value), patch(
                "sau_backend.create_real_publisher_factory"
            ) as create_factory:
                response = self.client.post(
                    f"/api/publish/jobs/{job['id']}/execute-real",
                    json={**base, field: value},
                )
                self.assertEqual(response.status_code, expected_status)
                create_factory.assert_not_called()

        missing_fingerprint = dict(base)
        missing_fingerprint.pop("confirmation_fingerprint")
        with patch("sau_backend.create_real_publisher_factory") as create_factory:
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/execute-real",
                json=missing_fingerprint,
            )
        self.assertEqual(response.status_code, 400)
        create_factory.assert_not_called()

    def test_article_content_change_invalidates_confirmation_fingerprint(self):
        job = self._create_job()
        confirmation = self._confirmation(job)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE articles SET content = ? WHERE id = ?",
                    ("确认后被修改的正文", self.article_id),
                )

        with patch("sau_backend.create_real_publisher_factory") as create_factory:
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/execute-real",
                json=confirmation,
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("文章内容或发布参数已变化", response.get_json()["msg"])
        create_factory.assert_not_called()
        self.assertEqual(get_publish_job(self.db_path, job["id"])["status"], "queued")

    def test_executor_rechecks_article_after_api_confirmation_before_publisher_call(self):
        job = self._create_job()
        confirmation = self._confirmation(job)
        original_get_article = get_article
        publisher_factory = Mock(name="publisher_factory_must_not_be_called")

        def read_confirmed_article_then_mutate(database_path, article_id):
            article = original_get_article(database_path, article_id)
            with closing(sqlite3.connect(self.db_path)) as conn:
                with conn:
                    conn.execute(
                        "UPDATE articles SET content = ? WHERE id = ?",
                        ("API 前置确认后发生竞态修改", self.article_id),
                    )
            return article

        with patch(
            "sau_backend.get_article",
            side_effect=read_confirmed_article_then_mutate,
        ), patch(
            "sau_backend.create_real_publisher_factory",
            return_value=publisher_factory,
        ):
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/execute-real",
                json=confirmation,
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("领取任务后检测到", response.get_json()["msg"])
        publisher_factory.assert_not_called()
        latest = get_publish_job(self.db_path, job["id"])
        self.assertEqual(latest["status"], "need_action")
        self.assertIn("未访问平台", latest["message"])

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
        confirmation = self._confirmation(job)
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
                json=confirmation,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "success")
        create_factory.assert_called_once()
        execute.assert_called_once_with(
            self.db_path,
            job["id"],
            publisher_factory=factory,
            media_root=app.config["MEDIA_ROOT"],
            expected_state_version=job["state_version"],
            expected_confirmation_fingerprint=confirmation[
                "confirmation_fingerprint"
            ],
        )

    def test_executes_confirmed_toutiao_job_with_explicit_factory(self):
        job = self._create_job(platform="toutiao")
        confirmation = self._confirmation(job)
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
                json=confirmation,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "processing")
        execute.assert_called_once_with(
            self.db_path,
            job["id"],
            publisher_factory=factory,
            media_root=app.config["MEDIA_ROOT"],
            expected_state_version=job["state_version"],
            expected_confirmation_fingerprint=confirmation[
                "confirmation_fingerprint"
            ],
        )

    def test_confirmed_state_version_is_used_for_atomic_claim(self):
        job = self._create_job()
        factory = Mock(name="never_used_factory")

        def change_job_before_claim(*_args, **_kwargs):
            transition_publish_job(
                self.db_path,
                job["id"],
                "failed",
                message="synthetic concurrent update",
                expected_state_version=job["state_version"],
            )
            return factory

        with patch(
            "sau_backend.create_real_publisher_factory",
            side_effect=change_job_before_claim,
        ):
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/execute-real",
                json=self._confirmation(job),
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("并发变化", response.get_json()["msg"])
        latest = get_publish_job(self.db_path, job["id"])
        self.assertEqual(latest["status"], "failed")
        factory.assert_not_called()

    def test_bilibili_real_execution_is_blocked_before_factory_by_default(self):
        job = self._create_job(platform="bilibili")

        with patch("sau_backend.create_real_publisher_factory") as create_factory:
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/execute-real",
                json=self._confirmation(job),
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Bilibili", response.get_json()["msg"])
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
        self.assertEqual(len(allowed["confirmation_fingerprint"]), 64)
        self.assertFalse(blocked["can_execute_real"])

    def test_job_detail_fingerprint_and_confirmation_snapshot_share_one_read(self):
        job = self._create_job()

        with patch(
            "sau_backend.get_article",
            side_effect=AssertionError("detail must use joined confirmation snapshot"),
        ):
            response = self.client.get(f"/api/publish/jobs/{job['id']}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        snapshot = payload["confirmation_snapshot"]
        self.assertEqual(
            snapshot,
            {
                "article_id": self.article_id,
                "title": "待发布文章",
                "content": "正文",
                "tags": [],
                "updated_at": snapshot["updated_at"],
            },
        )
        expected_fingerprint = build_publish_confirmation_fingerprint(
            job=get_publish_job(self.db_path, job["id"]),
            article={
                "id": snapshot["article_id"],
                "title": snapshot["title"],
                "content": snapshot["content"],
                "tags": snapshot["tags"],
            },
        )
        self.assertEqual(payload["confirmation_fingerprint"], expected_fingerprint)

        listing = self.client.get("/api/publish/jobs").get_json()["data"]
        self.assertNotIn("confirmation_snapshot", listing["items"][0])

    def test_toutiao_job_payload_exposes_real_action(self):
        job = self._create_job(platform="toutiao")

        payload = self.client.get(
            f"/api/publish/jobs/{job['id']}"
        ).get_json()["data"]

        self.assertTrue(payload["can_execute_real"])

    def test_rejects_real_scheduled_auto_execution_even_when_runtime_is_ready(self):
        account_id = self._create_account("zhihu")
        payload = {
            "article_id": self.article_id,
            "platform": "zhihu",
            "account_id": account_id,
            "publish_at": "2026-09-03T09:30:00+08:00",
            "auto_execute": True,
        }
        app.config["ALLOW_REAL_PUBLISHING"] = False
        blocked = self.client.post("/api/publish", json=payload)
        app.config["ALLOW_REAL_PUBLISHING"] = True
        still_blocked = self.client.post("/api/publish", json=payload)
        immediate = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "account_id": account_id,
                "auto_execute": True,
            },
        )
        malformed = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "account_id": account_id,
                "publish_at": "2026-09-03T09:30:00+08:00",
                "auto_execute": "yes",
            },
        )

        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(still_blocked.status_code, 403)
        self.assertFalse(
            still_blocked.get_json()["data"][
                "scheduled_real_publishing_enabled"
            ]
        )
        self.assertEqual(immediate.status_code, 400)
        self.assertEqual(malformed.status_code, 400)
        with closing(sqlite3.connect(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM publish_jobs").fetchone()[0]
        self.assertEqual(count, 0)

    def test_background_real_scheduler_requires_every_safety_gate(self):
        app.config.update(
            SCHEDULED_REAL_PUBLISHING_ENABLED=False,
            REAL_PUBLISHING_AUTHORIZED=True,
            PUBLISHING_RUNTIME_READY=True,
            ALLOW_REAL_PUBLISHING=True,
            DEMO_MODE=False,
        )
        self.assertFalse(_scheduled_real_publishing_allowed())

        app.config["SCHEDULED_REAL_PUBLISHING_ENABLED"] = True
        self.assertTrue(_scheduled_real_publishing_allowed())
        for gate in (
            "REAL_PUBLISHING_AUTHORIZED",
            "PUBLISHING_RUNTIME_READY",
            "ALLOW_REAL_PUBLISHING",
        ):
            with self.subTest(gate=gate):
                app.config[gate] = False
                self.assertFalse(_scheduled_real_publishing_allowed())
                app.config[gate] = True
        app.config["DEMO_MODE"] = True
        self.assertFalse(_scheduled_real_publishing_allowed())

    def test_demo_scheduled_task_remains_available_without_real_auto_execution(self):
        app.config["DEMO_MODE"] = True

        response = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "publish_at": "2026-09-03T09:30:00+08:00",
                "auto_execute": True,
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()["data"]
        self.assertTrue(payload["demo"])
        self.assertEqual(payload["status"], "scheduled")
        self.assertFalse(payload["auto_execute"])

    def test_sohu_job_payload_exposes_real_action(self):
        job = self._create_job(platform="sohu")

        payload = self.client.get(
            f"/api/publish/jobs/{job['id']}"
        ).get_json()["data"]

        self.assertTrue(payload["can_execute_real"])

    def test_sohu_multi_article_batch_is_rejected_before_job_creation(self):
        account_id = self._create_account("sohu")
        app.config["REAL_PUBLISHING_AUTHORIZED"] = True
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                second_article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    SELECT project_id, '第二篇待发布文章', '第二篇正文', 'ready'
                    FROM articles WHERE id = ?
                    """,
                    (self.article_id,),
                ).lastrowid

        response = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "batch_article_ids": [self.article_id, second_article_id],
                "platform": "sohu",
                "account_id": account_id,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["data"]["action"],
            "sohu_one_article_per_batch_required",
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM publish_jobs").fetchone()[0],
                0,
            )

    def test_baijiahao_and_xiaohongshu_payloads_expose_real_action(self):
        for platform in ("baijiahao", "xiaohongshu"):
            with self.subTest(platform=platform):
                job = self._create_job(platform=platform)
                payload = self.client.get(
                    f"/api/publish/jobs/{job['id']}"
                ).get_json()["data"]
                self.assertTrue(payload["can_execute_real"])

    def test_real_job_creation_stops_before_database_write_when_runtime_is_missing(self):
        account_id = self._create_account("zhihu")
        app.config["PUBLISHING_RUNTIME_READY"] = False

        response = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "account_id": account_id,
            },
        )

        self.assertEqual(response.status_code, 503)
        with closing(sqlite3.connect(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM publish_jobs").fetchone()[0]
        self.assertEqual(count, 0)

    def test_platform_contract_distinguishes_authorization_demo_and_runtime(self):
        app.config["REAL_PUBLISHING_AUTHORIZED"] = True
        app.config["ALLOW_REAL_PUBLISHING"] = False
        app.config["PUBLISHING_RUNTIME_READY"] = False

        capabilities = self.client.get("/api/publish/platforms").get_json()["data"]
        zhihu = next(item for item in capabilities if item["key"] == "zhihu")
        bilibili = next(item for item in capabilities if item["key"] == "bilibili")

        self.assertTrue(zhihu["real_publishing_authorized"])
        self.assertFalse(zhihu["demo_available"])
        self.assertFalse(zhihu["task_creation_available"])
        self.assertFalse(zhihu["login_available"])
        self.assertFalse(zhihu["scheduled_real_publishing_enabled"])
        self.assertEqual(zhihu["release_status"], "stable")
        self.assertEqual(bilibili["release_status"], "review_required")
        health = self.client.get("/api/health").get_json()["data"]
        self.assertFalse(health["scheduled_real_publishing_enabled"])


if __name__ == "__main__":
    unittest.main()
