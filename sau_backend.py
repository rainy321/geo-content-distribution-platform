import asyncio
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
from flask_cors import CORS
from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
)
from conf import BASE_DIR
from db.createTable import initialize_database
from services.ai_service import (
    AIConfigurationError,
    AIServiceError,
    AISettings,
    generate_geo_content,
    optimize_geo_content,
)
from services.article_service import (
    ARTICLE_STATUSES,
    ArticleNotFoundError,
    create_article,
    create_articles_bulk,
    get_article,
    list_articles,
    update_article,
)
from services.article_import_service import (
    ArticleImportError,
    build_article_import_template,
    parse_article_import,
)
from services.content_template_service import (
    BuiltinTemplateMutationError,
    ContentTemplateNotFoundError,
    create_content_template,
    delete_content_template,
    get_content_template,
    list_content_templates,
    update_content_template,
)
from services.cover_image_service import (
    ensure_article_cover,
    generate_article_cover,
    recommend_article_images,
)
from services.geo_score_service import score_geo_content
from services.dashboard_service import get_dashboard_overview
from services.demo_seed_service import seed_demo_data
from services.media_account_service import (
    MediaAccountCheckError,
    MediaAccountNotFoundError,
    MediaAccountRuntimeDisabledError,
    check_media_account,
    get_media_accounts_overview,
)
from services.platform_capability_service import (
    BILIBILI_ACCOUNT_TYPE,
    BILIBILI_PLATFORM_KEY,
    BILIBILI_RUNTIME_DISABLED_MESSAGE,
    get_platform_capability,
    list_platform_capabilities,
    normalize_platform_key,
)
from services.content_generation_run_service import (
    GenerationRunNotFoundError,
    GenerationRunStateError,
    IdempotencyConflictError,
    begin_or_replay,
    get_run,
    get_run_by_idempotency_key,
    get_run_by_request_id,
    get_run_score_context,
)
from services.content_engine_contract import (
    CONTENT_ENGINE_CONTRACT_VERSION,
    ContentEngineConfigurationError,
    ContentEngineError,
    ContentEngineProtocolError,
    ContentEngineRateLimitError,
    ContentEngineTimeoutError,
    ContentEngineUnavailableError,
    ContentGenerationRequest,
)
from services.content_generation_service import (
    ContentGenerationInProgressError,
    ContentGenerationService,
)
from services.project_service import (
    ProjectHasArticlesError,
    ProjectNotFoundError,
    create_project,
    delete_project,
    get_project,
    list_projects,
    update_project,
)
from services.publish_job_executor import execute_publish_job
from services.publish_job_service import (
    PUBLISH_JOB_STATUSES,
    InvalidPublishJobTransitionError,
    PublishJobNotFoundError,
    UnsupportedPublishPlatformError,
    create_publish_job,
    get_publish_job,
    list_publish_jobs,
    normalize_publish_images,
    normalize_publish_video,
    retry_publish_job,
)
from services.publish_scheduler_runtime import create_publish_scheduler
from services.real_publisher_factory import (
    REAL_PUBLISH_PLATFORMS,
    create_real_publisher_factory,
)
from services.request_guard import create_rate_limiter_from_environment

active_queues = {}
active_queues_lock = threading.Lock()
app = Flask(__name__)


class BackendPrefixMiddleware:
    """Strip the stable service prefix before Flask performs URL routing."""

    def __init__(self, wrapped_app, prefix="/backend"):
        self.wrapped_app = wrapped_app
        self.prefix = prefix.rstrip("/")

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == self.prefix:
            environ["SCRIPT_NAME"] = (
                environ.get("SCRIPT_NAME", "") + self.prefix
            )
            environ["PATH_INFO"] = "/"
        elif path.startswith(f"{self.prefix}/"):
            environ["SCRIPT_NAME"] = (
                environ.get("SCRIPT_NAME", "") + self.prefix
            )
            environ["PATH_INFO"] = path[len(self.prefix):]
        return self.wrapped_app(environ, start_response)


app.wsgi_app = BackendPrefixMiddleware(app.wsgi_app)


