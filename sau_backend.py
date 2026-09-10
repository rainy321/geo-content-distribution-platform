import asyncio
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import uuid
import weakref
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty, Full, Queue
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
from services.ai_cost_ledger_service import (
    AIUsageLedgerError,
    get_ai_usage_records_by_references,
    get_project_ai_cost_summary,
    record_ai_usage,
    summarize_ai_usage_records,
)
from services.ai_visibility_service import (
    VisibilityAnalysisError,
    analyze_ai_visibility,
)
from services.ai_visibility_run_service import (
    VisibilityRunDataError,
    VisibilityRunNotFoundError,
    VisibilityRunValidationError,
    get_ai_visibility_run,
    list_ai_visibility_runs,
    save_ai_visibility_run,
)
from services.ai_visibility_sampling_service import (
    VisibilitySamplingConflictError,
    VisibilitySamplingInProgressError,
    VisibilitySamplingValidationError,
    run_ai_visibility_sample,
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
from services.geo_article_compiler_service import (
    GEOArticleCompilerError,
    build_geo_article_blueprint,
    score_ai_citeability,
)
from services.geo_diagnostic_service import (
    GEODiagnosticError,
    GEODiagnosticNotFoundError,
    diagnose_geo_readiness,
    get_geo_diagnostic_run,
    list_geo_diagnostic_runs,
)
from services.dashboard_service import get_dashboard_overview
from services.demo_seed_service import seed_demo_data
from services.local_auth_service import (
    InvalidCurrentPasswordError,
    LocalAuthConfigurationError,
    LocalAuthService,
    PasswordAlreadyConfiguredError,
    PasswordPolicyError,
)
from services.media_account_service import (
    DISCONNECTED_ACCOUNT_STATUS,
    MediaAccountBusyError,
    MediaAccountCheckError,
    MediaAccountDisconnectError,
    MediaAccountInvalidCookiePathError,
    MediaAccountNotFoundError,
    MediaAccountRuntimeDisabledError,
    check_media_account,
    cleanup_disconnected_account_files,
    disconnect_media_account,
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
from services.publish_job_executor import (
    PublishConfirmationMismatchError,
    execute_publish_job,
)
from services.publish_job_service import (
    PUBLISH_JOB_STATUSES,
    InvalidPublishJobTransitionError,
    PublishJobNotFoundError,
    UnsupportedPublishPlatformError,
    build_publish_confirmation_fingerprint,
    create_publish_job,
    get_publish_job,
    get_publish_job_with_confirmation_snapshot,
    list_publish_jobs,
    normalize_publish_images,
    normalize_publish_video,
    reconcile_publish_job_failure,
    reconcile_publish_job_platform_success,
    retry_publish_job,
)
from services.publish_preflight_service import (
    PublishContentLimitError,
    build_platform_publish_variant,
    get_platform_frequency_guidance,
    get_publish_content_policy,
    get_publish_title_policy,
    get_platform_verification_policy,
)
from services.publish_scheduler_runtime import create_publish_scheduler
from services.real_publisher_factory import (
    REAL_PUBLISH_PLATFORMS,
    create_real_publisher_factory,
)
from services.request_guard import create_rate_limiter_from_environment
from utils.log import login_logger

class LoginStatusQueue(Queue):
    """SSE event queue with a cooperative browser-login cancellation signal."""

    def __init__(
        self,
        *,
        protocol_version=1,
        existing_account_id=None,
        client_session_id=None,
    ):
        self.protocol_version = int(protocol_version)
        super().__init__(
            maxsize=(
                LOGIN_COMPAT_QUEUE_MAX_COUNT if self.protocol_version >= 2 else 0
            )
        )
        self.existing_account_id = existing_account_id
        self.client_session_id = str(client_session_id or "")
        self.cancel_event = threading.Event()
        self.finished_event = threading.Event()
        self._outcome_lock = threading.Lock()
        self._cancel_requested = False
        self._outcome = "pending"
        # Protocol 2 streams can reconnect after a short desktop/network hop.
        # Keep a tiny per-login journal so a resumed EventSource cannot miss the
        # success/failure event that raced with its transport disconnect.
        self._event_condition = threading.Condition()
        self._event_log = []
        self._event_log_bytes = 0
        self._next_event_id = 1
        self.finished_at = None

    def put(self, item, block=True, timeout=None):
        if self.protocol_version >= 2:
            item = _bounded_login_event_payload(item)
        terminal = str(item)
        with self._outcome_lock:
            if self._outcome == "pending" and terminal == "200":
                self._outcome = "success"
            elif self._outcome == "pending" and terminal == "500":
                self._outcome = (
                    "cancelled" if self._cancel_requested else "failed"
                )
        if self.protocol_version >= 2:
            # Protocol 2 reads from the replay journal rather than consuming
            # this compatibility Queue. Keep the latter bounded as well so a
            # long-running headed login cannot accumulate producer output.
            try:
                result = super().put(item, block=False)
            except Full:
                try:
                    super().get_nowait()
                except Empty:
                    pass
                result = super().put(item, block=False)
        else:
            result = super().put(item, block=block, timeout=timeout)
        with self._event_condition:
            event_id = self._next_event_id
            self._next_event_id += 1
            self._event_log.append((event_id, item))
            self._event_log_bytes += len(terminal.encode("utf-8"))
            while (
                len(self._event_log) > LOGIN_EVENT_MAX_COUNT
                or self._event_log_bytes > LOGIN_EVENT_MAX_TOTAL_BYTES
            ):
                _removed_id, removed_item = self._event_log.pop(0)
                self._event_log_bytes -= len(str(removed_item).encode("utf-8"))
            self._event_condition.notify_all()
        return result

    def wait_for_events(self, after_event_id, *, timeout=0.5):
        """Return replayable events after one SSE cursor without consuming them."""

        cursor = max(0, int(after_event_id or 0))
        with self._event_condition:
            events = [event for event in self._event_log if event[0] > cursor]
            if not events and not self.finished_event.is_set():
                self._event_condition.wait(timeout=max(0.0, float(timeout)))
                events = [event for event in self._event_log if event[0] > cursor]
            return events

    def wake_stream_waiters(self):
        with self._event_condition:
            self._event_condition.notify_all()

    def cancel(self):
        with self._outcome_lock:
            self._cancel_requested = True
        self.cancel_event.set()

    def mark_finished(self):
        with self._outcome_lock:
            if self._outcome == "pending":
                self._outcome = (
                    "cancelled" if self._cancel_requested else "failed"
                )
            self.finished_at = time.monotonic()
        self.finished_event.set()
        with self._event_condition:
            self._event_condition.notify_all()

    @property
    def outcome(self):
        with self._outcome_lock:
            return self._outcome


active_queues = {}
active_queues_lock = threading.Lock()
active_account_checks = set()
recent_login_queues = {}
LOGIN_SESSION_REPLAY_TTL_SECONDS = 60.0
LOGIN_EVENT_MAX_COUNT = 128
LOGIN_EVENT_MAX_ITEM_BYTES = 256 * 1024
LOGIN_EVENT_MAX_TOTAL_BYTES = 1024 * 1024
# Protocol 2 is served from the byte-bounded replay journal; this inherited
# Queue exists only for helper/test compatibility.  Keep just a tiny tail so
# QR payloads are not duplicated 128 times in memory.
LOGIN_COMPAT_QUEUE_MAX_COUNT = 4
# The desktop host has eight Waitress threads. Reserve at least half for API
# requests and cancellation/status calls while headed logins are waiting.
LOGIN_MAX_ACTIVE_STREAMS = 4
active_login_stream_transports = {}
app = Flask(__name__)


def _bounded_login_event_payload(item):
    text = str(item)
    if len(text.encode("utf-8")) <= LOGIN_EVENT_MAX_ITEM_BYTES:
        return item
    return json.dumps(
        {
            "type": "error",
            "code": "event_payload_too_large",
            "message": "登录页面返回的数据过大，已停止在软件内展示，请关闭登录窗口后重试",
            "action": "关闭登录窗口并重试；如持续出现，请查看本机登录日志",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _acquire_login_stream_transport_locked(status_queue):
    """Install one replaceable SSE lease while active_queues_lock is held."""

    current = active_login_stream_transports.get(status_queue)
    if (
        current is None
        and len(active_login_stream_transports) >= LOGIN_MAX_ACTIVE_STREAMS
    ):
        return None
    token = object()
    active_login_stream_transports[status_queue] = token
    if current is not None:
        wake_waiters = getattr(status_queue, "wake_stream_waiters", None)
        if callable(wake_waiters):
            wake_waiters()
    return token


def _is_login_stream_transport_current(status_queue, token):
    with active_queues_lock:
        return active_login_stream_transports.get(status_queue) is token


def _release_login_stream_transport(status_queue, token):
    with active_queues_lock:
        if active_login_stream_transports.get(status_queue) is token:
            active_login_stream_transports.pop(status_queue, None)


def _normalize_login_client_session_id(value):
    session_id = str(value or "").strip()
    if not session_id:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", session_id):
        raise ValueError("登录会话标识无效")
    return session_id


def _prune_recent_login_queues(now=None):
    """Drop bounded protocol-2 replay state while holding active_queues_lock."""

    current = time.monotonic() if now is None else float(now)
    expired = [
        key
        for key, status_queue in recent_login_queues.items()
        if status_queue.finished_at is None
        or current - status_queue.finished_at > LOGIN_SESSION_REPLAY_TTL_SECONDS
    ]
    for key in expired:
        recent_login_queues.pop(key, None)
    if len(recent_login_queues) > 128:
        oldest = sorted(
            recent_login_queues,
            key=lambda key: recent_login_queues[key].finished_at or 0.0,
        )[: len(recent_login_queues) - 128]
        for key in oldest:
            recent_login_queues.pop(key, None)


def _expire_recent_login_queue(replay_key, status_queue_ref, *, now=None):
    """Expire one exact replay entry even when no later HTTP request arrives."""

    status_queue = (
        status_queue_ref()
        if isinstance(status_queue_ref, weakref.ReferenceType)
        else status_queue_ref
    )
    if status_queue is None:
        return
    current = time.monotonic() if now is None else float(now)
    with active_queues_lock:
        registered = recent_login_queues.get(replay_key)
        finished_at = getattr(status_queue, "finished_at", None)
        if (
            registered is status_queue
            and finished_at is not None
            and current - finished_at >= LOGIN_SESSION_REPLAY_TTL_SECONDS
        ):
            recent_login_queues.pop(replay_key, None)


def _schedule_recent_login_queue_expiry(replay_key, status_queue):
    timer = threading.Timer(
        LOGIN_SESSION_REPLAY_TTL_SECONDS,
        _expire_recent_login_queue,
        # Do not let an already-pruned queue stay alive merely because its
        # expiry timer has not fired yet.
        args=(replay_key, weakref.ref(status_queue)),
    )
    timer.daemon = True
    timer.start()


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
_real_publishing_authorized_value = os.getenv(
    "GEO_REAL_PUBLISHING_AUTHORIZED"
)
app.config["REAL_PUBLISHING_AUTHORIZED"] = (
    None
    if _real_publishing_authorized_value is None
    else _environment_flag("GEO_REAL_PUBLISHING_AUTHORIZED")
)
app.config["ENABLE_BILIBILI_RUNTIME"] = _environment_flag(
    "ENABLE_BILIBILI_RUNTIME"
)
app.config["ALLOW_LEGACY_DIRECT_PUBLISHING"] = _environment_flag(
    "ALLOW_LEGACY_DIRECT_PUBLISHING"
)
app.config["PUBLISHING_RUNTIME_READY"] = _environment_flag(
    "GEO_PUBLISHING_RUNTIME_READY",
    default=not _environment_flag("GEO_DESKTOP_MODE"),
)
# A future two-phase authorization flow may safely enable unattended real
# publishing.  This release deliberately keeps the capability fail-closed.
app.config["SCHEDULED_REAL_PUBLISHING_ENABLED"] = False


def _real_publishing_authorized():
    """Return immutable desktop intent, or the web deployment switch."""

    configured = app.config.get("REAL_PUBLISHING_AUTHORIZED")
    if configured is None:
        return bool(app.config.get("ALLOW_REAL_PUBLISHING", False))
    return bool(configured)


def _scheduled_real_publishing_allowed():
    """Require every safety gate before a background worker may load adapters."""

    return bool(
        app.config.get("SCHEDULED_REAL_PUBLISHING_ENABLED", False)
        and _real_publishing_authorized()
        and app.config.get("PUBLISHING_RUNTIME_READY", False)
        and app.config.get("ALLOW_REAL_PUBLISHING", False)
        and not app.config.get("DEMO_MODE", False)
    )


access_password = str(os.getenv("APP_ACCESS_PASSWORD", "")).strip()
session_secret = str(os.getenv("APP_SESSION_SECRET", "")).strip()
desktop_mode = _environment_flag("GEO_DESKTOP_MODE")
local_auth_service = None
if desktop_mode:
    local_auth_path = str(os.getenv("GEO_DESKTOP_AUTH_PATH", "")).strip()
    if not local_auth_path:
        raise RuntimeError("桌面模式缺少 GEO_DESKTOP_AUTH_PATH")
    local_auth_service = LocalAuthService(local_auth_path)
    # The persisted per-user secret is authoritative for the desktop session.
    session_secret = local_auth_service.get_session_secret()
    access_password = ""
if access_password and not session_secret:
    raise RuntimeError(
        "配置 APP_ACCESS_PASSWORD 时必须同时配置 APP_SESSION_SECRET"
    )
rate_limiter = create_rate_limiter_from_environment()
app.config.update(
    ACCESS_CONTROL_ENABLED=bool(access_password or local_auth_service),
    ACCESS_PASSWORD=access_password,
    DESKTOP_MODE=desktop_mode,
    LOCAL_AUTH_SERVICE=local_auth_service,
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
    "setup_local_operator",
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


def _local_auth_setup_required():
    service = app.config.get("LOCAL_AUTH_SERVICE")
    return bool(service is not None and service.status().setup_required)


def _mark_operator_authenticated():
    session.clear()
    session.permanent = True
    session["operator_authenticated"] = True


@app.before_request
def require_operator_access():
    if request.method == "OPTIONS" or request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if _operator_is_authenticated():
        return None
    setup_required = _local_auth_setup_required()
    return jsonify(
        {
            "code": 401,
            "msg": "需要先设置管理员口令" if setup_required else "需要输入管理员口令",
            "data": {
                "authentication_required": True,
                "setup_required": setup_required,
            },
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
    setup_required = _local_auth_setup_required()
    return jsonify(
        {
            "code": 200,
            "msg": "success",
            "data": {
                "required": required,
                "setup_required": setup_required,
                "mode": (
                    "desktop_local"
                    if app.config.get("LOCAL_AUTH_SERVICE") is not None
                    else ("environment" if required else "disabled")
                ),
                "authenticated": bool(
                    not required
                    or (not setup_required and _operator_is_authenticated())
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
    candidate = password if isinstance(password, str) else ""
    local_auth = app.config.get("LOCAL_AUTH_SERVICE")
    if local_auth is not None and local_auth.status().setup_required:
        return jsonify(
            {
                "code": 409,
                "msg": "请先设置本机管理员口令",
                "data": {
                    "authenticated": False,
                    "required": True,
                    "setup_required": True,
                },
            }
        ), 409
    expected = str(app.config.get("ACCESS_PASSWORD", ""))
    accepted = (
        local_auth.verify_password(candidate)
        if local_auth is not None
        else bool(candidate)
        and len(candidate) <= 512
        and hmac.compare_digest(candidate, expected)
    )
    if not accepted:
        return jsonify(
            {
                "code": 401,
                "msg": "管理员口令不正确",
                "data": {
                    "authenticated": False,
                    "required": True,
                    "setup_required": False,
                },
            }
        ), 401

    _mark_operator_authenticated()
    return jsonify(
        {
            "code": 200,
            "msg": "success",
            "data": {
                "authenticated": True,
                "required": True,
                "setup_required": False,
            },
        }
    ), 200


@app.route('/api/auth/setup', methods=['POST'])
def setup_local_operator():
    """Create the desktop administrator password exactly once."""

    local_auth = app.config.get("LOCAL_AUTH_SERVICE")
    if local_auth is None:
        return jsonify(
            {"code": 404, "msg": "此部署不支持本机口令初始化", "data": None}
        ), 404

    decision = app.config["AUTH_RATE_LIMITER"].consume(
        f"setup:{_client_rate_key()}",
        limit=app.config["AUTH_LOGIN_ATTEMPTS_PER_MINUTE"],
    )
    if not decision.allowed:
        response = jsonify(
            {"code": 429, "msg": "设置尝试过于频繁，请稍后再试", "data": None}
        )
        response.headers["Retry-After"] = str(decision.retry_after_seconds)
        return response, 429

    data = request.get_json(silent=True)
    password = data.get("password") if isinstance(data, dict) else None
    try:
        local_auth.set_initial_password(password)
    except PasswordPolicyError as exc:
        return jsonify(
            {
                "code": 400,
                "msg": str(exc),
                "data": {"policy_code": exc.code, "setup_required": True},
            }
        ), 400
    except PasswordAlreadyConfiguredError:
        return jsonify(
            {
                "code": 409,
                "msg": "管理员口令已经设置，请直接登录",
                "data": {"setup_required": False},
            }
        ), 409
    except LocalAuthConfigurationError:
        return jsonify(
            {"code": 500, "msg": "本地认证配置不可用", "data": None}
        ), 500

    _mark_operator_authenticated()
    return jsonify(
        {
            "code": 200,
            "msg": "success",
            "data": {
                "authenticated": True,
                "required": True,
                "setup_required": False,
            },
        }
    ), 200


@app.route('/api/auth/change-password', methods=['POST'])
def change_local_operator_password():
    """Rotate the desktop password after current-session authentication."""

    local_auth = app.config.get("LOCAL_AUTH_SERVICE")
    if local_auth is None:
        return jsonify(
            {"code": 404, "msg": "此部署不支持修改本机口令", "data": None}
        ), 404
    decision = app.config["AUTH_RATE_LIMITER"].consume(
        f"change-password:{_client_rate_key()}",
        limit=app.config["AUTH_LOGIN_ATTEMPTS_PER_MINUTE"],
    )
    if not decision.allowed:
        response = jsonify(
            {"code": 429, "msg": "修改尝试过于频繁，请稍后再试", "data": None}
        )
        response.headers["Retry-After"] = str(decision.retry_after_seconds)
        return response, 429

    data = request.get_json(silent=True)
    current_password = data.get("current_password") if isinstance(data, dict) else None
    new_password = data.get("new_password") if isinstance(data, dict) else None
    try:
        local_auth.change_password(current_password, new_password)
    except PasswordPolicyError as exc:
        return jsonify(
            {"code": 400, "msg": str(exc), "data": {"policy_code": exc.code}}
        ), 400
    except InvalidCurrentPasswordError:
        return jsonify(
            {"code": 401, "msg": "当前管理员口令不正确", "data": None}
        ), 401
    except LocalAuthConfigurationError:
        return jsonify(
            {"code": 500, "msg": "本地认证配置不可用", "data": None}
        ), 500

    return jsonify(
        {
            "code": 200,
            "msg": "success",
            "data": {"authenticated": True, "setup_required": False},
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
                "real_publishing_authorized": _real_publishing_authorized(),
                "bilibili_runtime_enabled": bool(
                    app.config.get("ENABLE_BILIBILI_RUNTIME", False)
                ),
                "publishing_runtime_ready": bool(
                    app.config.get("PUBLISHING_RUNTIME_READY", False)
                ),
                "legacy_direct_publishing_enabled": bool(
                    app.config.get("ALLOW_LEGACY_DIRECT_PUBLISHING", False)
                ),
                "scheduled_real_publishing_enabled": bool(
                    app.config.get("SCHEDULED_REAL_PUBLISHING_ENABLED", False)
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
BATCH_GENERATION_LIMIT = 20
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
    if len(normalized_topics) > BATCH_GENERATION_LIMIT:
        return jsonify(
            {
                "code": 400,
                "msg": f"一次最多批量生成 {BATCH_GENERATION_LIMIT} 篇文章",
                "data": None,
            }
        ), 400

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
                            "generation_run_id": int(current_run["id"]),
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
                    "generation_run_id": run_id,
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
                    "generation_run_id": getattr(exc, "run_id", None),
                }
            )

    run_reference_ids = [
        item["generation_run_id"]
        for item in items
        if item.get("generation_run_id") is not None
    ]
    usage_by_run = get_ai_usage_records_by_references(
        app.config["DATABASE_PATH"],
        operation="generate",
        reference_type="generation_run",
        reference_ids=run_reference_ids,
    )
    usage_records = []
    for item in items:
        run_id = item.get("generation_run_id")
        usage_record = usage_by_run.get(str(run_id)) if run_id is not None else None
        if usage_record is not None:
            item["cost_receipt"] = {"recorded": True, **usage_record}
            usage_records.append(usage_record)
        else:
            item["cost_receipt"] = {
                "recorded": False,
                "usage_status": "unknown",
                "cost_status": "not_recorded",
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "cached_tokens": None,
                "reasoning_tokens": None,
                "estimated_cost": None,
                "currency": "",
                "reason": "no_ledger_receipt",
                "message": "本项没有可用的模型用量回执，Token 与成本保持未知。",
            }

    cost_summary = summarize_ai_usage_records(
        usage_records,
        expected_item_count=len(items),
    )

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
                "batch_limit": BATCH_GENERATION_LIMIT,
                "execution_mode": "sequential",
                "cost_summary": cost_summary,
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
    visibility_guidance = _get_latest_visibility_guidance(project_id)
    if visibility_guidance is not None:
        project = {
            **project,
            "latest_visibility_result": visibility_guidance,
        }
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


@app.route('/api/ai-usage/projects/<int:project_id>/summary', methods=['GET'])
def get_project_ai_usage_summary(project_id):
    """Return model-only token/cost totals; publishing is explicitly excluded."""

    try:
        limit = int(str(request.args.get("limit", "50")).strip())
        summary = get_project_ai_cost_summary(
            app.config["DATABASE_PATH"],
            project_id=project_id,
            limit=limit,
        )
    except AIUsageLedgerError as exc:
        status = 404 if "不存在" in str(exc) else 400
        return jsonify({"code": status, "msg": str(exc), "data": None}), status
    except ValueError:
        return jsonify({"code": 400, "msg": "limit 必须是正整数", "data": None}), 400
    return jsonify({"code": 200, "msg": "success", "data": summary}), 200


@app.route('/api/dashboard', methods=['GET'])
def get_dashboard_data():
    overview = get_dashboard_overview(app.config["DATABASE_PATH"])
    return jsonify({"code": 200, "msg": "success", "data": overview}), 200


@app.route('/api/media-accounts', methods=['GET'])
def get_media_accounts():
    cleanup_result = cleanup_disconnected_account_files(
        app.config["DATABASE_PATH"],
        app.config["COOKIES_DIRECTORY"]
    )
    if cleanup_result["remaining"]:
        app.logger.warning(
            "media account credential cleanup still pending count=%s",
            cleanup_result["remaining"],
        )
    overview = get_media_accounts_overview(
        app.config["DATABASE_PATH"],
        cookies_directory=app.config["COOKIES_DIRECTORY"],
    )
    return jsonify({"code": 200, "msg": "success", "data": overview}), 200


@app.route('/api/media-accounts/<int:account_id>/check', methods=['POST'])
async def check_media_account_status(account_id):
    # The checker loads Playwright, so keep it out of the serverless cold-start
    # path. Account/browser operations are only supported by the local worker.
    if not _real_publishing_authorized():
        return jsonify(
            {"code": 403, "msg": "当前桌面构建未授权媒体账号检测", "data": None}
        ), 403
    if not app.config.get("PUBLISHING_RUNTIME_READY", False):
        return jsonify(
            {
                "code": 503,
                "msg": "本机发布运行时未就绪，请安装 Google Chrome 或重新安装完整发布版",
                "data": None,
            }
        ), 503
    if not app.config.get("ALLOW_REAL_PUBLISHING", False):
        return jsonify(
            {"code": 403, "msg": "媒体账号检测开关未开启", "data": None}
        ), 403
    from myUtils.auth import check_cookie

    try:
        with active_queues_lock:
            with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
                account_identity = conn.execute(
                    """
                    SELECT type, userName, status
                    FROM user_info
                    WHERE id = ?
                    """,
                    (account_id,),
                ).fetchone()
            session_key = (
                (str(account_identity[0]), str(account_identity[1]))
                if account_identity is not None
                else None
            )
            if session_key is not None and session_key in active_queues:
                return jsonify(
                    {"code": 409, "msg": "该账号正在登录，暂不能检测", "data": None}
                ), 409
            if account_id in active_account_checks:
                return jsonify(
                    {"code": 409, "msg": "该账号正在检测，请稍候", "data": None}
                ), 409
            active_account_checks.add(account_id)
    except sqlite3.Error:
        app.logger.exception("media account check identity lookup failed")
        return jsonify(
            {"code": 500, "msg": "无法读取媒体账号，请稍后重试", "data": None}
        ), 500

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
    finally:
        with active_queues_lock:
            active_account_checks.discard(account_id)
    return jsonify({"code": 200, "msg": "success", "data": account}), 200


@app.route('/api/media-accounts/<int:account_id>/disconnect', methods=['POST'])
def disconnect_media_account_binding(account_id):
    """Remove a local platform binding without contacting the platform."""

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get("confirm_unbind") is not True:
        return jsonify(
            {
                "code": 400,
                "msg": "需要明确确认本机退出解绑",
                "data": None,
            }
        ), 400

    try:
        # The same lifecycle mutex also guards check registration and existing-
        # account login registration.  Keep validation and the durable disable
        # in one critical section so neither operation can slip through a
        # check-then-act window.
        with active_queues_lock:
            with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
                account_identity = conn.execute(
                    "SELECT type, userName, status FROM user_info WHERE id = ?",
                    (account_id,),
                ).fetchone()
            if (
                account_identity is None
                or int(account_identity[2]) == DISCONNECTED_ACCOUNT_STATUS
            ):
                return jsonify(
                    {"code": 404, "msg": "媒体账号不存在或已解绑", "data": None}
                ), 404
            session_key = (str(account_identity[0]), str(account_identity[1]))
            if session_key in active_queues:
                return jsonify(
                    {
                        "code": 409,
                        "msg": "该账号正在登录，请先点击“结束本次登录”",
                        "data": None,
                    }
                ), 409
            if account_id in active_account_checks:
                return jsonify(
                    {
                        "code": 409,
                        "msg": "该账号正在检测，请等待检测结束后再退出解绑",
                        "data": None,
                    }
                ), 409
            result = disconnect_media_account(
                app.config["DATABASE_PATH"],
                account_id,
                cookies_directory=app.config["COOKIES_DIRECTORY"],
            )
    except sqlite3.Error:
        app.logger.exception("media account disconnect database failure")
        return jsonify(
            {"code": 500, "msg": "解绑未完成，请稍后重试", "data": None}
        ), 500
    except MediaAccountNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except (MediaAccountInvalidCookiePathError, MediaAccountBusyError) as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
    except (MediaAccountDisconnectError, OSError, sqlite3.Error):
        app.logger.exception("media account disconnect failed account_id=%s", account_id)
        return jsonify(
            {
                "code": 500,
                "msg": "解绑未完成；软件未主动删除该账号凭证，请稍后重试",
                "data": None,
            }
        ), 500

    if result["cleanup_pending"]:
        return jsonify(
            {
                "code": 202,
                "msg": "账号已安全禁用，但本机凭证文件仍待清理；关闭占用程序后刷新页面，软件会继续清理",
                "data": result,
            }
        ), 202
    return jsonify(
        {
            "code": 200,
            "msg": "已从本机退出并解绑媒体账号",
            "data": result,
        }
    ), 200


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


def _get_latest_visibility_guidance(project_id):
    """Return only rule metadata safe for the local pre-publish compiler.

    Captured model answers and evidence excerpts deliberately stay out of the
    article blueprint so third-party text cannot become a generation prompt.
    """

    with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
        row = conn.execute(
            """
            SELECT result_json
            FROM ai_visibility_runs
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (int(project_id),),
        ).fetchone()
    if row is None or not row[0]:
        return None
    try:
        result = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict):
        return None

    versions = result.get("algorithm_versions")
    summary = result.get("summary")
    confidence = summary.get("confidence") if isinstance(summary, dict) else None
    recommendation_details = result.get("recommendation_details")
    return {
        "algorithm_versions": {
            "integration": str(versions.get("integration") or "")
            if isinstance(versions, dict)
            else ""
        },
        "summary": {
            "sample_count": summary.get("sample_count", 0)
            if isinstance(summary, dict)
            else 0,
            "confidence": {
                "level": str(confidence.get("level") or "")
                if isinstance(confidence, dict)
                else ""
            },
        },
        "recommendation_details": [
            {"rule_id": str(item.get("rule_id") or "").strip()}
            for item in recommendation_details or []
            if isinstance(item, dict) and str(item.get("rule_id") or "").strip()
        ][:20],
    }


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


@app.route('/api/geo/visibility/analyze', methods=['POST'])
def analyze_geo_visibility():
    """Analyze user-supplied AI answers without invoking an external model."""

    maximum_request_bytes = 2 * 1024 * 1024
    if (
        request.content_length is not None
        and request.content_length > maximum_request_bytes
    ):
        return jsonify(
            {
                "code": 413,
                "msg": "AI 可见度分析请求不能超过 2 MB",
                "data": None,
            }
        ), 413

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400
    persist = data.get("persist", False)
    if not isinstance(persist, bool):
        return jsonify({"code": 400, "msg": "persist 必须是布尔值", "data": None}), 400

    raw_project_id = data.get("project_id")
    if isinstance(raw_project_id, bool):
        return jsonify({"code": 400, "msg": "project_id 必须是正整数", "data": None}), 400
    try:
        project_id = int(raw_project_id)
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "project_id 必须是正整数", "data": None}), 400
    if project_id <= 0 or str(raw_project_id).strip() != str(project_id):
        return jsonify({"code": 400, "msg": "project_id 必须是正整数", "data": None}), 400

    project = _get_project(project_id)
    if project is None:
        return jsonify({"code": 404, "msg": "品牌项目不存在", "data": None}), 404

    try:
        result = analyze_ai_visibility(
            project_id=project_id,
            project_name=project.get("name") or "",
            project_product=project.get("product") or "",
            project_website=project.get("website") or "",
            stored_competitors=project.get("competitors") or [],
            prompt=data.get("prompt"),
            responses=data.get("responses"),
            aliases=data.get("aliases") if "aliases" in data else None,
            competitors=data.get("competitors") if "competitors" in data else None,
        )
    except VisibilityAnalysisError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    if persist:
        try:
            saved = save_ai_visibility_run(
                app.config["DATABASE_PATH"],
                project_id=project_id,
                prompt=result["prompt"],
                input_samples=data.get("responses"),
                coverage_items={
                    "aliases": data.get("aliases") if "aliases" in data else None,
                    "competitors": (
                        data.get("competitors") if "competitors" in data else None
                    ),
                },
                result=result,
                algorithm_version=result["algorithm_versions"]["integration"],
            )
        except (VisibilityRunValidationError, ProjectNotFoundError) as exc:
            return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
        result = {
            **result,
            "run_id": saved["id"],
            "created_at": saved["created_at"],
        }
    return jsonify({"code": 200, "msg": "success", "data": result}), 200


@app.route('/api/geo/visibility/sample', methods=['POST'])
def sample_geo_visibility():
    """Run explicitly confirmed live samples against the configured AI model."""

    maximum_request_bytes = 128 * 1024
    if (
        request.content_length is not None
        and request.content_length > maximum_request_bytes
    ):
        return jsonify(
            {
                "code": 413,
                "msg": "AI 可见度采样请求不能超过 128 KB",
                "data": None,
            }
        ), 413

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400
    allowed_fields = {
        "project_id",
        "prompt",
        "sample_count",
        "aliases",
        "competitors",
        "persist",
        "confirm_external_call",
        "ai_config",
        "idempotency_key",
    }
    unsupported_fields = sorted(set(data) - allowed_fields)
    if unsupported_fields:
        return jsonify(
            {
                "code": 400,
                "msg": "采样请求包含不支持的字段: " + ", ".join(unsupported_fields),
                "data": None,
            }
        ), 400

    raw_project_id = data.get("project_id")
    if isinstance(raw_project_id, bool):
        return jsonify({"code": 400, "msg": "project_id 必须是正整数", "data": None}), 400
    try:
        project_id = int(raw_project_id)
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "project_id 必须是正整数", "data": None}), 400
    if project_id <= 0 or str(raw_project_id).strip() != str(project_id):
        return jsonify({"code": 400, "msg": "project_id 必须是正整数", "data": None}), 400
    project = _get_project(project_id)
    if project is None:
        return jsonify({"code": 404, "msg": "品牌项目不存在", "data": None}), 404

    sample_count = data.get("sample_count", 1)
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or not 1 <= sample_count <= 5
    ):
        return jsonify(
            {
                "code": 400,
                "msg": "sample_count 必须是 1 到 5 的整数",
                "data": None,
            }
        ), 400

    request_id = str(request.headers.get("X-Request-ID") or uuid.uuid4()).strip()
    idempotency_key = str(
        request.headers.get("Idempotency-Key")
        or data.get("idempotency_key")
        or ""
    ).strip()
    if not idempotency_key:
        return jsonify(
            {
                "code": 400,
                "msg": "真实模型采样必须提供 Idempotency-Key，系统不会自动生成以免误重发",
                "data": None,
            }
        ), 400
    if len(request_id) > 128 or not re.fullmatch(
        r"[A-Za-z0-9._:-]+", request_id
    ):
        return jsonify(
            {"code": 400, "msg": "X-Request-ID 格式无效", "data": None}
        ), 400
    if len(idempotency_key) > 150 or not re.fullmatch(
        r"[A-Za-z0-9._:-]+", idempotency_key
    ):
        return jsonify(
            {"code": 400, "msg": "Idempotency-Key 格式无效", "data": None}
        ), 400
    if data.get("confirm_external_call", False) is not True:
        return jsonify(
            {
                "code": 400,
                "msg": (
                    "必须设置 confirm_external_call=true，确认只把 prompt "
                    "发送给当前配置模型"
                ),
                "data": None,
            }
        ), 400

    try:
        ai_settings = _request_ai_settings(data)
    except AIConfigurationError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    if ai_settings is None:
        try:
            ai_settings = AISettings.from_environment()
        except AIConfigurationError as exc:
            return jsonify({"code": 503, "msg": str(exc), "data": None}), 503

    rate_limit_response = _enforce_ai_rate_limit(cost=sample_count)
    if rate_limit_response is not None:
        response, status_code = rate_limit_response
        response.headers["X-Request-ID"] = request_id
        response.headers["Idempotency-Key"] = idempotency_key
        response.headers["Idempotency-Replayed"] = "false"
        return response, status_code

    try:
        sampling = run_ai_visibility_sample(
            app.config["DATABASE_PATH"],
            project=project,
            prompt=data.get("prompt"),
            sample_count=sample_count,
            aliases=data.get("aliases") if "aliases" in data else None,
            competitors=(
                data.get("competitors") if "competitors" in data else None
            ),
            persist=data.get("persist", False),
            confirm_external_call=data.get("confirm_external_call", False),
            settings=ai_settings,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
    except VisibilitySamplingValidationError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    except VisibilitySamplingConflictError as exc:
        response = jsonify({"code": 409, "msg": str(exc), "data": None})
        response.headers["X-Request-ID"] = request_id
        response.headers["Idempotency-Key"] = idempotency_key
        response.headers["Idempotency-Replayed"] = "false"
        return response, 409
    except VisibilitySamplingInProgressError as exc:
        record = exc.record
        response = jsonify(
            {
                "code": 409,
                "msg": str(exc),
                "data": {
                    "status": "need_action",
                    "need_action": True,
                    "request_id": record["request_id"],
                    "idempotency_key": record["idempotency_key"],
                    "created_at": record["created_at"],
                    "retry_policy": {
                        "automatic_retry": False,
                        "action": "先核对模型账单与原请求；不要自动重发",
                    },
                },
            }
        )
        response.headers["X-Request-ID"] = record["request_id"]
        response.headers["Idempotency-Key"] = record["idempotency_key"]
        response.headers["Idempotency-Replayed"] = "false"
        return response, 409
    except sqlite3.Error:
        response = jsonify(
            {
                "code": 503,
                "msg": "本地采样状态未能可靠保存；为避免重复扣费，系统不会自动重试",
                "data": {
                    "status": "need_action",
                    "need_action": True,
                    "request_id": request_id,
                    "idempotency_key": idempotency_key,
                    "retry_policy": {
                        "automatic_retry": False,
                        "action": "先核对模型账单和本机数据库状态，再决定是否新建请求",
                    },
                },
            }
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["Idempotency-Key"] = idempotency_key
        response.headers["Idempotency-Replayed"] = "false"
        return response, 503

    response = jsonify(sampling["payload"])
    response.headers["X-Request-ID"] = sampling["request_id"]
    response.headers["Idempotency-Key"] = sampling["idempotency_key"]
    response.headers["Idempotency-Replayed"] = (
        "true" if sampling["replayed"] else "false"
    )
    failure = (sampling["payload"].get("data") or {}).get("failure") or {}
    retry_after = failure.get("retry_after_seconds")
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response, sampling["http_status"]


@app.route('/api/geo/visibility/runs', methods=['GET'])
def list_geo_visibility_runs():
    try:
        project_id = int(str(request.args.get("project_id", "")).strip())
        limit = int(str(request.args.get("limit", "20")).strip())
        if project_id <= 0:
            raise ValueError
        rows = list_ai_visibility_runs(
            app.config["DATABASE_PATH"],
            project_id=project_id,
            limit=limit,
        )
    except VisibilityRunValidationError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    except ValueError:
        return jsonify(
            {"code": 400, "msg": "project_id 和 limit 必须是正整数", "data": None}
        ), 400
    except ProjectNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except VisibilityRunDataError:
        return jsonify(
            {"code": 500, "msg": "本地分析历史无法读取", "data": None}
        ), 500
    return jsonify({"code": 200, "msg": "success", "data": rows}), 200


@app.route('/api/geo/visibility/runs/<int:run_id>', methods=['GET'])
def get_geo_visibility_run(run_id):
    try:
        row = get_ai_visibility_run(app.config["DATABASE_PATH"], run_id)
    except (VisibilityRunNotFoundError, ProjectNotFoundError) as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except VisibilityRunValidationError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    except VisibilityRunDataError:
        return jsonify(
            {"code": 500, "msg": "本地分析历史无法读取", "data": None}
        ), 500
    return jsonify({"code": 200, "msg": "success", "data": row}), 200


@app.route('/api/geo/diagnostics', methods=['POST'])
def create_geo_diagnostic():
    """Measure a project's foundation or one content item without model calls."""

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400
    try:
        result = diagnose_geo_readiness(
            app.config["DATABASE_PATH"],
            project_id=data.get("project_id"),
            article_id=data.get("article_id"),
            title=data.get("title") if "title" in data else None,
            content=data.get("content") if "content" in data else None,
            persist=data.get("persist", True),
        )
    except GEODiagnosticError as exc:
        status = 404 if "不存在" in str(exc) else 400
        return jsonify({"code": status, "msg": str(exc), "data": None}), status
    return jsonify({"code": 200, "msg": "诊断完成", "data": result}), 200


@app.route('/api/geo/diagnostics/runs', methods=['GET'])
def get_geo_diagnostic_runs():
    try:
        rows = list_geo_diagnostic_runs(
            app.config["DATABASE_PATH"],
            project_id=request.args.get("project_id"),
            limit=request.args.get("limit", 20),
        )
    except GEODiagnosticError as exc:
        status = 404 if "不存在" in str(exc) else 400
        return jsonify({"code": status, "msg": str(exc), "data": None}), status
    return jsonify({"code": 200, "msg": "success", "data": rows}), 200


@app.route('/api/geo/diagnostics/runs/<int:run_id>', methods=['GET'])
def get_geo_diagnostic_run_detail(run_id):
    try:
        result = get_geo_diagnostic_run(app.config["DATABASE_PATH"], run_id)
    except GEODiagnosticNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    except GEODiagnosticError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    return jsonify({"code": 200, "msg": "success", "data": result}), 200


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

    score_context = None
    citeability_sources = data.get("sources", [])
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
        generation_run = get_run(
            app.config["DATABASE_PATH"],
            generation_run_id,
        )
        result_payload = (
            generation_run.get("result_payload", {})
            if isinstance(generation_run, dict)
            else {}
        )
        stored_sources = (
            result_payload.get("sources")
            if isinstance(result_payload, dict)
            else None
        )
        # A generated draft is scored only against its immutable provider
        # receipt, never a mutable client copy.  Receipts that predate the
        # sources field have no recorded evidence, so their safe value is an
        # empty list rather than client-supplied replacement evidence.
        citeability_sources = stored_sources if isinstance(stored_sources, list) else []

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
    project_snapshot = (
        score_context.get("project_snapshot", {})
        if isinstance(score_context, dict)
        else {}
    )
    try:
        result["citeability"] = score_ai_citeability(
            title=title,
            summary=str(data.get("summary") or "").strip(),
            content=content,
            brand=brand,
            product=str(
                project_snapshot.get("product") or data.get("product") or ""
            ).strip(),
            website=str(
                project_snapshot.get("website") or data.get("website") or ""
            ).strip(),
            keywords=_normalize_string_list(keywords),
            sources=citeability_sources,
        )
    except GEOArticleCompilerError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    if generation_run_id not in (None, ""):
        result["score_context"] = {
            "generation_run_id": generation_run_id,
            "brand": brand,
            "keywords": keywords,
        }
    return jsonify(result), 200


@app.route('/api/geo/content-blueprint', methods=['POST'])
def build_geo_content_blueprint():
    """Build a deterministic pre-publish GEO plan without calling an AI model."""

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    try:
        project_id = int(data.get("project_id"))
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "project_id 必须是整数", "data": None}), 400
    if project_id <= 0:
        return jsonify({"code": 400, "msg": "project_id 必须是正整数", "data": None}), 400

    topic = str(data.get("topic") or "").strip()
    if not topic:
        return jsonify({"code": 400, "msg": "文章主题不能为空", "data": None}), 400
    project = _get_project(project_id)
    if project is None:
        return jsonify({"code": 404, "msg": "品牌项目不存在", "data": None}), 404

    keywords = data.get("keywords", project.get("keywords", []))
    if not isinstance(keywords, list):
        return jsonify({"code": 400, "msg": "keywords 必须是字符串数组", "data": None}), 400
    try:
        length = int(data.get("length", 1000))
        query_count = int(data.get("query_count", 12))
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "length 和 query_count 必须是整数", "data": None}), 400
    if length not in ARTICLE_LENGTHS:
        return jsonify({"code": 400, "msg": "文章长度仅支持 600、1000、1500", "data": None}), 400
    if query_count < 6 or query_count > 30:
        return jsonify({"code": 400, "msg": "query_count 仅支持 6 到 30", "data": None}), 400

    content_type = str(data.get("content_type") or "行业科普").strip()
    if content_type not in ARTICLE_CONTENT_TYPES:
        return jsonify({"code": 400, "msg": "不支持的内容类型", "data": None}), 400
    target_platform_raw = str(
        data.get("target_platform") or data.get("targetPlatform") or ""
    ).strip()
    target_platform = normalize_platform_key(target_platform_raw)
    if target_platform_raw and not target_platform:
        return jsonify({"code": 400, "msg": "不支持的目标平台", "data": None}), 400

    compiler_project = dict(project)
    visibility_guidance = _get_latest_visibility_guidance(project_id)
    if visibility_guidance is not None:
        compiler_project["latest_visibility_result"] = visibility_guidance
    try:
        blueprint = build_geo_article_blueprint(
            project=compiler_project,
            topic=topic,
            keywords=_normalize_string_list(keywords),
            content_type=content_type,
            target_platform=target_platform,
            length=length,
            requested_query_count=query_count,
        )
    except GEOArticleCompilerError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    return jsonify({"code": 200, "msg": "success", "data": blueprint}), 200


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
    scoring_project = project
    generation_sources = []
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
        scoring_project = score_context.get("project_snapshot") or project
        generation = article.get("generation")
        if isinstance(generation, dict) and isinstance(
            generation.get("sources"), list
        ):
            generation_sources = generation["sources"]

    before_score = score_geo_content(
        title=article["title"],
        content=article["content"],
        brand=score_brand,
        keywords=score_keywords,
    )
    before_score["citeability"] = score_ai_citeability(
        title=article["title"],
        summary=article.get("summary", ""),
        content=article["content"],
        brand=score_brand,
        product=str(scoring_project.get("product") or "").strip(),
        website=str(scoring_project.get("website") or "").strip(),
        keywords=score_keywords,
        sources=generation_sources,
    )
    try:
        ai_settings = _request_ai_settings(data)
    except AIConfigurationError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    rate_limit_response = _enforce_ai_rate_limit()
    if rate_limit_response is not None:
        return rate_limit_response
    try:
        optimized_result = optimize_geo_content(
            article=article,
            score=before_score,
            settings=ai_settings,
            include_metadata=True,
        )
    except AIConfigurationError as exc:
        return jsonify({"code": 503, "msg": str(exc), "data": None}), 503
    except AIServiceError as exc:
        return jsonify({"code": 502, "msg": str(exc), "data": None}), 502

    if isinstance(optimized_result, dict):
        # Test doubles and compatibility adapters may still return the legacy
        # article-only shape. No usage is invented for that shape.
        optimized = dict(optimized_result)
        ai_usage_record = None
    else:
        optimized = dict(optimized_result.article)
        ai_usage_record = record_ai_usage(
            app.config["DATABASE_PATH"],
            operation="optimize",
            provider=optimized_result.provider,
            model=optimized_result.model,
            usage=optimized_result.usage,
            usage_status=(
                "reported" if optimized_result.usage is not None else "unknown"
            ),
            project_id=article["project_id"],
            article_id=article_id,
            reference_type="optimization_request",
            reference_id=(optimized_result.provider_request_id or str(uuid.uuid4())),
            metadata={
                "outcome": "succeeded",
                "usage_origin": (
                    "provider_response"
                    if optimized_result.usage is not None
                    else "not_reported"
                ),
            },
        )

    after_score = score_geo_content(
        title=optimized["title"],
        content=optimized["content"],
        brand=score_brand,
        keywords=score_keywords,
    )
    after_score["citeability"] = score_ai_citeability(
        title=optimized["title"],
        summary=optimized.get("summary", ""),
        content=optimized["content"],
        brand=score_brand,
        product=str(scoring_project.get("product") or "").strip(),
        website=str(scoring_project.get("website") or "").strip(),
        keywords=score_keywords,
        # Optimization does not create a new persisted source receipt. Score
        # the preview with the same immutable sources that article update will
        # reuse, so the preview cannot jump when the operator saves it.
        sources=generation_sources,
    )
    return jsonify(
        {
            "code": 200,
            "msg": "优化稿已生成，确认后再保存",
            "data": {
                "optimized": optimized,
                "before": before_score,
                "after": after_score,
                "ai_usage": ai_usage_record,
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


@app.route('/api/publish/preflight', methods=['POST'])
def preview_publish_variants():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    try:
        article_id = int(data.get("article_id"))
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "article_id 必须是整数", "data": None}), 400
    if article_id <= 0:
        return jsonify({"code": 400, "msg": "article_id 必须是正整数", "data": None}), 400

    requested_platforms = data.get("platforms")
    if not isinstance(requested_platforms, list) or not requested_platforms:
        return jsonify({"code": 400, "msg": "platforms 必须是非空数组", "data": None}), 400
    if len(requested_platforms) > len(list_platform_capabilities()):
        return jsonify({"code": 400, "msg": "platforms 数量超过支持范围", "data": None}), 400

    overrides = data.get("overrides", {})
    if not isinstance(overrides, dict):
        return jsonify({"code": 400, "msg": "overrides 必须是对象", "data": None}), 400
    try:
        article = get_article(app.config["DATABASE_PATH"], article_id)
    except ArticleNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404

    variants = []
    seen = set()
    try:
        for raw_platform in requested_platforms:
            if not isinstance(raw_platform, str):
                raise PublishContentLimitError("platforms 必须是字符串数组")
            platform = normalize_platform_key(raw_platform)
            if not platform or platform in seen:
                if platform in seen:
                    continue
                raise PublishContentLimitError(f"暂不支持发布平台：{raw_platform}")
            override = overrides.get(platform)
            if override is not None and not isinstance(override, str):
                raise PublishContentLimitError(f"{platform} 平台副本必须是字符串")
            variants.append(
                build_platform_publish_variant(
                    platform=platform,
                    title=article["title"],
                    content=article["content"],
                    tags=article.get("tags") or (),
                    publish_content=override,
                )
            )
            seen.add(platform)
    except PublishContentLimitError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400

    return jsonify(
        {
            "code": 200,
            "msg": "平台发布副本已生成，原稿未修改",
            "data": {
                "article_id": article_id,
                "article_updated_at": article.get("updated_at"),
                "variants": variants,
            },
        }
    ), 200


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

    raw_account_id = data.get("account_id")
    account_id = None
    if raw_account_id not in {None, ""}:
        if isinstance(raw_account_id, bool):
            return jsonify(
                {"code": 400, "msg": "account_id 必须是正整数", "data": None}
            ), 400
        try:
            account_id = int(raw_account_id)
        except (TypeError, ValueError):
            return jsonify(
                {"code": 400, "msg": "account_id 必须是正整数", "data": None}
            ), 400
        if account_id <= 0:
            return jsonify(
                {"code": 400, "msg": "account_id 必须是正整数", "data": None}
            ), 400

    platform = str(data.get("platform") or "").strip().lower()
    if not platform:
        return jsonify({"code": 400, "msg": "platform 不能为空", "data": None}), 400

    try:
        images = _validate_publish_images(data.get("images"))
        video = _validate_publish_video(data.get("video"))
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400

    publish_content = data.get("publish_content")
    if publish_content is not None and not isinstance(publish_content, str):
        return jsonify(
            {"code": 400, "msg": "publish_content 必须是字符串", "data": None}
        ), 400

    publish_title = data.get("publish_title")
    if publish_title is not None and not isinstance(publish_title, str):
        return jsonify(
            {"code": 400, "msg": "publish_title 必须是字符串", "data": None}
        ), 400

    capability = get_platform_capability(platform)
    if capability is None:
        return jsonify({"code": 400, "msg": "暂不支持该发布平台", "data": None}), 400
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
        and not app.config.get("SCHEDULED_REAL_PUBLISHING_ENABLED", False)
    ):
        return jsonify(
            {
                "code": 403,
                "msg": (
                    "定时真实自动发布当前关闭；请创建普通定时任务，"
                    "到期后人工确认"
                ),
                "data": {"scheduled_real_publishing_enabled": False},
            }
        ), 403
    if (
        not app.config.get("DEMO_MODE", False)
        and not app.config.get("PUBLISHING_RUNTIME_READY", False)
    ):
        return jsonify(
            {
                "code": 503,
                "msg": "本机发布运行时未就绪，未创建真实发布任务",
                "data": {
                    "action": "install_runtime",
                    "platform": platform,
                },
            }
        ), 503
    if (
        not app.config.get("DEMO_MODE", False)
        and app.config.get("ALLOW_REAL_PUBLISHING", False)
        and account_id is None
    ):
        return jsonify(
            {
                "code": 409,
                "msg": f"真实发布任务必须先选择一个{capability['name']}账号",
                "data": {
                    "action": "login_required",
                    "platform": platform,
                    "account_type": capability["account_type"],
                },
            }
        ), 409

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

    if (
        auto_execute
        and not app.config.get("DEMO_MODE", False)
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
            account_id=account_id,
            publish_title=publish_title,
            publish_content=publish_content,
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
        job, confirmation_article = get_publish_job_with_confirmation_snapshot(
            app.config["DATABASE_PATH"],
            job_id,
        )
    except PublishJobNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    return jsonify(
        {
            "code": 200,
            "msg": "success",
            "data": _publish_job_payload(
                job,
                confirmation_article=confirmation_article,
                include_confirmation_snapshot=True,
            ),
        }
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


@app.route('/api/publish/jobs/<int:job_id>/reconcile', methods=['POST'])
def reconcile_publish_task(job_id):
    """Close a platform-ambiguous result after an explicit human check.

    This endpoint never calls a publisher.  It only applies a version-checked
    ledger correction after the operator has inspected the platform backend.
    """

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "对账请求不能为空", "data": None}), 400
    if data.get("confirm") is not True or data.get("platform_checked") is not True:
        return jsonify(
            {
                "code": 400,
                "msg": "必须明确确认已在平台后台核验发布结果",
                "data": None,
            }
        ), 400

    confirmed_job_id = data.get("job_id")
    expected_state_version = data.get("state_version")
    if (
        isinstance(confirmed_job_id, bool)
        or not isinstance(confirmed_job_id, int)
        or isinstance(expected_state_version, bool)
        or not isinstance(expected_state_version, int)
        or expected_state_version < 0
    ):
        return jsonify(
            {"code": 400, "msg": "对账任务或状态版本无效", "data": None}
        ), 400
    if confirmed_job_id != job_id:
        return jsonify(
            {"code": 409, "msg": "对账任务标识不一致，请刷新后重试", "data": None}
        ), 409

    decision = data.get("decision")
    if decision not in {"published", "not_published"}:
        return jsonify(
            {"code": 400, "msg": "对账结论必须是已发布或未发布", "data": None}
        ), 400
    if (
        decision == "not_published"
        and data.get("execution_stopped_confirmed") is not True
    ):
        return jsonify(
            {
                "code": 400,
                "msg": "结案为未发布前，必须确认旧发布窗口和进程已结束并完成冷却核验",
                "data": None,
            }
        ), 400
    verification_note = data.get("verification_note")
    if not isinstance(verification_note, str):
        return jsonify(
            {"code": 400, "msg": "必须填写平台后台核验说明", "data": None}
        ), 400
    verification_note = verification_note.strip()
    if len(verification_note) < 4 or len(verification_note) > 500:
        return jsonify(
            {"code": 400, "msg": "平台后台核验说明须为 4 至 500 个字符", "data": None}
        ), 400
    result_url = data.get("result_url", "")
    if not isinstance(result_url, str) or len(result_url.strip()) > 2048:
        return jsonify(
            {"code": 400, "msg": "公开链接格式无效或过长", "data": None}
        ), 400
    result_url = result_url.strip()

    try:
        current = get_publish_job(app.config["DATABASE_PATH"], job_id)
    except PublishJobNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    if (
        current["status"] != "need_action"
        or not current.get("reconciliation_required", False)
    ):
        return jsonify(
            {
                "code": 409,
                "msg": f"状态为 {current['status']} 的任务无需人工对账，请刷新任务列表",
                "data": None,
            }
        ), 409
    if int(current.get("state_version", -1)) != expected_state_version:
        return jsonify(
            {
                "code": 409,
                "msg": "任务状态已变化，对账未写入；请刷新后重新核验",
                "data": None,
            }
        ), 409

    try:
        if decision == "published":
            job = reconcile_publish_job_platform_success(
                app.config["DATABASE_PATH"],
                job_id,
                message=f"人工核验已发布：{verification_note}",
                result_url=result_url,
                expected_state_version=expected_state_version,
            )
        else:
            if result_url:
                return jsonify(
                    {
                        "code": 400,
                        "msg": "结论为未发布时不能填写公开链接",
                        "data": None,
                    }
                ), 400
            job = reconcile_publish_job_failure(
                app.config["DATABASE_PATH"],
                job_id,
                message=f"人工核验未发布：{verification_note}",
                expected_state_version=expected_state_version,
            )
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    except InvalidPublishJobTransitionError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409

    return jsonify(
        {
            "code": 200,
            "msg": "平台结果已人工对账，未触发任何再次发布",
            "data": _publish_job_payload(job),
        }
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
    if not _real_publishing_authorized():
        return jsonify(
            {
                "code": 403,
                "msg": "当前桌面构建未授权真实发布",
                "data": None,
            }
        ), 403
    if not app.config.get("PUBLISHING_RUNTIME_READY", False):
        return jsonify(
            {
                "code": 503,
                "msg": "本机发布运行时未就绪，请安装 Google Chrome 或重新安装完整发布版",
                "data": None,
            }
        ), 503
    if not app.config.get("ALLOW_REAL_PUBLISHING", False):
        return jsonify(
            {"code": 403, "msg": "真实发布运行开关未开启", "data": None}
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
    if current.get("account_id") is None:
        return jsonify(
            {
                "code": 409,
                "msg": "该任务未绑定明确账号，禁止使用后来登录的账号执行；请重新创建任务",
                "data": {
                    "action": "recreate_with_account",
                    "platform": current["platform"],
                },
            }
        ), 409
    confirmation_fields = {
        "job_id": data.get("job_id"),
        "state_version": data.get("state_version"),
        "account_id": data.get("account_id"),
    }
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in confirmation_fields.values()
    ):
        return jsonify(
            {"code": 400, "msg": "真实发布确认信息不完整", "data": None}
        ), 400
    confirmed_job_id = confirmation_fields["job_id"]
    confirmed_state_version = confirmation_fields["state_version"]
    confirmed_account_id = confirmation_fields["account_id"]
    if (
        confirmed_job_id != job_id
        or confirmed_state_version != int(current.get("state_version", -1))
        or confirmed_account_id != int(current["account_id"])
    ):
        return jsonify(
            {
                "code": 409,
                "msg": "任务、账号或内容状态已变化，请刷新后重新确认",
                "data": None,
            }
        ), 409
    provided_fingerprint = data.get("confirmation_fingerprint")
    if not isinstance(provided_fingerprint, str) or re.fullmatch(
        r"[0-9a-f]{64}", provided_fingerprint
    ) is None:
        return jsonify(
            {"code": 400, "msg": "真实发布确认指纹缺失或格式无效", "data": None}
        ), 400
    article = get_article(app.config["DATABASE_PATH"], current["article_id"])
    expected_fingerprint = build_publish_confirmation_fingerprint(
        job=current,
        article=article,
    )
    if not hmac.compare_digest(provided_fingerprint, expected_fingerprint):
        return jsonify(
            {
                "code": 409,
                "msg": "文章内容或发布参数已变化，请刷新后重新确认",
                "data": None,
            }
        ), 409
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
            expected_state_version=confirmed_state_version,
            expected_confirmation_fingerprint=provided_fingerprint,
        )
    except PublishConfirmationMismatchError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
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


def _publish_job_payload(
    job,
    *,
    confirmation_article=None,
    include_confirmation_snapshot=False,
):
    confirmation_fingerprint = ""
    article = confirmation_article
    if not job["demo"] and job.get("account_id") is not None:
        if article is None:
            article = get_article(app.config["DATABASE_PATH"], job["article_id"])
        confirmation_fingerprint = build_publish_confirmation_fingerprint(
            job=job,
            article=article,
        )
    payload = {
        "job_id": job["id"],
        "article_id": job["article_id"],
        "account_id": job.get("account_id"),
        "account_name": str(job.get("account_name") or ""),
        "state_version": int(job.get("state_version", 0)),
        "reconciliation_required": bool(
            job.get("reconciliation_required", False)
        ),
        "confirmation_fingerprint": confirmation_fingerprint,
        "platform": job["platform"],
        "status": job["status"],
        "message": job["message"],
        "url": job["result_url"],
        "images": list(job.get("images") or ()),
        "video": str(job.get("video") or ""),
        "publish_content_length": len(str(job.get("publish_content") or "")),
        "content_policy_version": str(job.get("content_policy_version") or ""),
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
            and _real_publishing_authorized()
            and app.config.get("ALLOW_REAL_PUBLISHING", False)
            and app.config.get("PUBLISHING_RUNTIME_READY", False)
            and job.get("account_id") is not None
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
    if include_confirmation_snapshot:
        snapshot = article
        if snapshot is None:
            snapshot = get_article(
                app.config["DATABASE_PATH"],
                job["article_id"],
            )
        payload["confirmation_snapshot"] = {
            "article_id": int(snapshot["id"]),
            "title": str(snapshot.get("title") or ""),
            "content": str(snapshot.get("content") or ""),
            "tags": list(snapshot.get("tags") or ()),
            "updated_at": snapshot.get("updated_at"),
        }
        publish_title = str(
            job.get("publish_title") or snapshot.get("title") or ""
        )
        publish_content = str(
            job.get("publish_content") or snapshot.get("content") or ""
        )
        payload["publication_snapshot"] = {
            "title": publish_title,
            "content": publish_content,
            "original_title_length": len(str(snapshot.get("title") or "")),
            "title_length": len(publish_title),
            "title_adapted": publish_title != str(snapshot.get("title") or "").strip(),
            "title_policy": get_publish_title_policy(job["platform"]),
            "original_length": len(str(snapshot.get("content") or "")),
            "publish_length": len(publish_content),
            "adapted": publish_content != str(snapshot.get("content") or "").strip(),
            "content_policy": get_publish_content_policy(
                job["platform"],
                tags=snapshot.get("tags") or (),
            ),
            "verification": get_platform_verification_policy(job["platform"]),
            "frequency_guidance": get_platform_frequency_guidance(job["platform"]),
        }
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
    demo_available = bool(app.config.get("DEMO_MODE", False))
    real_publishing_authorized = _real_publishing_authorized()
    real_enabled = bool(
        real_publishing_authorized
        and app.config.get("ALLOW_REAL_PUBLISHING", False)
        and app.config.get("PUBLISHING_RUNTIME_READY", False)
        and not demo_available
    )
    capabilities = []
    for item in list_platform_capabilities():
        runtime_available = bool(
            real_enabled
            and (
                item["key"] != BILIBILI_PLATFORM_KEY
                or app.config.get("ENABLE_BILIBILI_RUNTIME", False)
            )
        )
        if item["key"] == BILIBILI_PLATFORM_KEY and not app.config.get(
            "ENABLE_BILIBILI_RUNTIME", False
        ):
            unavailable_reason = (
                "未找到通过固定版本与 SHA-256 校验的 Bilibili 本机兼容运行时"
            )
        elif demo_available:
            unavailable_reason = "当前为演示模式，不会访问真实平台"
        elif not real_publishing_authorized:
            unavailable_reason = "当前构建未授权真实发布"
        elif not app.config.get("PUBLISHING_RUNTIME_READY", False):
            unavailable_reason = "本机发布运行时未就绪，请安装 Google Chrome"
        elif not app.config.get("ALLOW_REAL_PUBLISHING", False):
            unavailable_reason = "真实发布运行开关未开启"
        else:
            unavailable_reason = ""
        capabilities.append(
            {
                **item,
                "content_policy": get_publish_content_policy(item["key"]),
                "title_policy": get_publish_title_policy(item["key"]),
                "verification": get_platform_verification_policy(item["key"]),
                "frequency_guidance": get_platform_frequency_guidance(item["key"]),
                "runtime_available": runtime_available,
                "login_available": runtime_available,
                "real_publishing_authorized": real_publishing_authorized,
                "demo_available": demo_available,
                "task_creation_available": bool(
                    demo_available or runtime_available
                ),
                "scheduled_real_publishing_enabled": bool(
                    app.config.get("SCHEDULED_REAL_PUBLISHING_ENABLED", False)
                ),
                "unavailable_reason": unavailable_reason,
            }
        )
    return jsonify(
        {"code": 200, "msg": "success", "data": capabilities}
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
        # sqlite3.Connection's context manager commits/rolls back but does not
        # close the handle.  Explicit closing is required on Windows so the
        # desktop database can be backed up, moved, or removed after upload.
        with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
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
        # sqlite3's transaction context does not close the Windows file
        # handle.  Material browsing is part of the desktop product, so close
        # explicitly to keep backups, upgrades and test cleanup reliable.
        with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
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
        with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
            SELECT * FROM user_info WHERE status != -2''')
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

    with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM user_info WHERE status != ?",
            (DISCONNECTED_ACCOUNT_STATUS,),
        )
        rows = cursor.fetchall()

    for row in rows:
        account_id = int(row[0])
        if (
            row[1] == BILIBILI_ACCOUNT_TYPE
            and not app.config.get("ENABLE_BILIBILI_RUNTIME", False)
        ):
            continue
        session_key = (str(row[1]), str(row[3]))
        with active_queues_lock:
            if (
                account_id in active_account_checks
                or session_key in active_queues
            ):
                continue
            active_account_checks.add(account_id)
        try:
            flag = await check_cookie(
                row[1],
                row[2],
                cookies_directory=app.config["COOKIES_DIRECTORY"],
            )
            new_status = 1 if flag else 0
            with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
                with conn:
                    conn.execute(
                        """
                        UPDATE user_info
                        SET status = ?
                        WHERE id = ? AND status != ?
                        """,
                        (
                            new_status,
                            account_id,
                            DISCONNECTED_ACCOUNT_STATUS,
                        ),
                    )
        finally:
            with active_queues_lock:
                active_account_checks.discard(account_id)

    # Build the response from a fresh snapshot so an account unbound while a
    # platform check was awaiting I/O cannot reappear from the stale list.
    with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
        rows_list = [
            list(row)
            for row in conn.execute(
                "SELECT * FROM user_info WHERE status != ?",
                (DISCONNECTED_ACCOUNT_STATUS,),
            ).fetchall()
        ]
    return jsonify({"code": 200, "msg": None, "data": rows_list}), 200

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
        # Close explicitly; sqlite3.Connection.__exit__ only ends the
        # transaction and otherwise leaves the database handle open.
        with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
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
                    file_path.unlink()
                    app.logger.info("material file deleted path=%s", file_path)
                except Exception as e:
                    app.logger.warning(
                        "material file delete failed path=%s error=%s",
                        file_path,
                        e,
                    )
                    # 即使删除文件失败，也要继续删除数据库记录，避免数据不一致
            else:
                app.logger.warning("material file missing path=%s", file_path)

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

    except Exception:
        app.logger.exception("delete_file failed")
        return jsonify({
            "code": 500,
            "msg": str("delete failed!"),
            "data": None
        }), 500

@app.route('/deleteAccount', methods=['GET'])
def delete_account():
    # Destructive GET requests are vulnerable to accidental navigation and
    # cross-site triggering.  Keep the legacy URL explicit but non-mutating;
    # current clients use the confirmed POST endpoint above.
    return jsonify(
        {
            "code": 405,
            "msg": "旧版删除接口已停用，请使用媒体账号页的“退出 / 解绑”",
            "data": None,
        }
    ), 405


# SSE 登录接口
@app.route('/login')
def login():
    # 1 小红书 2 视频号 3 抖音 4 快手 5 百家号 6 B站 7 今日头条
    login_type = str(request.args.get('type') or '').strip()
    # 账号名
    account_id = str(request.args.get('id') or '').strip()
    protocol_text = str(request.args.get("protocol") or "1").strip()
    if protocol_text not in {"1", "2"}:
        return jsonify(
            {"code": 400, "msg": "不支持的登录协议版本", "data": None}
        ), 400
    protocol_version = int(protocol_text)
    try:
        client_session_id = _normalize_login_client_session_id(
            request.args.get("session_id")
        )
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    if protocol_version >= 2 and not client_session_id:
        return jsonify(
            {"code": 400, "msg": "登录协议 2 必须提供随机会话标识", "data": None}
        ), 400
    last_event_text = str(
        request.args.get("last_event_id")
        or request.headers.get("Last-Event-ID")
        or "0"
    ).strip()
    try:
        last_event_id = int(last_event_text)
    except ValueError:
        return jsonify(
            {"code": 400, "msg": "登录事件游标无效", "data": None}
        ), 400
    if last_event_id < 0 or last_event_id > 1_000_000:
        return jsonify(
            {"code": 400, "msg": "登录事件游标无效", "data": None}
        ), 400
    existing_account_text = str(
        request.args.get("existing_account_id") or ""
    ).strip()
    existing_account_id = None
    if existing_account_text:
        try:
            existing_account_id = int(existing_account_text)
        except ValueError:
            return jsonify(
                {"code": 400, "msg": "已有账号标识无效", "data": None}
            ), 400
        if existing_account_id <= 0:
            return jsonify(
                {"code": 400, "msg": "已有账号标识无效", "data": None}
            ), 400
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
    if not _real_publishing_authorized():
        return jsonify(
            {"code": 403, "msg": "当前桌面构建未开启媒体账号登录", "data": None}
        ), 403
    if not app.config.get("PUBLISHING_RUNTIME_READY", False):
        return jsonify(
            {
                "code": 503,
                "msg": "本机发布运行时未就绪，请安装 Google Chrome 或重新安装完整发布版",
                "data": None,
            }
        ), 503
    if not app.config.get("ALLOW_REAL_PUBLISHING", False):
        return jsonify(
            {"code": 403, "msg": "媒体账号登录运行开关未开启", "data": None}
        ), 403
    login_logger.info(
        "媒体账号登录请求 platform_type={} protocol={}",
        login_type,
        protocol_version,
    )

    session_key = (login_type, account_id)
    status_queue = None
    resumed_session = False
    created_session = False
    with active_queues_lock:
        _prune_recent_login_queues()
        active_status_queue = active_queues.get(session_key)
        if active_status_queue is not None:
            active_session_id = str(
                getattr(active_status_queue, "client_session_id", "") or ""
            )
            if (
                client_session_id
                and active_session_id
                and hmac.compare_digest(client_session_id, active_session_id)
            ):
                status_queue = active_status_queue
                resumed_session = True
            else:
                return jsonify(
                    {"code": 409, "msg": "该账号已有登录会话进行中", "data": None}
                ), 409

        if status_queue is None and client_session_id:
            replay_key = (login_type, account_id, client_session_id)
            status_queue = recent_login_queues.get(replay_key)
            resumed_session = status_queue is not None

        if status_queue is None and existing_account_id is not None:
            try:
                with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
                    existing_account = conn.execute(
                        """
                        SELECT type, userName, status
                        FROM user_info
                        WHERE id = ?
                        """,
                        (existing_account_id,),
                    ).fetchone()
            except sqlite3.Error:
                return jsonify(
                    {"code": 500, "msg": "无法校验已有账号", "data": None}
                ), 500
            if (
                existing_account is None
                or int(existing_account[2]) == DISCONNECTED_ACCOUNT_STATUS
                or str(existing_account[0]) != login_type
                or str(existing_account[1]) != account_id
            ):
                return jsonify(
                    {
                        "code": 409,
                        "msg": "已有账号不存在、已解绑，或与当前平台备注不匹配",
                        "data": None,
                    }
                ), 409
            if existing_account_id in active_account_checks:
                return jsonify(
                    {"code": 409, "msg": "该账号正在检测，请稍候再登录", "data": None}
                ), 409

        if status_queue is None:
            status_queue = LoginStatusQueue(
                protocol_version=protocol_version,
                existing_account_id=existing_account_id,
                client_session_id=client_session_id,
            )
            created_session = True

        transport_token = _acquire_login_stream_transport_locked(status_queue)
        if transport_token is None:
            return jsonify(
                {
                    "code": 503,
                    "msg": "同时等待登录的平台过多，请先完成或关闭其他登录窗口",
                    "data": None,
                }
            ), 503

        if created_session:
            active_queues[session_key] = status_queue

    def on_close():
        _release_login_stream_transport(status_queue, transport_token)
        # Protocol 2 clients cancel through the explicit endpoint.  A transport
        # error must not be mistaken for operator intent because EventSource is
        # designed to reconnect.  Legacy clients retain their close-to-cancel
        # behavior so an old UI cannot strand a headed browser indefinitely.
        if protocol_version < 2 or not client_session_id:
            status_queue.cancel()

    # 启动异步任务线程
    if not resumed_session:
        try:
            thread = threading.Thread(
                target=run_async_function,
                args=(login_type, account_id, status_queue),
                daemon=True,
            )
            thread.start()
        except Exception as exc:
            try:
                _queue_login_runtime_error(status_queue, login_type)
            except Exception:
                pass
            status_queue.cancel()
            _finish_login_session(login_type, account_id, status_queue)
            _release_login_stream_transport(status_queue, transport_token)
            login_logger.exception(
                "媒体账号登录线程启动失败 platform_type={} error_type={}",
                login_type,
                exc.__class__.__name__,
            )
            return jsonify(
                {
                    "code": 500,
                    "msg": "登录服务未能启动，请重试并查看本机日志",
                    "data": None,
                }
            ), 500
    response = Response(
        sse_stream(
            status_queue,
            on_close=on_close,
            after_event_id=last_event_id,
            transport_token=transport_token,
        ),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Pragma'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'  # 关键：禁用 Nginx 缓冲
    response.headers['Content-Type'] = 'text/event-stream'
    response.call_on_close(
        lambda: _release_login_stream_transport(status_queue, transport_token)
    )
    return response


@app.route('/api/media-login/cancel', methods=['POST'])
def cancel_media_login():
    """Cooperatively stop one active headed login without killing processes."""

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(
            {"code": 400, "msg": "请求格式无效", "data": None}
        ), 400
    login_type = str(payload.get("type") or "").strip()
    account_id = str(payload.get("id") or "").strip()
    try:
        client_session_id = _normalize_login_client_session_id(
            payload.get("session_id")
        )
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400
    if login_type not in {str(value) for value in range(1, 11)}:
        return jsonify(
            {"code": 400, "msg": "不支持的平台类型", "data": None}
        ), 400
    if not account_id or len(account_id) > 100:
        return jsonify(
            {"code": 400, "msg": "账号名无效", "data": None}
        ), 400

    with active_queues_lock:
        _prune_recent_login_queues()
        status_queue = active_queues.get((login_type, account_id))
        if status_queue is not None:
            active_session_id = str(
                getattr(status_queue, "client_session_id", "") or ""
            )
            if active_session_id and (
                not client_session_id
                or not hmac.compare_digest(client_session_id, active_session_id)
            ):
                return jsonify(
                    {"code": 409, "msg": "登录会话已变化，请刷新账号状态", "data": None}
                ), 409
            if client_session_id and not active_session_id:
                return jsonify(
                    {"code": 409, "msg": "登录会话已变化，请刷新账号状态", "data": None}
                ), 409
        completed_queue = (
            recent_login_queues.get(
                (login_type, account_id, client_session_id)
            )
            if status_queue is None and client_session_id
            else None
        )
    if status_queue is None:
        if completed_queue is not None:
            return jsonify(
                {
                    "code": 200,
                    "msg": (
                        "账号登录已完成"
                        if completed_queue.outcome == "success"
                        else "登录窗口已结束"
                    ),
                    "data": {
                        "cancel_requested": False,
                        "active": False,
                        "outcome": completed_queue.outcome,
                    },
                }
            ), 200
        return jsonify(
            {
                "code": 200,
                "msg": "当前没有进行中的登录",
                "data": {
                    "cancel_requested": False,
                    "active": False,
                    "outcome": "unknown",
                },
            }
        ), 200
    cancel = getattr(status_queue, "cancel", None)
    if callable(cancel):
        cancel()
    finished_event = getattr(status_queue, "finished_event", None)
    finished = bool(
        finished_event is not None
        and finished_event.wait(timeout=3.0)
    )
    with active_queues_lock:
        still_registered = active_queues.get((login_type, account_id)) is status_queue
        protocol_two_owned = (
            int(getattr(status_queue, "protocol_version", 1)) >= 2
            and bool(getattr(status_queue, "client_session_id", ""))
        )
        # ``finished_event`` is set only after the login coroutine (including
        # browser/context cleanup) has returned.  Removing the exact same queue
        # here is retained only for legacy protocol 1. Protocol 2 must let the
        # worker perform the atomic active -> replay-journal transfer; removing
        # it here creates a reconnect window that can launch a duplicate worker.
        if finished and still_registered and not protocol_two_owned:
            active_queues.pop((login_type, account_id), None)
            still_registered = False
    return jsonify(
        {
            "code": 200,
            "msg": (
                "账号登录已完成"
                if status_queue.outcome == "success"
                else "登录窗口已结束"
                if not still_registered
                else (
                    "已发送结束登录请求，Bilibili 扫码窗口正在关闭"
                    if login_type == str(BILIBILI_ACCOUNT_TYPE)
                    else "已发送结束登录请求，平台登录窗口正在关闭"
                )
            ),
            "data": {
                "cancel_requested": True,
                "active": still_registered,
                "outcome": status_queue.outcome,
            },
        }
    ), 200

@app.route('/postVideo', methods=['POST'])
def postVideo():
    # This legacy endpoint directly imports and starts browser publishers. Keep
    # the process-wide real-publishing switch as its outermost guard so a
    # disabled deployment cannot even load publisher runtimes or create a
    # background publish thread. Safe job preparation and demo execution remain
    # available through the /api/publish endpoints above.
    if (
        not app.config.get("ALLOW_REAL_PUBLISHING", False)
        or not app.config.get("ALLOW_LEGACY_DIRECT_PUBLISHING", False)
    ):
        return jsonify(
            {
                "code": 403,
                "msg": "旧版直发入口已关闭，请使用发布任务的逐次确认入口",
                "data": None,
            }
        ), 403
    if not app.config.get("PUBLISHING_RUNTIME_READY", False):
        return jsonify(
            {
                "code": 503,
                "msg": "本机发布运行时未就绪，请安装 Google Chrome 或重新安装完整发布版",
                "data": None,
            }
        ), 503

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
        with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 更新数据库记录
            cursor.execute('''
                           UPDATE user_info
                           SET type     = ?,
                               userName = ?
                           WHERE id = ? AND status != -2;
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
    # Match /postVideo: reject before parsing work or importing any browser
    # publisher. This endpoint executes items synchronously after the import, so
    # the feature gate must stay before both operations.
    if (
        not app.config.get("ALLOW_REAL_PUBLISHING", False)
        or not app.config.get("ALLOW_LEGACY_DIRECT_PUBLISHING", False)
    ):
        return jsonify(
            {
                "code": 403,
                "msg": "旧版直发入口已关闭，请使用发布任务的逐次确认入口",
                "data": None,
            }
        ), 403
    if not app.config.get("PUBLISHING_RUNTIME_READY", False):
        return jsonify(
            {
                "code": 503,
                "msg": "本机发布运行时未就绪，请安装 Google Chrome 或重新安装完整发布版",
                "data": None,
            }
        ), 503

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

        cookies_root = _cookies_root()
        cookies_root.mkdir(parents=True, exist_ok=True)
        temporary_path = cookies_root / f".geo-cookie-upload-{uuid.uuid4().hex}.tmp"
        try:
            # Receive into a private temporary file first.  The lifecycle lock
            # is acquired only for the final account revalidation and atomic
            # replace, so a concurrent disconnect cannot leave an orphaned
            # credential after it has disabled the account.
            file.save(str(temporary_path))
            with active_queues_lock:
                with closing(sqlite3.connect(app.config["DATABASE_PATH"])) as conn:
                    conn.row_factory = sqlite3.Row
                    result = conn.execute(
                        """
                        SELECT id, type, filePath, userName, status
                        FROM user_info
                        WHERE id = ? AND status != ?
                        """,
                        (account_id, DISCONNECTED_ACCOUNT_STATUS),
                    ).fetchone()
                    if result is None:
                        return jsonify(
                            {"code": 404, "msg": "账号不存在或已解绑", "data": None}
                        ), 404
                    normalized_account_id = int(result["id"])
                    session_key = (str(result["type"]), str(result["userName"]))
                    if (
                        normalized_account_id in active_account_checks
                        or session_key in active_queues
                    ):
                        return jsonify(
                            {"code": 409, "msg": "账号正在登录或检测，请稍后上传", "data": None}
                        ), 409
                    publishing = conn.execute(
                        """
                        SELECT 1
                        FROM publish_jobs
                        WHERE account_id = ? AND status = 'processing'
                        LIMIT 1
                        """,
                        (normalized_account_id,),
                    ).fetchone()
                    if publishing is not None:
                        return jsonify(
                            {"code": 409, "msg": "账号正在发布，已拒绝替换凭证", "data": None}
                        ), 409
                    cookie_filename = _normalize_media_filename(result["filePath"])
                    shared_reference = conn.execute(
                        """
                        SELECT 1
                        FROM user_info
                        WHERE id != ? AND status != ? AND filePath = ?
                        LIMIT 1
                        """,
                        (
                            normalized_account_id,
                            DISCONNECTED_ACCOUNT_STATUS,
                            cookie_filename,
                        ),
                    ).fetchone()
                    if shared_reference is not None:
                        return jsonify(
                            {"code": 409, "msg": "多个账号共享该凭证文件，已拒绝覆盖", "data": None}
                        ), 409
                os.replace(temporary_path, cookies_root / cookie_filename)

            return jsonify({
                "code": 200,
                "msg": "Cookie文件上传成功",
                "data": None
            }), 200
        finally:
            temporary_path.unlink(missing_ok=True)

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
def _finish_login_session(type, id, status_queue):
    mark_finished = getattr(status_queue, "mark_finished", None)
    session_key = (str(type), str(id))
    replay_key = None
    with active_queues_lock:
        # Finish notification and the active -> recent registry transfer are a
        # single state change to reconnecting clients. They can observe either
        # an active worker or a replay journal, never a gap that starts a second
        # browser worker.
        if callable(mark_finished):
            mark_finished()
        if active_queues.get(session_key) is status_queue:
            active_queues.pop(session_key, None)
        client_session_id = str(
            getattr(status_queue, "client_session_id", "") or ""
        )
        if client_session_id:
            replay_key = (session_key[0], session_key[1], client_session_id)
            recent_login_queues[replay_key] = status_queue
        _prune_recent_login_queues()
    if replay_key is not None:
        _schedule_recent_login_queue_expiry(replay_key, status_queue)


def _load_login_helpers():
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

    return {
        "1": xiaohongshu_cookie_gen,
        "2": get_tencent_cookie,
        "3": douyin_cookie_gen,
        "4": get_ks_cookie,
        "5": baijiahao_cookie_gen,
        "6": bilibili_cookie_gen,
        "7": toutiao_cookie_gen,
        "8": sohu_cookie_gen,
        "9": zhihu_cookie_gen,
        "10": tiktok_cookie_gen,
    }


def _run_login_coroutine(coroutine):
    """Run one login coroutine and always release its thread-local event loop."""

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coroutine)
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def _queue_login_runtime_error(status_queue, platform_type):
    is_zhihu = str(platform_type) == "9"
    payload = {
        "type": "error",
        "code": "unexpected_error",
        "message": (
            "知乎登录发生异常，请重试并查看本机日志"
            if is_zhihu
            else "平台登录发生异常，请重试并查看本机日志"
        ),
        "action": "重试；如仍失败，请查看本机登录日志",
    }
    if int(getattr(status_queue, "protocol_version", 1)) >= 2:
        status_queue.put(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
    status_queue.put("500")


def run_async_function(type, id, status_queue):
    normalized_type = str(type)
    try:
        if (
            normalized_type == str(BILIBILI_ACCOUNT_TYPE)
            and not app.config.get("ENABLE_BILIBILI_RUNTIME", False)
        ):
            status_queue.put("500")
            return
        login_helpers = _load_login_helpers()
        login_runner = login_helpers.get(normalized_type)
        if login_runner is None:
            raise ValueError("unsupported login platform")
        login_storage = {
            "database_path": app.config["DATABASE_PATH"],
            "cookies_directory": app.config["COOKIES_DIRECTORY"],
        }
        login_logger.info(
            "媒体账号登录线程启动 platform_type={}", normalized_type
        )
        _run_login_coroutine(
            login_runner(id, status_queue, **login_storage)
        )
    except Exception as e:
        login_logger.exception(
            "媒体账号登录线程异常 platform_type={} error_type={}",
            normalized_type,
            e.__class__.__name__,
        )
        try:
            _queue_login_runtime_error(status_queue, normalized_type)
        except Exception:
            pass
    finally:
        _finish_login_session(type, id, status_queue)

# SSE 流生成器函数
def sse_stream(
    status_queue,
    *,
    on_close=None,
    after_event_id=0,
    transport_token=None,
):
    replay_reader = getattr(status_queue, "wait_for_events", None)
    if callable(replay_reader) and int(
        getattr(status_queue, "protocol_version", 1)
    ) >= 2:
        cursor = max(0, int(after_event_id or 0))
        last_heartbeat = time.monotonic()
        try:
            while True:
                if (
                    transport_token is not None
                    and not _is_login_stream_transport_current(
                        status_queue, transport_token
                    )
                ):
                    return
                events = replay_reader(cursor, timeout=0.5)
                for event_id, msg in events:
                    if (
                        transport_token is not None
                        and not _is_login_stream_transport_current(
                            status_queue, transport_token
                        )
                    ):
                        return
                    cursor = int(event_id)
                    yield f"id: {cursor}\ndata: {msg}\n\n"
                    if str(msg) in {"200", "500"}:
                        return
                if status_queue.finished_event.is_set() and not events:
                    return
                now = time.monotonic()
                if now - last_heartbeat >= 15:
                    yield ": keep-alive\n\n"
                    last_heartbeat = now
        finally:
            if on_close is not None:
                on_close()
        return

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
        scheduler_allows_real = _scheduled_real_publishing_allowed()
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
