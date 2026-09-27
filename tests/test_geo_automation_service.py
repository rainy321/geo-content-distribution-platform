import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from services.article_service import get_article
from services.geo_automation_service import (
    GEOAutomationAttemptUnknownError,
    GEOAutomationConflictError,
    GEOAutomationCredentialChangedError,
    GEOAutomationLeaseBusyError,
    GEOAutomationValidationError,
    acquire_geo_automation_item_lease,
    assert_geo_automation_item_lease,
    checkpoint_geo_automation_item,
    create_geo_automation_run,
    geo_automation_project_snapshot,
    get_geo_automation_run,
    release_geo_automation_item_lease,
    renew_geo_automation_item_lease,
    replay_geo_automation_run,
    set_geo_automation_pause,
)
from services.project_service import get_project
from services.publish_job_service import (
    AutomationPublishCredentialChangedError,
    AutomationPublishManifestError,
    AutomationPublishPausedError,
    AutomationPublishSequenceBlockedError,
    InvalidPublishJobTransitionError,
    PublishJobIdempotencyConflictError,
    claim_publish_job,
    create_publish_job,
    transition_publish_job,
)


class GEOAutomationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                self.project_id = conn.execute(
                    """
                    INSERT INTO projects (
                        name, website, product, industry, description,
                        keywords, competitors
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "XX科技",
                        "https://example.test",
                        "企业 AI Agent",
                        "人工智能",
                        "提供企业智能体平台。",
                        '["AI Agent", "企业智能体"]',
                        "[]",
                    ),
                ).lastrowid
                self.zhihu_account_id = conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (9, 'zhihu.json', '知乎账号', 1)
                    """
                ).lastrowid

    def tearDown(self):
        self.temp_dir.cleanup()

    def _config(self, *, topics=None, publish_mode="prepare", channels=None):
        return {
            "name": "季度 GEO 自动化",
            "topics": topics or ["企业如何评估 AI Agent"],
            "keywords": ["AI Agent", "企业智能体"],
            "optimization_direction": "优先回答采购成本、适用边界和实施风险。",
            "target_length": 1000,
            "content_type": "行业科普",
            "target_platform": "zhihu",
            "channels": channels
            or [{"platform": "zhihu", "account_id": None}],
            "publish_mode": publish_mode,
            "optimize": True,
            "continue_on_error": True,
        }

    def _authorization(self, config):
        return {
            "confirmed": True,
            "project_id": self.project_id,
            "topic_count": len(config["topics"]),
            "publish_mode": config["publish_mode"],
            "channels": config["channels"],
            "project_snapshot": geo_automation_project_snapshot(
                get_project(self.db_path, self.project_id)
            ),
        }

    def _create_run(self, *, topics=None, publish_mode="prepare", channels=None):
        config = self._config(
            topics=topics,
            publish_mode=publish_mode,
            channels=channels,
        )
        return create_geo_automation_run(
            self.db_path,
            request_id=f"request-{publish_mode}-{len(config['topics'])}",
            idempotency_key=f"key-{publish_mode}-{len(config['topics'])}",
            project_id=self.project_id,
            config=config,
            authorization_scope=self._authorization(config),
        )

    def _article(self, title="企业 AI Agent 指南"):
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                return conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, ?, ?, 'ready')
                    """,
                    (self.project_id, title, "正文"),
                ).lastrowid

    def _article_fingerprint(self, article_id):
        return get_article(self.db_path, article_id)["content_fingerprint"]

    def _advance_to_preflighting(self, run, article_id):
        for status, stage in (
            ("generating", "generating"),
            ("optimizing", "optimizing"),
            ("preflighting", "preflighting"),
        ):
            item = get_geo_automation_run(self.db_path, run["id"])["items"][0]
            run = checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status=status,
                stage=stage,
                article_id=article_id if status != "generating" else None,
            )
        return run

    def _advance_to_publishing(self, run, article_id):
        run = self._advance_to_preflighting(run, article_id)
        item = run["items"][0]
        return checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="publishing",
            stage="publishing",
            preflight_article_fingerprint=self._article_fingerprint(article_id),
        )

    def test_creates_idempotent_manifest_and_rejects_changed_replay(self):
        config = self._config()
        first = create_geo_automation_run(
            self.db_path,
            request_id="run-request-1",
            idempotency_key="run-key-1",
            project_id=self.project_id,
            config=config,
            authorization_scope=self._authorization(config),
        )
        replayed = create_geo_automation_run(
            self.db_path,
            request_id="run-request-1",
            idempotency_key="run-key-1",
            project_id=self.project_id,
            config=config,
            authorization_scope=self._authorization(config),
        )

        self.assertFalse(first["replayed"])
        self.assertTrue(replayed["replayed"])
        self.assertEqual(first["id"], replayed["id"])
        self.assertEqual(len(first["items"]), 1)
        self.assertTrue(first["config"]["authorization_scope"]["confirmed"])

        changed = {**config, "optimization_direction": "不同方向"}
        with self.assertRaises(GEOAutomationConflictError):
            create_geo_automation_run(
                self.db_path,
                request_id="run-request-1",
                idempotency_key="run-key-1",
                project_id=self.project_id,
                config=changed,
                authorization_scope=self._authorization(changed),
            )

        second = create_geo_automation_run(
            self.db_path,
            request_id="run-request-2",
            idempotency_key="run-key-2",
            project_id=self.project_id,
            config=config,
            authorization_scope=self._authorization(config),
        )
        self.assertNotEqual(first["id"], second["id"])
        with self.assertRaisesRegex(GEOAutomationConflictError, "绑定不同运行"):
            create_geo_automation_run(
                self.db_path,
                request_id="run-request-1",
                idempotency_key="run-key-2",
                project_id=self.project_id,
                config=config,
                authorization_scope=self._authorization(config),
            )

        with self.assertRaisesRegex(
            GEOAutomationConflictError, "request_id 已绑定其他 idempotency_key"
        ):
            create_geo_automation_run(
                self.db_path,
                request_id="run-request-1",
                idempotency_key="unused-alias-key",
                project_id=self.project_id,
                config=config,
                authorization_scope=self._authorization(config),
            )

    def test_exact_replay_uses_frozen_manifest_after_project_changes(self):
        config = self._config()
        authorization = self._authorization(config)
        first = create_geo_automation_run(
            self.db_path,
            request_id="frozen-replay-request",
            idempotency_key="frozen-replay-key",
            project_id=self.project_id,
            config=config,
            authorization_scope=authorization,
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE projects SET description = '运行创建后修改' WHERE id = ?",
                    (self.project_id,),
                )
        replayed = replay_geo_automation_run(
            self.db_path,
            request_id="frozen-replay-request",
            idempotency_key="frozen-replay-key",
            project_id=self.project_id,
            config=config,
            authorization_scope=authorization,
        )
        self.assertIsNotNone(replayed)
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["id"], first["id"])

    def test_happy_path_checkpoints_complete_run(self):
        run = self._create_run()
        item = run["items"][0]
        article_id = self._article()

        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="generating",
            stage="generating",
            generation_request_id="generated-request-1",
        )
        item = run["items"][0]
        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="optimizing",
            stage="optimizing",
            article_id=article_id,
        )
        item = run["items"][0]
        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="preflighting",
            stage="preflighting",
        )
        item = run["items"][0]
        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="completed",
            stage="preflighting",
            message="预检完成",
            preflight_article_fingerprint=self._article_fingerprint(article_id),
        )

        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["progress"], 100)
        self.assertEqual(run["completed_items"], 1)
        self.assertEqual(run["issue_items"], 0)

    def test_preflight_completion_requires_current_article_and_unpaused_run(self):
        run = self._advance_to_preflighting(self._create_run(), self._article())
        item = run["items"][0]
        with self.assertRaisesRegex(
            GEOAutomationValidationError,
            "预检文章指纹",
        ):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status="completed",
                stage="preflighting",
            )
        with self.assertRaisesRegex(GEOAutomationConflictError, "文章已发生变化"):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status="completed",
                stage="preflighting",
                preflight_article_fingerprint="0" * 64,
            )

        paused = set_geo_automation_pause(
            self.db_path,
            run_id=run["id"],
            paused=True,
            expected_state_version=run["state_version"],
        )
        item = paused["items"][0]
        with self.assertRaisesRegex(GEOAutomationConflictError, "已请求暂停"):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status="completed",
                stage="preflighting",
                preflight_article_fingerprint=self._article_fingerprint(
                    item["article_id"]
                ),
            )
        resumed = set_geo_automation_pause(
            self.db_path,
            run_id=run["id"],
            paused=False,
            expected_state_version=paused["state_version"],
        )
        item = resumed["items"][0]
        completed = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="completed",
            stage="preflighting",
            preflight_article_fingerprint=self._article_fingerprint(
                item["article_id"]
            ),
        )
        self.assertEqual(completed["status"], "completed")

    def test_failed_item_does_not_block_following_item(self):
        run = self._create_run(topics=["主题一", "主题二"])
        first, second = run["items"]
        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=first["id"],
            expected_state_version=first["state_version"],
            status="generating",
            stage="generating",
        )
        first = run["items"][0]
        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=first["id"],
            expected_state_version=first["state_version"],
            status="need_action",
            stage="generating",
            message="模型结果未知",
            error={"code": "outcome_unknown", "state_unknown": True},
        )
        second = run["items"][1]
        article_id = self._article("主题二")
        for status, stage in (
            ("generating", "generating"),
            ("optimizing", "optimizing"),
            ("preflighting", "preflighting"),
            ("completed", "preflighting"),
        ):
            second = get_geo_automation_run(self.db_path, run["id"])["items"][1]
            run = checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=second["id"],
                expected_state_version=second["state_version"],
                status=status,
                stage=stage,
                article_id=article_id if status != "generating" else None,
                preflight_article_fingerprint=(
                    self._article_fingerprint(article_id)
                    if status == "publishing"
                    or (status == "completed" and stage == "preflighting")
                    else None
                ),
            )

        self.assertEqual(run["status"], "completed_with_issues")
        self.assertEqual(run["completed_items"], 2)
        self.assertEqual(run["issue_items"], 1)
        self.assertEqual(run["items"][1]["status"], "completed")

    def test_items_are_strictly_serial_even_with_two_clients(self):
        run = self._create_run(topics=["主题一", "主题二"])
        first, second = run["items"]
        with self.assertRaisesRegex(GEOAutomationConflictError, "严格串行"):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=second["id"],
                expected_state_version=second["state_version"],
                status="generating",
                stage="generating",
            )

        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=first["id"],
            expected_state_version=first["state_version"],
            status="generating",
            stage="generating",
        )
        second = run["items"][1]
        with self.assertRaisesRegex(GEOAutomationConflictError, "严格串行"):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=second["id"],
                expected_state_version=second["state_version"],
                status="generating",
                stage="generating",
            )

        first = run["items"][0]
        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=first["id"],
            expected_state_version=first["state_version"],
            status="need_action",
            stage="generating",
            message="第一篇已暂存",
        )
        second = run["items"][1]
        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=second["id"],
            expected_state_version=second["state_version"],
            status="generating",
            stage="generating",
        )
        self.assertEqual(run["items"][1]["status"], "generating")

    def test_model_stage_lease_fences_two_clients_and_expired_attempts(self):
        run = self._create_run()
        item = run["items"][0]
        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="generating",
            stage="generating",
            generation_request_id="leased-generation-request",
        )
        item = run["items"][0]
        lease = acquire_geo_automation_item_lease(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            worker_id="worker-a",
            stage="generating",
        )
        self.assertEqual(lease["worker_id"], "worker-a")

        with self.assertRaises(GEOAutomationLeaseBusyError):
            acquire_geo_automation_item_lease(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                worker_id="worker-b",
                stage="generating",
            )
        with self.assertRaises(GEOAutomationLeaseBusyError):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status="need_action",
                stage="generating",
                worker_id="worker-b",
                error={"code": "must-not-steal"},
            )

        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="generating",
            stage="generating",
            worker_id="worker-a",
            lease_token=lease["lease_token"],
            lease_epoch=lease["lease_epoch"],
            result={"generation_attempt_started": True},
        )
        item = run["items"][0]
        assert_geo_automation_item_lease(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            worker_id="worker-a",
            stage="generating",
            lease_token=lease["lease_token"],
            lease_epoch=lease["lease_epoch"],
            generation_request_id="leased-generation-request",
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE geo_automation_items
                    SET worker_lease_expires_at = datetime('now', '-1 second')
                    WHERE id = ?
                    """,
                    (item["id"],),
                )
        with self.assertRaises(GEOAutomationAttemptUnknownError):
            renew_geo_automation_item_lease(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                worker_id="worker-a",
                stage="generating",
                lease_token=lease["lease_token"],
                lease_epoch=lease["lease_epoch"],
            )
        with self.assertRaises(GEOAutomationAttemptUnknownError):
            acquire_geo_automation_item_lease(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                worker_id="worker-a",
                stage="generating",
            )

        parked = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="need_action",
            stage="generating",
            worker_id="worker-b",
            error={
                "code": "automation_attempt_outcome_unknown",
                "state_unknown": True,
            },
        )
        self.assertEqual(parked["items"][0]["status"], "need_action")
        self.assertEqual(parked["items"][0]["worker_lease_owner"], "")

    def test_succeeded_generation_receipt_can_be_recovered_after_lease_expiry(self):
        run = self._create_run()
        item = run["items"][0]
        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="generating",
            stage="generating",
            generation_request_id="recoverable-generation-request",
        )
        item = run["items"][0]
        old_lease = acquire_geo_automation_item_lease(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            worker_id="worker-old",
            stage="generating",
        )
        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="generating",
            stage="generating",
            worker_id="worker-old",
            lease_token=old_lease["lease_token"],
            lease_epoch=old_lease["lease_epoch"],
            result={"generation_attempt_started": True},
        )
        item = run["items"][0]
        result_payload = {"title": "恢复稿", "content": "已持久化的完整正文"}
        encoded_payload = json.dumps(
            result_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        result_digest = hashlib.sha256(encoded_payload.encode("utf-8")).hexdigest()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO content_generation_runs (
                        request_id, request_fingerprint, project_id,
                        project_snapshot, brief_snapshot, configured_engine,
                        status, result_payload, result_digest
                    ) VALUES (?, 'fingerprint', ?, '{}', '{}', 'test',
                              'succeeded', ?, ?)
                    """,
                    (
                        "recoverable-generation-request",
                        self.project_id,
                        encoded_payload,
                        result_digest,
                    ),
                )
                conn.execute(
                    """
                    UPDATE geo_automation_items
                    SET worker_lease_expires_at = datetime('now', '-1 second')
                    WHERE id = ?
                    """,
                    (item["id"],),
                )
        recovered_lease = acquire_geo_automation_item_lease(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            worker_id="worker-new",
            stage="generating",
        )
        self.assertEqual(recovered_lease["worker_id"], "worker-new")
        release_geo_automation_item_lease(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            worker_id="worker-new",
            stage="generating",
            lease_token=recovered_lease["lease_token"],
            lease_epoch=recovered_lease["lease_epoch"],
        )

    def test_expired_same_worker_lease_is_fenced_by_new_epoch(self):
        run = self._create_run()
        item = run["items"][0]
        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="generating",
            stage="generating",
            generation_request_id="fenced-generation-request",
        )
        item = run["items"][0]
        old_lease = acquire_geo_automation_item_lease(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            worker_id="same-worker",
            stage="generating",
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE geo_automation_items
                    SET worker_lease_expires_at = datetime('now', '-1 second')
                    WHERE id = ?
                    """,
                    (item["id"],),
                )
        new_lease = acquire_geo_automation_item_lease(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            worker_id="same-worker",
            stage="generating",
        )
        self.assertGreater(new_lease["lease_epoch"], old_lease["lease_epoch"])
        self.assertNotEqual(new_lease["lease_token"], old_lease["lease_token"])

        with self.assertRaises(GEOAutomationLeaseBusyError):
            release_geo_automation_item_lease(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                worker_id="same-worker",
                stage="generating",
                lease_token=old_lease["lease_token"],
                lease_epoch=old_lease["lease_epoch"],
            )
        with self.assertRaises(GEOAutomationLeaseBusyError):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status="need_action",
                stage="generating",
                worker_id="same-worker",
                lease_token=old_lease["lease_token"],
                lease_epoch=old_lease["lease_epoch"],
                error={"code": "obsolete-worker"},
            )
        release_geo_automation_item_lease(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            worker_id="same-worker",
            stage="generating",
            lease_token=new_lease["lease_token"],
            lease_epoch=new_lease["lease_epoch"],
        )

    def test_failed_or_unknown_generation_receipt_never_authorizes_retry(self):
        for receipt_status in ("failed", "unknown"):
            with self.subTest(receipt_status=receipt_status):
                config = self._config(topics=[f"{receipt_status} 回执"])
                run = create_geo_automation_run(
                    self.db_path,
                    request_id=f"receipt-{receipt_status}-request",
                    idempotency_key=f"receipt-{receipt_status}-key",
                    project_id=self.project_id,
                    config=config,
                    authorization_scope=self._authorization(config),
                )
                item = run["items"][0]
                generation_request_id = f"receipt-{receipt_status}-generation"
                run = checkpoint_geo_automation_item(
                    self.db_path,
                    run_id=run["id"],
                    item_id=item["id"],
                    expected_state_version=item["state_version"],
                    status="generating",
                    stage="generating",
                    generation_request_id=generation_request_id,
                )
                item = run["items"][0]
                lease = acquire_geo_automation_item_lease(
                    self.db_path,
                    run_id=run["id"],
                    item_id=item["id"],
                    expected_state_version=item["state_version"],
                    worker_id=f"worker-{receipt_status}",
                    stage="generating",
                )
                run = checkpoint_geo_automation_item(
                    self.db_path,
                    run_id=run["id"],
                    item_id=item["id"],
                    expected_state_version=item["state_version"],
                    status="generating",
                    stage="generating",
                    worker_id=f"worker-{receipt_status}",
                    lease_token=lease["lease_token"],
                    lease_epoch=lease["lease_epoch"],
                    result={"generation_attempt_started": True},
                )
                item = run["items"][0]
                with closing(sqlite3.connect(self.db_path)) as conn:
                    with conn:
                        conn.execute(
                            """
                            INSERT INTO content_generation_runs (
                                request_id, request_fingerprint, project_id,
                                project_snapshot, brief_snapshot,
                                configured_engine, status
                            ) VALUES (?, 'fingerprint', ?, '{}', '{}', 'test', ?)
                            """,
                            (
                                generation_request_id,
                                self.project_id,
                                receipt_status,
                            ),
                        )
                        conn.execute(
                            """
                            UPDATE geo_automation_items
                            SET worker_lease_expires_at = datetime('now', '-1 second')
                            WHERE id = ?
                            """,
                            (item["id"],),
                        )
                with self.assertRaises(GEOAutomationAttemptUnknownError):
                    acquire_geo_automation_item_lease(
                        self.db_path,
                        run_id=run["id"],
                        item_id=item["id"],
                        expected_state_version=item["state_version"],
                        worker_id=f"retry-{receipt_status}",
                        stage="generating",
                    )

    def test_project_snapshot_drift_stops_the_next_external_step(self):
        run = self._create_run()
        item = run["items"][0]
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE projects SET name = '运行后改名' WHERE id = ?",
                    (self.project_id,),
                )
        with self.assertRaisesRegex(GEOAutomationConflictError, "避免混用快照"):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status="generating",
                stage="generating",
            )

    def test_project_drift_can_be_parked_and_does_not_leave_a_zombie_run(self):
        run = self._create_run(topics=["主题一", "主题二"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE projects SET name = '运行后改名' WHERE id = ?",
                    (self.project_id,),
                )
        for position in range(2):
            item = get_geo_automation_run(self.db_path, run["id"])["items"][position]
            with self.assertRaisesRegex(
                GEOAutomationConflictError, "避免混用快照"
            ):
                checkpoint_geo_automation_item(
                    self.db_path,
                    run_id=run["id"],
                    item_id=item["id"],
                    expected_state_version=item["state_version"],
                    status="generating",
                    stage="generating",
                )
            run = checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status="need_action",
                stage="generating",
                error={
                    "code": "automation_project_snapshot_changed",
                    "state_unknown": False,
                },
            )
        self.assertEqual(run["status"], "completed_with_issues")
        self.assertEqual(run["completed_items"], 2)

    def test_pause_and_resume_are_version_checked(self):
        run = self._create_run()
        paused = set_geo_automation_pause(
            self.db_path,
            run_id=run["id"],
            paused=True,
            expected_state_version=run["state_version"],
        )
        self.assertTrue(paused["pause_requested"])
        self.assertEqual(paused["status"], "paused")
        resumed = set_geo_automation_pause(
            self.db_path,
            run_id=run["id"],
            paused=False,
            expected_state_version=paused["state_version"],
        )
        self.assertFalse(resumed["pause_requested"])
        self.assertEqual(resumed["status"], "queued")

        with self.assertRaises(GEOAutomationConflictError):
            set_geo_automation_pause(
                self.db_path,
                run_id=run["id"],
                paused=True,
                expected_state_version=run["state_version"],
            )

    def test_pause_request_waits_for_active_model_lease_before_claiming_safe(self):
        run = self._create_run()
        item = run["items"][0]
        run = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="generating",
            stage="generating",
            generation_request_id="pause-active-generation",
        )
        item = run["items"][0]
        lease = acquire_geo_automation_item_lease(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            worker_id="pause-worker",
            stage="generating",
        )
        paused = set_geo_automation_pause(
            self.db_path,
            run_id=run["id"],
            paused=True,
            expected_state_version=run["state_version"],
        )
        self.assertTrue(paused["pause_requested"])
        self.assertEqual(paused["status"], "running")
        self.assertIn("等待当前外部调用", paused["message"])

        renew_geo_automation_item_lease(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            worker_id="pause-worker",
            stage="generating",
            lease_token=lease["lease_token"],
            lease_epoch=lease["lease_epoch"],
        )
        release_geo_automation_item_lease(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            worker_id="pause-worker",
            stage="generating",
            lease_token=lease["lease_token"],
            lease_epoch=lease["lease_epoch"],
        )
        refreshed = get_geo_automation_run(self.db_path, run["id"])
        self.assertEqual(refreshed["status"], "paused")
        self.assertEqual(refreshed["message"], "流程已停在安全检查点")

    def test_real_mode_requires_explicit_account(self):
        config = self._config(publish_mode="real")
        with self.assertRaisesRegex(GEOAutomationValidationError, "绑定明确账号"):
            create_geo_automation_run(
                self.db_path,
                request_id="real-request",
                idempotency_key="real-key",
                project_id=self.project_id,
                config=config,
                authorization_scope=self._authorization(config),
            )

        missing_epoch = self._config(
            publish_mode="real",
            channels=[
                {"platform": "zhihu", "account_id": self.zhihu_account_id}
            ],
        )
        with self.assertRaisesRegex(GEOAutomationValidationError, "凭证版本"):
            create_geo_automation_run(
                self.db_path,
                request_id="real-missing-epoch-request",
                idempotency_key="real-missing-epoch-key",
                project_id=self.project_id,
                config=missing_epoch,
                authorization_scope=self._authorization(missing_epoch),
            )

    def test_real_mode_freezes_account_credential_epoch_and_exact_replay_is_stable(self):
        channels = [
            {
                "platform": "zhihu",
                "account_id": self.zhihu_account_id,
                "credential_epoch": 1,
            }
        ]
        config = self._config(publish_mode="real", channels=channels)
        authorization = self._authorization(config)
        first = create_geo_automation_run(
            self.db_path,
            request_id="real-epoch-request",
            idempotency_key="real-epoch-key",
            project_id=self.project_id,
            config=config,
            authorization_scope=authorization,
        )
        self.assertEqual(first["config"]["channels"][0]["credential_epoch"], 1)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE user_info SET credential_epoch = 2 WHERE id = ?",
                    (self.zhihu_account_id,),
                )
        replayed = replay_geo_automation_run(
            self.db_path,
            request_id="real-epoch-request",
            idempotency_key="real-epoch-key",
            project_id=self.project_id,
            config=config,
            authorization_scope=authorization,
        )
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["id"], first["id"])
        with self.assertRaises(GEOAutomationCredentialChangedError):
            create_geo_automation_run(
                self.db_path,
                request_id="real-epoch-request-2",
                idempotency_key="real-epoch-key-2",
                project_id=self.project_id,
                config=config,
                authorization_scope=authorization,
            )

    def test_real_publish_job_persists_epoch_and_claim_blocks_relogin_drift(self):
        channels = [
            {
                "platform": "zhihu",
                "account_id": self.zhihu_account_id,
                "credential_epoch": 1,
            }
        ]
        article_id = self._article()
        run = self._advance_to_publishing(
            self._create_run(publish_mode="real", channels=channels),
            article_id,
        )
        item = run["items"][0]
        job = create_publish_job(
            self.db_path,
            article_id=article_id,
            platform="zhihu",
            account_id=self.zhihu_account_id,
            idempotency_key="real-epoch-publish-job",
            automation_item_id=item["id"],
        )
        self.assertEqual(job["account_credential_epoch"], 1)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE user_info SET credential_epoch = 2 WHERE id = ?",
                    (self.zhihu_account_id,),
                )
        replayed_job = create_publish_job(
            self.db_path,
            article_id=article_id,
            platform="zhihu",
            account_id=self.zhihu_account_id,
            idempotency_key="real-epoch-publish-job",
            automation_item_id=item["id"],
        )
        self.assertTrue(replayed_job["idempotency_replayed"])
        self.assertEqual(replayed_job["id"], job["id"])
        self.assertEqual(replayed_job["account_credential_epoch"], 1)
        with self.assertRaises(AutomationPublishCredentialChangedError):
            claim_publish_job(self.db_path, job["id"])

    def test_automation_channels_claim_strictly_in_order_and_honor_pause(self):
        channels = [
            {"platform": "zhihu", "account_id": None},
            {"platform": "toutiao", "account_id": None},
        ]
        article_id = self._article()
        run = self._advance_to_publishing(
            self._create_run(publish_mode="demo", channels=channels),
            article_id,
        )
        item = run["items"][0]
        first = create_publish_job(
            self.db_path,
            article_id=article_id,
            platform="zhihu",
            demo=True,
            idempotency_key="ordered-zhihu",
            automation_item_id=item["id"],
        )
        second = create_publish_job(
            self.db_path,
            article_id=article_id,
            platform="toutiao",
            demo=True,
            idempotency_key="ordered-toutiao",
            automation_item_id=item["id"],
        )
        with self.assertRaises(AutomationPublishSequenceBlockedError):
            claim_publish_job(self.db_path, second["id"])
        claimed_first = claim_publish_job(self.db_path, first["id"])
        self.assertEqual(claimed_first["status"], "processing")
        with self.assertRaisesRegex(
            GEOAutomationConflictError, "渠道任务.*执行"
        ):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status="need_action",
                stage="publishing",
            )
        transition_publish_job(self.db_path, first["id"], "failed")
        paused = set_geo_automation_pause(
            self.db_path,
            run_id=run["id"],
            paused=True,
            expected_state_version=run["state_version"],
        )
        with self.assertRaises(AutomationPublishPausedError):
            claim_publish_job(self.db_path, second["id"])
        set_geo_automation_pause(
            self.db_path,
            run_id=run["id"],
            paused=False,
            expected_state_version=paused["state_version"],
        )
        self.assertEqual(
            claim_publish_job(self.db_path, second["id"])["status"],
            "processing",
        )

    def test_automation_claim_rejects_changed_article_and_other_active_job(self):
        channels = [
            {"platform": "zhihu", "account_id": None},
            {"platform": "toutiao", "account_id": None},
        ]
        article_id = self._article()
        run = self._advance_to_publishing(
            self._create_run(publish_mode="demo", channels=channels),
            article_id,
        )
        item = run["items"][0]
        first = create_publish_job(
            self.db_path,
            article_id=article_id,
            platform="zhihu",
            demo=True,
            idempotency_key="isolated-zhihu",
            automation_item_id=item["id"],
        )
        second = create_publish_job(
            self.db_path,
            article_id=article_id,
            platform="toutiao",
            demo=True,
            idempotency_key="isolated-toutiao",
            automation_item_id=item["id"],
        )
        claim_publish_job(self.db_path, first["id"])
        transition_publish_job(self.db_path, first["id"], "failed")
        manual = create_publish_job(
            self.db_path,
            article_id=article_id,
            platform="baijiahao",
            demo=True,
        )
        claim_publish_job(self.db_path, manual["id"])
        with self.assertRaises(AutomationPublishSequenceBlockedError):
            claim_publish_job(self.db_path, second["id"])
        transition_publish_job(self.db_path, manual["id"], "failed")
        self.assertEqual(
            claim_publish_job(self.db_path, second["id"])["status"],
            "processing",
        )

        changed_article_id = self._article("内容改变测试")
        changed_run = create_geo_automation_run(
            self.db_path,
            request_id="changed-claim-request",
            idempotency_key="changed-claim-key",
            project_id=self.project_id,
            config=self._config(publish_mode="demo"),
            authorization_scope=self._authorization(
                self._config(publish_mode="demo")
            ),
        )
        changed_run = self._advance_to_publishing(changed_run, changed_article_id)
        changed_item = changed_run["items"][0]
        changed_job = create_publish_job(
            self.db_path,
            article_id=changed_article_id,
            platform="zhihu",
            demo=True,
            idempotency_key="changed-claim-job",
            automation_item_id=changed_item["id"],
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE articles SET content = content || ' 人工改动' WHERE id = ?",
                    (changed_article_id,),
                )
        with self.assertRaises(AutomationPublishManifestError):
            claim_publish_job(self.db_path, changed_job["id"])

        reverse_article_id = self._article("反向并发测试")
        reverse_config = self._config(publish_mode="demo")
        reverse_run = create_geo_automation_run(
            self.db_path,
            request_id="reverse-claim-request",
            idempotency_key="reverse-claim-key",
            project_id=self.project_id,
            config=reverse_config,
            authorization_scope=self._authorization(reverse_config),
        )
        reverse_run = self._advance_to_publishing(
            reverse_run, reverse_article_id
        )
        reverse_item = reverse_run["items"][0]
        reverse_automation = create_publish_job(
            self.db_path,
            article_id=reverse_article_id,
            platform="zhihu",
            demo=True,
            idempotency_key="reverse-automation-job",
            automation_item_id=reverse_item["id"],
        )
        reverse_manual = create_publish_job(
            self.db_path,
            article_id=reverse_article_id,
            platform="toutiao",
            demo=True,
        )
        claim_publish_job(self.db_path, reverse_automation["id"])
        with self.assertRaises(InvalidPublishJobTransitionError):
            claim_publish_job(self.db_path, reverse_manual["id"])

    def test_publish_job_replays_once_per_automation_item_and_channel(self):
        run = self._create_run(publish_mode="demo")
        item = run["items"][0]
        article_id = self._article()
        for status, stage in (
            ("generating", "generating"),
            ("optimizing", "optimizing"),
            ("preflighting", "preflighting"),
            ("publishing", "publishing"),
        ):
            item = get_geo_automation_run(self.db_path, run["id"])["items"][0]
            run = checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status=status,
                stage=stage,
                article_id=article_id if status != "generating" else None,
                preflight_article_fingerprint=(
                    self._article_fingerprint(article_id)
                    if status == "publishing"
                    else None
                ),
            )
        item = run["items"][0]

        first = create_publish_job(
            self.db_path,
            article_id=article_id,
            platform="zhihu",
            demo=True,
            idempotency_key="automation-publish-1",
            automation_item_id=item["id"],
        )
        replayed = create_publish_job(
            self.db_path,
            article_id=article_id,
            platform="zhihu",
            demo=True,
            idempotency_key="automation-publish-1",
            automation_item_id=item["id"],
        )

        self.assertEqual(first["id"], replayed["id"])
        self.assertFalse(first["idempotency_replayed"])
        self.assertTrue(replayed["idempotency_replayed"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM publish_jobs").fetchone()[0]
        self.assertEqual(count, 1)

        claimed = claim_publish_job(self.db_path, first["id"])
        transition_publish_job(
            self.db_path,
            first["id"],
            "success",
            expected_state_version=claimed["state_version"],
        )
        item = get_geo_automation_run(self.db_path, run["id"])["items"][0]
        checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="completed",
            stage="publishing",
            article_id=article_id,
            publish_jobs=[
                {
                    "platform": "zhihu",
                    "job_id": first["id"],
                    "status": "success",
                }
            ],
        )
        late_replay = create_publish_job(
            self.db_path,
            article_id=article_id,
            platform="zhihu",
            demo=True,
            idempotency_key="automation-publish-1",
            automation_item_id=item["id"],
        )
        self.assertEqual(late_replay["id"], first["id"])
        self.assertTrue(late_replay["idempotency_replayed"])

        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE articles SET content = content || ' 已改变' WHERE id = ?",
                    (article_id,),
                )
        with self.assertRaises(PublishJobIdempotencyConflictError):
            create_publish_job(
                self.db_path,
                article_id=article_id,
                platform="zhihu",
                demo=True,
                idempotency_key="automation-publish-1",
                automation_item_id=item["id"],
            )

    def test_rejects_generation_run_from_another_project(self):
        run = self._create_run()
        item = run["items"][0]
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                other_project_id = conn.execute(
                    """
                    INSERT INTO projects (name, keywords, competitors)
                    VALUES ('其他品牌', '[]', '[]')
                    """
                ).lastrowid
                foreign_run_id = conn.execute(
                    """
                    INSERT INTO content_generation_runs (
                        request_id, request_fingerprint, project_id,
                        project_snapshot, brief_snapshot, configured_engine
                    ) VALUES (?, 'fingerprint', ?, '{}', '{}', 'test')
                    """,
                    ("foreign-generation-request", other_project_id),
                ).lastrowid

        with self.assertRaisesRegex(
            GEOAutomationValidationError, "不属于该一键 GEO 品牌项目"
        ):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status="generating",
                stage="generating",
                generation_request_id="foreign-generation-request",
                generation_run_id=foreign_run_id,
            )

    def test_rejects_publish_evidence_from_another_item(self):
        run = self._create_run(publish_mode="demo")
        article_id = self._article()
        for status, stage in (
            ("generating", "generating"),
            ("optimizing", "optimizing"),
            ("preflighting", "preflighting"),
            ("publishing", "publishing"),
        ):
            item = get_geo_automation_run(self.db_path, run["id"])["items"][0]
            run = checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status=status,
                stage=stage,
                article_id=article_id if status != "generating" else None,
                preflight_article_fingerprint=(
                    self._article_fingerprint(article_id)
                    if status == "publishing"
                    else None
                ),
            )
        item = run["items"][0]
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                rogue_job_id = conn.execute(
                    """
                    INSERT INTO publish_jobs (article_id, platform, demo)
                    VALUES (?, 'zhihu', 1)
                    """,
                    (article_id,),
                ).lastrowid

        with self.assertRaisesRegex(
            GEOAutomationValidationError, "发布任务证据不属于"
        ):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status="completed",
                stage="publishing",
                article_id=article_id,
                publish_jobs=[{"platform": "zhihu", "job_id": rogue_job_id}],
            )

    def test_completed_publish_state_is_derived_from_database_jobs(self):
        run = self._create_run(publish_mode="demo")
        article_id = self._article()
        for status, stage in (
            ("generating", "generating"),
            ("optimizing", "optimizing"),
            ("preflighting", "preflighting"),
            ("publishing", "publishing"),
        ):
            item = get_geo_automation_run(self.db_path, run["id"])["items"][0]
            run = checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status=status,
                stage=stage,
                article_id=article_id if status != "generating" else None,
                preflight_article_fingerprint=(
                    self._article_fingerprint(article_id)
                    if status == "publishing"
                    else None
                ),
            )
        item = run["items"][0]

        with self.assertRaisesRegex(
            GEOAutomationValidationError, "尚未完整覆盖"
        ):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status="completed",
                stage="publishing",
                article_id=article_id,
                publish_jobs=[],
            )

        job = create_publish_job(
            self.db_path,
            article_id=article_id,
            platform="zhihu",
            demo=True,
            idempotency_key="automation-completion-job",
            automation_item_id=item["id"],
        )
        with self.assertRaisesRegex(
            GEOAutomationValidationError, "尚未确认成功"
        ):
            checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status="completed",
                stage="publishing",
                article_id=article_id,
                publish_jobs=[
                    {
                        "platform": "zhihu",
                        "job_id": job["id"],
                        "status": "success",
                    }
                ],
            )

        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE publish_jobs
                    SET status = 'success', message = '数据库确认成功'
                    WHERE id = ?
                    """,
                    (job["id"],),
                )
        completed = checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="completed",
            stage="publishing",
            article_id=article_id,
            publish_jobs=[
                {
                    "platform": "zhihu",
                    "job_id": job["id"],
                    "status": "queued",
                    "message": "客户端伪造值",
                }
            ],
        )
        evidence = completed["items"][0]["publish_jobs"][0]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(evidence["status"], "success")
        self.assertEqual(evidence["message"], "数据库确认成功")

    def test_article_change_after_preflight_blocks_new_publish_job(self):
        run = self._create_run(publish_mode="demo")
        article_id = self._article()
        for status, stage in (
            ("generating", "generating"),
            ("optimizing", "optimizing"),
            ("preflighting", "preflighting"),
            ("publishing", "publishing"),
        ):
            item = get_geo_automation_run(self.db_path, run["id"])["items"][0]
            run = checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status=status,
                stage=stage,
                article_id=article_id if status != "generating" else None,
                preflight_article_fingerprint=(
                    self._article_fingerprint(article_id)
                    if status == "publishing"
                    else None
                ),
            )
        item = run["items"][0]
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE articles SET content = content || ' 人工修改' WHERE id = ?",
                    (article_id,),
                )
        with self.assertRaisesRegex(ValueError, "平台预检后已变更"):
            create_publish_job(
                self.db_path,
                article_id=article_id,
                platform="zhihu",
                demo=True,
                idempotency_key="changed-after-preflight",
                automation_item_id=item["id"],
            )


if __name__ == "__main__":
    unittest.main()
