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

        with patch("sau_backend.generate_geo_content") as generate:
            generate.return_value = {
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
        self.assertEqual(
            generate.call_args.kwargs["template_instruction"],
            "按目标、过程、结果和限制组织内容。",
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

    @patch("sau_backend.generate_geo_content")
    def test_batch_generation_saves_independent_drafts(self, generate):
        generate.side_effect = [
            {"title": "主题一", "summary": "", "content": "正文一", "tags": ["A"], "faq": []},
            {"title": "主题二", "summary": "", "content": "正文二", "tags": ["B"], "faq": []},
        ]
        response = self.client.post(
            "/api/articles/generate-batch",
            json={
                "project_id": self.project_id,
                "topics": ["主题一", "主题二"],
                "keywords": ["AI Agent"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["created_count"], 2)
        with closing(sqlite3.connect(self.db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE status = 'draft'"
            ).fetchone()[0]
        self.assertEqual(count, 2)

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
        self.assertEqual(len(platforms), 10)
        bilibili = next(item for item in platforms if item["key"] == "bilibili")
        douyin = next(item for item in platforms if item["key"] == "douyin")
        self.assertTrue(bilibili["requires_video"])
        self.assertTrue(douyin["requires_images"])

        app.config["DEMO_MODE"] = False
        missing = self.client.post(
            "/api/publish",
            json={"article_id": self.article_id, "platform": "bilibili"},
        )
        self.assertEqual(missing.status_code, 400)
        self.assertIn("视频素材", missing.get_json()["msg"])

        (self.media_root / "demo.mp4").write_bytes(b"video")
        created = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "bilibili",
                "video": "demo.mp4",
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["data"]["video"], "demo.mp4")

    def test_auto_image_creates_portable_cover_for_image_channel(self):
        app.config["DEMO_MODE"] = False
        response = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "douyin",
                "auto_image": True,
            },
        )
        self.assertEqual(response.status_code, 201)
        images = response.get_json()["data"]["images"]
        self.assertEqual(len(images), 1)
        self.assertTrue((self.media_root / images[0]).is_file())


if __name__ == "__main__":
    unittest.main()