def _environment_integer(name, default, *, minimum=0, maximum=1000000):
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _environment_flag(name, default=False):
    fallback = "true" if default else "false"
    return str(os.getenv(name, fallback)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _cors_allowed_origins():
    configured = str(os.getenv("CORS_ALLOWED_ORIGINS", "")).strip()
    if configured:
        return [item.strip() for item in configured.split(",") if item.strip()]
    return ["http://127.0.0.1:5173", "http://localhost:5173"]

# Web 启动时只补齐运行目录和已有表，不删除或覆盖现有数据。
configured_database_path = os.getenv("DATABASE_PATH")
configured_cookies_directory = os.getenv("COOKIES_DIRECTORY")
configured_media_root = os.getenv("MEDIA_ROOT")
app.config["DATABASE_PATH"] = (
    Path(configured_database_path).expanduser().resolve()
    if configured_database_path
    else Path(BASE_DIR / "db" / "database.db")
)
app.config["MEDIA_ROOT"] = (
    Path(configured_media_root).expanduser().resolve()
    if configured_media_root
    else Path(BASE_DIR / "videoFile").resolve()
)
app.config["COOKIES_DIRECTORY"] = (
    Path(configured_cookies_directory).expanduser().resolve()
    if configured_cookies_directory
    else Path(BASE_DIR / "cookiesFile").resolve()
)
Path(app.config["COOKIES_DIRECTORY"]).mkdir(parents=True, exist_ok=True)
Path(app.config["MEDIA_ROOT"]).mkdir(parents=True, exist_ok=True)
app.config["DEMO_MODE"] = str(os.getenv("DEMO_MODE", "false")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
app.config["SEED_DEMO_DATA"] = str(
    os.getenv("SEED_DEMO_DATA", "true")
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
app.config["ALLOW_REAL_PUBLISHING"] = str(
    os.getenv("ALLOW_REAL_PUBLISHING", "false")
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
app.config["ENABLE_BILIBILI_RUNTIME"] = _environment_flag(
    "ENABLE_BILIBILI_RUNTIME"
)
access_password = str(os.getenv("APP_ACCESS_PASSWORD", "")).strip()
session_secret = str(os.getenv("APP_SESSION_SECRET", "")).strip()
if access_password and not session_secret:
    raise RuntimeError(
        "配置 APP_ACCESS_PASSWORD 时必须同时配置 APP_SESSION_SECRET"
    )
rate_limiter = create_rate_limiter_from_environment()
app.config.update(
    ACCESS_CONTROL_ENABLED=bool(access_password),
    ACCESS_PASSWORD=access_password,
    SECRET_KEY=session_secret or os.urandom(32),
    SESSION_COOKIE_NAME="geo_operator_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_environment_flag(
        "SESSION_COOKIE_SECURE",
        default=bool(os.getenv("VERCEL")),
    ),
    PERMANENT_SESSION_LIFETIME=timedelta(
        hours=_environment_integer(
            "APP_SESSION_HOURS",
            12,
            minimum=1,
            maximum=168,
        )
    ),
    AI_RATE_LIMIT_PER_MINUTE=_environment_integer(
        "AI_RATE_LIMIT_PER_MINUTE",
        0,
        minimum=0,
        maximum=10000,
    ),
    AUTH_LOGIN_ATTEMPTS_PER_MINUTE=_environment_integer(
        "AUTH_LOGIN_ATTEMPTS_PER_MINUTE",
        5,
        minimum=1,
        maximum=1000,
    ),
    TRUST_PROXY_HEADERS=bool(os.getenv("VERCEL")),
    STORAGE_SCOPE=("ephemeral" if os.getenv("VERCEL") else "filesystem"),
    AI_RATE_LIMITER=rate_limiter,
    AUTH_RATE_LIMITER=rate_limiter,
)
initialize_database(app.config["DATABASE_PATH"])
if app.config["DEMO_MODE"] and app.config["SEED_DEMO_DATA"]:
    app.config["DEMO_SEED_RESULT"] = seed_demo_data(app.config["DATABASE_PATH"])

# 本地分离开发可通过白名单跨域；生产前后端同源，不向任意站点开放凭据请求。
CORS(
    app,
    origins=_cors_allowed_origins(),
    supports_credentials=True,
)

# 限制上传文件大小为160MB
app.config['MAX_CONTENT_LENGTH'] = 160 * 1024 * 1024

# Docker 将前端产物复制到应用根目录；源码运行则使用 Vite 的 dist。
current_dir = Path(__file__).resolve().parent
_configured_frontend_dir = str(os.getenv("FRONTEND_BUILD_DIR", "")).strip()
_frontend_candidates = tuple(
    candidate
    for candidate in (
        Path(_configured_frontend_dir).expanduser()
        if _configured_frontend_dir
        else None,
        current_dir,
        current_dir / "sau_frontend" / "dist",
    )
    if candidate is not None
)
app.config["FRONTEND_BUILD_DIR"] = next(
    (candidate for candidate in _frontend_candidates if (candidate / "index.html").is_file()),
    current_dir,
)


def _frontend_build_dir() -> Path:
    return Path(app.config["FRONTEND_BUILD_DIR"])


def _media_root() -> Path:
    return Path(app.config["MEDIA_ROOT"]).expanduser().resolve()


def _cookies_root() -> Path:
    return Path(app.config["COOKIES_DIRECTORY"]).expanduser().resolve()


def _normalize_media_filename(value) -> str:
    filename = str(value or "").strip()
    if (
        not filename
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or ".." in filename
    ):
        raise ValueError("Invalid filename")
    return filename


_PUBLIC_ENDPOINTS = {
    "custom_static",
    "favicon",
    "frontend_history_fallback",
    "get_auth_status",
    "get_health_status",
    "index",
    "login_operator",
    "logout_operator",
    "vite_svg",
}


def _client_rate_key():
    if app.config.get("TRUST_PROXY_HEADERS"):
        forwarded = (
            request.headers.get("X-Vercel-Forwarded-For")
            or request.headers.get("X-Forwarded-For")
            or ""
        )
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"


def _operator_is_authenticated():
    if not app.config.get("ACCESS_CONTROL_ENABLED", False):
        return True
    return session.get("operator_authenticated") is True


@app.before_request
def require_operator_access():
    if request.method == "OPTIONS" or request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if _operator_is_authenticated():
        return None
    return jsonify(
        {
            "code": 401,
            "msg": "需要输入运营访问口令",
            "data": {"authentication_required": True},
        }
    ), 401


@app.after_request
def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    if request.path.startswith("/api/auth"):
        response.headers["Cache-Control"] = "no-store"
    return response

# 处理 Vite 构建后的静态资源。
@app.route('/assets/<filename>')
def custom_static(filename):
    return send_from_directory(_frontend_build_dir() / "assets", filename)

# 兼容浏览器和代理直接请求 favicon.ico，优先返回 GEO 品牌图标。
@app.route('/favicon.ico')
def favicon():
    build_dir = _frontend_build_dir()
    if (build_dir / "geo-favicon.svg").is_file():
        return send_from_directory(build_dir, "geo-favicon.svg")
    icon_dir = build_dir / "assets" if (build_dir / "assets" / "vite.svg").is_file() else build_dir
    return send_from_directory(icon_dir, "vite.svg")

@app.route('/vite.svg')
def vite_svg():
    build_dir = _frontend_build_dir()
    icon_dir = build_dir if (build_dir / "vite.svg").is_file() else build_dir / "assets"
    return send_from_directory(icon_dir, 'vite.svg')


@app.route('/api/auth/status', methods=['GET'])
def get_auth_status():
    required = bool(app.config.get("ACCESS_CONTROL_ENABLED", False))
    return jsonify(
        {
            "code": 200,
            "msg": "success",
            "data": {
                "required": required,
                "authenticated": bool(
                    not required or _operator_is_authenticated()
                ),
                "session_hours": int(
                    app.config["PERMANENT_SESSION_LIFETIME"].total_seconds()
                    // 3600
                ),
                "ai_rate_limit_enabled": bool(
                    app.config.get("AI_RATE_LIMIT_PER_MINUTE", 0) > 0
                ),
                "rate_limit_scope": getattr(
                    app.config.get("AI_RATE_LIMITER"),
                    "scope",
                    "instance",
                ),
            },
        }
    ), 200


@app.route('/api/auth/login', methods=['POST'])
def login_operator():
    if not app.config.get("ACCESS_CONTROL_ENABLED", False):
        return jsonify(
            {
                "code": 200,
                "msg": "访问控制未启用",
                "data": {"authenticated": True, "required": False},
            }
        ), 200

    decision = app.config["AUTH_RATE_LIMITER"].consume(
        f"login:{_client_rate_key()}",
        limit=app.config["AUTH_LOGIN_ATTEMPTS_PER_MINUTE"],
    )
    if not decision.allowed:
        response = jsonify(
            {
                "code": 429,
                "msg": "访问口令尝试过于频繁，请稍后再试",
                "data": None,
            }
        )
        response.headers["Retry-After"] = str(decision.retry_after_seconds)
        return response, 429

    data = request.get_json(silent=True)
    password = data.get("password") if isinstance(data, dict) else None
    candidate = str(password or "")
    expected = str(app.config.get("ACCESS_PASSWORD", ""))
    if (
        not candidate
        or len(candidate) > 512
        or not hmac.compare_digest(candidate, expected)
    ):
        return jsonify(
            {
                "code": 401,
                "msg": "访问口令不正确",
                "data": {"authenticated": False, "required": True},
            }
        ), 401

    session.clear()
    session.permanent = True
    session["operator_authenticated"] = True
    return jsonify(
        {
            "code": 200,
            "msg": "success",
            "data": {"authenticated": True, "required": True},
        }
    ), 200


@app.route('/api/auth/logout', methods=['POST'])
def logout_operator():
    session.clear()
    return jsonify(
        {
            "code": 200,
            "msg": "success",
            "data": {"authenticated": False},
        }
    ), 200


@app.route('/api/health', methods=['GET'])
def get_health_status():
    """Return a dependency-light readiness signal for local/container probes."""

    try:
        with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
            conn.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        return jsonify(
            {
                "code": 503,
                "msg": "unhealthy",
                "data": {"status": "unhealthy", "database": "unavailable"},
            }
        ), 503
    return jsonify(
        {
            "code": 200,
            "msg": "ok",
            "data": {
                "status": "ok",
                "database": "ok",
                "demo_mode": bool(app.config.get("DEMO_MODE", False)),
                "storage_scope": app.config.get(
                    "STORAGE_SCOPE",
                    "filesystem",
                ),
                "rate_limit_scope": getattr(
                    app.config.get("AI_RATE_LIMITER"),
                    "scope",
                    "instance",
                ),
                "real_publishing_enabled": bool(
                    app.config.get("ALLOW_REAL_PUBLISHING", False)
                ),
                "bilibili_runtime_enabled": bool(
                    app.config.get("ENABLE_BILIBILI_RUNTIME", False)
                ),
            },
        }
    ), 200

@app.route('/')
def index():
    return send_from_directory(_frontend_build_dir(), 'index.html')


@app.route('/<path:frontend_path>')
def frontend_history_fallback(frontend_path):
    """Serve Vite history routes without turning unknown APIs into HTML."""

    if frontend_path == "api" or frontend_path.startswith("api/"):
        return jsonify({"code": 404, "msg": "API 不存在", "data": None}), 404
    build_dir = _frontend_build_dir()
    requested_file = build_dir / frontend_path
    if requested_file.is_file():
        return send_from_directory(build_dir, frontend_path)
    return send_from_directory(build_dir, "index.html")


ARTICLE_LENGTHS = {600, 1000, 1500}
ARTICLE_CONTENT_TYPES = {
    "行业科普",
    "品牌介绍",
    "产品介绍",
    "解决方案",
    "对比文章",
    "FAQ",
    "新闻稿",
}
USER_EDITABLE_ARTICLE_STATUSES = frozenset({"draft", "ready"})


def _enforce_ai_rate_limit(*, cost=1):
    limit = int(app.config.get("AI_RATE_LIMIT_PER_MINUTE", 0))
    if limit <= 0:
        return None
    decision = app.config["AI_RATE_LIMITER"].consume(
        f"ai:{_client_rate_key()}",
        limit=limit,
        cost=cost,
    )
    if decision.allowed:
        return None
    response = jsonify(
        {
            "code": 429,
            "msg": "AI 请求过于频繁，请稍后再试",
            "data": None,
        }
    )
    response.headers["Retry-After"] = str(decision.retry_after_seconds)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = "0"
    return response, 429


@app.route('/api/articles/generate', methods=['POST'])
def generate_article():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    generation, error = _prepare_generation_request(data)
    if error is not None:
        return error

    request_id, idempotency_key, identity_error = _generation_request_identity(data)
    if identity_error is not None:
        return identity_error
    content_request = ContentGenerationRequest.create(
        project_context=generation["project"],
        topic=generation["topic"],
        keywords=generation["keywords"],
        length=generation["length"],
        content_type=generation["content_type"],
        target_platform=generation["target_platform"],
        template_instruction=generation["template_instruction"],
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    existing_run = get_run_by_idempotency_key(
        app.config["DATABASE_PATH"],
        idempotency_key,
    ) or get_run_by_request_id(app.config["DATABASE_PATH"], request_id)
    try:
        if existing_run is not None:
            outcome = begin_or_replay(
                app.config["DATABASE_PATH"],
                content_request,
                configured_engine=str(
                    existing_run.get("configured_engine") or "unknown"
                ),
            )
            article = ContentGenerationService._replay(outcome)
        else:
            rate_limit_response = _enforce_ai_rate_limit()
            if rate_limit_response is not None:
                return rate_limit_response
            service = _create_content_generation_service(generation["settings"])
            article = service.generate(content_request)
    except ContentEngineError as exc:
        return _content_generation_error_response(
            exc,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    replayed = bool((article.get("generation") or {}).get("replayed"))
    run_id = (article.get("generation") or {}).get("run_id")
    run = get_run(app.config["DATABASE_PATH"], int(run_id)) if run_id else None
    effective_request_id = str(
        (article.get("generation") or {}).get("request_id") or request_id
    )
    effective_idempotency_key = str(
        (run or {}).get("idempotency_key") or idempotency_key
    )
    response = jsonify({"code": 200, "msg": "success", "data": article})
    response.headers["X-Request-ID"] = effective_request_id
    response.headers["Idempotency-Key"] = effective_idempotency_key
    response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
    return response, 200


def _create_content_generation_service(settings=None):
    return ContentGenerationService.from_environment(
        app.config["DATABASE_PATH"],
        qwen_settings=settings,
    )


def _generation_request_identity(data):
    request_id = str(request.headers.get("X-Request-ID") or uuid.uuid4()).strip()
    if len(request_id) > 128 or not re.fullmatch(r"[A-Za-z0-9._:-]+", request_id):
        return None, None, (
            jsonify({"code": 400, "msg": "X-Request-ID 格式无效", "data": None}),
            400,
        )
    idempotency_key = str(
        request.headers.get("Idempotency-Key")
        or data.get("idempotency_key")
        or f"gen-{uuid.uuid4()}"
    ).strip()
    if len(idempotency_key) > 200 or not re.fullmatch(
        r"[A-Za-z0-9._:-]+", idempotency_key
    ):
        return None, None, (
            jsonify({"code": 400, "msg": "Idempotency-Key 格式无效", "data": None}),
            400,
        )
    return request_id, idempotency_key, None


def _content_generation_error_response(exc, *, request_id, idempotency_key=""):
    error_code = str(getattr(exc, "error_code", "engine_error") or "engine_error")
    if isinstance(exc, (IdempotencyConflictError, ContentGenerationInProgressError)) or error_code in {
        "idempotency_conflict",
        "generation_in_progress",
    }:
        status_code = 409
    elif isinstance(exc, ContentEngineRateLimitError) or error_code == "engine_rate_limited":
        status_code = 429
    elif isinstance(exc, ContentEngineTimeoutError) or error_code in {
        "engine_timeout_unknown",
        "generation_state_unknown",
        "generation_result_state_unknown",
    }:
        status_code = 504
    elif isinstance(exc, (ContentEngineConfigurationError, ContentEngineUnavailableError)) or error_code in {
        "engine_not_configured",
        "engine_unavailable",
    }:
        status_code = 503
    elif isinstance(exc, ContentEngineProtocolError) or error_code == "engine_protocol_error":
        status_code = 502
    else:
        status_code = 502
    payload = {
        "code": status_code,
        "msg": str(exc),
        "data": {
            "error_code": error_code,
            "state_unknown": bool(getattr(exc, "state_unknown", False)),
            "request_id": str(getattr(exc, "request_id", "") or request_id),
            "run_id": getattr(exc, "run_id", None),
            "trace_id": str(getattr(exc, "trace_id", "") or request_id),
            "retry_after_seconds": getattr(exc, "retry_after_seconds", None),
        },
    }
    response = jsonify(payload)
    effective_request_id = str(getattr(exc, "request_id", "") or request_id)
    response.headers["X-Request-ID"] = effective_request_id
    run_id = getattr(exc, "run_id", None)
    run = get_run(app.config["DATABASE_PATH"], int(run_id)) if run_id else None
    effective_idempotency_key = str(
        (run or {}).get("idempotency_key") or idempotency_key or ""
    )
    if effective_idempotency_key:
        response.headers["Idempotency-Key"] = effective_idempotency_key
    if effective_request_id != request_id or exc.__class__.__name__.startswith("Replayed"):
        response.headers["Idempotency-Replayed"] = "true"
    retry_after = getattr(exc, "retry_after_seconds", None)
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response, status_code


@app.route('/api/articles/generate-batch', methods=['POST'])
def generate_article_batch():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400
    topics = data.get("topics")
    if not isinstance(topics, list):
        return jsonify({"code": 400, "msg": "topics 必须是字符串数组", "data": None}), 400
    normalized_topics = _normalize_string_list(topics)
    if not normalized_topics:
        return jsonify({"code": 400, "msg": "至少提供一个文章主题", "data": None}), 400
    if len(normalized_topics) > 5:
        return jsonify({"code": 400, "msg": "一次最多批量生成 5 篇文章", "data": None}), 400

    generation, error = _prepare_generation_request(
        {**data, "topic": normalized_topics[0]}
    )
    if error is not None:
        return error

    base_request_id, base_idempotency_key, identity_error = (
        _generation_request_identity(data)
    )
    if identity_error is not None:
        return identity_error

    content_requests = []
    existing_outcomes = {}
    unseen_request_count = 0
    for index, topic in enumerate(normalized_topics, start=1):
        child_request_id = f"{base_request_id}:{index}"
        child_idempotency_key = f"{base_idempotency_key}:{index}"
        content_request = ContentGenerationRequest.create(
            project_context=generation["project"],
            topic=topic,
            keywords=generation["keywords"],
            length=generation["length"],
            content_type=generation["content_type"],
            target_platform=generation["target_platform"],
            template_instruction=generation["template_instruction"],
            request_id=child_request_id,
            idempotency_key=child_idempotency_key,
        )
        content_requests.append(content_request)
        existing_run = get_run_by_idempotency_key(
            app.config["DATABASE_PATH"],
            child_idempotency_key,
        ) or get_run_by_request_id(
            app.config["DATABASE_PATH"],
            child_request_id,
        )
        if existing_run is None:
            unseen_request_count += 1
        else:
            try:
                existing_outcomes[child_idempotency_key] = begin_or_replay(
                    app.config["DATABASE_PATH"],
                    content_request,
                    configured_engine=str(
                        existing_run.get("configured_engine") or "unknown"
                    ),
                )
            except ContentEngineError as exc:
                response, status_code = _content_generation_error_response(
                    exc,
                    request_id=child_request_id,
                    idempotency_key=child_idempotency_key,
                )
                canonical_child_key = response.headers.get("Idempotency-Key", "")
                child_suffix = f":{index}"
                if canonical_child_key.endswith(child_suffix):
                    response.headers["Idempotency-Key"] = canonical_child_key[
                        :-len(child_suffix)
                    ]
                return response, status_code

    if unseen_request_count:
        rate_limit_response = _enforce_ai_rate_limit(cost=unseen_request_count)
        if rate_limit_response is not None:
            return rate_limit_response

    items = []
    stopped = False
    service = None
    if unseen_request_count:
        try:
            service = _create_content_generation_service(generation["settings"])
        except ContentEngineError as exc:
            return _content_generation_error_response(
                exc,
                request_id=base_request_id,
                idempotency_key=base_idempotency_key,
            )
    for content_request in content_requests:
        topic = content_request.topic
        if stopped:
            items.append(
                {
                    "topic": topic,
                    "status": "skipped",
                    "message": "前一个模型请求失败，未继续消耗额度",
                }
            )
            continue
        try:
            outcome = existing_outcomes.get(content_request.idempotency_key)
            if outcome is not None:
                current_run = get_run(app.config["DATABASE_PATH"], outcome.run_id)
                if current_run is not None and current_run.get("article_id") is not None:
                    article = get_article(
                        app.config["DATABASE_PATH"],
                        int(current_run["article_id"]),
                    )
                    items.append(
                        {
                            "topic": topic,
                            "status": "created",
                            "replayed": True,
                            "article": article,
                        }
                    )
                    continue
                draft = ContentGenerationService._replay(outcome)
            else:
                draft = service.generate(content_request)
            run_id = int((draft.get("generation") or {})["run_id"])
            article = create_article(
                app.config["DATABASE_PATH"],
                project_id=int(data["project_id"]),
                title=draft["title"],
                summary=draft["summary"],
                content=draft["content"],
                tags=draft["tags"],
                status="draft",
                generation_run_id=run_id,
            )
            items.append(
                {
                    "topic": topic,
                    "status": "created",
                    "replayed": bool((draft.get("generation") or {}).get("replayed")),
                    "article": article,
                }
            )
        except ContentEngineError as exc:
            items.append(
                {
                    "topic": topic,
                    "status": "failed",
                    "message": str(exc),
                    "error_code": str(
                        getattr(exc, "error_code", "engine_error") or "engine_error"
                    ),
                    "state_unknown": bool(getattr(exc, "state_unknown", False)),
                    "request_id": str(
                        getattr(exc, "request_id", "") or content_request.request_id
                    ),
                    "run_id": getattr(exc, "run_id", None),
                }
            )
            stopped = True

    created_count = sum(item["status"] == "created" for item in items)
    replayed_count = sum(
        item["status"] == "created" and item.get("replayed") for item in items
    )
    response = jsonify(
        {
            "code": 200,
            "msg": f"批量任务完成，已保存 {created_count} 篇草稿",
            "data": {
                "items": items,
                "created_count": created_count,
                "failed_count": len(items) - created_count,
                "replayed_count": replayed_count,
            },
        }
    )
    response.headers["X-Request-ID"] = base_request_id
    response.headers["Idempotency-Key"] = base_idempotency_key
    response.headers["Idempotency-Replayed"] = (
        "true" if replayed_count == created_count and created_count else "false"
    )
    return response, 200


def _prepare_generation_request(data):
    try:
        project_id = int(data.get("project_id"))
    except (TypeError, ValueError):
        return None, (jsonify({"code": 400, "msg": "project_id 必须是整数", "data": None}), 400)
    if project_id <= 0:
        return None, (jsonify({"code": 400, "msg": "project_id 必须是正整数", "data": None}), 400)
    topic = str(data.get("topic") or "").strip()
    if not topic:
        return None, (jsonify({"code": 400, "msg": "文章主题不能为空", "data": None}), 400)
    try:
        length = int(data.get("length", 1000))
    except (TypeError, ValueError):
        return None, (jsonify({"code": 400, "msg": "文章长度必须是整数", "data": None}), 400)
    if length not in ARTICLE_LENGTHS:
        return None, (jsonify({"code": 400, "msg": "文章长度仅支持 600、1000、1500", "data": None}), 400)
    content_type = str(data.get("content_type") or "行业科普").strip()
    if content_type not in ARTICLE_CONTENT_TYPES:
        return None, (jsonify({"code": 400, "msg": "不支持的内容类型", "data": None}), 400)
    project = _get_project(project_id)
    if project is None:
        return None, (jsonify({"code": 404, "msg": "品牌项目不存在", "data": None}), 404)
    requested_keywords = data.get("keywords", project["keywords"])
    if not isinstance(requested_keywords, list):
        return None, (jsonify({"code": 400, "msg": "keywords 必须是字符串数组", "data": None}), 400)
    template_instruction = ""
    if data.get("template_id") not in (None, ""):
        try:
            template_id = int(data["template_id"])
            if template_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return None, (jsonify({"code": 400, "msg": "template_id 必须是正整数", "data": None}), 400)
        try:
            template_instruction = get_content_template(
                app.config["DATABASE_PATH"], template_id
            )["instruction"]
        except ContentTemplateNotFoundError as exc:
            return None, (jsonify({"code": 404, "msg": str(exc), "data": None}), 404)
    try:
        ai_settings = _request_ai_settings(data)
    except AIConfigurationError as exc:
        return None, (jsonify({"code": 400, "msg": str(exc), "data": None}), 400)
    target_platform_raw = str(
        data.get("target_platform") or data.get("targetPlatform") or ""
    ).strip()
    target_platform = normalize_platform_key(target_platform_raw)
    if target_platform_raw and not target_platform:
        return None, (
            jsonify({"code": 400, "msg": "不支持的目标平台", "data": None}),
            400,
        )
    return {
        "project": project,
        "topic": topic,
        "keywords": _normalize_string_list(requested_keywords),
        "length": length,
        "content_type": content_type,
        "target_platform": target_platform,
        "template_instruction": template_instruction,
        "settings": ai_settings,
    }, None


@app.route('/api/content-templates', methods=['GET'])
def get_content_template_list():
    return jsonify(
        {
            "code": 200,
            "msg": "success",
            "data": list_content_templates(app.config["DATABASE_PATH"]),
        }
    ), 200


@app.route('/api/content-templates', methods=['POST'])
def save_content_template():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400
    values, error = _parse_content_template_fields(data, partial=False)
    if error:
        return jsonify({"code": 400, "msg": error, "data": None}), 400
    try:
        template = create_content_template(app.config["DATABASE_PATH"], **values)
    except ValueError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
    return jsonify({"code": 201, "msg": "内容模板已创建", "data": template}), 201


@app.route('/api/content-templates/<int:template_id>', methods=['PUT'])
def edit_content_template(template_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400
    values, error = _parse_content_template_fields(data, partial=True)
    if error:
        return jsonify({"code": 400, "msg": error, "data": None}), 400
    if not values:
        return jsonify({"code": 400, "msg": "没有可更新的模板字段", "data": None}), 400
    try:
        template = update_content_template(
            app.config["DATABASE_PATH"], template_id, values
        )
    except ContentTemplateNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except BuiltinTemplateMutationError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
    except ValueError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
    return jsonify({"code": 200, "msg": "内容模板已更新", "data": template}), 200


@app.route('/api/content-templates/<int:template_id>', methods=['DELETE'])
def remove_content_template(template_id):
    try:
        delete_content_template(app.config["DATABASE_PATH"], template_id)
    except ContentTemplateNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except BuiltinTemplateMutationError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
    return jsonify({"code": 200, "msg": "内容模板已删除", "data": None}), 200


def _parse_content_template_fields(data, *, partial):
    limits = {"name": 80, "description": 300, "instruction": 2000}
    result = {}
    for field, maximum in limits.items():
        if partial and field not in data:
            continue
        value = data.get(field, "")
        if not isinstance(value, str):
            return None, f"{field} 必须是字符串"
        value = value.strip()
        if field in {"name", "instruction"} and not value:
            return None, "模板名称和写作要求不能为空"
        if len(value) > maximum:
            return None, f"{field} 不能超过 {maximum} 字"
        result[field] = value
    if not partial or "content_type" in data:
        content_type = str(data.get("content_type") or "行业科普").strip()
        if content_type not in ARTICLE_CONTENT_TYPES:
            return None, "不支持的内容类型"
        result["content_type"] = content_type
    return result, None


@app.route('/api/ai/config-status', methods=['GET'])
def get_ai_config_status():
    return jsonify(
        {
            "code": 200,
            "msg": "success",
            "data": {
                "server_configured": AISettings.environment_is_configured(),
                "custom_config_supported": True,
                "custom_key_storage": "browser_session",
            },
        }
    ), 200


@app.route('/api/content-engine/status', methods=['GET'])
def get_content_engine_status():
    """Expose deployment-managed engine readiness without configuration secrets."""

    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        engine_health = _create_content_generation_service().health()
    except ContentEngineError as exc:
        return jsonify(
            {
                "code": 200,
                "msg": "success",
                "data": {
                    "contract_version": CONTENT_ENGINE_CONTRACT_VERSION,
                    "status": "unavailable",
                    "ready": False,
                    "primary": {
                        "engine": str(os.getenv("CONTENT_ENGINE", "qwen")).strip().lower(),
                        "version": "unknown",
                        "ready": False,
                        "status": "not_configured",
                    },
                    "fallback": None,
                    "last_checked_at": checked_at,
                    "message": str(exc),
                },
            }
        ), 200
    return jsonify(
        {
            "code": 200,
            "msg": "success",
            "data": {
                "contract_version": CONTENT_ENGINE_CONTRACT_VERSION,
                **engine_health,
                "last_checked_at": checked_at,
                "managed_by": "deployment_environment",
            },
        }
    ), 200


@app.route('/api/content-generation/runs/<int:run_id>', methods=['GET'])
def get_content_generation_run(run_id):
    run = get_run(app.config["DATABASE_PATH"], run_id)
    if run is None:
        return jsonify({"code": 404, "msg": "生成记录不存在", "data": None}), 404
    public_fields = {
        key: run.get(key)
        for key in (
            "id",
            "request_id",
            "status",
            "configured_engine",
            "actual_engine",
            "engine_version",
            "trace_id",
            "elapsed_ms",
            "usage",
            "usage_status",
            "provider_geo_score",
            "fallback_used",
            "fallback_from",
            "warnings",
            "local_score",
            "local_analysis",
            "error_code",
            "error_message",
            "retry_after_seconds",
            "article_id",
            "created_at",
            "updated_at",
        )
    }
    result_payload = run.get("result_payload") or {}
    sources = result_payload.get("sources")
    public_fields["sources"] = sources if isinstance(sources, list) else []
    project_snapshot = run.get("project_snapshot") or {}
    brief_snapshot = run.get("brief_snapshot") or {}
    keywords = brief_snapshot.get("keywords")
    if not isinstance(keywords, list):
        keywords = project_snapshot.get("keywords")
    public_fields["score_context"] = {
        "brand": str(project_snapshot.get("name") or "").strip(),
        "keywords": _normalize_string_list(keywords if isinstance(keywords, list) else []),
    }
    return jsonify({"code": 200, "msg": "success", "data": public_fields}), 200


@app.route('/api/dashboard', methods=['GET'])
def get_dashboard_data():
    overview = get_dashboard_overview(app.config["DATABASE_PATH"])
    return jsonify({"code": 200, "msg": "success", "data": overview}), 200


@app.route('/api/media-accounts', methods=['GET'])
def get_media_accounts():
    overview = get_media_accounts_overview(
        app.config["DATABASE_PATH"],
        cookies_directory=app.config["COOKIES_DIRECTORY"],
    )
    return jsonify({"code": 200, "msg": "success", "data": overview}), 200


@app.route('/api/media-accounts/<int:account_id>/check', methods=['POST'])
async def check_media_account_status(account_id):
    # The checker loads Playwright, so keep it out of the serverless cold-start
    # path. Account/browser operations are only supported by the local worker.
    from myUtils.auth import check_cookie

    try:
        account = await check_media_account(
            app.config["DATABASE_PATH"],
            account_id,
            cookies_directory=app.config["COOKIES_DIRECTORY"],
            enable_bilibili_runtime=bool(
                app.config.get("ENABLE_BILIBILI_RUNTIME", False)
            ),
            checker=lambda account_type, file_path: check_cookie(
                account_type,
                file_path,
                cookies_directory=app.config["COOKIES_DIRECTORY"],
            ),
        )
    except MediaAccountNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except MediaAccountRuntimeDisabledError as exc:
        return jsonify({"code": 403, "msg": str(exc), "data": None}), 403
    except MediaAccountCheckError as exc:
        return jsonify({"code": 502, "msg": str(exc), "data": None}), 502
    return jsonify({"code": 200, "msg": "success", "data": account}), 200


def _get_project(project_id):
    with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    if row is None:
        return None
    project = dict(row)
    project["keywords"] = _decode_json_list(project.get("keywords"))
    project["competitors"] = _decode_json_list(project.get("competitors"))
    return project


def _decode_json_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return _normalize_string_list(parsed) if isinstance(parsed, list) else []


def _normalize_string_list(values):
    result = []
    for value in values:
        item = str(value).strip()
        if item and item not in result:
            result.append(item)
    return result


@app.route('/api/geo/score', methods=['POST'])
def calculate_geo_score():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    title = str(data.get("title") or "").strip()
    content = str(data.get("content") or "").strip()
    brand = str(data.get("brand") or "").strip()
    keywords = data.get("keywords", [])
    generation_run_id = data.get("generation_run_id")

    if generation_run_id not in (None, ""):
        try:
            generation_run_id = int(generation_run_id)
            if generation_run_id <= 0:
                raise ValueError
            score_context = get_run_score_context(
                app.config["DATABASE_PATH"],
                generation_run_id,
            )
        except (TypeError, ValueError):
            return jsonify({"code": 400, "msg": "generation_run_id 必须是正整数", "data": None}), 400
        except GenerationRunNotFoundError as exc:
            return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
        except GenerationRunStateError as exc:
            return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
        brand = score_context["brand"]
        keywords = score_context["keywords"]

    if not title:
        return jsonify({"code": 400, "msg": "文章标题不能为空", "data": None}), 400
    if not content:
        return jsonify({"code": 400, "msg": "文章正文不能为空", "data": None}), 400
    if not brand:
        return jsonify({"code": 400, "msg": "品牌名称不能为空", "data": None}), 400
    if not isinstance(keywords, list):
        return jsonify({"code": 400, "msg": "keywords 必须是字符串数组", "data": None}), 400

    result = score_geo_content(
        title=title,
        content=content,
        brand=brand,
        keywords=_normalize_string_list(keywords),
    )
    if generation_run_id not in (None, ""):
        result["score_context"] = {
            "generation_run_id": generation_run_id,
            "brand": brand,
            "keywords": keywords,
        }
    return jsonify(result), 200


@app.route('/api/articles', methods=['POST'])
def save_article():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    try:
        project_id = int(data.get("project_id"))
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "project_id 必须是整数", "data": None}), 400
    if project_id <= 0:
        return jsonify({"code": 400, "msg": "project_id 必须是正整数", "data": None}), 400

    article_data, validation_error = _parse_article_fields(data, partial=False)
    if validation_error:
        return jsonify({"code": 400, "msg": validation_error, "data": None}), 400

    generation_run_id = None
    if data.get("generation_run_id") not in (None, ""):
        try:
            generation_run_id = int(data["generation_run_id"])
        except (TypeError, ValueError):
            return jsonify({"code": 400, "msg": "generation_run_id 必须是整数", "data": None}), 400
        if generation_run_id <= 0:
            return jsonify({"code": 400, "msg": "generation_run_id 必须是正整数", "data": None}), 400

    try:
        article = create_article(
            app.config["DATABASE_PATH"],
            project_id=project_id,
            generation_run_id=generation_run_id,
            **article_data,
        )
    except ProjectNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except GenerationRunNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except GenerationRunStateError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409

    return jsonify({"code": 201, "msg": "文章已保存", "data": article}), 201


@app.route('/api/articles', methods=['GET'])
def get_article_list():
    page, error = _parse_positive_query_integer("page", default=1)
    if error:
        return jsonify({"code": 400, "msg": error, "data": None}), 400
    page_size, error = _parse_positive_query_integer("page_size", default=20)
    if error:
        return jsonify({"code": 400, "msg": error, "data": None}), 400
    if page_size > 100:
        return jsonify({"code": 400, "msg": "page_size 不能超过 100", "data": None}), 400

    project_id = None
    if "project_id" in request.args:
        project_id, error = _parse_positive_query_integer("project_id")
        if error:
            return jsonify({"code": 400, "msg": error, "data": None}), 400

    status = request.args.get("status")
    if status is not None and status not in ARTICLE_STATUSES:
        return jsonify({"code": 400, "msg": "不支持的文章状态", "data": None}), 400

    result = list_articles(
        app.config["DATABASE_PATH"],
        project_id=project_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return jsonify({"code": 200, "msg": "success", "data": result}), 200


@app.route('/api/articles/import-template.xlsx', methods=['GET'])
def download_article_import_template():
    return send_file(
        build_article_import_template(),
        as_attachment=True,
        download_name="GEO-文章导入模板.xlsx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )


@app.route('/api/articles/import', methods=['POST'])
def import_articles():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"code": 400, "msg": "请选择 Excel 或 CSV 文件", "data": None}), 400
    try:
        project_id = int(request.form.get("project_id"))
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "project_id 必须是整数", "data": None}), 400
    if project_id <= 0:
        return jsonify({"code": 400, "msg": "project_id 必须是正整数", "data": None}), 400
    try:
        records = parse_article_import(upload.stream, filename=upload.filename)
        articles = create_articles_bulk(
            app.config["DATABASE_PATH"],
            project_id=project_id,
            articles=records,
        )
    except ArticleImportError as exc:
        return jsonify(
            {
                "code": 400,
                "msg": str(exc),
                "data": {"row_errors": exc.rows},
            }
        ), 400
    except ProjectNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    return jsonify(
        {
            "code": 201,
            "msg": f"已导入 {len(articles)} 篇文章",
            "data": {"items": articles, "created_count": len(articles)},
        }
    ), 201


@app.route('/api/articles/<int:article_id>', methods=['GET'])
def get_article_detail(article_id):
    try:
        article = get_article(app.config["DATABASE_PATH"], article_id)
    except ArticleNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    return jsonify({"code": 200, "msg": "success", "data": article}), 200


@app.route('/api/articles/<int:article_id>/images/recommend', methods=['GET'])
def get_article_image_recommendations(article_id):
    try:
        article = get_article(app.config["DATABASE_PATH"], article_id)
    except ArticleNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    project = _get_project(article["project_id"])
    if project is None:
        return jsonify({"code": 409, "msg": "文章关联的品牌项目不存在", "data": None}), 409
    try:
        limit = int(request.args.get("limit", 3))
        items = recommend_article_images(
            app.config["DATABASE_PATH"],
            media_root=app.config["MEDIA_ROOT"],
            article=article,
            project=project,
            limit=limit,
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    return jsonify({"code": 200, "msg": "success", "data": {"items": items}}), 200


@app.route('/api/articles/<int:article_id>/images/generate', methods=['POST'])
def create_article_cover_image(article_id):
    try:
        article = get_article(app.config["DATABASE_PATH"], article_id)
    except ArticleNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    project = _get_project(article["project_id"])
    if project is None:
        return jsonify({"code": 409, "msg": "文章关联的品牌项目不存在", "data": None}), 409
    record = generate_article_cover(
        app.config["DATABASE_PATH"],
        media_root=app.config["MEDIA_ROOT"],
        article=article,
        project=project,
    )
    return jsonify({"code": 201, "msg": "文章封面已生成", "data": record}), 201


@app.route('/api/articles/<int:article_id>', methods=['PUT'])
def edit_article(article_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400
    if "project_id" in data:
        return jsonify({"code": 400, "msg": "project_id 不支持修改", "data": None}), 400

    article_data, validation_error = _parse_article_fields(data, partial=True)
    if validation_error:
        return jsonify({"code": 400, "msg": validation_error, "data": None}), 400
    if not article_data:
        return jsonify({"code": 400, "msg": "没有可更新的文章字段", "data": None}), 400

    try:
        article = update_article(
            app.config["DATABASE_PATH"],
            article_id,
            article_data,
        )
    except ArticleNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except ProjectNotFoundError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409

    return jsonify({"code": 200, "msg": "文章已更新", "data": article}), 200


@app.route('/api/articles/<int:article_id>/optimize', methods=['POST'])
def optimize_article(article_id):
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据必须是对象", "data": None}), 400

    try:
        article = get_article(app.config["DATABASE_PATH"], article_id)
    except ArticleNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404

    project = _get_project(article["project_id"])
    if project is None:
        return jsonify({"code": 409, "msg": "文章关联的品牌项目不存在", "data": None}), 409

    score_brand = project["name"]
    score_keywords = project["keywords"]
    if article.get("generation_run_id") is not None:
        try:
            score_context = get_run_score_context(
                app.config["DATABASE_PATH"],
                article["generation_run_id"],
            )
        except (GenerationRunNotFoundError, GenerationRunStateError) as exc:
            return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
        score_brand = score_context["brand"]
        score_keywords = score_context["keywords"]

    before_score = score_geo_content(
        title=article["title"],
        content=article["content"],
        brand=score_brand,
        keywords=score_keywords,
    )
    try:
        ai_settings = _request_ai_settings(data)
    except AIConfigurationError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    rate_limit_response = _enforce_ai_rate_limit()
    if rate_limit_response is not None:
        return rate_limit_response
    try:
        optimized = optimize_geo_content(
            article=article,
            score=before_score,
            settings=ai_settings,
        )
    except AIConfigurationError as exc:
        return jsonify({"code": 503, "msg": str(exc), "data": None}), 503
    except AIServiceError as exc:
        return jsonify({"code": 502, "msg": str(exc), "data": None}), 502

    after_score = score_geo_content(
        title=optimized["title"],
        content=optimized["content"],
        brand=score_brand,
        keywords=score_keywords,
    )
    return jsonify(
        {
            "code": 200,
            "msg": "优化稿已生成，确认后再保存",
            "data": {
                "optimized": optimized,
                "before": before_score,
                "after": after_score,
            },
        }
    ), 200


def _request_ai_settings(data):
    if "ai_config" not in data or data["ai_config"] is None:
        return None
    return AISettings.from_mapping(data["ai_config"])


def _parse_article_fields(data, *, partial):
    result = {}
    defaults = {
        "title": "",
        "summary": "",
        "content": "",
        "tags": [],
        "status": "draft",
    }
    for field in ("title", "summary", "content"):
        if partial and field not in data:
            continue
        value = data.get(field, defaults[field])
        if value is None and field == "summary":
            value = ""
        if not isinstance(value, str):
            return None, f"{field} 必须是字符串"
        value = value.strip()
        if field in {"title", "content"} and not value:
            message = "文章标题不能为空" if field == "title" else "文章正文不能为空"
            return None, message
        result[field] = value

    if not partial or "tags" in data:
        tags = data.get("tags", defaults["tags"])
        if not isinstance(tags, list):
            return None, "tags 必须是字符串数组"
        result["tags"] = _normalize_string_list(tags)

    if not partial or "status" in data:
        status = data.get("status", defaults["status"])
        if not isinstance(status, str) or status not in USER_EDITABLE_ARTICLE_STATUSES:
            return None, "文章只允许保存为草稿或待发布状态"
        result["status"] = status

    return result, None


def _parse_positive_query_integer(name, *, default=None):
    raw_value = request.args.get(name)
    if raw_value is None and default is not None:
        return default, None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None, f"{name} 必须是整数"
    if value <= 0:
        return None, f"{name} 必须是正整数"
    return value, None


@app.route('/api/publish', methods=['POST'])
def create_publish_task():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    try:
        article_id = int(data.get("article_id"))
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "article_id 必须是整数", "data": None}), 400
    if article_id <= 0:
        return jsonify({"code": 400, "msg": "article_id 必须是正整数", "data": None}), 400

    platform = str(data.get("platform") or "").strip().lower()
    if not platform:
        return jsonify({"code": 400, "msg": "platform 不能为空", "data": None}), 400

    try:
        images = _validate_publish_images(data.get("images"))
        video = _validate_publish_video(data.get("video"))
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400

    capability = get_platform_capability(platform)
    if capability is None:
        return jsonify({"code": 400, "msg": "暂不支持该发布平台", "data": None}), 400

    auto_image = data.get("auto_image", False)
    if not isinstance(auto_image, bool):
        return jsonify({"code": 400, "msg": "auto_image 必须是布尔值", "data": None}), 400
    if auto_image and not images and platform in {
        "baijiahao",
        "xiaohongshu",
        "douyin",
        "kuaishou",
    }:
        try:
            article = get_article(app.config["DATABASE_PATH"], article_id)
        except ArticleNotFoundError as exc:
            return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
        project = _get_project(article["project_id"])
        if project is None:
            return jsonify({"code": 409, "msg": "文章关联的品牌项目不存在", "data": None}), 409
        record, _generated = ensure_article_cover(
            app.config["DATABASE_PATH"],
            media_root=app.config["MEDIA_ROOT"],
            article=article,
            project=project,
        )
        images = [record["file_path"]]

    if (
        capability["requires_images"]
        and not app.config.get("DEMO_MODE", False)
        and not images
    ):
        return jsonify(
            {
                "code": 400,
                "msg": f"{platform} 图文发布必须提供至少一张素材图片",
                "data": None,
            }
        ), 400

    if (
        capability["requires_video"]
        and not app.config.get("DEMO_MODE", False)
        and not video
    ):
        return jsonify(
            {
                "code": 400,
                "msg": f"{capability['name']} 发布必须提供一段视频素材",
                "data": None,
            }
        ), 400

    auto_execute = data.get("auto_execute", False)
    if not isinstance(auto_execute, bool):
        return jsonify(
            {"code": 400, "msg": "auto_execute 必须是布尔值", "data": None}
        ), 400
    if auto_execute and not data.get("publish_at"):
        return jsonify(
            {
                "code": 400,
                "msg": "auto_execute 仅适用于定时发布任务",
                "data": None,
            }
        ), 400
    if (
        auto_execute
        and not app.config.get("DEMO_MODE", False)
        and not app.config.get("ALLOW_REAL_PUBLISHING", False)
    ):
        return jsonify(
            {
                "code": 403,
                "msg": "定时真实自动执行需要先开启 ALLOW_REAL_PUBLISHING",
                "data": None,
            }
        ), 403
    if (
        auto_execute
        and platform == BILIBILI_PLATFORM_KEY
        and not app.config.get("ENABLE_BILIBILI_RUNTIME", False)
    ):
        return jsonify(
            {
                "code": 403,
                "msg": BILIBILI_RUNTIME_DISABLED_MESSAGE,
                "data": None,
            }
        ), 403

    try:
        job = create_publish_job(
            app.config["DATABASE_PATH"],
            article_id=article_id,
            platform=platform,
            images=images,
            video=video,
            publish_at=data.get("publish_at"),
            auto_execute=auto_execute,
            demo=bool(app.config.get("DEMO_MODE", False)),
        )
    except ArticleNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except (UnsupportedPublishPlatformError, ValueError) as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400

    return jsonify(
        {"code": 201, "msg": "发布任务已创建", "data": _publish_job_payload(job)}
    ), 201


@app.route('/api/publish/jobs', methods=['GET'])
def get_publish_task_list():
    page, error = _parse_positive_query_integer("page", default=1)
    if error:
        return jsonify({"code": 400, "msg": error, "data": None}), 400
    page_size, error = _parse_positive_query_integer("page_size", default=20)
    if error:
        return jsonify({"code": 400, "msg": error, "data": None}), 400
    if page_size > 100:
        return jsonify({"code": 400, "msg": "page_size 不能超过 100", "data": None}), 400

    article_id = None
    if "article_id" in request.args:
        article_id, error = _parse_positive_query_integer("article_id")
        if error:
            return jsonify({"code": 400, "msg": error, "data": None}), 400

    status = request.args.get("status")
    if status is not None and status not in PUBLISH_JOB_STATUSES:
        return jsonify({"code": 400, "msg": "不支持的发布任务状态", "data": None}), 400

    try:
        result = list_publish_jobs(
            app.config["DATABASE_PATH"],
            article_id=article_id,
            platform=request.args.get("platform"),
            status=status,
            page=page,
            page_size=page_size,
        )
    except UnsupportedPublishPlatformError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400

    result["items"] = [_publish_job_payload(job) for job in result["items"]]
    return jsonify({"code": 200, "msg": "success", "data": result}), 200


@app.route('/api/publish/jobs/<int:job_id>', methods=['GET'])
def get_publish_task_detail(job_id):
    try:
        job = get_publish_job(app.config["DATABASE_PATH"], job_id)
    except PublishJobNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    return jsonify(
        {"code": 200, "msg": "success", "data": _publish_job_payload(job)}
    ), 200


@app.route('/api/publish/jobs/<int:job_id>/retry', methods=['POST'])
def retry_publish_task(job_id):
    try:
        job = retry_publish_job(app.config["DATABASE_PATH"], job_id)
    except PublishJobNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except InvalidPublishJobTransitionError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
    return jsonify(
        {"code": 200, "msg": "发布任务已重新排队", "data": _publish_job_payload(job)}
    ), 200


@app.route('/api/publish/jobs/<int:job_id>/execute', methods=['POST'])
def execute_demo_publish_task(job_id):
    try:
        current = get_publish_job(app.config["DATABASE_PATH"], job_id)
    except PublishJobNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    if not current["demo"]:
        return jsonify(
            {
                "code": 409,
                "msg": "真实任务不能通过演示执行入口执行；请使用真实发布确认入口",
                "data": None,
            }
        ), 409

    try:
        job = execute_publish_job(
            app.config["DATABASE_PATH"],
            job_id,
            media_root=app.config["MEDIA_ROOT"],
        )
    except InvalidPublishJobTransitionError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
    return jsonify(
        {"code": 200, "msg": "演示发布任务执行完成", "data": _publish_job_payload(job)}
    ), 200


@app.route('/api/publish/jobs/<int:job_id>/execute-real', methods=['POST'])
def execute_real_publish_task(job_id):
    try:
        current = get_publish_job(app.config["DATABASE_PATH"], job_id)
    except PublishJobNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404

    if current["demo"]:
        return jsonify(
            {"code": 409, "msg": "演示任务不能通过真实发布入口执行", "data": None}
        ), 409
    if app.config.get("DEMO_MODE", False):
        return jsonify(
            {"code": 403, "msg": "Demo Mode 已开启，真实发布被禁用", "data": None}
        ), 403
    if not app.config.get("ALLOW_REAL_PUBLISHING", False):
        return jsonify(
            {
                "code": 403,
                "msg": "真实发布总开关未开启，请设置 ALLOW_REAL_PUBLISHING=true",
                "data": None,
            }
        ), 403
    if (
        current["platform"] == BILIBILI_PLATFORM_KEY
        and not app.config.get("ENABLE_BILIBILI_RUNTIME", False)
    ):
        return jsonify(
            {
                "code": 403,
                "msg": BILIBILI_RUNTIME_DISABLED_MESSAGE,
                "data": None,
            }
        ), 403
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return jsonify(
            {"code": 400, "msg": "真实发布需要明确确认", "data": None}
        ), 400
    if current["platform"] not in REAL_PUBLISH_PLATFORMS:
        return jsonify(
            {
                "code": 409,
                "msg": f"{current['platform']} 尚未接入真实文章发布器",
                "data": None,
            }
        ), 409

    publisher_factory = create_real_publisher_factory(
        app.config["DATABASE_PATH"],
        cookies_directory=app.config["COOKIES_DIRECTORY"],
        enable_bilibili_runtime=bool(
            app.config.get("ENABLE_BILIBILI_RUNTIME", False)
        ),
    )
    try:
        job = execute_publish_job(
            app.config["DATABASE_PATH"],
            job_id,
            publisher_factory=publisher_factory,
            media_root=app.config["MEDIA_ROOT"],
        )
    except InvalidPublishJobTransitionError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
    return jsonify(
        {
            "code": 200,
            "msg": (
                f"{current['platform']} 真实发布流程已执行，"
                "请以任务状态和平台结果为准"
            ),
            "data": _publish_job_payload(job),
        }
    ), 200


def _publish_job_payload(job):
    payload = {
        "job_id": job["id"],
        "article_id": job["article_id"],
        "platform": job["platform"],
        "status": job["status"],
        "message": job["message"],
        "url": job["result_url"],
        "images": list(job.get("images") or ()),
        "video": str(job.get("video") or ""),
        "publish_at": job["publish_at"],
        "auto_execute": bool(job.get("auto_execute", False)),
        "authorization_bound": bool(job.get("authorization_bound", False)),
        "demo": bool(job["demo"]),
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "can_execute_real": bool(
            not job["demo"]
            and not app.config.get("DEMO_MODE", False)
            and app.config.get("ALLOW_REAL_PUBLISHING", False)
            and job["platform"] in REAL_PUBLISH_PLATFORMS
            and (
                job["platform"] != BILIBILI_PLATFORM_KEY
                or app.config.get("ENABLE_BILIBILI_RUNTIME", False)
            )
            and job["status"] == "queued"
        ),
    }
    if "article_title" in job:
        payload["article_title"] = job["article_title"]
    return payload


def _validate_publish_images(value):
    images = normalize_publish_images(value)
    media_root = Path(app.config["MEDIA_ROOT"]).expanduser().resolve()
    allowed_extensions = {".jpg", ".jpeg", ".png"}
    for filename in images:
        if Path(filename).suffix.lower() not in allowed_extensions:
            raise ValueError("发布图片仅支持 JPG、JPEG、PNG 格式")
        image_path = (media_root / filename).resolve()
        try:
            image_path.relative_to(media_root)
        except ValueError as exc:
            raise ValueError("发布图片必须来自素材库") from exc
        if not image_path.is_file():
            raise ValueError(f"素材图片不存在：{filename}")
    return images


def _validate_publish_video(value):
    video = normalize_publish_video(value)
    if not video:
        return ""
    if Path(video).suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
        raise ValueError("发布视频仅支持 MP4、MOV、MKV、WEBM 格式")
    media_root = Path(app.config["MEDIA_ROOT"]).expanduser().resolve()
    video_path = (media_root / video).resolve()
    try:
        video_path.relative_to(media_root)
    except ValueError as exc:
        raise ValueError("发布视频必须来自素材库") from exc
    if not video_path.is_file():
        raise ValueError(f"素材视频不存在：{video}")
    return video


@app.route('/api/publish/platforms', methods=['GET'])
def get_publish_platforms():
    return jsonify(
        {"code": 200, "msg": "success", "data": list_platform_capabilities()}
    ), 200


@app.route('/api/projects', methods=['POST'])
def save_project():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    project_data, validation_error = _parse_project_fields(data, partial=False)
    if validation_error:
        return jsonify({"code": 400, "msg": validation_error, "data": None}), 400
    project = create_project(app.config["DATABASE_PATH"], **project_data)
    return jsonify({"code": 201, "msg": "项目已创建", "data": project}), 201


@app.route('/api/projects', methods=['GET'])
def get_project_list():
    projects = list_projects(app.config["DATABASE_PATH"])
    return jsonify({"code": 200, "msg": "success", "data": projects}), 200


@app.route('/api/projects/<int:project_id>', methods=['GET'])
def get_project_detail(project_id):
    try:
        project = get_project(app.config["DATABASE_PATH"], project_id)
    except ProjectNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    return jsonify({"code": 200, "msg": "success", "data": project}), 200


@app.route('/api/projects/<int:project_id>', methods=['PUT'])
def edit_project(project_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    project_data, validation_error = _parse_project_fields(data, partial=True)
    if validation_error:
        return jsonify({"code": 400, "msg": validation_error, "data": None}), 400
    if not project_data:
        return jsonify({"code": 400, "msg": "没有可更新的项目字段", "data": None}), 400

    try:
        project = update_project(
            app.config["DATABASE_PATH"],
            project_id,
            project_data,
        )
    except ProjectNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    return jsonify({"code": 200, "msg": "项目已更新", "data": project}), 200


@app.route('/api/projects/<int:project_id>', methods=['DELETE'])
def remove_project(project_id):
    try:
        delete_project(app.config["DATABASE_PATH"], project_id)
    except ProjectNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except ProjectHasArticlesError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
    return jsonify({"code": 200, "msg": "项目已删除", "data": {"id": project_id}}), 200


def _parse_project_fields(data, *, partial):
    result = {}
    text_fields = (
        "name",
        "website",
        "product",
        "industry",
        "description",
    )
    for field in text_fields:
        if partial and field not in data:
            continue
        value = data.get(field, "")
        if value is None and field != "name":
            value = ""
        if not isinstance(value, str):
            return None, f"{field} 必须是字符串"
        value = value.strip()
        if field == "name" and not value:
            return None, "品牌名称不能为空"
        if field == "name" and len(value) > 100:
            return None, "品牌名称不能超过 100 个字符"
        result[field] = value

    for field in ("keywords", "competitors"):
        if partial and field not in data:
            continue
        value = data.get(field, [])
        if not isinstance(value, list):
            return None, f"{field} 必须是字符串数组"
        result[field] = _normalize_string_list(value)

    return result, None

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No file part in the request"
        }), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No selected file"
        }), 400
    try:
        # 保存文件到指定位置
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")
        original_filename = _normalize_media_filename(file.filename)
        stored_filename = f"{uuid_v1}_{original_filename}"
        filepath = _media_root() / stored_filename
        file.save(filepath)
        return jsonify({"code":200,"msg": "File uploaded successfully", "data": stored_filename}), 200
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    except Exception as e:
        return jsonify({"code":500,"msg": str(e),"data":None}), 500

