import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from db.createTable import initialize_database
from sau_backend import app
from services.content_engine_adapters import MockContentEngineAdapter
from services.content_engine_contract import ContentEngineUnavailableError
from services.content_generation_service import ContentGenerationService


class P2ContentWorkflowApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "p2.db"
        self.media_root = self.root / "media"
        self.media_root.mkdir()
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                self.project_id = conn.execute(
                    """
                    INSERT INTO projects (
                        name, product, industry, description, keywords
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "星河科技",
                        "企业智能体",
                        "人工智能",
                        "帮助团队建立可靠的 AI 工作流。",
                        json.dumps(["AI Agent", "企业智能体"], ensure_ascii=False),
                    ),
                ).lastrowid
                self.article_id = conn.execute(
                    """
                    INSERT INTO articles (
                        project_id, title, summary, content, tags, status
                    ) VALUES (?, ?, ?, ?, ?, 'ready')
                    """,
                    (
                        self.project_id,
                        "企业智能体选择指南",
                        "选型摘要",
                        "## 先明确问题\n\n选择企业智能体前应核对业务边界。",
                        json.dumps(["AI Agent", "企业智能体"], ensure_ascii=False),
                    ),
                ).lastrowid

        self.original = {
            "DATABASE_PATH": app.config["DATABASE_PATH"],
            "MEDIA_ROOT": app.config["MEDIA_ROOT"],
            "DEMO_MODE": app.config["DEMO_MODE"],
            "AI_RATE_LIMIT_PER_MINUTE": app.config["AI_RATE_LIMIT_PER_MINUTE"],
            "REAL_PUBLISHING_AUTHORIZED": app.config.get(
                "REAL_PUBLISHING_AUTHORIZED"
            ),
            "ALLOW_REAL_PUBLISHING": app.config.get("ALLOW_REAL_PUBLISHING"),
            "PUBLISHING_RUNTIME_READY": app.config.get(
                "PUBLISHING_RUNTIME_READY"
            ),
            "ENABLE_BILIBILI_RUNTIME": app.config.get(
                "ENABLE_BILIBILI_RUNTIME"
            ),
        }
        app.config.update(
            DATABASE_PATH=self.db_path,
            MEDIA_ROOT=self.media_root,
            DEMO_MODE=True,
            AI_RATE_LIMIT_PER_MINUTE=0,
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original)
        self.temp_dir.cleanup()

    def _enable_real_task_creation(self, *, account_type, platform_name):
        app.config.update(
            DEMO_MODE=False,
            REAL_PUBLISHING_AUTHORIZED=True,
            ALLOW_REAL_PUBLISHING=True,
            PUBLISHING_RUNTIME_READY=True,
            ENABLE_BILIBILI_RUNTIME=True,
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                return conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, 1)
                    """,
                    (
                        account_type,
                        f"{platform_name}-contract.json",
                        f"{platform_name}-contract-account",
                    ),
                ).lastrowid

    def test_template_crud_and_generation_instruction(self):
        listed = self.client.get("/api/content-templates")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.get_json()["data"]), 4)

        created = self.client.post(
            "/api/content-templates",
            json={
                "name": "实施复盘",
                "description": "复盘项目实施过程",
                "content_type": "解决方案",
                "instruction": "按目标、过程、结果和限制组织内容。",
            },
        )
        self.assertEqual(created.status_code, 201)
        template = created.get_json()["data"]

        with patch("sau_backend._create_content_generation_service") as factory:
            factory.return_value.generate.return_value = {
                "title": "生成标题",
                "summary": "摘要",
                "content": "正文",
                "tags": [],
                "faq": [],
            }
            response = self.client.post(
                "/api/articles/generate",
                json={
                    "project_id": self.project_id,
                    "topic": "项目实施复盘",
                    "content_type": "解决方案",
                    "template_id": template["id"],
                },
            )

        self.assertEqual(response.status_code, 200)
        request_envelope = factory.return_value.generate.call_args.args[0]
        self.assertTrue(
            request_envelope.template_instruction.startswith(
                "按目标、过程、结果和限制组织内容。"
            )
        )
        self.assertIn("本次内容差异化编排", request_envelope.template_instruction)
        self.assertEqual(
            request_envelope.diversity_profile["fact_policy"],
            "input_only_no_invention",
        )
        updated = self.client.put(
            f"/api/content-templates/{template['id']}",
            json={"description": "更新后的说明"},
        )
        self.assertEqual(updated.get_json()["data"]["description"], "更新后的说明")
        self.assertEqual(
            self.client.delete(f"/api/content-templates/{template['id']}").status_code,
            200,
        )
        self.assertEqual(self.client.delete("/api/content-templates/1").status_code, 409)

    def test_batch_generation_saves_independent_idempotent_drafts(self):
        adapter = MockContentEngineAdapter([
            {
                "title": "主题一",
                "summary": "",
                "content": "正文一",
                "tags": ["A"],
                "faq": [],
                "sources": [],
            },
            {
                "title": "主题二",
                "summary": "",
                "content": "正文二",
                "tags": ["B"],
                "faq": [],
                "sources": [],
            },
        ])
        service = ContentGenerationService(self.db_path, adapter)
        payload = {
            "project_id": self.project_id,
            "topics": ["主题一", "主题二"],
            "keywords": ["AI Agent"],
        }
        headers = {
            "X-Request-ID": "batch-request",
            "Idempotency-Key": "batch-request",
        }
        with patch(
            "sau_backend._create_content_generation_service",
            return_value=service,
        ):
            response = self.client.post(
                "/api/articles/generate-batch",
                headers=headers,
                json=payload,
            )
            replay = self.client.post(
                "/api/articles/generate-batch",
                headers=headers,
                json=payload,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["created_count"], 2)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.get_json()["data"]["replayed_count"], 2)
        self.assertEqual(replay.headers["Idempotency-Replayed"], "true")
        self.assertEqual(len(adapter.calls), 2)
        with closing(sqlite3.connect(self.db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE status = 'draft'"
            ).fetchone()[0]
            run_count = conn.execute(
                "SELECT COUNT(*) FROM content_generation_runs"
            ).fetchone()[0]
        self.assertEqual(count, 2)
        self.assertEqual(run_count, 2)

    def test_batch_generation_rejects_reusing_key_for_changed_topics(self):
        adapter = MockContentEngineAdapter([
            {
                "title": "主题一",
                "summary": "",
                "content": "正文一",
                "tags": [],
                "faq": [],
                "sources": [],
            }
        ])
        service = ContentGenerationService(self.db_path, adapter)
        headers = {
            "X-Request-ID": "batch-conflict",
            "Idempotency-Key": "batch-conflict",
        }
        with patch(
            "sau_backend._create_content_generation_service",
            return_value=service,
        ):
            first = self.client.post(
                "/api/articles/generate-batch",
                headers=headers,
                json={"project_id": self.project_id, "topics": ["主题一"]},
            )
            conflict = self.client.post(
                "/api/articles/generate-batch",
                headers=headers,
                json={"project_id": self.project_id, "topics": ["换了主题"]},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.headers["Idempotency-Key"], "batch-conflict")
        self.assertEqual(
            conflict.get_json()["data"]["error_code"],
            "idempotency_conflict",
        )
        self.assertEqual(len(adapter.calls), 1)
        with closing(sqlite3.connect(self.db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE title IN ('主题一', '换了主题')"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_batch_generation_accepts_twenty_topics_and_rejects_twenty_one(self):
        topics = [f"主题{i}" for i in range(1, 21)]
        adapter = MockContentEngineAdapter(
            [
                {
                    "title": topic,
                    "summary": "",
                    "content": f"{topic}正文",
                    "tags": [],
                    "faq": [],
                    "sources": [],
                }
                for topic in topics
            ]
        )
        service = ContentGenerationService(self.db_path, adapter)
        headers = {
            "X-Request-ID": "batch-limit-twenty",
            "Idempotency-Key": "batch-limit-twenty",
        }
        with patch(
            "sau_backend._create_content_generation_service",
            return_value=service,
        ):
            accepted = self.client.post(
                "/api/articles/generate-batch",
                headers=headers,
                json={"project_id": self.project_id, "topics": topics},
            )
            rejected = self.client.post(
                "/api/articles/generate-batch",
                json={
                    "project_id": self.project_id,
                    "topics": [*topics, "主题21"],
                },
            )

        self.assertEqual(accepted.status_code, 200)
        data = accepted.get_json()["data"]
        self.assertEqual(data["batch_limit"], 20)
        self.assertEqual(data["execution_mode"], "sequential")
        self.assertEqual(data["created_count"], 20)
        self.assertEqual([call.topic for call in adapter.calls], topics)
        self.assertEqual(data["cost_summary"]["operation_count"], 20)
        self.assertEqual(data["cost_summary"]["unknown_usage_count"], 20)
        self.assertIsNone(data["cost_summary"]["known_total_tokens"])
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("20", rejected.get_json()["msg"])

    def test_batch_generation_continues_after_failure_and_summarizes_costs(self):
        adapter = MockContentEngineAdapter(
            [
                {
                    "title": "成功一",
                    "summary": "",
                    "content": "正文一",
                    "tags": [],
                    "faq": [],
                    "sources": [],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
                ContentEngineUnavailableError("模型暂时不可用"),
                {
                    "title": "成功三",
                    "summary": "",
                    "content": "正文三",
                    "tags": [],
                    "faq": [],
                    "sources": [],
                    "usage": {"input_tokens": 60, "output_tokens": 40},
                },
            ],
            version="batch-model",
        )
        service = ContentGenerationService(self.db_path, adapter)
        payload = {
            "project_id": self.project_id,
            "topics": ["成功主题一", "失败主题二", "成功主题三"],
        }
        headers = {
            "X-Request-ID": "batch-cost-partial",
            "Idempotency-Key": "batch-cost-partial",
        }
        catalog = json.dumps(
            {
                "mock/batch-model": {
                    "input_per_million": "1",
                    "output_per_million": "2",
                    "currency": "CNY",
                }
            }
        )
        with patch.dict("os.environ", {"AI_PRICE_CATALOG_JSON": catalog}), patch(
            "sau_backend._create_content_generation_service",
            return_value=service,
        ):
            response = self.client.post(
                "/api/articles/generate-batch",
                headers=headers,
                json=payload,
            )
            replay = self.client.post(
                "/api/articles/generate-batch",
                headers=headers,
                json=payload,
            )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["created_count"], 2)
        self.assertEqual(data["failed_count"], 1)
        self.assertEqual(
            [item["status"] for item in data["items"]],
            ["created", "failed", "created"],
        )
        self.assertEqual(len(adapter.calls), 3)
        self.assertTrue(data["items"][0]["cost_receipt"]["recorded"])
        self.assertEqual(
            data["items"][0]["cost_receipt"]["cost_status"],
            "calculated",
        )
        self.assertFalse(data["items"][1]["cost_receipt"]["recorded"])
        self.assertIsNone(data["items"][1]["cost_receipt"]["estimated_cost"])
        self.assertTrue(data["items"][2]["cost_receipt"]["recorded"])

        summary = data["cost_summary"]
        self.assertEqual(summary["operation_count"], 2)
        self.assertEqual(summary["known_total_tokens"], 250)
        self.assertEqual(summary["known_token_record_count"], 2)
        self.assertEqual(summary["unknown_usage_count"], 1)
        self.assertEqual(summary["unpriced_count"], 0)
        self.assertEqual(summary["unknown_cost_item_count"], 1)
        self.assertEqual(summary["missing_receipt_item_count"], 1)
        self.assertEqual(
            summary["calculated_costs"],
            [{"currency": "CNY", "estimated_cost": "0.00034"}],
        )

        self.assertEqual(replay.status_code, 200)
        replay_data = replay.get_json()["data"]
        self.assertEqual(replay_data["replayed_count"], 2)
        self.assertEqual(replay_data["cost_summary"], summary)
        self.assertEqual(len(adapter.calls), 3)
        with closing(sqlite3.connect(self.db_path)) as conn:
            receipt_count = conn.execute(
                "SELECT COUNT(*) FROM ai_usage_ledger WHERE operation = 'generate'"
            ).fetchone()[0]
        self.assertEqual(receipt_count, 2)

    def test_excel_template_and_import(self):
        template = self.client.get("/api/articles/import-template.xlsx")
        self.assertEqual(template.status_code, 200)
        self.assertTrue(template.data.startswith(b"PK"))

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["标题", "摘要", "正文", "标签", "状态"])
        sheet.append(["导入稿一", "摘要", "正文内容", "AI,自动化", "草稿"])
        output = io.BytesIO()
        workbook.save(output)
        output.seek(0)
        response = self.client.post(
            "/api/articles/import",
            data={
                "project_id": str(self.project_id),
                "file": (output, "articles.xlsx"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 201)
        article = response.get_json()["data"]["items"][0]
        self.assertEqual(article["title"], "导入稿一")
        self.assertEqual(article["tags"], ["AI", "自动化"])

    def test_generates_and_recommends_cover_image(self):
        generated = self.client.post(
            f"/api/articles/{self.article_id}/images/generate"
        )
        self.assertEqual(generated.status_code, 201)
        record = generated.get_json()["data"]
        image_path = self.media_root / record["file_path"]
        self.assertTrue(image_path.is_file())
        self.assertEqual(image_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

        recommended = self.client.get(
            f"/api/articles/{self.article_id}/images/recommend"
        )
        self.assertEqual(recommended.status_code, 200)
        self.assertEqual(
            recommended.get_json()["data"]["items"][0]["file_path"],
            record["file_path"],
        )

    def test_platform_manifest_and_video_job_contract(self):
        platforms = self.client.get("/api/publish/platforms").get_json()["data"]
        self.assertEqual(len(platforms), 11)
        bilibili = next(item for item in platforms if item["key"] == "bilibili")
        douyin = next(item for item in platforms if item["key"] == "douyin")
        self.assertTrue(bilibili["requires_video"])
        self.assertTrue(douyin["requires_images"])

        account_id = self._enable_real_task_creation(
            account_type=6,
            platform_name="bilibili",
        )
        missing = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "bilibili",
                "account_id": account_id,
            },
        )
        self.assertEqual(missing.status_code, 400)
        self.assertIn("视频素材", missing.get_json()["msg"])

        (self.media_root / "demo.mp4").write_bytes(b"video")
        with patch(
            "sau_backend._read_publish_video_metadata",
            return_value={
                "width": 1920,
                "height": 1080,
                "duration_seconds": 12,
            },
        ), patch(
            "sau_backend.evaluate_publish_citeability",
            return_value={
                "score": 100,
                "passed": True,
                "publish_gate_passed": True,
                "blocking_issues": [],
            },
        ):
            created = self.client.post(
                "/api/publish",
                json={
                    "article_id": self.article_id,
                    "platform": "bilibili",
                    "account_id": account_id,
                    "video": "demo.mp4",
                },
            )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["data"]["video"], "demo.mp4")

    def test_auto_image_creates_portable_cover_for_image_channel(self):
        account_id = self._enable_real_task_creation(
            account_type=3,
            platform_name="douyin",
        )
        with patch(
            "sau_backend.evaluate_publish_citeability",
            return_value={
                "score": 100,
                "passed": True,
                "publish_gate_passed": True,
                "blocking_issues": [],
            },
        ):
            response = self.client.post(
                "/api/publish",
                json={
                    "article_id": self.article_id,
                    "platform": "douyin",
                    "account_id": account_id,
                    "auto_image": True,
                },
            )
        self.assertEqual(response.status_code, 201)
        images = response.get_json()["data"]["images"]
        self.assertEqual(len(images), 1)
        self.assertTrue((self.media_root / images[0]).is_file())


if __name__ == "__main__":
    unittest.main()
