import unittest
import json
from unittest.mock import patch
from pathlib import Path

from sau_backend import get_server_bind
from services.platform_capability_service import normalize_platform_key


ROOT = Path(__file__).resolve().parents[1]


def _compose_service_block(document: str, service_name: str) -> str:
    """Return one two-space-indented service without requiring PyYAML."""

    marker = f"  {service_name}:"
    lines = document.splitlines()
    start = lines.index(marker)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if (
            line.startswith("  ")
            and not line.startswith("    ")
            and line.strip()
            and not line.lstrip().startswith("#")
        ):
            end = index
            break
    return "\n".join(lines[start:end])


class DeliverySafetyTests(unittest.TestCase):
    def test_user_facing_sohu_label_normalizes_to_stable_platform_key(self):
        self.assertEqual("sohu", normalize_platform_key("搜狐"))
        self.assertEqual("sohu", normalize_platform_key("搜狐号"))

    def test_primary_install_docs_clone_the_geo_repository(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        install = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
        primary_docs = readme + install

        self.assertIn(
            "git clone https://github.com/rainy321/geo-content-distribution-platform.git",
            readme,
        )
        self.assertIn(
            "git clone https://github.com/rainy321/geo-content-distribution-platform.git",
            install,
        )
        self.assertNotIn("git clone https://github.com/rehatRobot/omnipost.git", primary_docs)
        self.assertNotIn(
            "git clone https://github.com/dreammis/social-auto-upload.git",
            primary_docs,
        )
        self.assertIn('uv pip install -e ".[web]"', install)
        self.assertIn("playwright install chromium", install)
        self.assertIn("npm ci", install)

    def test_readme_matches_real_platform_and_scheduler_gates(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        for platform in ("知乎", "今日头条", "搜狐号", "百家号", "小红书"):
            self.assertIn(platform, readme)
        self.assertIn("授权内容指纹到期仍匹配", readme)
        self.assertIn("失败或状态不明不会自动重试", readme)
        self.assertNotIn("真实任务只进入队列，等待已配置账号", readme)

    def test_install_docs_preserve_environment_driven_safe_config(self):
        install = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
        example = (ROOT / "conf.example.py").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertNotIn("cp conf.example.py conf.py", install)
        self.assertIn("不要用示例文件覆盖", install)
        self.assertIn('"LOCAL_CHROME_HEADLESS", "true"', example)
        self.assertIn('os.getenv("DEBUG_MODE", "false")', example)
        self.assertNotIn("DEBUG_MODE = True", example)
        self.assertIn("LOCAL_CHROME_HEADLESS=true", env_example)
        self.assertIn("DEBUG_MODE=false", env_example)
        self.assertIn("MEDIA_ROOT=videoFile", env_example)
        self.assertNotIn("AI_API_KEY=sk-", env_example)

    def test_vercel_runtime_is_split_from_local_browser_worker(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        required, optional = pyproject.split("[project.optional-dependencies]", 1)

        for dependency in (
            '"Flask[async]==3.1.1"',
            '"apscheduler==3.11.0"',
            '"flask-cors==6.0.0"',
        ):
            self.assertIn(dependency, required)
            self.assertNotIn(dependency, optional)

        for dependency in (
            '"opencv-python>=4.13.0.92"',
            '"patchright==1.58.2"',
            '"playwright==1.52.0"',
        ):
            self.assertNotIn(dependency, required)
            self.assertIn(dependency, optional)

    def test_docker_context_excludes_local_credentials_and_runtime_data(self):
        patterns = {
            line.strip()
            for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertTrue(
            {
                "cookiesFile",
                ".tmp",
                "/.env",
                "/.env.*",
                "!/.env.example",
                "/.vercel",
                "deploy/production/app.env",
                "deploy/production/content-engine.env",
                "db/database.db",
            }.issubset(
                patterns
            )
        )

    def test_vercel_context_excludes_local_runtime_and_browser_dependencies(self):
        patterns = {
            line.strip()
            for line in (ROOT / ".vercelignore").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertTrue(
            {
                ".venv",
                ".tmp",
                "/.env",
                "cookiesFile",
                "db/database.db",
                "sau_frontend/node_modules",
            }.issubset(patterns)
        )

    def test_git_excludes_local_credentials_but_keeps_frontend_lockfile(self):
        patterns = {
            line.strip()
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertTrue(
            {"cookiesFile", ".tmp/", ".vercel/", "db/database.db"}.issubset(patterns)
        )
        self.assertNotIn("package-lock.json", patterns)
        self.assertNotIn("conf.py", patterns)
        self.assertTrue((ROOT / "sau_frontend" / "package-lock.json").is_file())

    def test_compose_defaults_to_local_demo_with_persistent_runtime_data(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn('"127.0.0.1:5409:5409"', compose)
        self.assertIn('ALLOW_REAL_PUBLISHING: "false"', compose)
        self.assertIn('ENABLE_BILIBILI_RUNTIME: "false"', compose)
        self.assertIn('DATABASE_PATH: "/app/data/database.db"', compose)
        self.assertIn('SERVER_HOST: "0.0.0.0"', compose)
        self.assertIn("geo_cookies:/app/cookiesFile", compose)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/api/health", dockerfile)
        self.assertIn("ARG NODE_IMAGE=node:22.21.1", dockerfile)
        self.assertIn("ARG PYTHON_IMAGE=python:3.10.19", dockerfile)
        self.assertIn("PATCHRIGHT_CHROMIUM_REVISION=1208", dockerfile)
        self.assertIn(
            "ARG PATCHRIGHT_CHROMIUM_SHA256="
            "b5e3195041af345a668d110f5daf5581961fa3608626ea588c97dd0fe81c4e38",
            dockerfile,
        )
        chromium_checksum_check = (
            'echo "${PATCHRIGHT_CHROMIUM_SHA256}  '
            '/tmp/patchright-chromium.zip" | sha256sum -c -'
        )
        self.assertIn(chromium_checksum_check, dockerfile)
        self.assertLess(
            dockerfile.index(chromium_checksum_check),
            dockerfile.index("unzip -q /tmp/patchright-chromium.zip"),
        )
        self.assertIn("https://mirrors.aliyun.com/debian", dockerfile)
        self.assertIn("Acquire::Retries=3", dockerfile)
        self.assertIn("fonts-noto-cjk", dockerfile)
        self.assertIn("fc-match 'Noto Sans CJK SC'", dockerfile)
        self.assertNotIn("RUN patchright install chromium", dockerfile)
        self.assertIn("groupadd --gid 1000 geo", dockerfile)
        self.assertIn("useradd --uid 1000 --gid 1000", dockerfile)
        self.assertIn("/app/data", dockerfile)
        self.assertIn("/app/cookiesFile", dockerfile)
        self.assertIn("/app/logs", dockerfile)
        user_directives = [
            line.strip()
            for line in dockerfile.splitlines()
            if line.strip().startswith("USER ")
        ]
        self.assertEqual("USER geo", user_directives[-1])
        self.assertLess(
            dockerfile.rfind("USER geo"),
            dockerfile.rfind('CMD ["python", "-m", "sau_web"]'),
        )
        self.assertLess(
            dockerfile.rfind("USER geo"),
            dockerfile.rfind("from patchright.sync_api import sync_playwright"),
        )
        self.assertIn(
            "COPY --from=builder /app/dist/geo-favicon.svg /app/geo-favicon.svg",
            dockerfile,
        )

    def test_production_compose_is_isolated_and_keeps_real_publishing_opt_in(self):
        compose = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
        env_example = (
            ROOT / "deploy" / "production" / "app.env.example"
        ).read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("name: geo-platform", compose)
        self.assertIn("source: ${GEO_RUNTIME_ROOT:-../runtime}/data", compose)
        self.assertIn("target: /app/data", compose)
        self.assertEqual(compose.count("create_host_path: false"), 3)
        self.assertIn('user: "${GEO_CONTAINER_USER:-0:0}"', compose)
        self.assertIn("${GEO_BIND_ADDRESS:-127.0.0.1}", compose)
        self.assertIn("GEO_NODE_IMAGE", compose)
        self.assertIn("GEO_PYTHON_IMAGE", compose)
        self.assertIn("GEO_PATCHRIGHT_CHROMIUM_SHA256", compose)
        self.assertIn("@sha256:", compose)
        self.assertIn("RUN_PUBLISH_SCHEDULER: \"false\"", compose)
        self.assertIn('command: ["sau-worker"]', compose)
        self.assertIn('test: ["CMD", "sau-worker", "--check"]', compose)
        self.assertIn("ALLOW_REAL_PUBLISHING=false", env_example)
        self.assertIn("ENABLE_BILIBILI_RUNTIME=false", env_example)
        self.assertNotIn("AI_API_KEY=sk-", env_example)
        self.assertIn("deploy/production/app.env", dockerignore)
        self.assertIn("deploy/production/app.env", gitignore)

    def test_optional_content_engine_is_private_profile_gated_and_reversible(self):
        compose = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
        web = _compose_service_block(compose, "web")
        worker = _compose_service_block(compose, "worker")
        engine = _compose_service_block(compose, "content-engine")
        root_env = (ROOT / ".env.example").read_text(encoding="utf-8")
        app_env = (
            ROOT / "deploy" / "production" / "app.env.example"
        ).read_text(encoding="utf-8")
        engine_env = (
            ROOT / "deploy" / "production" / "content-engine.env.example"
        ).read_text(encoding="utf-8")
        runbook = (
            ROOT / "docs" / "content-engine-production-runbook.md"
        ).read_text(encoding="utf-8")

        self.assertIn('profiles: ["colleague"]', engine)
        self.assertIn("COLLEAGUE_CONTENT_ENGINE_IMAGE", engine)
        self.assertIn("geo-colleague-content-engine:not-configured", engine)
        self.assertIn("COLLEAGUE_CONTENT_ENGINE_ENV_FILE", engine)
        self.assertIn('expose:\n      - "8080"', engine)
        self.assertIn("read_only: true", engine)
        self.assertIn("no-new-privileges:true", engine)
        self.assertNotIn("ports:", engine)
        self.assertNotIn("volumes:", engine)
        self.assertNotIn("app.env", engine)
        self.assertNotIn("depends_on:", web)

        for secret_name in (
            "AI_API_KEY",
            "APP_ACCESS_PASSWORD",
            "APP_SESSION_SECRET",
            "UPSTASH_REDIS_REST_TOKEN",
            "COLLEAGUE_CONTENT_ENGINE_TOKEN",
        ):
            self.assertIn(f'{secret_name}: ""', worker)
            self.assertNotIn(f"{secret_name}=", engine_env)

        for env_document in (root_env, app_env):
            self.assertIn("CONTENT_ENGINE=qwen", env_document)
            self.assertIn("CONTENT_ENGINE_FALLBACK=", env_document)
            self.assertNotIn("CONTENT_ENGINE_FALLBACK=qwen", env_document)
            self.assertIn(
                "COLLEAGUE_CONTENT_ENGINE_URL=http://content-engine:8080",
                env_document,
            )
            self.assertIn(
                "COLLEAGUE_CONTENT_ENGINE_ALLOWED_HOSTS=content-engine",
                env_document,
            )
            self.assertIn("COLLEAGUE_CONNECT_TIMEOUT_SECONDS=3", env_document)
            self.assertIn("COLLEAGUE_READ_TIMEOUT_SECONDS=120", env_document)
            self.assertIn("COLLEAGUE_MAX_RESPONSE_BYTES=2097152", env_document)

        self.assertIn("CONTENT_ENGINE=qwen", runbook)
        self.assertIn("config --quiet", runbook)
        self.assertIn("--no-deps web", runbook)
        self.assertIn("ALLOW_REAL_PUBLISHING=false", runbook)
        self.assertIn("ENABLE_BILIBILI_RUNTIME=false", runbook)
        self.assertIn("releases/latest", runbook)
        self.assertIn("COLLEAGUE_CONTENT_ENGINE_ALLOWED_HOSTS", runbook)

    def test_production_runbook_uses_actual_server_root_and_image_first_rollback(self):
        deployment = (
            ROOT / "docs" / "production-server-deployment.md"
        ).read_text(encoding="utf-8")

        self.assertIn("/srv/geo-platform/", deployment)
        self.assertNotIn("/opt/geo-platform", deployment)
        self.assertIn("rollback-<timestamp>", deployment)
        self.assertIn("保留当前数据库", deployment)

    def test_source_server_defaults_to_loopback(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(("127.0.0.1", 5409), get_server_bind())

    def test_vercel_services_keep_cloud_execution_in_explicit_demo_mode(self):
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        services = config["services"]

        self.assertEqual("3.12", (ROOT / ".python-version").read_text().strip())
        self.assertEqual("sau_frontend", services["frontend"]["root"])
        self.assertEqual(".", services["backend"]["root"])
        self.assertEqual("sau_backend:app", services["backend"]["entrypoint"])
        self.assertNotIn("runtime", services["backend"])
        self.assertEqual(
            {"service": "backend"}, config["rewrites"][0]["destination"]
        )
        self.assertEqual("/backend/:path*", config["rewrites"][0]["source"])
        self.assertNotIn("transforms", config["rewrites"][0])
        self.assertEqual("/", config["rewrites"][1]["source"])
        self.assertEqual(
            {"service": "frontend"}, config["rewrites"][1]["destination"]
        )
        self.assertEqual(
            {"service": "frontend"}, config["rewrites"][-1]["destination"]
        )
        self.assertEqual("true", config["env"]["DEMO_MODE"])
        self.assertEqual("false", config["env"]["ALLOW_REAL_PUBLISHING"])
        self.assertEqual("false", config["env"]["RUN_PUBLISH_SCHEDULER"])
        self.assertEqual("6", config["env"]["AI_RATE_LIMIT_PER_MINUTE"])
        self.assertEqual("12", config["env"]["APP_SESSION_HOURS"])
        self.assertNotIn("APP_ACCESS_PASSWORD", config["env"])
        self.assertNotIn("APP_SESSION_SECRET", config["env"])
        security_headers = {
            item["key"]: item["value"]
            for item in config["headers"][0]["headers"]
        }
        self.assertEqual("/(.*)", config["headers"][0]["source"])
        self.assertEqual("DENY", security_headers["X-Frame-Options"])
        self.assertEqual("nosniff", security_headers["X-Content-Type-Options"])
        self.assertEqual("same-origin", security_headers["Referrer-Policy"])
        self.assertTrue(config["env"]["DATABASE_PATH"].startswith("/tmp/"))
        self.assertTrue(config["env"]["COOKIES_DIRECTORY"].startswith("/tmp/"))
        self.assertTrue(config["env"]["MEDIA_ROOT"].startswith("/tmp/"))

    def test_vercel_frontend_uses_the_generated_backend_service_url(self):
        vite_config = (ROOT / "sau_frontend" / "vite.config.js").read_text(
            encoding="utf-8"
        )
        api_config = (
            ROOT / "sau_frontend" / "src" / "config" / "api.js"
        ).read_text(encoding="utf-8")

        self.assertIn("'NEXT_PUBLIC_'", vite_config)
        self.assertIn("import.meta.env.NEXT_PUBLIC_BACKEND_URL", api_config)
        self.assertIn("|| '/backend'", api_config)
        self.assertIn("'/backend':", vite_config)
        self.assertNotIn("path.replace(/^\\/api/", vite_config)

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