@app.route('/getFile', methods=['GET'])
def get_file():
    # 获取 filename 参数
    try:
        filename = _normalize_media_filename(request.args.get('filename'))
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400

    return send_from_directory(_media_root(), filename)


@app.route('/download/<path:filename>', methods=['GET'])
def download_file(filename):
    try:
        normalized_filename = _normalize_media_filename(filename)
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    return send_from_directory(
        _media_root(),
        normalized_filename,
        as_attachment=True,
    )


@app.route('/uploadSave', methods=['POST'])
def upload_save():
    if 'file' not in request.files:
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No file part in the request"
        }), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No selected file"
        }), 400

    # 获取表单中的自定义文件名（可选）
    custom_filename = request.form.get('filename', None)
    if custom_filename:
        filename = custom_filename + "." + file.filename.rsplit('.', 1)[-1]
    else:
        filename = file.filename
    filename = str(filename or "").strip()
    if (
        not filename
        or Path(filename).name != filename
        or '/' in filename
        or '\\' in filename
        or '..' in filename
    ):
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "Invalid filename"
        }), 400

    try:
        # 生成 UUID v1
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")

        # 构造文件名和路径
        final_filename = f"{uuid_v1}_{filename}"
        media_root = Path(app.config["MEDIA_ROOT"]).expanduser().resolve()
        media_root.mkdir(parents=True, exist_ok=True)
        filepath = media_root / final_filename

        # 保存文件
        file.save(filepath)

        suffix = filepath.suffix.lower()
        media_type = (
            "image" if suffix in {".jpg", ".jpeg", ".png"}
            else "video" if suffix in {".mp4", ".mov", ".mkv", ".webm"}
            else "file"
        )
        upload_tags = _normalize_string_list(
            re.split(r"[,，;；]", str(request.form.get("tags") or ""))
        )[:20]
        with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO file_records (
                    filename, filesize, file_path, media_type, tags, source
                ) VALUES (?, ?, ?, ?, ?, 'upload')
                ''', (
                    filename,
                    round(float(os.path.getsize(filepath)) / (1024 * 1024), 2),
                    final_filename,
                    media_type,
                    json.dumps(upload_tags, ensure_ascii=False),
                ))
            conn.commit()
            print("Upload file record saved")

        return jsonify({
            "code": 200,
            "msg": "File uploaded and saved successfully",
            "data": {
                "filename": filename,
                "filepath": final_filename
            }
        }), 200

    except Exception as e:
        print(f"Upload failed: {e}")
        return jsonify({
            "code": 500,
            "msg": f"upload failed: {e}",
            "data": None
        }), 500

@app.route('/getFiles', methods=['GET'])
def get_all_files():
    try:
        # 使用 with 自动管理数据库连接
        with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
            conn.row_factory = sqlite3.Row  # 允许通过列名访问结果
            cursor = conn.cursor()

            # 查询所有记录
            cursor.execute("SELECT * FROM file_records")
            rows = cursor.fetchall()
            
            # 将结果转为字典列表，并提取UUID
            data = []
            for row in rows:
                row_dict = dict(row)
                row_dict['tags'] = _decode_json_list(row_dict.get('tags'))
                # 从 file_path 中提取 UUID (文件名的第一部分，下划线前)
                if row_dict.get('file_path'):
                    file_path_parts = row_dict['file_path'].split('_', 1)  # 只分割第一个下划线
                    if len(file_path_parts) > 0:
                        row_dict['uuid'] = file_path_parts[0]  # UUID 部分
                    else:
                        row_dict['uuid'] = ''
                else:
                    row_dict['uuid'] = ''
                data.append(row_dict)

            return jsonify({
                "code": 200,
                "msg": "success",
                "data": data
            }), 200
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str("get file failed!"),
            "data": None
        }), 500


@app.route("/getAccounts", methods=['GET'])
def getAccounts():
    """快速获取所有账号信息，不进行cookie验证"""
    try:
        with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
            SELECT * FROM user_info''')
            rows = cursor.fetchall()
            rows_list = [list(row) for row in rows]

            print("\n📋 当前数据表内容（快速获取）：")
            for row in rows:
                print(row)

            return jsonify(
                {
                    "code": 200,
                    "msg": None,
                    "data": rows_list
                }), 200
    except Exception as e:
        print(f"获取账号列表时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"获取账号列表失败: {str(e)}",
            "data": None
        }), 500


