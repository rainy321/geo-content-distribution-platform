from __future__ import annotations

import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import requests
from PIL import Image

from db.createTable import initialize_database
from services.ai_image_generation_service import (
    AIImageGenerationError,
    generate_ai_article_cover,
)
from services.ai_service import AISettings
from services.cover_image_service import store_ai_generated_cover


class SyntheticResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def synthetic_png(width=32, height=24):
    output = BytesIO()
    Image.new("RGB", (width, height), (20, 80, 90)).save(output, format="PNG")
    return output.getvalue()


class AIImageGenerationServiceTests(unittest.TestCase):
    def setUp(self):
        self.settings = AISettings(
            base_url="https://images.example.test/v1",
            api_key="synthetic-secret",
            model="synthetic-image-model",
            timeout_seconds=3,
            provider="custom",
        )
        self.article = {"title": "Synthetic title"}
        self.project = {
            "name": "Synthetic Company",
            "product": "Synthetic Product",
            "industry": "Testing",
        }

    def test_base64_generation_uses_exact_endpoint_once_and_preserves_usage(self):
        encoded = base64.b64encode(synthetic_png()).decode("ascii")
        post = Mock(
            return_value=SyntheticResponse(
                payload={
                    "id": "provider-image-1",
                    "model": "provider-image-model",
                    "usage": {"prompt_tokens": 4, "total_tokens": 9},
                    "data": [{"b64_json": encoded}],
                }
            )
        )

        result = generate_ai_article_cover(
            article=self.article,
            project=self.project,
            settings=self.settings,
            http_post=post,
        )

        self.assertEqual(post.call_count, 1)
        self.assertEqual(
            post.call_args.args[0],
            "https://images.example.test/v1/images/generations",
        )
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(result.image_bytes, base64.b64decode(encoded))
        self.assertEqual(result.model, "provider-image-model")
        self.assertEqual(result.usage["input_tokens"], 4)
        self.assertEqual(result.usage["total_tokens"], 9)

    def test_read_timeout_is_unknown_and_never_retried(self):
        post = Mock(side_effect=requests.ReadTimeout("synthetic timeout"))

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
            )

        self.assertEqual(post.call_count, 1)
        self.assertTrue(context.exception.request_sent)
        self.assertTrue(context.exception.outcome_unknown)

    def test_http_failure_is_request_sent_and_not_silently_retried(self):
        post = Mock(return_value=SyntheticResponse(status_code=404))

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
            )

        self.assertEqual(post.call_count, 1)
        self.assertTrue(context.exception.request_sent)
        self.assertFalse(context.exception.outcome_unknown)
        self.assertIn("HTTP 404", str(context.exception))

    def test_private_remote_image_url_is_rejected_before_download(self):
        post = Mock(
            return_value=SyntheticResponse(
                payload={"data": [{"url": "https://127.0.0.1/image.png"}]}
            )
        )
        get = Mock()

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
                http_get=get,
            )

        get.assert_not_called()
        self.assertTrue(context.exception.request_sent)
        self.assertIn("私网", str(context.exception))

    def test_provider_image_is_normalized_before_entering_material_library(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        database_path = root / "database.db"
        media_root = root / "media"
        initialize_database(database_path)

        record = store_ai_generated_cover(
            database_path,
            media_root=media_root,
            article={"id": 7, "title": "Synthetic title", "tags": []},
            project=self.project,
            image_bytes=synthetic_png(64, 32),
        )

        self.assertEqual(record["source"], "generated_ai")
        saved = media_root / record["file_path"]
        self.assertTrue(saved.is_file())
        with Image.open(saved) as image:
            self.assertEqual(image.size, (1200, 628))
            self.assertEqual(image.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
