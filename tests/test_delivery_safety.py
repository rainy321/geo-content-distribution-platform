import unittest
from pathlib import Path


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
        self.assertIn("geo_cookies:/app/cookiesFile", compose)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/api/health", dockerfile)


if __name__ == "__main__":
    unittest.main()