@app.route("/getValidAccounts",methods=['GET'])
async def getValidAccounts():
    from myUtils.auth import check_cookie

    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        cursor = conn.cursor()
        cursor.execute('''
        SELECT * FROM user_info''')
        rows = cursor.fetchall()
        rows_list = [list(row) for row in rows]
        print("\n📋 当前数据表内容：")
        for row in rows:
            print(row)
        for row in rows_list:
            if (
                row[1] == BILIBILI_ACCOUNT_TYPE
                and not app.config.get("ENABLE_BILIBILI_RUNTIME", False)
            ):
                continue
            flag = await check_cookie(
                row[1],
                row[2],
                cookies_directory=app.config["COOKIES_DIRECTORY"],
            )
            new_status = 1 if flag else 0
            row[4] = new_status
            cursor.execute('''
            UPDATE user_info 
            SET status = ? 
            WHERE id = ?
            ''', (new_status, row[0]))
            conn.commit()
            print(f"✅ 用户状态已更新 id={row[0]} status={new_status}")
        for row in rows:
            print(row)
        return jsonify(
                        {
                            "code": 200,
                            "msg": None,
                            "data": rows_list
                        }),200

@app.route('/deleteFile', methods=['GET'])
def delete_file():
    file_id = request.args.get('id')

    if not file_id or not file_id.isdigit():
        return jsonify({
            "code": 400,
            "msg": "Invalid or missing file ID",
            "data": None
        }), 400

    try:
        # 获取数据库连接
        with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 查询要删除的记录
            cursor.execute("SELECT * FROM file_records WHERE id = ?", (file_id,))
            record = cursor.fetchone()

            if not record:
                return jsonify({
                    "code": 404,
                    "msg": "File not found",
                    "data": None
                }), 404

            record = dict(record)

            # 获取文件路径并删除实际文件
            try:
                stored_filename = _normalize_media_filename(record['file_path'])
            except ValueError:
                return jsonify({
                    "code": 409,
                    "msg": "Stored file path is invalid",
                    "data": None
                }), 409
            file_path = _media_root() / stored_filename
            if file_path.exists():
                try:
                    file_path.unlink()  # 删除文件
                    print(f"✅ 实际文件已删除: {file_path}")
                except Exception as e:
                    print(f"⚠️ 删除实际文件失败: {e}")
                    # 即使删除文件失败，也要继续删除数据库记录，避免数据不一致
            else:
                print(f"⚠️ 实际文件不存在: {file_path}")

            # 删除数据库记录
            cursor.execute("DELETE FROM file_records WHERE id = ?", (file_id,))
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": "File deleted successfully",
            "data": {
                "id": record['id'],
                "filename": record['filename']
            }
        }), 200

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str("delete failed!"),
            "data": None
        }), 500

