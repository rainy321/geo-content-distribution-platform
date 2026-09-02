import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from queue import Queue
from flask_cors import CORS
from myUtils.auth import check_cookie
from flask import Flask, request, jsonify, Response, render_template, send_from_directory
from conf import BASE_DIR
from myUtils.login import get_tencent_cookie, douyin_cookie_gen, get_ks_cookie, xiaohongshu_cookie_gen, baijiahao_cookie_gen, bilibili_cookie_gen, toutiao_cookie_gen, sohu_cookie_gen, zhihu_cookie_gen
from myUtils.postVideo import post_video_tencent, post_video_DouYin, post_video_ks, post_video_xhs, post_video_baijiahao, post_video_bilibili, post_video_toutiao, post_article_toutiao, post_article_baijiahao, post_article_sohu, post_article_zhihu
from db.createTable import initialize_database
from services.ai_service import (
    AIConfigurationError,
    AIServiceError,
    generate_geo_content,
    optimize_geo_content,
)
from services.article_service import (
    ARTICLE_STATUSES,
    ArticleNotFoundError,
    create_article,
    get_article,
    list_articles,
    update_article,
)
from services.geo_score_service import score_geo_content
from services.dashboard_service import get_dashboard_overview
from services.demo_seed_service import seed_demo_data
from services.media_account_service import (
    MediaAccountCheckError,
    MediaAccountNotFoundError,
    check_media_account,
    get_media_accounts_overview,
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
    retry_publish_job,
)
from services.publish_scheduler_runtime import create_publish_scheduler
from services.real_publisher_factory import (
    REAL_PUBLISH_PLATFORMS,
    create_real_publisher_factory,
)

active_queues = {}
active_queues_lock = threading.Lock()
app = Flask(__name__)

# Web 启动时只补齐运行目录和已有表，不删除或覆盖现有数据。
Path(BASE_DIR / "videoFile").mkdir(parents=True, exist_ok=True)
configured_database_path = os.getenv("DATABASE_PATH")
configured_cookies_directory = os.getenv("COOKIES_DIRECTORY")
app.config["DATABASE_PATH"] = (
    Path(configured_database_path).expanduser().resolve()
    if configured_database_path
    else Path(BASE_DIR / "db" / "database.db")
)
app.config["MEDIA_ROOT"] = Path(BASE_DIR / "videoFile").resolve()
app.config["COOKIES_DIRECTORY"] = (
    Path(configured_cookies_directory).expanduser().resolve()
    if configured_cookies_directory
    else Path(BASE_DIR / "cookiesFile").resolve()
)
Path(app.config["COOKIES_DIRECTORY"]).mkdir(parents=True, exist_ok=True)
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
initialize_database(app.config["DATABASE_PATH"])
if app.config["DEMO_MODE"] and app.config["SEED_DEMO_DATA"]:
    app.config["DEMO_SEED_RESULT"] = seed_demo_data(app.config["DATABASE_PATH"])

#允许所有来源跨域访问
CORS(app)

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

# 处理 Vite 构建后的静态资源。
@app.route('/assets/<filename>')
def custom_static(filename):
    return send_from_directory(_frontend_build_dir() / "assets", filename)

# 处理 favicon.ico 静态资源（未来打包用）
@app.route('/favicon.ico')
def favicon():
    build_dir = _frontend_build_dir()
    icon_dir = build_dir / "assets" if (build_dir / "assets" / "vite.svg").is_file() else build_dir
    return send_from_directory(icon_dir, 'vite.svg')

@app.route('/vite.svg')
def vite_svg():
    build_dir = _frontend_build_dir()
    icon_dir = build_dir if (build_dir / "vite.svg").is_file() else build_dir / "assets"
    return send_from_directory(icon_dir, 'vite.svg')


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


