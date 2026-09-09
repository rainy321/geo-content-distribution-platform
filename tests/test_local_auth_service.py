from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.local_auth_service import (
    DEFAULT_PBKDF2_ITERATIONS,
    InvalidCurrentPasswordError,
    LocalAuthConfigurationError,
    LocalAuthService,
    PasswordAlreadyConfiguredError,
    PasswordPolicyError,
    validate_password,
)


TEST_ITERATIONS = 100_000


class LocalAuthServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_path = (
            Path(self.temporary_directory.name)
            / "nested desktop config"
            / "auth.json"
        )
        self.service = LocalAuthService(
            self.config_path,
            iterations=TEST_ITERATIONS,
        )

    def test_default_work_factor_is_high(self):
        self.assertGreaterEqual(DEFAULT_PBKDF2_ITERATIONS, 600_000)

    def test_first_access_creates_stable_session_secret_and_setup_state(self):
        first_secret = self.service.get_session_secret()
        first_status = self.service.status()
        reloaded = LocalAuthService(
            self.config_path,
            iterations=TEST_ITERATIONS,
        )

        self.assertEqual(reloaded.get_session_secret(), first_secret)
        self.assertGreaterEqual(len(first_secret), 43)
        self.assertFalse(first_status.configured)
        self.assertTrue(first_status.setup_required)

        persisted = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["schema_version"], 1)
        self.assertIsNone(persisted["password"])

    def test_initial_password_is_salted_hashed_and_verifiable_after_reload(self):
        password = "Geo-Desktop-2026!"
        self.service.set_initial_password(password)

        persisted_text = self.config_path.read_text(encoding="utf-8")
        persisted = json.loads(persisted_text)
        record = persisted["password"]
        self.assertNotIn(password, persisted_text)
        self.assertEqual(record["algorithm"], "pbkdf2_hmac_sha256")
        self.assertEqual(record["iterations"], TEST_ITERATIONS)
        self.assertNotIn("plaintext", record)

        reloaded = LocalAuthService(
            self.config_path,
            iterations=TEST_ITERATIONS,
        )
        self.assertTrue(reloaded.verify_password(password))
        self.assertFalse(reloaded.verify_password("Geo-Desktop-2026?"))
        self.assertFalse(reloaded.verify_password(None))
        self.assertTrue(reloaded.status().configured)
        self.assertFalse(reloaded.status().setup_required)

    def test_repeated_unauthenticated_setup_is_rejected(self):
        self.service.set_initial_password("Geo-Desktop-2026!")

        with self.assertRaises(PasswordAlreadyConfiguredError):
            self.service.set_initial_password("Different-Desktop-2026!")

        self.assertTrue(self.service.verify_password("Geo-Desktop-2026!"))
        self.assertFalse(self.service.verify_password("Different-Desktop-2026!"))

    def test_change_password_requires_current_password_and_rotates_digest(self):
        self.service.set_initial_password("Geo-Desktop-2026!")
        before = json.loads(self.config_path.read_text(encoding="utf-8"))

        with self.assertRaises(InvalidCurrentPasswordError):
            self.service.change_password(
                "wrong-current-password",
                "Geo-Desktop-Next-2026!",
            )

        self.service.change_password(
            "Geo-Desktop-2026!",
            "Geo-Desktop-Next-2026!",
        )
        after = json.loads(self.config_path.read_text(encoding="utf-8"))

        self.assertNotEqual(before["password"]["salt"], after["password"]["salt"])
        self.assertNotEqual(
            before["password"]["digest"],
            after["password"]["digest"],
        )
        self.assertFalse(self.service.verify_password("Geo-Desktop-2026!"))
        self.assertTrue(self.service.verify_password("Geo-Desktop-Next-2026!"))

    def test_password_policy_supports_long_non_latin_passphrases(self):
        validate_password("这是一个足够长而且容易记住的管理员口令")

        weak_passwords = (
            "too-short",
            "abcdefghijkl",
            "aaaaaaaaaaaaaaaaaaaa",
            "Valid-looking-2026! ",
            "Valid\x00Password-2026!",
        )
        for password in weak_passwords:
            with self.subTest(password=repr(password)):
                with self.assertRaises(PasswordPolicyError):
                    validate_password(password)

    def test_corrupt_or_unsafe_config_fails_closed(self):
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(LocalAuthConfigurationError):
            self.service.get_session_secret()

        self.config_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "session_secret": "predictable",
                    "password": None,
                    "created_at": "2026-09-09T00:00:00Z",
                    "updated_at": "2026-09-09T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(LocalAuthConfigurationError):
            self.service.status()

    def test_atomic_replace_failure_keeps_previous_file_and_removes_temp_file(self):
        original_secret = self.service.get_session_secret()
        original_contents = self.config_path.read_bytes()

        with mock.patch(
            "services.local_auth_service.os.replace",
            side_effect=OSError("simulated replace failure"),
        ):
            with self.assertRaises(LocalAuthConfigurationError):
                self.service.set_initial_password("Geo-Desktop-2026!")

        self.assertEqual(self.config_path.read_bytes(), original_contents)
        self.assertEqual(self.service.get_session_secret(), original_secret)
        self.assertFalse(self.service.status().configured)
        self.assertEqual(
            list(self.config_path.parent.glob(f".{self.config_path.name}.*.tmp")),
            [],
        )

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not reliable on Windows")
    def test_config_file_is_owner_read_write_only_on_posix(self):
        self.service.get_session_secret()
        mode = self.config_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