@app.route('/deleteAccount', methods=['GET'])
def delete_account():
    account_id = request.args.get('id')

    if not account_id or not account_id.isdigit():
        return jsonify({
            "code": 400,
            "msg": "Invalid or missing account ID",
            "data": None
        }), 400

    account_id = int(account_id)

    try:
        # 获取数据库连接
        with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 查询要删除的记录
            cursor.execute("SELECT * FROM user_info WHERE id = ?", (account_id,))
            record = cursor.fetchone()

            if not record:
                return jsonify({
                    "code": 404,
                    "msg": "account not found",
                    "data": None
                }), 404

            record = dict(record)

            # 删除关联的cookie文件
            if record.get('filePath'):
                try:
                    cookie_filename = _normalize_media_filename(
                        record['filePath']
                    )
                except ValueError:
                    return jsonify({
                        "code": 409,
                        "msg": "Stored cookie path is invalid",
                        "data": None
                    }), 409
                cookie_file_path = _cookies_root() / cookie_filename
                if cookie_file_path.exists():
                    try:
                        cookie_file_path.unlink()
                        print(f"✅ Cookie文件已删除: {cookie_file_path}")
                    except Exception as e:
                        print(f"⚠️ 删除Cookie文件失败: {e}")

            # 删除数据库记录
            cursor.execute("DELETE FROM user_info WHERE id = ?", (account_id,))
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": "account deleted successfully",
            "data": None
        }), 200

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"delete failed: {str(e)}",
            "data": None
        }), 500


