import unittest
from pathlib import Path

from sau_web import build_waitress_options, main


ROOT = Path(__file__).resolve().parents[1]


class ProductionWebEntrypointTests(unittest.TestCase):
    def test_waitress_options_have_safe_local_defaults(self):
        self.assertEqual(
            {
                "host": "127.0.0.1",
                "port": 5409,
                "threads": 8,
                "channel_timeout": 180,
                "ident": "GEO",
            },
            build_waitress_options({}),
        )

    def test_waitress_options_accept_container_configuration(self):
        self.assertEqual(
            {
                "host": "0.0.0.0",
                "port": 5410,
                "threads": 12,
                "channel_timeout": 240,
                "ident": "GEO",
            },
            build_waitress_options(
                {
                    "SERVER_HOST": "0.0.0.0",
                    "SERVER_PORT": "5410",
                    "SERVER_THREADS": "12",
                    "SERVER_CHANNEL_TIMEOUT_SECONDS": "240",
                }
            ),
        )

    def test_invalid_numeric_options_fall_back_to_bounded_defaults(self):
        options = build_waitress_options(
            {
                "SERVER_PORT": "70000",
                "SERVER_THREADS": "0",
                "SERVER_CHANNEL_TIMEOUT_SECONDS": "not-a-number",
            }
        )

        self.assertEqual(5409, options["port"])
        self.assertEqual(8, options["threads"])
        self.assertEqual(180, options["channel_timeout"])

    def test_main_serves_the_supplied_application_without_starting_a_socket(self):
        application = object()
        calls = []

        def fake_serve(app, **options):
            calls.append((app, options))

        main(
            environ={"SERVER_HOST": "0.0.0.0", "SERVER_PORT": "5409"},
            serve_callable=fake_serve,
            application=application,
        )

        self.assertEqual(1, len(calls))
        self.assertIs(application, calls[0][0])
        self.assertEqual("0.0.0.0", calls[0][1]["host"])
        self.assertEqual(5409, calls[0][1]["port"])

    def test_container_uses_wsgi_entrypoint_and_existing_health_endpoint(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('CMD ["python", "-m", "sau_web"]', dockerfile)
        self.assertIn("FRONTEND_BUILD_DIR=/app", dockerfile)
        self.assertIn("import sau_backend", dockerfile)
        self.assertIn("'FRONTEND_BUILD_DIR'], 'index.html'", dockerfile)
        self.assertIn('"sau_backend"', pyproject)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/api/health", dockerfile)
        self.assertNotIn('CMD ["python", "sau_backend.py"]', dockerfile)


if __name__ == "__main__":
    unittest.main()