@app.route('/api/articles/generate', methods=['POST'])
def generate_article():
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

    try:
        length = int(data.get("length", 1000))
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "文章长度必须是整数", "data": None}), 400
    if length not in ARTICLE_LENGTHS:
        return jsonify({"code": 400, "msg": "文章长度仅支持 600、1000、1500", "data": None}), 400

    content_type = str(data.get("content_type") or "行业科普").strip()
    if content_type not in ARTICLE_CONTENT_TYPES:
        return jsonify({"code": 400, "msg": "不支持的内容类型", "data": None}), 400

    project = _get_project(project_id)
    if project is None:
        return jsonify({"code": 404, "msg": "品牌项目不存在", "data": None}), 404

    requested_keywords = data.get("keywords", project["keywords"])
    if not isinstance(requested_keywords, list):
        return jsonify({"code": 400, "msg": "keywords 必须是字符串数组", "data": None}), 400
    keywords = _normalize_string_list(requested_keywords)
    target_platform = str(
        data.get("target_platform") or data.get("targetPlatform") or ""
    ).strip()

    try:
        article = generate_geo_content(
            project=project,
            topic=topic,
            keywords=keywords,
            length=length,
            content_type=content_type,
            target_platform=target_platform,
        )
    except AIConfigurationError as exc:
        return jsonify({"code": 503, "msg": str(exc), "data": None}), 503
    except AIServiceError as exc:
        return jsonify({"code": 502, "msg": str(exc), "data": None}), 502

    return jsonify({"code": 200, "msg": "success", "data": article}), 200


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


@app.route('/api/articles/<int:article_id>', methods=['GET'])
def get_article_detail(article_id):
    try:
        article = get_article(app.config["DATABASE_PATH"], article_id)
    except ArticleNotFoundError as exc:
        return jsonify({"code": 404, "msg": str(exc), "data": None}), 404
    return jsonify({"code": 200, "msg": "success", "data": article}), 200


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
        optimized = optimize_geo_content(article=article, score=before_score)
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
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc), "data": None}), 400

    if (
        platform in {"baijiahao", "xiaohongshu"}
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

    try:
        job = create_publish_job(
            app.config["DATABASE_PATH"],
            article_id=article_id,
            platform=platform,
            images=images,
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
                "msg": "真实平台执行尚未开放；请先配置媒体账号和人工登录",
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
        filepath = Path(BASE_DIR / "videoFile" / f"{uuid_v1}_{file.filename}")
        file.save(filepath)
        return jsonify({"code":200,"msg": "File uploaded successfully", "data": f"{uuid_v1}_{file.filename}"}), 200
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

        with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                                INSERT INTO file_records (filename, filesize, file_path)
            VALUES (?, ?, ?)
                                ''', (filename, round(float(os.path.getsize(filepath)) / (1024 * 1024),2), final_filename))
            conn.commit()
            print("✅ 上传文件已记录")

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
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
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
    with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
        cursor = conn.cursor()
        cursor.execute('''
        SELECT * FROM user_info''')
        rows = cursor.fetchall()
        rows_list = [list(row) for row in rows]
        print("\n📋 当前数据表内容：")
        for row in rows:
            print(row)
        for row in rows_list:
            flag = await check_cookie(row[1],row[2])
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
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
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
                cookie_file_path = Path(BASE_DIR / "cookiesFile" / record['filePath'])
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
    if login_type not in {str(value) for value in range(1, 10)}:
        return jsonify({"code": 400, "msg": "不支持的平台类型", "data": None}), 400
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
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
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
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
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
        cookie_file_path = Path(BASE_DIR / "cookiesFile" / result['filePath'])
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
        cookie_file_path = Path(BASE_DIR / "cookiesFile" / file_path).resolve()
        base_path = Path(BASE_DIR / "cookiesFile").resolve()

        if not cookie_file_path.is_relative_to(base_path):
            return jsonify({
                "code": 500,
                "msg": "非法文件路径",
                "data": None
            }), 400

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
        publish_scheduler.shutdown(wait=False)