# SSE 登录接口
@app.route('/login')
def login():
    # 1 小红书 2 视频号 3 抖音 4 快手 5 百家号 6 B站 7 今日头条
    login_type = str(request.args.get('type') or '').strip()
    # 账号名
    account_id = str(request.args.get('id') or '').strip()
    if login_type not in {str(value) for value in range(1, 11)}:
        return jsonify({"code": 400, "msg": "不支持的平台类型", "data": None}), 400
    if (
        login_type == str(BILIBILI_ACCOUNT_TYPE)
        and not app.config.get("ENABLE_BILIBILI_RUNTIME", False)
    ):
        return jsonify(
            {
                "code": 403,
                "msg": BILIBILI_RUNTIME_DISABLED_MESSAGE,
                "data": None,
            }
        ), 403
    if not account_id:
        return jsonify({"code": 400, "msg": "账号名不能为空", "data": None}), 400
    if len(account_id) > 100:
        return jsonify({"code": 400, "msg": "账号名不能超过 100 个字符", "data": None}), 400
    print(f"登录请求: type={login_type}, id={account_id}")

    # 模拟一个用于异步通信的队列
    status_queue = Queue()
    session_key = (login_type, account_id)
    with active_queues_lock:
        if session_key in active_queues:
            return jsonify(
                {"code": 409, "msg": "该账号已有登录会话进行中", "data": None}
            ), 409
        active_queues[session_key] = status_queue

    def on_close():
        with active_queues_lock:
            if active_queues.get(session_key) is status_queue:
                active_queues.pop(session_key, None)
        print(f"清理登录会话: type={login_type}, id={account_id}")
    # 启动异步任务线程
    try:
        thread = threading.Thread(
            target=run_async_function,
            args=(login_type, account_id, status_queue),
            daemon=True,
        )
        thread.start()
    except Exception:
        on_close()
        raise
    response = Response(
        sse_stream(status_queue, on_close=on_close),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'  # 关键：禁用 Nginx 缓冲
    response.headers['Content-Type'] = 'text/event-stream'
    response.headers['Connection'] = 'keep-alive'
    return response

@app.route('/postVideo', methods=['POST'])
def postVideo():
    # 获取JSON数据
    data = request.get_json()

    if not data:
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    # 从JSON数据中提取fileList和accountList
    file_list = data.get('fileList', [])
    account_list = data.get('accountList', [])
    type = data.get('type')
    title = data.get('title')
    tags = data.get('tags')
    category = data.get('category')
    enableTimer = data.get('enableTimer')
    if category == 0:
        category = None
    productLink = data.get('productLink', '')
    productTitle = data.get('productTitle', '')
    thumbnail_path = data.get('thumbnail', '') or data.get('coverImage', '')
    cover_images = data.get('coverImages') or data.get('cover_images') or []
    if isinstance(cover_images, str):
        cover_images = [x.strip() for x in cover_images.split(',') if x.strip()]
    elif not isinstance(cover_images, list):
        cover_images = []
    else:
        cover_images = [str(x).strip() for x in cover_images if str(x).strip()]
    # 兼容：仅传单图时并入 coverImages
    if thumbnail_path and str(thumbnail_path).strip() and str(thumbnail_path).strip() not in cover_images:
        if not cover_images:
            cover_images = [str(thumbnail_path).strip()]
    is_draft = data.get('isDraft', False)  # 新增参数：是否保存为草稿
    ai_generated = data.get('aiGenerated', False)  # 抖音/快手/小红书 AI 内容声明
    dry_run = data.get('dryRun', False)  # 仅预览不发布：填完表后不点发布
    # B站创作声明 id：-1/1/2/3/4；None 表示不传
    bilibili_creation_statement = data.get('bilibiliCreationStatement', None)
    # 今日头条内容类型：video（默认）| article
    content_type = (data.get('contentType') or 'video').strip().lower()
    article_body = data.get('articleBody') or data.get('body') or ''
    # 今日头条图文作品声明（单选）
    work_statement = data.get('workStatement') or data.get('work_statement') or ''
    work_statements = data.get('workStatements') or data.get('work_statements') or []
    if work_statement and str(work_statement).strip():
        work_statements = [str(work_statement).strip()]
    elif isinstance(work_statements, str):
        work_statements = [work_statements] if work_statements.strip() else []
    elif not isinstance(work_statements, list):
        work_statements = []
    else:
        work_statements = [str(x).strip() for x in work_statements if str(x).strip()]
    # 单选：最多保留第一项
    work_statements = work_statements[:1]

    videos_per_day = data.get('videosPerDay')
    daily_times = data.get('dailyTimes')
    start_days = data.get('startDays')

    if type is None or type == "":
        return jsonify({"code": 400, "msg": "平台类型不能为空", "data": None}), 400
    try:
        type = int(type)
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": f"不支持的平台类型: {type}", "data": None}), 400
    if (
        type == BILIBILI_ACCOUNT_TYPE
        and not app.config.get("ENABLE_BILIBILI_RUNTIME", False)
    ):
        return jsonify(
            {
                "code": 403,
                "msg": BILIBILI_RUNTIME_DISABLED_MESSAGE,
                "data": None,
            }
        ), 403

    # 支持图文文章的平台：5=百家号，7=今日头条，8=搜狐号，9=知乎
    ARTICLE_CAPABLE_PLATFORMS = (5, 7, 8, 9)
    # 搜狐号/知乎第一期仅支持图文
    if type == 8:
        content_type = 'article'
    if type == 9:
        content_type = 'article'
    is_article = (type in ARTICLE_CAPABLE_PLATFORMS and content_type == 'article')
    is_toutiao_article = (type == 7 and content_type == 'article')
    is_baijiahao_article = (type == 5 and content_type == 'article')
    is_sohu_article = (type == 8)
    is_zhihu_article = (type == 9)

    # 参数校验
    if not is_article and not file_list:
        return jsonify({"code": 400, "msg": "文件列表不能为空", "data": None}), 400
    if not account_list:
        return jsonify({"code": 400, "msg": "账号列表不能为空", "data": None}), 400
    if is_article:
        if not title or not str(title).strip():
            return jsonify({"code": 400, "msg": "文章标题不能为空", "data": None}), 400
        if is_sohu_article:
            title_len = len(str(title).strip())
            if title_len < 5 or title_len > 72:
                return jsonify({"code": 400, "msg": "搜狐号文章标题需 5-72 个字", "data": None}), 400
        if is_zhihu_article:
            title_len = len(str(title).strip())
            if title_len > 100:
                return jsonify({"code": 400, "msg": "知乎文章标题最多 100 个字", "data": None}), 400
        if not article_body or not str(article_body).strip():
            return jsonify({"code": 400, "msg": "文章正文不能为空", "data": None}), 400
        # 百家号图文标题下限 2 字
        if is_baijiahao_article and len(str(title).strip()) < 2:
            return jsonify({"code": 400, "msg": "百家号图文标题至少 2 个字", "data": None}), 400
        if is_baijiahao_article and not str(thumbnail_path or "").strip():
            return jsonify({"code": 400, "msg": "百家号图文展示封面不能为空", "data": None}), 400
    # 小红书（type=1）、抖音（type=3）、快手（type=4）标题非必填，其它平台仍必填
    elif type not in (1, 3, 4) and not title:
        return jsonify({"code": 400, "msg": "标题不能为空", "data": None}), 400
    if title is None:
        title = ""
    if is_sohu_article:
        title = str(title).strip()[:72]
    if is_zhihu_article:
        title = str(title).strip()[:100]
    if bilibili_creation_statement is not None and bilibili_creation_statement != "":
        try:
            bilibili_creation_statement = int(bilibili_creation_statement)
        except (TypeError, ValueError):
            return jsonify({"code": 400, "msg": f"非法的创作声明: {bilibili_creation_statement}", "data": None}), 400
    else:
        bilibili_creation_statement = None

    # 打印获取到的数据（仅作为示例）
    print("File List:", file_list)
    print("Account List:", account_list)
    print(
        f"dryRun={dry_run}, aiGenerated={ai_generated}, bilibiliCreationStatement={bilibili_creation_statement}, "
        f"contentType={content_type}, cover={thumbnail_path}, workStatements={work_statements}",
        flush=True,
    )

    # 参数校验通过后，在后台线程中执行发布任务，避免阻塞Flask主线程。
    # 前端拿到的 200 只表示“任务已提交”，不代表平台已经发布成功。
    def run_publish_task():
        try:
            # Import the browser uploaders only after a local publish request is
            # accepted. This keeps the Vercel demo API lightweight and bootable.
            from myUtils.postVideo import (
                post_article_baijiahao,
                post_article_sohu,
                post_article_toutiao,
                post_article_zhihu,
                post_video_DouYin,
                post_video_baijiahao,
                post_video_bilibili,
                post_video_ks,
                post_video_tencent,
                post_video_toutiao,
                post_video_xhs,
            )

            print(
                f"🧵 后台发布线程启动: type={type}, title={title}, dryRun={dry_run}, contentType={content_type}",
                flush=True,
            )
            match type:
                case 1:
                    post_video_xhs(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                       start_days, dry_run, ai_generated)
                case 2:
                    post_video_tencent(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                       start_days, is_draft, dry_run)
                case 3:
                    post_video_DouYin(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                              start_days, thumbnail_path, productLink, productTitle, ai_generated, dry_run)
                case 4:
                    post_video_ks(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                              start_days, dry_run, ai_generated)
                case 5:
                    if content_type == 'article':
                        post_article_baijiahao(
                            title,
                            article_body,
                            tags,
                            account_list,
                            enableTimer,
                            videos_per_day,
                            daily_times,
                            start_days,
                            dry_run,
                            thumbnail_path,
                        )
                    else:
                        post_video_baijiahao(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                  start_days, dry_run)
                case 6:
                    post_video_bilibili(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                              start_days, dry_run, bilibili_creation_statement)
                case 7:
                    if content_type == 'article':
                        post_article_toutiao(
                            title,
                            article_body,
                            tags,
                            account_list,
                            enableTimer,
                            videos_per_day,
                            daily_times,
                            start_days,
                            dry_run,
                            thumbnail_path,
                            work_statements,
                        )
                    else:
                        post_video_toutiao(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                  start_days, dry_run)
                case 8:
                    post_article_sohu(
                        title,
                        article_body,
                        tags,
                        account_list,
                        enableTimer,
                        videos_per_day,
                        daily_times,
                        start_days,
                        dry_run,
                        thumbnail_path,
                        work_statements[0] if work_statements else (work_statement or None),
                        cover_images,
                    )
                case 9:
                    post_article_zhihu(
                        title,
                        article_body,
                        tags,
                        account_list,
                        enableTimer,
                        videos_per_day,
                        daily_times,
                        start_days,
                        dry_run,
                        thumbnail_path,
                        work_statements[0] if work_statements else (work_statement or None),
                    )
            print(f"✅ 发布任务完成: type={type}, title={title}", flush=True)
        except Exception as e:
            print(f"❌ 发布视频时出错: {str(e)}", flush=True)
            import traceback
            traceback.print_exc()

    thread = threading.Thread(target=run_publish_task, daemon=True)
    thread.start()

    if type == 6 and dry_run:
        submit_msg = "B站仅预览模式不会实际上传（无浏览器可预览）"
    elif type == 6:
        submit_msg = "B站上传任务已提交，后台通过 biliup 执行（不会打开浏览器）"
    elif is_toutiao_article and dry_run:
        submit_msg = "今日头条文章任务已提交：仅预览不发布"
    elif is_toutiao_article:
        submit_msg = "今日头条文章发布任务已提交，正在后台执行"
    elif is_sohu_article and dry_run:
        submit_msg = "搜狐号文章任务已提交：仅预览不发布"
    elif is_sohu_article:
        submit_msg = "搜狐号文章发布任务已提交，正在后台执行"
    elif is_zhihu_article and dry_run:
        submit_msg = "知乎文章任务已提交：仅预览不发布"
    elif is_zhihu_article:
        submit_msg = "知乎文章发布任务已提交，正在后台执行"
    elif type == 5 and content_type == 'article' and dry_run:
        submit_msg = "百家号图文文章任务已提交：仅预览不发布（可能出现自动保存草稿，不等于已发布）"
    elif type == 5 and content_type == 'article':
        submit_msg = "百家号图文文章发布任务已提交，浏览器将自动打开"
    elif type == 5:
        submit_msg = "百家号视频发布任务已提交，浏览器将自动打开上传"
    else:
        submit_msg = "发布任务已提交，正在后台执行"

    return jsonify(
        {
            "code": 200,
            "msg": submit_msg,
            "data": None
        }), 200


@app.route('/updateUserinfo', methods=['POST'])
def updateUserinfo():
    # 获取JSON数据
    data = request.get_json()

    # 从JSON数据中提取 type 和 userName
    user_id = data.get('id')
    type = data.get('type')
    userName = data.get('userName')
    try:
        # 获取数据库连接
        with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 更新数据库记录
            cursor.execute('''
                           UPDATE user_info
                           SET type     = ?,
                               userName = ?
                           WHERE id = ?;
                           ''', (type, userName, user_id))
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": "account update successfully",
            "data": None
        }), 200

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str("update failed!"),
            "data": None
        }), 500

