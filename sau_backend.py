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
from datetime import datetime, timedelta
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
from werkzeug.exceptions import RequestEntityTooLarge
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
from services.geo_test_service import generate_test_plan, normalize_profile, case_quotas
from services.geo_test_reports import list_saved_reports, read_saved_report
from services import geo_content_store, geo_content_research, geo_destination_service
from services.dashboard_service import get_dashboard_overview
from services.demo_seed_service import seed_demo_data
from services.media_account_service import (
    MediaAccountCheckError,
    MediaAccountNotFoundError,
    check_media_account,
    get_media_accounts_overview,
)
from services.platform_capability_service import (
    get_platform_capability,
    list_platform_capabilities,
    list_planned_platforms,
)
from services.project_service import (
    ProjectHasArticlesError,
    ProjectNotFoundError,
    create_project,
    delete_project,
    get_project,
    list_projects,
    normalize_project_identity_fields,
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
from services.publish_policy_service import PublishPolicyViolation
from services.publish_scheduler_runtime import create_publish_scheduler
from services.real_publisher_factory import (
    REAL_PUBLISH_PLATFORMS,
    create_real_publisher_factory,
)
from services.request_guard import create_rate_limiter_from_environment
from services.login_runtime import LoginStatusQueue, check_login_cancelled, report_login_error

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
app.config["ALLOW_LEGACY_DIRECT_PUBLISHING"] = str(
    os.getenv("ALLOW_LEGACY_DIRECT_PUBLISHING", "false")
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
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
_frontend_candidates = (
    current_dir,
    current_dir / "sau_frontend" / "dist",
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
    if request.path.startswith("/api/auth") or request.endpoint in ('geo_probe_business_reviews', 'geo_website_audit', 'geo_search_onboarding'):
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

    rate_limit_response = _enforce_ai_rate_limit()
    if rate_limit_response is not None:
        return rate_limit_response

    try:
        article = generate_geo_content(
            **generation,
        )
    except AIConfigurationError as exc:
        return jsonify({"code": 503, "msg": str(exc), "data": None}), 503
    except AIServiceError as exc:
        return jsonify({"code": 502, "msg": str(exc), "data": None}), 502

    return jsonify({"code": 200, "msg": "success", "data": article}), 200


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
    rate_limit_response = _enforce_ai_rate_limit(cost=len(normalized_topics))
    if rate_limit_response is not None:
        return rate_limit_response

    items = []
    stopped = False
    for topic in normalized_topics:
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
            draft = generate_geo_content(**{**generation, "topic": topic})
            article = create_article(
                app.config["DATABASE_PATH"],
                project_id=int(data["project_id"]),
                title=draft["title"],
                summary=draft["summary"],
                content=draft["content"],
                tags=draft["tags"],
                status="draft",
            )
            items.append({"topic": topic, "status": "created", "article": article})
        except (AIConfigurationError, AIServiceError) as exc:
            items.append({"topic": topic, "status": "failed", "message": str(exc)})
            stopped = True

    created_count = sum(item["status"] == "created" for item in items)
    return jsonify(
        {
            "code": 200,
            "msg": f"批量任务完成，已保存 {created_count} 篇草稿",
            "data": {
                "items": items,
                "created_count": created_count,
                "failed_count": len(items) - created_count,
            },
        }
    ), 200


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
    return {
        "project": project,
        "topic": topic,
        "keywords": _normalize_string_list(requested_keywords),
        "length": length,
        "content_type": content_type,
        "target_platform": str(
            data.get("target_platform") or data.get("targetPlatform") or ""
        ).strip(),
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


@app.route('/api/dashboard', methods=['GET'])
def get_dashboard_data():
    overview = get_dashboard_overview(app.config["DATABASE_PATH"])
    return jsonify({"code": 200, "msg": "success", "data": overview}), 200


@app.route('/api/geo-tests/reports', methods=['GET'])
def get_saved_geo_test_reports():
    root = Path(app.config.get("GEO_TEST_REPORTS_DIRECTORY", current_dir / "runtime" / "geo-tests"))
    return jsonify({"code": 200, "msg": "success", "data": list_saved_reports(root)}), 200


@app.route('/api/geo-tests/reports/<report_id>', methods=['GET'])
def get_saved_geo_test_report(report_id):
    root = Path(app.config.get("GEO_TEST_REPORTS_DIRECTORY", current_dir / "runtime" / "geo-tests"))
    report = read_saved_report(root, report_id)
    if report is None:
        return jsonify({"code": 404, "msg": "未找到已保存的测试报告", "data": None}), 404
    return jsonify({"code": 200, "msg": "success", "data": report}), 200


@app.route('/api/geo-tests/plan', methods=['POST'])
def create_geo_test_plan():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请提交 JSON 对象", "data": None}), 400
    project_id = data.get("project_id")
    if type(project_id) is not int or project_id <= 0:
        return jsonify({"code": 400, "msg": "请选择品牌项目", "data": None}), 400
    try:
        project = get_project(app.config["DATABASE_PATH"], project_id)
        profile = normalize_profile({**project, **{key: data[key] for key in
                                    ("company", "aliases", "audience", "region", "entity_type", "official_urls") if key in data}})
        count = data.get("case_count", 4)
        case_quotas(profile, count)
        ai_settings = _request_ai_settings(data)
    except ProjectNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except (ValueError, AIConfigurationError) as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    limited = _enforce_ai_rate_limit()
    if limited is not None:
        return limited
    try:
        plan = generate_test_plan(profile=profile, case_count=count, settings=ai_settings)
    except AIConfigurationError:
        return jsonify({"code": 503, "msg": "请在系统设置配置 AI，或导出客户资料后用本机已登录的豆包生成计划", "data": None}), 503
    except AIServiceError as exc:
        return jsonify({"code": 502, "msg": str(exc), "data": None}), 502
    return jsonify({"code": 200, "msg": "测试计划已生成，可下载后执行", "data": plan}), 200


@app.route('/api/geo-content/cycles', methods=['GET', 'POST'])
def geo_content_cycles():
    database = app.config['DATABASE_PATH']
    if request.method == 'GET':
        return jsonify(code=200, data=geo_content_store.list_cycles(database))
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(code=400, msg='请提交采集报告', data=None), 400
    root = Path(app.config.get('GEO_TEST_REPORTS_DIRECTORY', current_dir / 'runtime/geo-tests'))
    report_ref = data.get('report_id') or ''
    report = read_saved_report(root, report_ref) if report_ref else data.get('report')
    if report is None:
        return jsonify(code=404, msg='采集报告不存在', data=None), 404
    try:
        cycle = geo_content_store.create_cycle(database, report, report_ref or 'user_import', data.get('project_id'))
    except ValueError as error:
        return jsonify(code=400, msg=str(error), data=None), 400
    return jsonify(code=201, msg='采集内容任务单已保存', data=cycle), 201


@app.route('/api/geo-content/cycles/<identifier>', methods=['GET', 'PUT'])
def geo_content_cycle(identifier):
    try:
        if request.method == 'PUT':
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                raise ValueError('请提交已核对的事实资料')
            cycle = geo_content_store.save_facts(app.config['DATABASE_PATH'], identifier, data.get('facts'), data.get('revision'))
        else:
            cycle = geo_content_store.get_cycle(app.config['DATABASE_PATH'], identifier)
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except ValueError as error:
        return jsonify(code=400, msg=str(error), data=None), 400
    return jsonify(code=200, data=cycle)


def _feedback_same_origin():
    origin = request.headers.get('Origin')
    allowed = {request.host_url.rstrip('/'), *_cors_allowed_origins()}
    if ((origin is not None and origin not in allowed)
            or (origin is None and request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'))):
        return jsonify(code=403, msg='请从当前软件页面访问反馈记录', data=None), 403
    return None


def _external_feedback_json(*, maximum=1100 * 1024):
    """JSON-only same-origin/CORS writes, bounded independently of media uploads."""
    if request.mimetype != 'application/json':
        return None, (jsonify(code=415, msg='反馈记录只接受JSON文本，不接受文件或表单', data=None), 415)
    rejected = _feedback_same_origin()
    if rejected is not None:
        return None, rejected
    request.max_content_length = maximum

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON field')
            value[key] = item
        return value

    def finite_value(value):
        raise ValueError('Nonfinite JSON number')

    try:
        raw = request.get_data(cache=False)
        # Unknown-length streams can stop at Flask's limit without raising.
        # Refuse that boundary rather than parsing a silently truncated body.
        if len(raw) >= maximum:
            raise RequestEntityTooLarge()
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=finite_value)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('请提交完整JSON对象，不使用重复字段或非有限数值') from None
    return value, None


from services.geo_external_baseline import ExternalBaselineConflict


@app.errorhandler(ExternalBaselineConflict)
def external_baseline_conflict_response(error):
    response = jsonify(code=409, msg=str(error), data=None)
    response.headers['Cache-Control'] = 'no-store'
    return response, 409


@app.route('/api/geo-content/external-baseline-plans', methods=['GET'])
def geo_external_baseline_pending_plans():
    from services import geo_external_baseline as external
    rejected = _feedback_same_origin()
    if rejected is not None:
        return rejected
    try:
        if (set(request.args) - {'project_id', 'offset'} or 'project_id' not in request.args
                or any(len(request.args.getlist(key)) != 1 for key in request.args)):
            raise ValueError('请明确选择当前品牌项目和冻结计划页码')
        project_value, offset_value = request.args['project_id'], request.args.get('offset', '0')
        if (not re.fullmatch(r'[1-9][0-9]{0,14}', project_value)
                or not re.fullmatch(r'(?:0|[1-9][0-9]{0,6})', offset_value)):
            raise ValueError('品牌项目或冻结计划页码无效')
        result = external.list_pending_plans(app.config['DATABASE_PATH'], int(project_value), offset=int(offset_value))
        response = jsonify(code=200, data=result)
        response.headers['Cache-Control'] = 'no-store'
        return response
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except ValueError as error:
        return jsonify(code=400, msg=str(error), data=None), 400


@app.route('/api/geo-content/external-baseline-plans/prepare', methods=['POST'])
@app.route('/api/geo-content/external-baseline-plans/freeze', methods=['POST'])
@app.route('/api/geo-content/external-cycles', methods=['POST'])
def geo_external_baseline_entry():
    from services import geo_external_baseline as external
    operation = 'create' if request.path.endswith('/external-cycles') else request.path.rsplit('/', 1)[-1]
    try:
        data, rejected = _external_feedback_json(maximum=1100*1024 if operation == 'create' else 64*1024)
        if rejected is not None:
            return rejected
        result = {'prepare': external.prepare, 'freeze': external.freeze, 'create': external.create}[operation](
            app.config['DATABASE_PATH'], data)
        response = jsonify(code=200, data=result)
        response.headers['Cache-Control'] = 'no-store'
        return response
    except RequestEntityTooLarge:
        return jsonify(code=413, msg='外部基线请求过大，原始回答不能超过1MiB', data=None), 413
    except external.ExternalBaselineConflict as error:
        return jsonify(code=409, msg=str(error), data=None), 409
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except ValueError as error:
        return jsonify(code=400, msg=str(error), data=None), 400
    except (KeyError, TypeError):
        return jsonify(code=400, msg='外部基线字段格式无效，请刷新并核对冻结原题', data=None), 400


@app.route('/api/geo-content/cycles/<identifier>/external-observations', methods=['POST'])
@app.route('/api/geo-content/cycles/<identifier>/external-observations/<probe_id>/context', methods=['POST'])
def geo_external_observation(identifier, probe_id=None):
    from services import geo_research_probe_service as probes
    try:
        data, rejected = _external_feedback_json()
        if rejected is not None:
            return rejected
        database = app.config['DATABASE_PATH']
        if probe_id is None:
            record = probes.submit_external_observation(database, identifier, data)
        else:
            record = probes.submit_external_observation_context(database, identifier, probe_id, data)
        response = jsonify(code=200, data={'probe': record, 'cycle': geo_content_store.get_cycle(database, identifier)})
        response.headers['Cache-Control'] = 'no-store'
        return response
    except RequestEntityTooLarge:
        return jsonify(code=413, msg='外部反馈JSON不能超过1100KiB，回答原文不能超过1MiB', data=None), 413
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except (ValueError, TypeError, KeyError) as error:
        return jsonify(code=400, msg=str(error), data=None), 400


@app.route('/api/geo-content/cycles/<identifier>/probes/<probe_id>/business-reviews', methods=['GET', 'POST'])
def geo_probe_business_reviews(identifier, probe_id):
    from services import geo_research_probe_service as probes
    try:
        database = app.config['DATABASE_PATH']
        if request.method == 'GET':
            rejected = _feedback_same_origin()
            if rejected is not None:
                return rejected
            if request.args or request.content_length:
                raise ValueError('核对条件从本轮原记录读取，不接受覆盖字段')
            return jsonify(code=200, data=probes.business_review_context(database, identifier, probe_id))
        data, rejected = _external_feedback_json(maximum=64 * 1024)
        if rejected is not None:
            return rejected
        record = probes.submit_operator_business_review(database, identifier, probe_id, data)
        return jsonify(code=200, data={'probe': record, 'cycle': geo_content_store.get_cycle(database, identifier)})
    except RequestEntityTooLarge:
        return jsonify(code=413, msg='业务材料核对JSON不能超过64KiB', data=None), 413
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except (ValueError, TypeError, KeyError) as error:
        return jsonify(code=400, msg=str(error), data=None), 400


@app.route('/api/geo-content/cycles/<identifier>/source-maintenance', methods=['GET', 'POST'])
def geo_source_maintenance(identifier):
    from services import source_maintenance_service as maintenance
    try:
        if request.method == 'GET':
            result = maintenance.get_overview(app.config['DATABASE_PATH'], identifier)
        else:
            data = request.get_json(silent=True)
            names = {'module', 'proposal', 'expected_context_sha256', 'reference_fact_ids', 'task_id'}
            if not isinstance(data, dict) or set(data) - names:
                raise ValueError('请仅提交公司模块、公开说明和已确认资料引用')
            result = maintenance.save_task(app.config['DATABASE_PATH'], identifier,
                data.get('module'), data.get('proposal'), data.get('expected_context_sha256'),
                data.get('reference_fact_ids'), data.get('task_id'))
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except (ValueError, TypeError, KeyError) as error:
        return jsonify(code=400, msg=str(error), data=None), 400
    return jsonify(code=200, data=result)


@app.route('/api/geo-content/cycles/<identifier>/webmaster', methods=['GET', 'POST'])
def geo_webmaster(identifier):
    from services import webmaster_service as webmaster
    try:
        if request.method == 'GET':
            result = webmaster.get_overview(app.config['DATABASE_PATH'], identifier)
        else:
            result = webmaster.perform_action(app.config['DATABASE_PATH'], identifier, request.get_json(silent=True))
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except (ValueError, TypeError, KeyError) as error:
        return jsonify(code=400, msg=str(error), data=None), 400
    return jsonify(code=200, data=result)


@app.route('/api/geo-content/cycles/<identifier>/search-onboarding', methods=['GET', 'POST'])
def geo_search_onboarding(identifier):
    from services import geo_search_onboarding as onboarding
    try:
        if request.method == 'GET':
            rejected = _feedback_same_origin()
            if rejected is not None:
                return rejected
            if request.args or request.content_length or request.stream.read(1):
                return jsonify(code=400, msg='查询不接受覆盖当前任务范围的参数或正文', data=None), 400
            result = onboarding.get_overview(app.config['DATABASE_PATH'], identifier)
        else:
            data, rejected = _external_feedback_json(maximum=16 * 1024)
            if rejected is not None:
                return rejected
            result = onboarding.save_progress(app.config['DATABASE_PATH'], identifier, data)
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except (ValueError, TypeError, KeyError) as error:
        return jsonify(code=400, msg=str(error), data=None), 400
    return jsonify(code=200, data=result)


@app.route('/api/geo-content/cycles/<identifier>/website-audit', methods=['GET', 'POST'])
def geo_website_audit(identifier):
    from services import geo_website_audit as audit
    try:
        database = app.config['DATABASE_PATH']
        if request.method == 'GET':
            rejected = _feedback_same_origin()
            if rejected is not None:
                return rejected
            if request.args or request.content_length:
                raise ValueError('官网范围只从本轮登记资料读取，不接受覆盖网址')
            return jsonify(code=200, data=audit.get_overview(database, identifier))
        data, rejected = _external_feedback_json(maximum=64 * 1024)
        if rejected is not None:
            return rejected
        return jsonify(code=200, data=audit.perform_audit(database, identifier, data))
    except RequestEntityTooLarge:
        return jsonify(code=413, msg='官网检查JSON不能超过64KiB', data=None), 413
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except (ValueError, TypeError, KeyError) as error:
        return jsonify(code=400, msg=str(error), data=None), 400
    except sqlite3.Error:
        return jsonify(code=503, msg='检测记录暂时无法安全保存，请检查原记录，不要自动重复检测', data=None), 503


@app.route('/api/geo-content/cycles/<identifier>/generate', methods=['POST'])
def geo_content_generate(identifier):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(code=400, msg='请提交生成选项', data=None), 400
    try:
        cycle = geo_content_store.get_cycle(app.config['DATABASE_PATH'], identifier)
        kind = data.get('kind')
        if kind == 'analysis':
            prompt = geo_content_research.research_prompt(cycle)
        elif kind == 'draft':
            prompt = geo_content_research.draft_prompt(cycle, data.get('brief_id'), data.get('platform'))
            existing = geo_content_store.existing_draft(app.config['DATABASE_PATH'], cycle, data.get('brief_id'), data.get('platform'))
            if existing:
                return jsonify(code=200, msg='相同资料已有草稿，请在内容库编辑', data={'cycle': cycle, **existing})
        else:
            raise ValueError('生成类型应为 analysis 或 draft')
        settings = _request_ai_settings(data) or AISettings.from_environment()
        limited = _enforce_ai_rate_limit()
        if limited is not None:
            return limited
        from services.ai_service import _request_chat_completion
        import requests as research_requests
        raw = _request_chat_completion(settings=settings, prompt=prompt, temperature=0.3,
                                       http_post=research_requests.post, system_message=geo_content_research.SYSTEM)
        provenance = {'method': 'ai_api', 'model': settings.model, 'prompt_sha256': geo_content_research.fingerprint(prompt)}
        if kind == 'analysis':
            provenance['feedback_inputs_sha256'] = geo_content_research.fingerprint(geo_content_research.planning_feedback(cycle))
        if kind == 'draft':
            provenance['input_sha256'] = geo_content_research.fingerprint(geo_content_research.draft_input(cycle, data['brief_id'], data['platform']))
        result = {}
        if kind == 'analysis':
            geo_content_store.save_analysis(app.config['DATABASE_PATH'], identifier, raw, provenance, cycle['revision'])
        else:
            result = geo_content_store.save_draft(app.config['DATABASE_PATH'], identifier, data['brief_id'], data['platform'], raw, provenance, cycle['revision'])
        return jsonify(code=200, msg='内容结果已保存', data={'cycle': geo_content_store.get_cycle(app.config['DATABASE_PATH'], identifier), **result})
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except AIConfigurationError as error:
        return jsonify(code=503, msg=str(error), data=None), 503
    except AIServiceError as error:
        return jsonify(code=502, msg=str(error), data=None), 502
    except ValueError as error:
        return jsonify(code=400, msg=str(error), data=None), 400


@app.route('/api/geo-content/cycles/<identifier>/retests', methods=['POST'])
def geo_content_attach_retest(identifier):
    data = request.get_json(silent=True) or {}
    root = Path(app.config.get('GEO_TEST_REPORTS_DIRECTORY', current_dir / 'runtime/geo-tests'))
    if not isinstance(data, dict) or not isinstance(data.get('report_id'), str):
        return jsonify(code=400, msg='请选择已有的复测报告', data=None), 400
    report = read_saved_report(root, data['report_id'])
    if not report:
        return jsonify(code=404, msg='复测报告不存在', data=None), 404
    try:
        cycle = geo_content_store.attach_retest(app.config['DATABASE_PATH'], identifier, report)
        return jsonify(code=200, data=cycle)
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except ValueError as error:
        return jsonify(code=400, msg=str(error), data=None), 400


@app.route('/api/geo-content/articles/<int:article_id>', methods=['GET'])
def geo_content_article_research(article_id):
    return jsonify(code=200, data=geo_content_store.article_research(app.config['DATABASE_PATH'], article_id))


@app.route('/api/geo-content/cycles/<identifier>/publications', methods=['POST'])
def geo_content_report_publication(identifier):
    try:
        cycle = geo_content_store.save_reported_publication(app.config['DATABASE_PATH'], identifier, request.get_json(silent=True))
        return jsonify(code=200, msg='已保存用户登记链接；公开内容和审核状态尚未独立核验', data=cycle)
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except ValueError as error:
        return jsonify(code=400, msg=str(error), data=None), 400


@app.route('/api/geo-content/cycles/<identifier>/moderation-observations', methods=['POST'])
def geo_content_report_moderation(identifier):
    try:
        cycle = geo_content_store.save_moderation_observation(
            app.config['DATABASE_PATH'], identifier, request.get_json(silent=True))
        return jsonify(code=200, msg='已保存运营方观察；审核和公开状态不等于自动核验结果，不会重新提交文章', data=cycle)
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except ValueError as error:
        return jsonify(code=400, msg=str(error), data=None), 400


@app.route('/api/geo-content/cycles/<identifier>/destinations', methods=['POST'])
@app.route('/api/geo-content/cycles/<identifier>/destinations/<target_id>', methods=['GET', 'PUT'])
def geo_content_destinations(identifier, target_id=None):
    try:
        database = app.config['DATABASE_PATH']
        cycle = geo_content_store.get_cycle(database, identifier)
        if request.method == 'GET':
            target = geo_destination_service.get_target(database, cycle['project_id'], target_id)
        else:
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                raise ValueError('请填写平台配置')
            target = geo_destination_service.save_target(database, cycle['project_id'], data.get('config'), target_id, data.get('revision'))
        return jsonify(code=200, data={'target': target, 'cycle': geo_content_store.get_cycle(database, identifier)})
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except ValueError as error:
        return jsonify(code=400, msg=str(error), data=None), 400


@app.route('/api/geo-content/cycles/<identifier>/destinations/<target_id>/review', methods=['POST'])
def geo_content_destination_review(identifier, target_id):
    try:
        database = app.config['DATABASE_PATH']
        cycle = geo_content_store.get_cycle(database, identifier)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('请确认已检查页面与尝试记录')
        target = geo_destination_service.review_block(database, cycle['project_id'], target_id, data.get('confirmed'))
        return jsonify(code=200, data=target)
    except geo_content_store.ResearchNotFoundError as error:
        return jsonify(code=404, msg=str(error), data=None), 404
    except ValueError as error:
        return jsonify(code=400, msg=str(error), data=None), 400


@app.route('/api/media-accounts', methods=['GET'])
def get_media_accounts():
    publish_at = None
    if 'publish_at' in request.args:
        values = request.args.getlist('publish_at')
        value = values[0].strip() if len(values) == 1 else ''
        try:
            if not value or len(value) > 80 or ('T' not in value and ' ' not in value):
                raise ValueError()
            publish_at = datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat()
        except ValueError:
            return jsonify({'code':400,'msg':'计划时间必须是完整的 ISO 8601 日期和时间','data':None}),400
    try:
        overview = get_media_accounts_overview(
            app.config["DATABASE_PATH"],
            cookies_directory=app.config["COOKIES_DIRECTORY"],
            publish_at=publish_at,
        )
    except ValueError:
        return jsonify({'code':400,'msg':'无法按指定时间核对发布额度，请重新选择时间','data':None}),400
    overview['execution_mode'] = 'demo' if app.config.get('DEMO_MODE', False) else 'real'
    overview['real_publishing_enabled'] = bool(app.config.get('ALLOW_REAL_PUBLISHING', False))
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
            checker=lambda account_type, file_path: check_cookie(
                account_type,
                file_path,
                cookies_directory=app.config["COOKIES_DIRECTORY"],
            ),
        )
    except MediaAccountNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
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

    try:
        article = create_article(
            app.config["DATABASE_PATH"],
            project_id=project_id,
            **article_data,
        )
    except ProjectNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404

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

    before_score = score_geo_content(
        title=article["title"],
        content=article["content"],
        brand=project["name"],
        keywords=project["keywords"],
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
        brand=project["name"],
        keywords=project["keywords"],
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
        if not isinstance(status, str) or status not in ARTICLE_STATUSES:
            return None, "不支持的文章状态"
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
    expected_review = data.get('expected_review_fingerprint')
    if not app.config.get('DEMO_MODE', False) and (
        not isinstance(expected_review, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_review)
    ):
        return jsonify({'code':400,'msg':'请先刷新并核对当前稿件，再创建发布任务','data':None}),400
    if (
        capability.get("delivery_state") == "paused"
        and not app.config.get("DEMO_MODE", False)
    ):
        return jsonify(
            {
                "code": 409,
                "msg": (
                    f"{capability['name']}真实自动发布当前暂停；"
                    "可先生成平台稿并由账号持有人手工发布"
                ),
                "data": {
                    "reason_code": "platform_delivery_paused",
                    "platform": platform,
                    "policy": capability,
                },
            }
        ), 409

    if not app.config.get('DEMO_MODE', False):
        from services.publish_confirmation import article_review_fingerprint
        try:
            reviewed_article = get_article(app.config['DATABASE_PATH'], article_id)
        except ArticleNotFoundError as exc:
            return jsonify({'code':404,'msg':str(exc),'data':None}),404
        if article_review_fingerprint(reviewed_article) != expected_review:
            return jsonify({'code':409,'msg':'稿件已经更新，请重新读取并核对后再创建发布任务','data':None}),409

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
        and not capability.get("supports_auto_execute", False)
    ):
        return jsonify(
            {
                "code": 409,
                "msg": (
                    f"{capability['name']}当前只支持到点转人工确认，"
                    "不支持无人值守自动发布"
                ),
                "data": {
                    "reason_code": "auto_execute_not_allowed",
                    "platform": platform,
                    "policy": capability,
                },
            }
        ), 409
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

    try:
        from services.publish_job_service import PublishConfirmationMismatchError
        job = create_publish_job(
            app.config["DATABASE_PATH"],
            article_id=article_id,
            platform=platform,
            images=images,
            video=video,
            publish_at=data.get("publish_at"),
            auto_execute=auto_execute,
            demo=bool(app.config.get("DEMO_MODE", False)),
            account_id=data.get("account_id"),
            require_account=not bool(app.config.get("DEMO_MODE", False)),
            enforce_publish_policy=not bool(app.config.get("DEMO_MODE", False)),
            expected_review_fingerprint=expected_review if not app.config.get('DEMO_MODE', False) else None,
        )
    except ArticleNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except PublishConfirmationMismatchError as exc:
        return jsonify({'code':409,'msg':str(exc),'data':None}),409
    except PublishPolicyViolation as exc:
        return jsonify(
            {
                "code": 429,
                "msg": str(exc),
                "data": exc.decision,
            }
        ), 429
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


@app.route('/api/publish/jobs/<int:job_id>/check-result', methods=['POST'])
def check_publish_task_result(job_id):
    # This endpoint accepts no claimed URL, title or success flag. Only normal
    # visible platform pages can supply the observation used for reconciliation.
    if app.config.get('DEMO_MODE',False) or not app.config.get('DESKTOP_MODE',False):
        return jsonify({'code':403,'msg':'仅本机桌面真实任务可打开可见窗口核对结果','data':None}),403
    if request.get_json(silent=True) not in (None,{}):
        return jsonify({'code':400,'msg':'核对结果不接受自报网址、内容或成功状态','data':None}),400
    from services.toutiao_result_check import start_result_check,ResultCheckUnavailable
    try:
        value=start_result_check(app.config['DATABASE_PATH'],app.config['COOKIES_DIRECTORY'],job_id)
    except PublishJobNotFoundError as error:
        return jsonify({'code':404,'msg':str(error),'data':None}),404
    except (ResultCheckUnavailable,ValueError) as error:
        return jsonify({'code':409,'msg':str(error),'data':None}),409
    return jsonify({'code':202,'msg':value['message'],'data':value}),202


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
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("confirm") is not True:
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
    capability = get_platform_capability(current["platform"])
    if capability and capability.get("delivery_state") == "paused":
        return jsonify(
            {
                "code": 409,
                "msg": (
                    f"{capability['name']}真实自动发布当前暂停，"
                    "本次不会访问平台"
                ),
                "data": {
                    "reason_code": "platform_delivery_paused",
                    "platform": current["platform"],
                    "policy": capability,
                },
            }
        ), 409

    expected_confirmation = data.get('expected_confirmation_snapshot')
    if not isinstance(expected_confirmation, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_confirmation):
        return jsonify({'code':400,'msg':'请刷新原任务并核对当前稿件后，再确认真实发布','data':None}),400

    factory_options = {}
    if app.config.get('DESKTOP_MODE', False) and current['platform'] == 'toutiao':
        from services.publish_job_service import update_publish_job_progress
        def publish_progress(message):
            update_publish_job_progress(app.config['DATABASE_PATH'], job_id, message=message)
        def submission_recorder(state):
            from services.publish_job_service import record_publish_submission_state
            record_publish_submission_state(app.config['DATABASE_PATH'], job_id, 'toutiao', state)
        factory_options = {'toutiao_interactive': True, 'publish_progress': publish_progress,
                           'submission_recorder': submission_recorder}
    publisher_factory = create_real_publisher_factory(
        app.config["DATABASE_PATH"],
        cookies_directory=app.config["COOKIES_DIRECTORY"],
        **factory_options,
    )
    try:
        job = execute_publish_job(
            app.config["DATABASE_PATH"],
            job_id,
            publisher_factory=publisher_factory,
            media_root=app.config["MEDIA_ROOT"],
            expected_confirmation_snapshot=expected_confirmation,
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
    from services.toutiao_result_check import result_check_running
    from services.publish_messages import public_publish_message
    from services.publish_confirmation import publish_confirmation_snapshot
    payload = {
        "job_id": job["id"],
        "confirmation_snapshot": publish_confirmation_snapshot(job),
        "article_id": job["article_id"],
        "account_id": job.get("account_id"),
        "platform": job["platform"],
        "status": job["status"],
        "message": public_publish_message(job["message"], job["platform"]),
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
        "platform_published_at": job.get('platform_published_at'),
        "platform_published_precision": job.get('platform_published_precision'),
        "result_checked_at": job.get('result_checked_at'),
        "result_checking": result_check_running(app.config['DATABASE_PATH'],job['id']),
        "can_execute_real": bool(
            not job["demo"]
            and not app.config.get("DEMO_MODE", False)
            and app.config.get("ALLOW_REAL_PUBLISHING", False)
            and job["platform"] in REAL_PUBLISH_PLATFORMS
            and job["status"] == "queued"
            and (get_platform_capability(job["platform"]) or {}).get(
                "delivery_state"
            ) != "paused"
        ),
        "public_url_recorded": bool(
            job["status"] == "success" and str(job.get("result_url") or "").strip()
        ),
    }
    if "article_title" in job:
        payload["article_title"] = job["article_title"]
    if "account_name" in job:
        payload["account_name"] = job.get("account_name")
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


@app.route('/api/publish/platform-roadmap', methods=['GET'])
def get_publish_platform_roadmap():
    return jsonify(
        {"code": 200, "msg": "success", "data": list_planned_platforms()}
    ), 200


@app.route('/api/projects', methods=['POST'])
def save_project():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    project_data, validation_error = _parse_project_fields(data, partial=False)
    if validation_error:
        return jsonify({"code": 400, "msg": validation_error, "data": None}), 400
    try:
        project = create_project(app.config["DATABASE_PATH"], **project_data)
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
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
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
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

    identity = {field: data[field] for field in ('entity_type', 'company', 'audience', 'aliases', 'official_urls') if field in data}
    try:
        result.update(normalize_project_identity_fields({**result, **identity}, partial=partial))
    except ValueError as exc:
        return None, str(exc)

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
    # ``id`` is the legacy display-name argument; account_id explicitly binds
    # a reconnect to the existing database row and all of its publish tasks.
    account_name = str(request.args.get('id') or '').strip()
    if login_type not in {str(value) for value in range(1, 11)}:
        return jsonify({"code": 400, "msg": "不支持的平台类型", "data": None}), 400
    reconnect_id = None
    if request.args.get('account_id') is not None:
        raw_reconnect_id = str(request.args.get('account_id') or '').strip()
        if len(raw_reconnect_id) > 19 or not raw_reconnect_id.isdecimal() or not 0 < int(raw_reconnect_id) <= 9223372036854775807:
            return jsonify({"code": 400, "msg": "重连账号 ID 无效", "data": None}), 400
        reconnect_id = int(raw_reconnect_id)
        with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
            account = conn.execute('SELECT type, userName FROM user_info WHERE id=?', (reconnect_id,)).fetchone()
        if account is None:
            return jsonify({"code": 404, "msg": "重连账号不存在", "data": None}), 404
        if int(account[0]) != int(login_type):
            return jsonify({"code": 409, "msg": "重连账号的平台不匹配", "data": None}), 409
        account_name = account_name or str(account[1] or '').strip()
    if not account_name:
        return jsonify({"code": 400, "msg": "账号名不能为空", "data": None}), 400
    if len(account_name) > 100:
        return jsonify({"code": 400, "msg": "账号名不能超过 100 个字符", "data": None}), 400
    print(f"登录请求: type={login_type}, account_id={reconnect_id or 'new'}")

    # 模拟一个用于异步通信的队列
    status_queue = LoginStatusQueue()
    session_key = (login_type, f"account:{reconnect_id}" if reconnect_id is not None else account_name)
    with active_queues_lock:
        if session_key in active_queues:
            return jsonify(
                {"code": 409, "msg": "该账号已有登录会话进行中", "data": None}
            ), 409
        active_queues[session_key] = status_queue

    def release_session():
        with active_queues_lock:
            if active_queues.get(session_key) is status_queue:
                active_queues.pop(session_key, None)
        print(f"清理登录会话: type={login_type}, account_id={reconnect_id or 'new'}")
    def on_close():
        # Closing an SSE response does not mean its browser worker has exited.
        # Keep the duplicate guard until cleanup really completes.
        if not status_queue.terminal_sent:
            status_queue.cancel_event.set()

    def registered_worker(*args):
        try:
            run_async_function(*args)
        finally:
            release_session()

    # 启动异步任务线程
    try:
        thread = threading.Thread(
            target=registered_worker,
            args=(login_type, account_name, status_queue, reconnect_id),
            daemon=True,
        )
        thread.start()
    except Exception:
        release_session()
        return jsonify({"code": 500, "msg": "登录组件启动失败，请重试", "data": None}), 500
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
    if (
        dry_run is not True
        and not app.config.get("ALLOW_LEGACY_DIRECT_PUBLISHING", False)
    ):
        return jsonify(
            {
                "code": 410,
                "msg": (
                    "旧版直发入口已停用，请在发布中心创建可审计的渠道任务；"
                    "仅预览 dryRun 仍可使用"
                ),
                "data": {"reason_code": "legacy_direct_publish_disabled"},
            }
        ), 410
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
    if (
        any(
            isinstance(item, dict) and item.get("dryRun") is not True
            for item in data_list
        )
        and not app.config.get("ALLOW_LEGACY_DIRECT_PUBLISHING", False)
    ):
        return jsonify(
            {
                "code": 410,
                "msg": (
                    "旧版批量直发入口已停用，请在发布中心逐平台创建任务；"
                    "仅全部为 dryRun 时允许预览"
                ),
                "data": {"reason_code": "legacy_direct_publish_disabled"},
            }
        ), 410

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
def run_async_function(type,id,status_queue,account_id=None):
    status_queue.put("LOGIN_OPENING")
    try:
        # Optional browser/platform dependencies must fail through the SSE stream,
        # including imports that fail before any platform coroutine starts.
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
        check_login_cancelled(status_queue)
        runners = {
            "1": xiaohongshu_cookie_gen, "2": get_tencent_cookie,
            "3": douyin_cookie_gen, "4": get_ks_cookie,
            "5": baijiahao_cookie_gen, "6": bilibili_cookie_gen,
            "7": toutiao_cookie_gen, "8": sohu_cookie_gen,
            "9": zhihu_cookie_gen, "10": tiktok_cookie_gen,
        }
        runner = runners.get(str(type))
        if runner is None:
            report_login_error(status_queue, code="login_failed")
            return
        login_storage = {
            "database_path": app.config["DATABASE_PATH"],
            "cookies_directory": app.config["COOKIES_DIRECTORY"],
        }
        if account_id is not None:
            login_storage["account_id"] = account_id
        asyncio.run(runner(id, status_queue, **login_storage))
    except (ModuleNotFoundError, ImportError) as exc:
        # Retain the existing safe component hint for older clients.
        component = re.sub(r"[^A-Za-z0-9_.-]", "", str(getattr(exc, "name", None) or "browser-runtime"))[:80]
        status_queue.put(f"COMPONENT_MISSING:{component}")
        report_login_error(status_queue, code="component_missing")
    except Exception as exc:
        report_login_error(status_queue, exc)
    finally:
        if isinstance(status_queue, LoginStatusQueue) and not status_queue.terminal_sent:
            report_login_error(status_queue, code="login_failed")

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
