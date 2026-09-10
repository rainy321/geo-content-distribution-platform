import io
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image
from db.createTable import initialize_database
from sau_backend import (
    PRODUCT_PUBLISH_IMAGE_MAX_BYTES,
    PRODUCT_PUBLISH_VIDEO_MAX_BYTES,
    app,
)
from services.publish_job_service import transition_publish_job


def _test_image_bytes(*, image_format="PNG"):
    payload = io.BytesIO()
    Image.new("RGB", (2, 2), color=(32, 96, 160)).save(
        payload,
        format=image_format,
    )
    return payload.getvalue()


class PublishJobsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        self.media_root = Path(self.temp_dir.name) / "videoFile"
        self.media_root.mkdir()
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
                    (project_id, "AI Agent 指南", "文章正文", '["AI"]', "ready"),
                ).lastrowid

        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_demo_mode = app.config["DEMO_MODE"]
        self.original_media_root = app.config["MEDIA_ROOT"]
        self.original_testing = app.testing
        app.config["DATABASE_PATH"] = self.db_path
        app.config["DEMO_MODE"] = True
        app.config["MEDIA_ROOT"] = self.media_root
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.config["DEMO_MODE"] = self.original_demo_mode
        app.config["MEDIA_ROOT"] = self.original_media_root
        app.testing = self.original_testing
        self.temp_dir.cleanup()

    def test_product_media_byte_caps_match_the_upload_ui_contract(self):
        self.assertEqual(PRODUCT_PUBLISH_IMAGE_MAX_BYTES, 5 * 1024 * 1024)
        self.assertEqual(PRODUCT_PUBLISH_VIDEO_MAX_BYTES, 150 * 1024 * 1024)

    def test_creates_and_reads_demo_job_from_server_configuration(self):
        response = self.client.post(
            "/api/publish",
            json={"article_id": self.article_id, "platform": " ZHIHU ", "demo": False},
        )

        self.assertEqual(response.status_code, 201)
        job = response.get_json()["data"]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["platform"], "zhihu")
        self.assertTrue(job["demo"])
        self.assertNotIn("id", job)
        self.assertIn("job_id", job)

        detail_response = self.client.get(f"/api/publish/jobs/{job['job_id']}")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.get_json()["data"]
        snapshot = detail.pop("confirmation_snapshot")
        publication_snapshot = detail.pop("publication_snapshot")
        self.assertEqual(detail, job)
        self.assertEqual(snapshot["article_id"], self.article_id)
        self.assertEqual(snapshot["title"], "AI Agent 指南")
        self.assertEqual(snapshot["content"], "文章正文")
        self.assertEqual(snapshot["tags"], ["AI"])
        self.assertEqual(publication_snapshot["content"], "文章正文")
        self.assertFalse(publication_snapshot["adapted"])

    def test_preflight_builds_editable_kuaishou_copy_without_changing_article(self):
        original = "完整原稿。" * 180
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE articles SET content = ?, tags = ? WHERE id = ?",
                    (original, '["AI","产品化"]', self.article_id),
                )

        preview_response = self.client.post(
            "/api/publish/preflight",
            json={
                "article_id": self.article_id,
                "platforms": ["kuaishou"],
                "title_overrides": {"kuaishou": "快手可见标题"},
            },
        )

        self.assertEqual(preview_response.status_code, 200)
        variant = preview_response.get_json()["data"]["variants"][0]
        self.assertEqual(variant["original_content"], original)
        self.assertEqual(variant["content_policy"]["hard_limit"], 500)
        self.assertLessEqual(
            variant["publish_length"], variant["content_policy"]["safe_limit"]
        )
        self.assertTrue(variant["adapted"])
        self.assertEqual(variant["title"], "快手可见标题")
        self.assertIn("citeability", variant)
        self.assertIn("passed", variant["citeability"])

        created = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "kuaishou",
                "publish_title": "快手发布标题",
                "publish_content": variant["publish_content"],
            },
        )
        self.assertEqual(created.status_code, 201)
        detail = self.client.get(
            f"/api/publish/jobs/{created.get_json()['data']['job_id']}"
        ).get_json()["data"]
        self.assertEqual(
            detail["publication_snapshot"]["content"],
            variant["publish_content"],
        )
        self.assertEqual(
            detail["publication_snapshot"]["title"],
            "快手发布标题",
        )
        self.assertEqual(detail["confirmation_snapshot"]["content"], original)
        with closing(sqlite3.connect(self.db_path)) as conn:
            stored_original = conn.execute(
                "SELECT content FROM articles WHERE id = ?", (self.article_id,)
            ).fetchone()[0]
        self.assertEqual(stored_original, original)

    def test_create_rejects_manual_platform_copy_above_safe_limit(self):
        response = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "kuaishou",
                "publish_content": "字" * 470,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("470 字", response.get_json()["msg"])

    def test_platform_capabilities_expose_limits_verification_and_non_official_frequency(self):
        capabilities = self.client.get("/api/publish/platforms").get_json()["data"]
        douyin = next(item for item in capabilities if item["key"] == "douyin")
        kuaishou = next(item for item in capabilities if item["key"] == "kuaishou")

        self.assertEqual(douyin["content_policy"]["hard_limit"], 1000)
        self.assertEqual(kuaishou["content_policy"]["hard_limit"], 500)
        self.assertIn("短信验证码", douyin["verification"]["methods"])
        self.assertEqual(
            douyin["frequency_guidance"]["source"],
            "geo_conservative_default",
        )
        self.assertIn(
            "不是平台官方",
            douyin["frequency_guidance"]["disclaimer"],
        )

    def test_executes_demo_job_through_full_state_flow(self):
        job = self._create_job()

        response = self.client.post(f"/api/publish/jobs/{job['job_id']}/execute")

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["data"]
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["demo"])
        self.assertIn("未访问真实平台", result["message"])
        self.assertTrue(result["started_at"])
        self.assertTrue(result["finished_at"])

    def test_accepts_existing_material_images_and_exposes_them_in_payload(self):
        (self.media_root / "cover.png").write_bytes(_test_image_bytes())

        response = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "baijiahao",
                "images": ["cover.png"],
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["data"]["images"], ["cover.png"])

    def test_accepts_decodable_jpg_and_jpeg_images(self):
        jpeg_payload = _test_image_bytes(image_format="JPEG")
        (self.media_root / "cover.jpg").write_bytes(jpeg_payload)
        (self.media_root / "detail.JPEG").write_bytes(jpeg_payload)

        response = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "images": ["cover.jpg", "detail.JPEG"],
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.get_json()["data"]["images"],
            ["cover.jpg", "detail.JPEG"],
        )

    def test_uploaded_material_flows_into_image_backed_publish_job(self):
        upload = self.client.post(
            "/uploadSave",
            data={"file": (io.BytesIO(_test_image_bytes()), "geo-cover.png")},
            content_type="multipart/form-data",
        )
        filename = upload.get_json()["data"]["filepath"]

        created = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "baijiahao",
                "images": [filename],
            },
        )

        self.assertEqual(upload.status_code, 200)
        self.assertTrue((self.media_root / filename).is_file())
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["data"]["images"], [filename])
        executed = self.client.post(
            f"/api/publish/jobs/{created.get_json()['data']['job_id']}/execute"
        )
        self.assertEqual(executed.status_code, 200)
        self.assertEqual(executed.get_json()["data"]["status"], "success")
        self.assertEqual(executed.get_json()["data"]["images"], [filename])
        with closing(sqlite3.connect(self.db_path)) as conn:
            stored_path = conn.execute(
                "SELECT file_path FROM file_records ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        self.assertEqual(stored_path, filename)

    def test_rejects_missing_unsupported_and_unsafe_publish_images(self):
        (self.media_root / "cover.gif").write_bytes(b"test-image")
        payload = {"article_id": self.article_id, "platform": "zhihu"}

        missing = self.client.post(
            "/api/publish", json={**payload, "images": ["missing.png"]}
        )
        unsupported = self.client.post(
            "/api/publish", json={**payload, "images": ["cover.gif"]}
        )
        unsafe = self.client.post(
            "/api/publish", json={**payload, "images": ["../cover.png"]}
        )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(unsafe.status_code, 400)

    def test_rejects_unreadable_mismatched_and_oversized_images_before_task_creation(self):
        (self.media_root / "empty.png").write_bytes(b"")
        (self.media_root / "corrupt.png").write_bytes(b"not-an-image")
        (self.media_root / "renamed.png").write_bytes(
            _test_image_bytes(image_format="JPEG")
        )
        (self.media_root / "large.png").write_bytes(_test_image_bytes())
        payload = {"article_id": self.article_id, "platform": "tiktok"}

        with (
            patch("sau_backend.create_publish_job") as create_job,
            patch("sau_backend.create_real_publisher_factory") as browser_factory,
        ):
            empty = self.client.post(
                "/api/publish", json={**payload, "images": ["empty.png"]}
            )
            corrupt = self.client.post(
                "/api/publish", json={**payload, "images": ["corrupt.png"]}
            )
            mismatched = self.client.post(
                "/api/publish", json={**payload, "images": ["renamed.png"]}
            )
            with patch("sau_backend.PRODUCT_PUBLISH_IMAGE_MAX_BYTES", 16):
                oversized = self.client.post(
                    "/api/publish", json={**payload, "images": ["large.png"]}
                )

        self.assertEqual(empty.status_code, 400)
        self.assertIn("不能为空", empty.get_json()["msg"])
        self.assertEqual(corrupt.status_code, 400)
        self.assertIn("无法解码", corrupt.get_json()["msg"])
        self.assertEqual(mismatched.status_code, 400)
        self.assertIn("实际格式不一致", mismatched.get_json()["msg"])
        self.assertEqual(oversized.status_code, 400)
        self.assertIn("产品侧保守上限", oversized.get_json()["msg"])
        self.assertIn("不是平台官方上限", oversized.get_json()["msg"])
        create_job.assert_not_called()
        browser_factory.assert_not_called()

    def test_accepts_video_only_after_positive_basic_metadata_is_read(self):
        (self.media_root / "clip.mp4").write_bytes(b"non-empty-video")
        capture = MagicMock()
        capture.isOpened.return_value = True
        width_property = object()
        height_property = object()
        frame_count_property = object()
        fps_property = object()
        capture.get.side_effect = lambda prop: {
            width_property: 1920,
            height_property: 1080,
            frame_count_property: 300,
            fps_property: 25,
        }[prop]
        fake_cv2 = SimpleNamespace(
            CAP_PROP_FRAME_WIDTH=width_property,
            CAP_PROP_FRAME_HEIGHT=height_property,
            CAP_PROP_FRAME_COUNT=frame_count_property,
            CAP_PROP_FPS=fps_property,
            VideoCapture=MagicMock(return_value=capture),
        )

        with patch.dict("sys.modules", {"cv2": fake_cv2}):
            response = self.client.post(
                "/api/publish",
                json={
                    "article_id": self.article_id,
                    "platform": "tiktok",
                    "video": "clip.mp4",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["data"]["video"], "clip.mp4")
        fake_cv2.VideoCapture.assert_called_once_with(
            str((self.media_root / "clip.mp4").resolve())
        )
        capture.release.assert_called_once_with()

    def test_video_preflight_fails_closed_before_task_or_browser_start(self):
        (self.media_root / "empty.mp4").write_bytes(b"")
        (self.media_root / "unreadable.mp4").write_bytes(b"not-a-video")
        (self.media_root / "large.mp4").write_bytes(b"12")
        payload = {"article_id": self.article_id, "platform": "tiktok"}

        with (
            patch("sau_backend.create_publish_job") as create_job,
            patch("sau_backend.create_real_publisher_factory") as browser_factory,
        ):
            empty = self.client.post(
                "/api/publish", json={**payload, "video": "empty.mp4"}
            )
            with patch.dict("sys.modules", {"cv2": None}):
                unreadable = self.client.post(
                    "/api/publish",
                    json={**payload, "video": "unreadable.mp4"},
                )
            nonpositive_metadata = []
            for field in ("width", "height", "duration_seconds"):
                metadata = {
                    "width": 1920,
                    "height": 1080,
                    "duration_seconds": 12,
                }
                metadata[field] = 0
                with patch(
                    "sau_backend._read_publish_video_metadata",
                    return_value=metadata,
                ):
                    nonpositive_metadata.append(
                        self.client.post(
                            "/api/publish",
                            json={**payload, "video": "unreadable.mp4"},
                        )
                    )
            with patch("sau_backend.PRODUCT_PUBLISH_VIDEO_MAX_BYTES", 1):
                oversized = self.client.post(
                    "/api/publish", json={**payload, "video": "large.mp4"}
                )

        self.assertEqual(empty.status_code, 400)
        self.assertIn("不能为空", empty.get_json()["msg"])
        self.assertEqual(unreadable.status_code, 400)
        self.assertIn("无法读取有效的宽高和时长元数据", unreadable.get_json()["msg"])
        self.assertIn("产品级", unreadable.get_json()["msg"])
        for response in nonpositive_metadata:
            self.assertEqual(response.status_code, 400)
            self.assertIn("必须大于 0", response.get_json()["msg"])
        self.assertEqual(oversized.status_code, 400)
        self.assertIn("产品侧保守上限", oversized.get_json()["msg"])
        self.assertIn("不是平台官方上限", oversized.get_json()["msg"])
        create_job.assert_not_called()
        browser_factory.assert_not_called()

    def test_real_image_backed_platform_requires_an_image_at_job_creation(self):
        app.config["DEMO_MODE"] = False

        response = self.client.post(
            "/api/publish",
            json={"article_id": self.article_id, "platform": "xiaohongshu"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("至少一张", response.get_json()["msg"])

    def test_lists_jobs_with_filters_and_pagination(self):
        zhihu = self._create_job(platform="zhihu")
        self._create_job(platform="toutiao")

        response = self.client.get(
            f"/api/publish/jobs?article_id={self.article_id}"
            "&platform=zhihu&status=queued&page=1&page_size=1"
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["data"]
        self.assertEqual(result["pagination"]["total"], 1)
        self.assertEqual(result["items"][0]["job_id"], zhihu["job_id"])
        self.assertEqual(result["items"][0]["article_title"], "AI Agent 指南")

    def test_retries_failed_job(self):
        job = self._create_job()
        transition_publish_job(self.db_path, job["job_id"], "processing")
        transition_publish_job(
            self.db_path,
            job["job_id"],
            "failed",
            message="网络异常",
        )

        response = self.client.post(f"/api/publish/jobs/{job['job_id']}/retry")

        self.assertEqual(response.status_code, 200)
        retried = response.get_json()["data"]
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["message"], "")
        self.assertIsNone(retried["started_at"])

    def test_real_job_cannot_be_executed_by_safe_demo_endpoint(self):
        app.config["DEMO_MODE"] = False
        job = self._create_job()

        response = self.client.post(f"/api/publish/jobs/{job['job_id']}/execute")

        self.assertEqual(response.status_code, 409)
        self.assertIn("不能通过演示执行入口", response.get_json()["msg"])
        detail = self.client.get(f"/api/publish/jobs/{job['job_id']}").get_json()["data"]
        self.assertEqual(detail["status"], "queued")
        self.assertFalse(detail["demo"])

    def test_authorized_real_job_creation_blocks_low_citeability_copy(self):
        original_authorized = app.config.get("REAL_PUBLISHING_AUTHORIZED")
        original_allow = app.config.get("ALLOW_REAL_PUBLISHING")
        original_runtime = app.config.get("PUBLISHING_RUNTIME_READY")
        app.config["DEMO_MODE"] = False
        app.config["REAL_PUBLISHING_AUTHORIZED"] = True
        app.config["ALLOW_REAL_PUBLISHING"] = False
        app.config["PUBLISHING_RUNTIME_READY"] = True
        try:
            response = self.client.post(
                "/api/publish",
                json={"article_id": self.article_id, "platform": "zhihu"},
            )
        finally:
            app.config["REAL_PUBLISHING_AUTHORIZED"] = original_authorized
            app.config["ALLOW_REAL_PUBLISHING"] = original_allow
            app.config["PUBLISHING_RUNTIME_READY"] = original_runtime

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(
            payload["data"]["action"], "content_optimization_required"
        )
        self.assertFalse(payload["data"]["citeability"]["passed"])
        self.assertFalse(payload["data"]["citeability"]["publish_gate_passed"])

    def test_authorized_real_job_creation_allows_score_71_without_p0(self):
        original_authorized = app.config.get("REAL_PUBLISHING_AUTHORIZED")
        original_allow = app.config.get("ALLOW_REAL_PUBLISHING")
        original_runtime = app.config.get("PUBLISHING_RUNTIME_READY")
        app.config["DEMO_MODE"] = False
        app.config["REAL_PUBLISHING_AUTHORIZED"] = True
        app.config["ALLOW_REAL_PUBLISHING"] = False
        app.config["PUBLISHING_RUNTIME_READY"] = True
        try:
            with patch(
                "sau_backend.evaluate_publish_citeability",
                return_value={
                    "score": 71,
                    "pass_score": 75,
                    "passed": False,
                    "publish_gate_passed": True,
                    "blocking_issues": [],
                    "warnings": [
                        {
                            "code": "quality.score_below_target",
                            "priority": "P1",
                            "message": "质量分低于建议目标。",
                            "action": "建议继续优化。",
                        }
                    ],
                    "suggestions": [],
                },
            ):
                response = self.client.post(
                    "/api/publish",
                    json={"article_id": self.article_id, "platform": "zhihu"},
                )
        finally:
            app.config["REAL_PUBLISHING_AUTHORIZED"] = original_authorized
            app.config["ALLOW_REAL_PUBLISHING"] = original_allow
            app.config["PUBLISHING_RUNTIME_READY"] = original_runtime

        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.get_json()["data"]["demo"])

    def test_scheduled_job_waits_instead_of_executing_early(self):
        response = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "publish_at": "2026-09-02T09:30:00+08:00",
            },
        )
        job = response.get_json()["data"]

        execute_response = self.client.post(
            f"/api/publish/jobs/{job['job_id']}/execute"
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(job["status"], "scheduled")
        self.assertFalse(job["auto_execute"])
        self.assertFalse(job["authorization_bound"])
        self.assertEqual(execute_response.status_code, 409)

    def test_validates_create_and_list_inputs(self):
        invalid_json = self.client.post(
            "/api/publish",
            data="not-json",
            content_type="application/json",
        )
        invalid_article = self.client.post(
            "/api/publish",
            json={"article_id": "abc", "platform": "zhihu"},
        )
        missing_article = self.client.post(
            "/api/publish",
            json={"article_id": 999999, "platform": "zhihu"},
        )
        invalid_platform = self.client.post(
            "/api/publish",
            json={"article_id": self.article_id, "platform": "unknown"},
        )
        invalid_time = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "publish_at": "tomorrow",
            },
        )
        invalid_list = self.client.get("/api/publish/jobs?status=unknown")

        self.assertEqual(invalid_json.status_code, 400)
        self.assertEqual(invalid_article.status_code, 400)
        self.assertEqual(missing_article.status_code, 404)
        self.assertEqual(invalid_platform.status_code, 400)
        self.assertEqual(invalid_time.status_code, 400)
        self.assertEqual(invalid_list.status_code, 400)

    def test_unknown_and_non_retryable_jobs_return_domain_errors(self):
        job = self._create_job()

        missing_detail = self.client.get("/api/publish/jobs/999999")
        missing_retry = self.client.post("/api/publish/jobs/999999/retry")
        missing_execute = self.client.post("/api/publish/jobs/999999/execute")
        queued_retry = self.client.post(f"/api/publish/jobs/{job['job_id']}/retry")

        self.assertEqual(missing_detail.status_code, 404)
        self.assertEqual(missing_retry.status_code, 404)
        self.assertEqual(missing_execute.status_code, 404)
        self.assertEqual(queued_retry.status_code, 409)

    def _create_job(self, *, platform="zhihu"):
        response = self.client.post(
            "/api/publish",
            json={"article_id": self.article_id, "platform": platform},
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["data"]


if __name__ == "__main__":
    unittest.main()