@app.route('/postVideoBatch', methods=['POST'])
def postVideoBatch():
    data_list = request.get_json()

    if not isinstance(data_list, list):
        return jsonify({"code": 400, "msg": "Expected a JSON array", "data": None}), 400
    if not app.config.get("ENABLE_BILIBILI_RUNTIME", False) and any(
        (
            item.get("type") == BILIBILI_ACCOUNT_TYPE
            or str(item.get("type")).strip() == str(BILIBILI_ACCOUNT_TYPE)
        )
        for item in data_list
        if isinstance(item, dict)
    ):
        return jsonify(
            {
                "code": 403,
                "msg": BILIBILI_RUNTIME_DISABLED_MESSAGE,
                "data": None,
            }
        ), 403

    from myUtils.postVideo import (
        post_article_baijiahao,
        post_article_sohu,
        post_article_toutiao,
        post_article_zhihu,
        post_video_DouYin,
        post_video_baijiahao,
        post_video_bilibili,
        post_video_ks,
        post_video_tencent,
        post_video_toutiao,
        post_video_xhs,
    )

    for data in data_list:
        # 从JSON数据中提取fileList和accountList
        file_list = data.get('fileList', [])
        account_list = data.get('accountList', [])
        type = data.get('type')
        title = data.get('title')
        tags = data.get('tags')
        category = data.get('category')
        enableTimer = data.get('enableTimer')
        if category == 0:
            category = None
        productLink = data.get('productLink', '')
        productTitle = data.get('productTitle', '')
        is_draft = data.get('isDraft', False)
        ai_generated = data.get('aiGenerated', False)
        dry_run = data.get('dryRun', False)
        thumbnail_path = data.get('thumbnail', '') or data.get('coverImage', '')
        cover_images = data.get('coverImages') or data.get('cover_images') or []
        if isinstance(cover_images, str):
            cover_images = [x.strip() for x in cover_images.split(',') if x.strip()]
        elif not isinstance(cover_images, list):
            cover_images = []
        else:
            cover_images = [str(x).strip() for x in cover_images if str(x).strip()]
        if thumbnail_path and str(thumbnail_path).strip() and str(thumbnail_path).strip() not in cover_images:
            if not cover_images:
                cover_images = [str(thumbnail_path).strip()]
        content_type = (data.get('contentType') or 'video').strip().lower()
        article_body = data.get('articleBody') or data.get('body') or ''
        work_statement = data.get('workStatement') or data.get('work_statement') or ''
        work_statements = data.get('workStatements') or data.get('work_statements') or []
        if work_statement and str(work_statement).strip():
            work_statements = [str(work_statement).strip()]
        elif isinstance(work_statements, str):
            work_statements = [work_statements] if work_statements.strip() else []
        elif not isinstance(work_statements, list):
            work_statements = []
        else:
            work_statements = [str(x).strip() for x in work_statements if str(x).strip()]
        work_statements = work_statements[:1]
        bilibili_creation_statement = data.get('bilibiliCreationStatement', None)
        if bilibili_creation_statement is not None and bilibili_creation_statement != "":
            try:
                bilibili_creation_statement = int(bilibili_creation_statement)
            except (TypeError, ValueError):
                bilibili_creation_statement = None
        else:
            bilibili_creation_statement = None

        videos_per_day = data.get('videosPerDay')
        daily_times = data.get('dailyTimes')
        start_days = data.get('startDays')
        # 搜狐号/知乎第一期仅支持图文
        if type == 8:
            content_type = 'article'
            title_text = str(title or "").strip()
            if len(title_text) < 5 or len(title_text) > 72:
                return jsonify({"code": 400, "msg": "搜狐号文章标题需 5-72 个字", "data": None}), 400
            title = title_text[:72]
        if type == 9:
            content_type = 'article'
            title_text = str(title or "").strip()
            if len(title_text) > 100:
                return jsonify({"code": 400, "msg": "知乎文章标题最多 100 个字", "data": None}), 400
            title = title_text[:100]
        is_baijiahao_article = (type == 5 and content_type == 'article')
        if is_baijiahao_article and not str(thumbnail_path or "").strip():
            return jsonify({"code": 400, "msg": "百家号图文展示封面不能为空", "data": None}), 400
        # 打印获取到的数据（仅作为示例）
        print("File List:", file_list)
        print("Account List:", account_list)
        match type:
            case 1:
                post_video_xhs(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                               start_days, dry_run, ai_generated)
            case 2:
                post_video_tencent(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                   start_days, is_draft, dry_run)
            case 3:
                post_video_DouYin(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days, thumbnail_path, productLink, productTitle, ai_generated, dry_run)
            case 4:
                post_video_ks(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days, dry_run)
            case 5:
                if content_type == 'article':
                    post_article_baijiahao(
                        title,
                        article_body,
                        tags,
                        account_list,
                        enableTimer,
                        videos_per_day,
                        daily_times,
                        start_days,
                        dry_run,
                        thumbnail_path,
                    )
                else:
                    post_video_baijiahao(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                              start_days, dry_run)
            case 6:
                post_video_bilibili(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days, dry_run, bilibili_creation_statement)
            case 7:
                if content_type == 'article':
                    post_article_toutiao(
                        title,
                        article_body,
                        tags,
                        account_list,
                        enableTimer,
                        videos_per_day,
                        daily_times,
                        start_days,
                        dry_run,
                        thumbnail_path,
                        work_statements,
                    )
                else:
                    post_video_toutiao(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                              start_days, dry_run)
            case 8:
                post_article_sohu(
                    title,
                    article_body,
                    tags,
                    account_list,
                    enableTimer,
                    videos_per_day,
                    daily_times,
                    start_days,
                    dry_run,
                    thumbnail_path,
                    work_statements[0] if work_statements else (work_statement or None),
                    cover_images,
                )
            case 9:
                post_article_zhihu(
                    title,
                    article_body,
                    tags,
                    account_list,
                    enableTimer,
                    videos_per_day,
                    daily_times,
                    start_days,
                    dry_run,
                    thumbnail_path,
                    work_statements[0] if work_statements else (work_statement or None),
                )
    # 返回响应给客户端
    return jsonify(
        {
            "code": 200,
            "msg": None,
            "data": None
        }), 200

