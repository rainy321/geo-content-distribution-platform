import unittest
from unittest.mock import patch
from pathlib import Path

from sau_backend import get_server_bind


ROOT = Path(__file__).resolve().parents[1]


class DeliverySafetyTests(unittest.TestCase):
    def test_docker_context_excludes_local_credentials_and_runtime_data(self):
        patterns = {
            line.strip()
            for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertTrue(
            {"cookiesFile", ".tmp", "/.env", "db/database.db"}.issubset(
                patterns
            )
        )

    def test_git_excludes_local_credentials_but_keeps_frontend_lockfile(self):
        patterns = {
            line.strip()
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertTrue({"cookiesFile", ".tmp/", "db/database.db"}.issubset(patterns))
        self.assertNotIn("package-lock.json", patterns)
        self.assertNotIn("conf.py", patterns)
        self.assertTrue((ROOT / "sau_frontend" / "package-lock.json").is_file())

    def test_compose_defaults_to_local_demo_with_persistent_runtime_data(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn('"127.0.0.1:5409:5409"', compose)
        self.assertIn('ALLOW_REAL_PUBLISHING: "false"', compose)
        self.assertIn('DATABASE_PATH: "/app/data/database.db"', compose)
        self.assertIn('SERVER_HOST: "0.0.0.0"', compose)
        self.assertIn("geo_cookies:/app/cookiesFile", compose)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/api/health", dockerfile)

    def test_source_server_defaults_to_loopback(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(("127.0.0.1", 5409), get_server_bind())

    def test_server_bind_accepts_explicit_container_values(self):
        with patch.dict(
            "os.environ",
            {"SERVER_HOST": "0.0.0.0", "SERVER_PORT": "5410"},
            clear=True,
        ):
            self.assertEqual(("0.0.0.0", 5410), get_server_bind())

    def test_invalid_server_port_falls_back_to_default(self):
        with patch.dict("os.environ", {"SERVER_PORT": "70000"}, clear=True):
            self.assertEqual(("127.0.0.1", 5409), get_server_bind())

    def test_windows_starter_uses_safe_local_demo_defaults(self):
        starter = (ROOT / "start-win.bat").read_text(encoding="utf-8").lower()

        self.assertIn('cd /d "%~dp0"', starter)
        self.assertIn('if not defined demo_mode set "demo_mode=true"', starter)
        self.assertIn(
            'if not defined allow_real_publishing set "allow_real_publishing=false"',
            starter,
        )
        self.assertIn("--host 127.0.0.1", starter)
        self.assertNotIn("--host 0.0.0.0", starter)


if __name__ == "__main__":
    unittest.main()
