from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from sau_backend import _create_content_generation_service, app
from services.ai_service import AICompletionResult, AISettings
from services.unified_ai_config_service import UnifiedAIConfigService


class SyntheticProtector:
    name = "synthetic_test_protector"

    def protect(self, plaintext: bytes) -> bytes:
        return b"protected:" + plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(b"protected:"):
            raise ValueError("invalid ciphertext")
        return ciphertext.removeprefix(b"protected:")[::-1]


class UnifiedAIConfigApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.database_path = root / "database.db"
        initialize_database(self.database_path)
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO projects (
                        name, website, product, industry, description,
                        keywords, competitors
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "Synthetic Company",
                        "https://synthetic.example.test",
                        "Synthetic Product",
                        "Testing",
                        "Synthetic test data only.",
                        "[]",
                        "[]",
                    ),
                )
                self.project_id = int(cursor.lastrowid)
                article = conn.execute(
                    """
                    INSERT INTO articles (
                        project_id, title, summary, content, tags, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.project_id,
                        "Synthetic article",
                        "Synthetic summary",
                        "Synthetic Company provides Synthetic Product for controlled tests.",
                        "[]",
                        "draft",
                    ),
                )
                self.article_id = int(article.lastrowid)
        self.config_path = root / "config" / "ai.json"
        self.config_service = UnifiedAIConfigService(
            self.config_path,
            protector=SyntheticProtector(),
            use_platform_protector=False,
        )
        self.original_config = {
            "DATABASE_PATH": app.config.get("DATABASE_PATH"),
            "ACCESS_CONTROL_ENABLED": app.config.get("ACCESS_CONTROL_ENABLED"),
            "AI_RATE_LIMIT_PER_MINUTE": app.config.get("AI_RATE_LIMIT_PER_MINUTE"),
            "UNIFIED_AI_CONFIG_SERVICE": app.config.get("UNIFIED_AI_CONFIG_SERVICE"),
            "TESTING": app.config.get("TESTING"),
        }
        app.config.update(
            DATABASE_PATH=self.database_path,
            ACCESS_CONTROL_ENABLED=False,
            AI_RATE_LIMIT_PER_MINUTE=0,
            UNIFIED_AI_CONFIG_SERVICE=self.config_service,
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)

    def save_payload(self, **overrides):
        payload = {
            "provider": "qwen",
            "base_url": "https://api.example.test/compatible-mode/v1",
            "model": "synthetic-model",
            "api_key": "synthetic-unified-api-key",
        }
        payload.update(overrides)
        return payload

    def test_save_status_and_clear_never_return_or_plaintext_persist_key(self):
        secret = self.save_payload()["api_key"]

        saved = self.client.put("/api/ai/config", json=self.save_payload())
        status = self.client.get("/api/ai/config-status")

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(status.status_code, 200)
        for response in (saved, status):
            serialized = response.get_data(as_text=True)
            self.assertNotIn(secret, serialized)
            data = response.get_json()["data"]
            self.assertTrue(data["configured"])
            self.assertTrue(data["saved_configured"])
            self.assertTrue(data["api_key_configured"])
            self.assertEqual(data["source"], "saved")
            self.assertEqual(data["provider"], "qwen")
            self.assertEqual(data["model"], "synthetic-model")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn(secret, self.config_path.read_text(encoding="utf-8"))
        self.assertNotIn(secret.encode("utf-8"), self.database_path.read_bytes())

        cleared = self.client.delete("/api/ai/config")
        self.assertEqual(cleared.status_code, 200)
        self.assertFalse(cleared.get_json()["data"]["saved_configured"])
        self.assertFalse(self.config_path.exists())

    def test_role_config_status_and_clear_are_secret_free(self):
        self.client.put("/api/ai/config", json=self.save_payload())
        evaluator_saved = self.client.put(
            "/api/ai/config/roles/evaluator",
            json={
                "provider": "custom",
                "base_url": "https://evaluation.example.test/v1",
                "model": "synthetic-evaluator-model",
                "api_key": "synthetic-evaluator-secret",
                "reuse_writer_credential": False,
            },
        )
        self.assertEqual(evaluator_saved.status_code, 200)

        saved = self.client.put(
            "/api/ai/config/roles/reviewer",
            json={
                "provider": "qwen",
                "base_url": "https://api.example.test/compatible-mode/v1",
                "model": "synthetic-reviewer-model",
                "reuse_writer_credential": True,
            },
        )

        self.assertEqual(saved.status_code, 200)
        roles = saved.get_json()["data"]["roles"]
        self.assertTrue(roles["reviewer"]["configured"])
        self.assertFalse(roles["reviewer"]["fallback"])
        self.assertFalse(roles["reviewer"]["same_as_writer"])
        self.assertEqual(
            self.config_service.load_settings("reviewer").api_key,
            self.save_payload()["api_key"],
        )
        self.assertNotIn(self.save_payload()["api_key"], saved.get_data(as_text=True))

        cleared = self.client.delete("/api/ai/config/roles/reviewer")
        self.assertEqual(cleared.status_code, 200)
        reviewer = cleared.get_json()["data"]["roles"]["reviewer"]
        self.assertFalse(reviewer["configured"])
        self.assertTrue(reviewer["fallback"])
        self.assertEqual(
            self.config_service.load_settings("writer").model,
            "synthetic-model",
        )
        self.assertEqual(
            self.config_service.load_settings("evaluator").model,
            "synthetic-evaluator-model",
        )
        self.assertTrue(cleared.get_json()["data"]["roles"]["evaluator"]["configured"])
        self.assertNotIn(
            "synthetic-evaluator-secret",
            cleared.get_data(as_text=True),
        )

    @patch("sau_backend.ContentGenerationService.from_environment")
    def test_generation_factory_only_wires_explicit_reviewer_role(self, build):
        self.config_service.save(**self.save_payload())
        writer_settings = self.config_service.load_settings("writer")

        _create_content_generation_service(writer_settings)

        first_kwargs = build.call_args.kwargs
        self.assertIsNone(first_kwargs["reviewer_settings"])
        self.assertFalse(first_kwargs["reviewer_role"]["configured"])
        self.assertTrue(first_kwargs["reviewer_role"]["fallback"])

        self.config_service.save_role(
            "reviewer",
            provider="deepseek",
            base_url="https://review.example.test/v1",
            model="review-model",
            api_key="review-secret",
        )
        _create_content_generation_service(writer_settings)

        second_kwargs = build.call_args.kwargs
        self.assertEqual(second_kwargs["qwen_settings"].model, "synthetic-model")
        self.assertEqual(second_kwargs["reviewer_settings"].model, "review-model")
        self.assertEqual(second_kwargs["reviewer_settings"].api_key, "review-secret")
        self.assertTrue(second_kwargs["reviewer_role"]["configured"])
        self.assertFalse(second_kwargs["reviewer_role"]["fallback"])
        self.assertFalse(second_kwargs["reviewer_role"]["same_as_writer"])

    def test_role_config_rejects_cross_provider_credential_reuse(self):
        self.client.put("/api/ai/config", json=self.save_payload())

        response = self.client.put(
            "/api/ai/config/roles/image",
            json={
                "provider": "custom",
                "base_url": "https://images.example.test/v1",
                "model": "synthetic-image-model",
                "reuse_writer_credential": True,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn(self.save_payload()["api_key"], response.get_data(as_text=True))

    @patch("sau_backend.generate_article_cover")
    def test_local_cover_reports_configured_image_role_as_not_invoked(self, generate):
        self.config_service.save(**self.save_payload())
        self.config_service.save_role(
            "image",
            provider="custom",
            base_url="https://images.example.test/v1",
            model="image-model",
            api_key="image-secret",
        )
        generate.return_value = {"id": 77, "source": "generated"}

        response = self.client.post(
            f"/api/articles/{self.article_id}/images/generate"
        )

        self.assertEqual(response.status_code, 201)
        data = response.get_json()["data"]
        self.assertEqual(data["generation_mode"], "local_deterministic")
        self.assertEqual(
            data["ai_role"]["execution"],
            "configured_but_not_invoked",
        )
        self.assertEqual(data["ai_role"]["model"], "image-model")
        self.assertNotIn("image-secret", response.get_data(as_text=True))

    def test_invalid_save_does_not_replace_existing_good_config(self):
        self.client.post("/api/ai/config", json=self.save_payload())

        response = self.client.put(
            "/api/ai/config",
            json=self.save_payload(base_url="http://localhost:9999/v1"),
        )

        self.assertEqual(response.status_code, 400)
        settings = self.config_service.load_settings()
        self.assertEqual(
            settings.base_url,
            "https://api.example.test/compatible-mode/v1",
        )

    def test_writer_endpoint_change_without_new_key_is_rejected_without_overwrite(self):
        original = self.save_payload()
        self.client.put("/api/ai/config", json=original)

        response = self.client.put(
            "/api/ai/config",
            json={
                "provider": "custom",
                "base_url": "https://other.example.test/v1",
                "model": "other-model",
            },
        )

        self.assertEqual(response.status_code, 400)
        settings = self.config_service.load_settings("writer")
        self.assertEqual(settings.base_url, original["base_url"])
        self.assertEqual(settings.model, original["model"])
        self.assertEqual(settings.api_key, original["api_key"])
        self.assertNotIn(original["api_key"], response.get_data(as_text=True))

    def test_content_engine_health_uses_saved_unified_configuration(self):
        self.config_service.save(**self.save_payload())

        response = self.client.get("/api/content-engine/status")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertTrue(data["ready"])
        self.assertEqual(data["primary"]["engine"], "qwen")
        self.assertEqual(data["primary"]["version"], "synthetic-model")

    @patch("sau_backend.sample_ai_prompt_detailed")
    def test_connection_test_uses_fixed_synthetic_prompt_and_saved_config(self, sample):
        self.config_service.save(**self.save_payload())
        sample.return_value = AICompletionResult(
            content="OK",
            model="synthetic-model",
            provider_request_id="provider-test-1",
            elapsed_ms=4,
            usage={"total_tokens": 3},
        )

        response = self.client.post(
            "/api/ai/config/test",
            json={"confirm_external_call": True},
        )

        self.assertEqual(response.status_code, 200)
        sample.assert_called_once()
        self.assertEqual(sample.call_args.kwargs["prompt"], "仅回复 OK")
        self.assertEqual(sample.call_args.kwargs["settings"].model, "synthetic-model")
        data = response.get_json()["data"]
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "ready")
        self.assertFalse(data["data_disclosure"]["project_data_sent"])
        self.assertFalse(data["data_disclosure"]["api_key_returned"])
        self.assertNotIn(self.save_payload()["api_key"], response.get_data(as_text=True))

    @patch("sau_backend.sample_ai_prompt_detailed")
    def test_ark_config_keeps_api_v3_endpoint_for_connection_test(self, sample):
        secret = "synthetic-ark-secret"
        saved = self.client.put(
            "/api/ai/config",
            json={
                "provider": "doubao",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3/",
                "model": "ep-synthetic-endpoint",
                "api_key": secret,
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(
            saved.get_json()["data"]["base_url"],
            "https://ark.cn-beijing.volces.com/api/v3",
        )
        sample.return_value = AICompletionResult(
            content="OK",
            model="ep-synthetic-endpoint",
            provider_request_id="provider-ark-test-1",
            elapsed_ms=5,
            usage={"total_tokens": 3},
        )

        response = self.client.post(
            "/api/ai/config/test",
            json={"confirm_external_call": True},
        )

        self.assertEqual(response.status_code, 200)
        settings = sample.call_args.kwargs["settings"]
        self.assertEqual(settings.provider, "doubao")
        self.assertEqual(settings.base_url, "https://ark.cn-beijing.volces.com/api/v3")
        self.assertEqual(settings.model, "ep-synthetic-endpoint")
        self.assertEqual(settings.api_key, secret)
        self.assertEqual(
            response.get_json()["data"]["endpoint"],
            "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        )
        self.assertNotIn(secret, response.get_data(as_text=True))

    @patch("sau_backend.sample_ai_prompt_detailed")
    def test_connection_test_resolves_requested_role_and_reports_fallback(self, sample):
        self.config_service.save(**self.save_payload())
        sample.return_value = AICompletionResult(
            content="OK",
            model="synthetic-model",
            provider_request_id="provider-role-test-1",
            elapsed_ms=4,
            usage={"total_tokens": 3},
        )

        response = self.client.post(
            "/api/ai/config/test",
            json={"confirm_external_call": True, "role": "reviewer"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sample.call_args.kwargs["settings"].model, "synthetic-model")
        role = response.get_json()["data"]["ai_role"]
        self.assertEqual(role["role"], "reviewer")
        self.assertTrue(role["fallback"])

    @patch("sau_backend.sample_ai_prompt_detailed")
    def test_connection_test_rejects_invalid_explicit_draft_as_bad_request(self, sample):
        response = self.client.post(
            "/api/ai/config/test",
            json={
                "confirm_external_call": True,
                "ai_config": {
                    "base_url": "http://localhost:9999/v1",
                    "model": "synthetic-model",
                    "api_key": "request-only-secret",
                },
            },
        )

        self.assertEqual(response.status_code, 400)
        sample.assert_not_called()

    @patch("sau_backend.sample_ai_prompt_detailed")
    def test_connection_test_requires_explicit_external_call_confirmation(self, sample):
        self.config_service.save(**self.save_payload())

        response = self.client.post(
            "/api/ai/config/test",
            json={"confirm_external_call": False},
        )

        self.assertEqual(response.status_code, 400)
        sample.assert_not_called()

    @patch("services.ai_visibility_sampling_service.sample_ai_prompt_detailed")
    def test_visibility_sampling_uses_saved_config_when_request_omits_it(self, sampler):
        self.config_service.save(**self.save_payload())
        sampler.return_value = AICompletionResult(
            content="Synthetic Product",
            model="synthetic-model",
            provider_request_id="provider-synthetic-1",
            elapsed_ms=3,
            usage={"total_tokens": 2},
        )

        response = self.client.post(
            "/api/geo/visibility/sample",
            json={
                "project_id": self.project_id,
                "prompt": "Which synthetic product is visible?",
                "sample_count": 1,
                "persist": False,
                "confirm_external_call": True,
            },
            headers={
                "X-Request-ID": "saved-config-sample-1",
                "Idempotency-Key": "saved-config-sample-key-1",
            },
        )

        self.assertEqual(response.status_code, 200)
        settings = sampler.call_args.kwargs["settings"]
        self.assertIsInstance(settings, AISettings)
        self.assertEqual(settings.model, "synthetic-model")
        self.assertEqual(settings.provider, "qwen")
        self.assertEqual(settings.api_key, self.save_payload()["api_key"])
        self.assertNotIn(
            self.save_payload()["api_key"],
            response.get_data(as_text=True),
        )

    @patch("services.ai_visibility_sampling_service.sample_ai_prompt_detailed")
    def test_visibility_sampling_uses_independent_evaluator_role(self, sampler):
        self.config_service.save(**self.save_payload())
        self.config_service.save_role(
            "evaluator",
            provider="deepseek",
            base_url="https://evaluation.example.test/v1",
            model="evaluation-model",
            api_key="evaluation-secret",
        )
        sampler.return_value = AICompletionResult(
            content="Synthetic Product",
            model="evaluation-model",
            provider_request_id="provider-evaluation-1",
            elapsed_ms=3,
            usage={"total_tokens": 2},
        )

        response = self.client.post(
            "/api/geo/visibility/sample",
            json={
                "project_id": self.project_id,
                "prompt": "Which synthetic product is visible?",
                "sample_count": 1,
                "persist": False,
                "confirm_external_call": True,
            },
            headers={
                "X-Request-ID": "evaluator-role-sample-1",
                "Idempotency-Key": "evaluator-role-sample-key-1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sampler.call_args.kwargs["settings"].model, "evaluation-model")
        self.assertEqual(response.get_json()["data"]["ai_role"]["role"], "evaluator")
        self.assertFalse(response.get_json()["data"]["ai_role"]["fallback"])

    @patch("sau_backend._create_content_generation_service")
    def test_content_generation_uses_same_saved_config(self, factory):
        self.config_service.save(**self.save_payload())
        factory.return_value.generate.return_value = {
            "title": "Synthetic title",
            "summary": "",
            "content": "Synthetic body",
            "tags": [],
            "faq": [],
        }

        response = self.client.post(
            "/api/articles/generate",
            json={
                "project_id": self.project_id,
                "topic": "Synthetic topic",
            },
        )

        self.assertEqual(response.status_code, 200)
        settings = factory.call_args.args[0]
        self.assertIsInstance(settings, AISettings)
        self.assertEqual(settings.model, "synthetic-model")
        self.assertEqual(settings.api_key, self.save_payload()["api_key"])
        self.assertNotIn(
            self.save_payload()["api_key"],
            response.get_data(as_text=True),
        )

    @patch("sau_backend._create_content_generation_service")
    def test_content_generation_routes_writer_and_tracks_diversity(self, factory):
        self.config_service.save(**self.save_payload())
        factory.return_value.generate.return_value = {
            "title": "Synthetic title",
            "summary": "",
            "content": "Synthetic body",
            "tags": [],
            "faq": [],
            "generation": {},
        }

        response = self.client.post(
            "/api/articles/generate",
            json={
                "project_id": self.project_id,
                "topic": "Synthetic topic",
                "diversity_seed": "stable-diversity-seed",
            },
            headers={
                "X-Request-ID": "writer-role-request-1",
                "Idempotency-Key": "writer-role-key-1",
            },
        )

        self.assertEqual(response.status_code, 200)
        request_value = factory.return_value.generate.call_args.args[0]
        self.assertEqual(
            request_value.diversity_profile["fact_policy"],
            "input_only_no_invention",
        )
        self.assertIn("不得为了差异化虚构", request_value.template_instruction)
        self.assertEqual(request_value.ai_role["role"], "writer")
        self.assertFalse(request_value.ai_role["fallback"])
        role = response.get_json()["data"]["generation"]["ai_role"]
        self.assertEqual(role["role"], "writer")
        self.assertFalse(role["fallback"])

    @patch("sau_backend.optimize_geo_content")
    def test_optimization_uses_independent_reviewer_role(self, optimize):
        self.config_service.save(**self.save_payload())
        self.config_service.save_role(
            "reviewer",
            provider="deepseek",
            base_url="https://review.example.test/v1",
            model="review-model",
            api_key="review-secret",
        )
        optimize.return_value = {
            "title": "Reviewed synthetic article",
            "summary": "Reviewed summary",
            "content": "Synthetic Company provides Synthetic Product for controlled tests.",
            "tags": [],
            "faq": [],
        }

        response = self.client.post(f"/api/articles/{self.article_id}/optimize")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(optimize.call_args.kwargs["settings"].model, "review-model")
        role = response.get_json()["data"]["ai_role"]
        self.assertEqual(role["role"], "reviewer")
        self.assertFalse(role["fallback"])

    @patch("sau_backend._create_content_generation_service")
    def test_explicit_request_config_overrides_saved_config(self, factory):
        self.config_service.save(**self.save_payload())
        factory.return_value.generate.return_value = {
            "title": "Synthetic title",
            "summary": "",
            "content": "Synthetic body",
            "tags": [],
            "faq": [],
        }
        explicit = {
            "base_url": "https://override.example.test/v1",
            "model": "override-model",
            "api_key": "request-only-secret",
        }

        response = self.client.post(
            "/api/articles/generate",
            json={
                "project_id": self.project_id,
                "topic": "Synthetic topic",
                "ai_config": explicit,
            },
        )

        self.assertEqual(response.status_code, 200)
        settings = factory.call_args.args[0]
        self.assertEqual(settings.base_url, explicit["base_url"])
        self.assertEqual(settings.model, explicit["model"])
        self.assertEqual(settings.api_key, explicit["api_key"])


if __name__ == "__main__":
    unittest.main()