# Cookie文件上传API
@app.route('/uploadCookie', methods=['POST'])
def upload_cookie():
    try:
        if 'file' not in request.files:
            return jsonify({
                "code": 400,
                "msg": "没有找到Cookie文件",
                "data": None
            }), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({
                "code": 400,
                "msg": "Cookie文件名不能为空",
                "data": None
            }), 400

        if not file.filename.endswith('.json'):
            return jsonify({
                "code": 400,
                "msg": "Cookie文件必须是JSON格式",
                "data": None
            }), 400

        # 获取账号信息
        account_id = request.form.get('id')
        platform = request.form.get('platform')

        if not account_id or not platform:
            return jsonify({
                "code": 400,
                "msg": "缺少账号ID或平台信息",
                "data": None
            }), 400

        # 从数据库获取账号的文件路径
        with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT filePath FROM user_info WHERE id = ?', (account_id,))
            result = cursor.fetchone()

        if not result:
            return jsonify({
                "code": 500,
                "msg": "账号不存在",
                "data": None
            }), 404

        # 保存上传的Cookie文件到对应路径
        cookie_filename = _normalize_media_filename(result['filePath'])
        cookie_file_path = _cookies_root() / cookie_filename
        cookie_file_path.parent.mkdir(parents=True, exist_ok=True)

        file.save(str(cookie_file_path))

        # 更新数据库中的账号信息（可选，比如更新更新时间）
        # 这里可以根据需要添加额外的处理逻辑

        return jsonify({
            "code": 200,
            "msg": "Cookie文件上传成功",
            "data": None
        }), 200

    except Exception as e:
        print(f"上传Cookie文件时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"上传Cookie文件失败: {str(e)}",
            "data": None
        }), 500


# Cookie文件下载API
@app.route('/downloadCookie', methods=['GET'])
def download_cookie():
    try:
        file_path = request.args.get('filePath')
        if not file_path:
            return jsonify({
                "code": 500,
                "msg": "缺少文件路径参数",
                "data": None
            }), 400

        # 验证文件路径的安全性，防止路径遍历攻击
        try:
            cookie_filename = _normalize_media_filename(file_path)
        except ValueError:
            return jsonify({
                "code": 500,
                "msg": "非法文件路径",
                "data": None
            }), 400
        cookie_file_path = _cookies_root() / cookie_filename

        if not cookie_file_path.exists():
            return jsonify({
                "code": 500,
                "msg": "Cookie文件不存在",
                "data": None
            }), 404

        # 返回文件
        return send_from_directory(
            directory=str(cookie_file_path.parent),
            path=cookie_file_path.name,
            as_attachment=True
        )

    except Exception as e:
        print(f"下载Cookie文件时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"下载Cookie文件失败: {str(e)}",
            "data": None
        }), 500


# 包装函数：在线程中运行异步函数
def run_async_function(type,id,status_queue):
    if (
        str(type) == str(BILIBILI_ACCOUNT_TYPE)
        and not app.config.get("ENABLE_BILIBILI_RUNTIME", False)
    ):
        print("Bilibili 登录运行时已被安全闸门阻断", flush=True)
        status_queue.put("500")
        return
    # Login helpers launch real browsers and belong to the local worker runtime.
    from myUtils.login import (
        baijiahao_cookie_gen,
        bilibili_cookie_gen,
        douyin_cookie_gen,
        get_ks_cookie,
        get_tencent_cookie,
        sohu_cookie_gen,
        toutiao_cookie_gen,
        tiktok_cookie_gen,
        xiaohongshu_cookie_gen,
        zhihu_cookie_gen,
    )

    print(f"🧵 登录线程启动: type={type}, id={id}", flush=True)
    login_storage = {
        "database_path": app.config["DATABASE_PATH"],
        "cookies_directory": app.config["COOKIES_DIRECTORY"],
    }
    try:
        match str(type):
            case '1':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    xiaohongshu_cookie_gen(id, status_queue, **login_storage)
                )
                loop.close()
            case '2':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    get_tencent_cookie(id, status_queue, **login_storage)
                )
                loop.close()
            case '3':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    douyin_cookie_gen(id, status_queue, **login_storage)
                )
                loop.close()
            case '4':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    get_ks_cookie(id, status_queue, **login_storage)
                )
                loop.close()
            case '5':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    baijiahao_cookie_gen(id, status_queue, **login_storage)
                )
                loop.close()
            case '6':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    bilibili_cookie_gen(id, status_queue, **login_storage)
                )
                loop.close()
            case '7':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    toutiao_cookie_gen(id, status_queue, **login_storage)
                )
                loop.close()
            case '8':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    sohu_cookie_gen(id, status_queue, **login_storage)
                )
                loop.close()
            case '9':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    zhihu_cookie_gen(id, status_queue, **login_storage)
                )
                loop.close()
            case '10':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    tiktok_cookie_gen(id, status_queue, **login_storage)
                )
                loop.close()
            case _:
                print(f"❌ 不支持的登录平台类型: {type}", flush=True)
                status_queue.put("500")
    except Exception as e:
        print(f"❌ 登录线程异常 type={type}, id={id}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        try:
            status_queue.put("500")
        except Exception:
            pass

# SSE 流生成器函数
def sse_stream(status_queue, *, on_close=None):
    last_heartbeat = time.monotonic()
    try:
        while True:
            if not status_queue.empty():
                msg = status_queue.get()
                yield f"data: {msg}\n\n"
                if str(msg) in {"200", "500"}:
                    break
            else:
                now = time.monotonic()
                if now - last_heartbeat >= 15:
                    yield ": keep-alive\n\n"
                    last_heartbeat = now
                # 避免 CPU 占满
                time.sleep(0.1)
    finally:
        if on_close is not None:
            on_close()


def get_server_bind():
    host = os.getenv("SERVER_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.getenv("SERVER_PORT", "5409"))
    except ValueError:
        port = 5409
    if not 1 <= port <= 65535:
        port = 5409
    return host, port

if __name__ == '__main__':
    publish_scheduler = None
    if _environment_flag("RUN_PUBLISH_SCHEDULER", default=True):
        try:
            scheduler_interval = max(
                5,
                int(os.getenv("PUBLISH_SCHEDULER_INTERVAL_SECONDS", "15")),
            )
        except ValueError:
            scheduler_interval = 15
        scheduler_allows_real = bool(
            app.config.get("ALLOW_REAL_PUBLISHING", False)
            and not app.config.get("DEMO_MODE", False)
        )
        scheduler_publisher_factory = (
            create_real_publisher_factory(
                app.config["DATABASE_PATH"],
                cookies_directory=app.config["COOKIES_DIRECTORY"],
                enable_bilibili_runtime=bool(
                    app.config.get("ENABLE_BILIBILI_RUNTIME", False)
                ),
            )
            if scheduler_allows_real
            else None
        )
        publish_scheduler = create_publish_scheduler(
            app.config["DATABASE_PATH"],
            interval_seconds=scheduler_interval,
            publisher_factory=scheduler_publisher_factory,
            allow_real=scheduler_allows_real,
            media_root=app.config["MEDIA_ROOT"],
        )
        publish_scheduler.start()
    try:
        server_host, server_port = get_server_bind()
        app.run(host=server_host, port=server_port)
    finally:
        if publish_scheduler is not None:
            publish_scheduler.shutdown(wait=False)
