from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from services.ai_service import AIConfigurationError
from services.unified_ai_config_service import (
    UnifiedAIConfigDataError,
    UnifiedAIConfigService,
    UnifiedAIConfigStorageUnavailableError,
    WindowsDPAPIProtector,
)


class SyntheticProtector:
    name = "synthetic_test_protector"

    def protect(self, plaintext: bytes) -> bytes:
        return b"protected:" + plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(b"protected:"):
            raise UnifiedAIConfigDataError("invalid synthetic ciphertext")
        return ciphertext.removeprefix(b"protected:")[::-1]


class UnifiedAIConfigServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_path = Path(self.temporary_directory.name) / "config" / "ai.json"
        self.service = UnifiedAIConfigService(
            self.config_path,
            protector=SyntheticProtector(),
            use_platform_protector=False,
        )

    def test_protects_secret_and_roundtrips_non_secret_configuration(self):
        secret = "synthetic-api-key-never-plaintext"

        status = self.service.save(
            provider="qwen",
            base_url="https://api.example.test/compatible-mode/v1",
            model="synthetic-model",
            api_key=secret,
        )

        self.assertTrue(status.configured)
        self.assertTrue(status.api_key_configured)
        self.assertEqual(status.provider, "qwen")
        self.assertEqual(status.credential_storage, SyntheticProtector.name)
        persisted_text = self.config_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, persisted_text)
        persisted = json.loads(persisted_text)
        self.assertNotIn("api_key", persisted)
        self.assertNotIn("key", persisted["credential"])

        settings = self.service.load_settings()
        self.assertEqual(settings.api_key, secret)
        self.assertEqual(settings.provider, "qwen")
        self.assertEqual(settings.model, "synthetic-model")
        self.assertEqual(
            settings.base_url,
            "https://api.example.test/compatible-mode/v1",
        )
        self.assertNotIn(secret, repr(settings))

    def test_update_can_reuse_existing_protected_key_without_returning_it(self):
        secret = "synthetic-api-key-never-returned"
        self.service.save(
            provider="qwen",
            base_url="https://api.example.test/v1",
            model="model-one",
            api_key=secret,
        )

        status = self.service.save(
            provider="qwen",
            base_url="https://api.example.test/v1",
            model="model-two",
        )

        self.assertEqual(status.model, "model-two")
        self.assertNotIn(secret, json.dumps(status.public_dict()))
        self.assertEqual(self.service.load_settings().api_key, secret)

    def test_equivalent_base_and_full_endpoint_share_one_connection_identity(self):
        secret = "synthetic-equivalent-endpoint-secret"
        self.service.save(
            provider="custom",
            base_url="https://gateway.example.test/openai/v2/chat/completions/",
            model="model-one",
            api_key=secret,
        )

        status = self.service.save(
            provider="custom",
            base_url="https://gateway.example.test/openai/v2",
            model="model-two",
        )

        self.assertEqual(status.base_url, "https://gateway.example.test/openai/v2")
        self.assertEqual(self.service.load_settings().api_key, secret)

    def test_role_credential_reuse_compares_resolved_endpoint(self):
        self.service.save(
            provider="custom",
            base_url="https://gateway.example.test/openai/v2",
            model="writer-model",
            api_key="writer-secret",
        )

        self.service.save_role(
            "reviewer",
            provider="custom",
            base_url="https://gateway.example.test/openai/v2/chat/completions",
            model="reviewer-model",
        )

        reviewer = self.service.load_settings("reviewer")
        self.assertEqual(reviewer.api_key, "writer-secret")
        self.assertEqual(
            reviewer.base_url,
            "https://gateway.example.test/openai/v2/chat/completions",
        )

    def test_writer_key_is_not_reused_after_provider_or_endpoint_changes(self):
        secret = "writer-secret-for-original-endpoint"
        self.service.save(
            provider="qwen",
            base_url="https://api.example.test/v1",
            model="writer-model",
            api_key=secret,
        )

        with self.assertRaises(AIConfigurationError):
            self.service.save(
                provider="custom",
                base_url="https://other.example.test/v1",
                model="replacement-model",
            )

        settings = self.service.load_settings("writer")
        self.assertEqual(settings.base_url, "https://api.example.test/v1")
        self.assertEqual(settings.model, "writer-model")
        self.assertEqual(settings.api_key, secret)

    def test_optional_roles_fallback_to_writer_until_independently_configured(self):
        self.service.save(
            provider="qwen",
            base_url="https://api.example.test/v1",
            model="writer-model",
            api_key="writer-secret",
        )

        initial = self.service.status().public_dict()

        self.assertTrue(initial["roles"]["reviewer"]["fallback"])
        self.assertEqual(initial["roles"]["reviewer"]["source_role"], "writer")
        self.assertEqual(self.service.load_settings("reviewer").model, "writer-model")
        self.assertTrue(initial["roles"]["reviewer"]["same_as_writer"])

        self.service.save_role(
            "reviewer",
            provider="deepseek",
            base_url="https://review.example.test/v1",
            model="review-model",
            api_key="review-secret",
        )
        reviewer = self.service.load_settings("optimizer")
        configured = self.service.status().public_dict()

        self.assertEqual(reviewer.model, "review-model")
        self.assertEqual(reviewer.api_key, "review-secret")
        self.assertTrue(configured["roles"]["reviewer"]["configured"])
        self.assertFalse(configured["roles"]["reviewer"]["fallback"])
        self.assertFalse(configured["roles"]["reviewer"]["same_as_writer"])
        serialized = self.config_path.read_text(encoding="utf-8")
        self.assertNotIn("writer-secret", serialized)
        self.assertNotIn("review-secret", serialized)

    def test_role_can_reuse_writer_credential_only_for_same_service(self):
        self.service.save(
            provider="qwen",
            base_url="https://api.example.test/v1",
            model="writer-model",
            api_key="writer-secret",
        )

        self.service.save_role(
            "evaluator",
            provider="qwen",
            base_url="https://api.example.test/v1",
            model="evaluation-model",
        )

        evaluator = self.service.load_settings("evaluator")
        self.assertEqual(evaluator.api_key, "writer-secret")
        self.assertEqual(evaluator.model, "evaluation-model")
        with self.assertRaises(AIConfigurationError):
            self.service.save_role(
                "image",
                provider="custom",
                base_url="https://images.example.test/v1",
                model="image-model",
            )

    def test_clear_optional_role_restores_explicit_fallback(self):
        self.service.save(
            provider="qwen",
            base_url="https://api.example.test/v1",
            model="writer-model",
            api_key="writer-secret",
        )
        self.service.save_role(
            "image",
            provider="custom",
            base_url="https://images.example.test/v1",
            model="image-model",
            api_key="image-secret",
        )

        status = self.service.clear_role("image").public_dict()

        self.assertFalse(status["roles"]["image"]["configured"])
        self.assertTrue(status["roles"]["image"]["fallback"])
        self.assertEqual(self.service.load_settings("image").model, "writer-model")

    def test_seedance_video_model_cannot_be_saved_as_image_role(self):
        self.service.save(
            provider="custom",
            base_url="https://tongyuye.com/v1",
            model="writer-model",
            api_key="writer-secret",
        )

        with self.assertRaisesRegex(
            AIConfigurationError,
            "Seedance.*视频模型.*Seedream",
        ):
            self.service.save_role(
                "image",
                provider="custom",
                base_url="https://tongyuye.com/v1",
                model="seedance-5.0",
                api_key="image-secret",
            )

        status = self.service.status().public_dict()
        self.assertFalse(status["roles"]["image"]["configured"])
        self.assertNotIn("image-secret", self.config_path.read_text(encoding="utf-8"))

    def test_reads_legacy_schema_one_and_upgrades_on_next_save(self):
        self.service.save(
            provider="qwen",
            base_url="https://api.example.test/v1",
            model="writer-model",
            api_key="writer-secret",
        )
        legacy = json.loads(self.config_path.read_text(encoding="utf-8"))
        legacy["schema_version"] = 1
        legacy.pop("roles", None)
        self.config_path.write_text(json.dumps(legacy), encoding="utf-8")

        self.assertEqual(self.service.load_settings().model, "writer-model")
        self.service.save(
            provider="qwen",
            base_url="https://api.example.test/v1",
            model="writer-model-v2",
        )
        upgraded = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(upgraded["schema_version"], 2)
        self.assertEqual(upgraded["roles"], {})

    def test_clear_removes_config_and_returns_empty_status(self):
        self.service.save(
            provider="qwen",
            base_url="https://api.example.test/v1",
            model="synthetic-model",
            api_key="synthetic-secret",
        )

        status = self.service.clear()

        self.assertFalse(status.configured)
        self.assertFalse(self.config_path.exists())

    def test_fails_closed_without_a_platform_protector(self):
        service = UnifiedAIConfigService(
            self.config_path,
            protector=None,
            use_platform_protector=False,
        )

        self.assertFalse(service.status().save_supported)
        with self.assertRaises(UnifiedAIConfigStorageUnavailableError):
            service.save(
                provider="qwen",
                base_url="https://api.example.test/v1",
                model="synthetic-model",
                api_key="must-not-be-written",
            )
        self.assertFalse(self.config_path.exists())

    def test_rejects_invalid_provider_or_endpoint_before_write(self):
        for values in (
            {
                "provider": "not a provider",
                "base_url": "https://api.example.test/v1",
            },
            {
                "provider": "qwen",
                "base_url": "http://127.0.0.1/v1",
            },
        ):
            with self.subTest(values=values):
                with self.assertRaises(AIConfigurationError):
                    self.service.save(
                        **values,
                        model="synthetic-model",
                        api_key="synthetic-secret",
                    )
        self.assertFalse(self.config_path.exists())

    def test_rejects_tampered_ciphertext(self):
        self.service.save(
            provider="qwen",
            base_url="https://api.example.test/v1",
            model="synthetic-model",
            api_key="synthetic-secret",
        )
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        payload["credential"]["protected_data"] = "dGFtcGVyZWQ="
        self.config_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(UnifiedAIConfigDataError):
            self.service.load_settings()

    def test_complete_save_replaces_corrupted_config_without_plaintext(self):
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text("{corrupt", encoding="utf-8")

        status = self.service.save(
            provider="qwen",
            base_url="https://api.example.test/v1",
            model="synthetic-model",
            api_key="replacement-synthetic-secret",
        )

        self.assertTrue(status.configured)
        self.assertEqual(
            self.service.load_settings().api_key,
            "replacement-synthetic-secret",
        )
        self.assertNotIn(
            "replacement-synthetic-secret",
            self.config_path.read_text(encoding="utf-8"),
        )

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is Windows-only")
    def test_windows_dpapi_roundtrip_is_current_user_protected(self):
        protector = WindowsDPAPIProtector()
        plaintext = b"synthetic-dpapi-roundtrip-secret"

        ciphertext = protector.protect(plaintext)

        self.assertNotEqual(ciphertext, plaintext)
        self.assertEqual(protector.unprotect(ciphertext), plaintext)


if __name__ == "__main__":
    unittest.main()
