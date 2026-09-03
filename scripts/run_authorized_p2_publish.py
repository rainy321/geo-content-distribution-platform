from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from services.publish_job_executor import execute_publish_job
from services.publish_job_service import create_publish_job
from services.real_publisher_factory import create_real_publisher_factory
from services.media_publisher_adapters import TiktokPublisherAdapter


TITLE = "GEO 内容生成与分发链路测试说明"
CONFIRMATION = "发布GEO测试内容到五个平台"
RETRY_CONFIRMATION = "重试未完成的P2发布"
PLATFORMS = ("douyin", "kuaishou", "bilibili", "channels", "tiktok")
IMAGE_PLATFORMS = frozenset({"douyin", "kuaishou"})
FINAL_STATUSES = frozenset({"success", "processing", "failed", "need_action"})


def _real_publishing_enabled() -> bool:
    return str(os.getenv("ALLOW_REAL_PUBLISHING", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _find_article(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT id FROM articles WHERE title = ? ORDER BY id DESC LIMIT 1",
        (TITLE,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"没有找到精确标题文章：{TITLE}")
    return int(row[0])


def _find_existing_job(
    connection: sqlite3.Connection,
    *,
    article_id: int,
    platform: str,
) -> dict[str, object] | None:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT id, status, message, result_url
        FROM publish_jobs
        WHERE article_id = ? AND platform = ? AND demo = 0
        ORDER BY id DESC
        LIMIT 1
        """,
        (article_id, platform),
    ).fetchone()
    return dict(row) if row else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="一次性执行五个 P2 平台的已授权真实发布；绝不自动重试。"
    )
    parser.add_argument("--confirm", required=True)
    parser.add_argument(
        "--retry-confirm",
        default="",
        help="为 failed/need_action 任务创建一次新的真实尝试；必须传入精确确认短语。",
    )
    parser.add_argument("--database", type=Path, default=Path("db/database.db"))
    parser.add_argument("--cookies", type=Path, default=Path("cookiesFile"))
    parser.add_argument("--media", type=Path, default=Path("videoFile"))
    parser.add_argument("--image", default="geo-test-cover.png")
    parser.add_argument("--video", default="geo-p2-neutral-test.mp4")
    parser.add_argument(
        "--platform",
        action="append",
        choices=PLATFORMS,
        help="只执行指定平台；可重复传入。默认五个平台。",
    )
    parser.add_argument(
        "--resume-pre-submit-failure",
        action="store_true",
        help="只允许补做已确认发生在最终按钮前的技术失败。",
    )
    parser.add_argument(
        "--use-verified-tiktok-session",
        action="store_true",
        help="跳过重复的 TikTok 无头登录页访问，复用本轮已独立验证的会话。",
    )
    args = parser.parse_args()

    if args.confirm != CONFIRMATION:
        raise SystemExit("确认短语不匹配，未访问任何真实平台")
    retry_authorized = args.retry_confirm == RETRY_CONFIRMATION
    if args.retry_confirm and not retry_authorized:
        raise SystemExit("重试确认短语不匹配，未访问任何真实平台")
    if not _real_publishing_enabled():
        raise SystemExit("ALLOW_REAL_PUBLISHING 未开启，未访问任何真实平台")

    database_path = args.database.expanduser().resolve()
    cookies_directory = args.cookies.expanduser().resolve()
    media_root = args.media.expanduser().resolve()
    image_path = (media_root / args.image).resolve()
    video_path = (media_root / args.video).resolve()
    image_path.relative_to(media_root)
    video_path.relative_to(media_root)
    if not image_path.is_file():
        raise FileNotFoundError(image_path)
    if not video_path.is_file():
        raise FileNotFoundError(video_path)

    with sqlite3.connect(database_path) as connection:
        article_id = _find_article(connection)

    base_factory = create_real_publisher_factory(
        database_path,
        cookies_directory=cookies_directory,
    )

    def factory(job):
        publisher = base_factory(job)
        if job["platform"] == "tiktok" and args.use_verified_tiktok_session:
            return TiktokPublisherAdapter(
                publisher.account_file,
                login_checker=lambda _account_file: True,
            )
        return publisher
    results: list[dict[str, object]] = []
    selected_platforms = tuple(args.platform or PLATFORMS)
    for platform in selected_platforms:
        with sqlite3.connect(database_path) as connection:
            existing = _find_existing_job(
                connection,
                article_id=article_id,
                platform=platform,
            )
        safe_pre_submit_failure = bool(
            existing
            and existing["status"] in {"failed", "need_action"}
            and args.resume_pre_submit_failure
            and any(
                marker in str(existing.get("message") or "")
                for marker in (
                    "object has no attribute 'set_ai_generated_declaration'",
                    "credential check",
                    "ERR_CONNECTION_CLOSED",
                    "spawn . ENOENT",
                    "BrowserType.launch",
                )
            )
        )
        retryable_status = bool(
            existing and str(existing["status"]) in {"failed", "need_action"}
        )
        if (
            existing
            and str(existing["status"]) in FINAL_STATUSES
            and not safe_pre_submit_failure
            and not (retry_authorized and retryable_status)
        ):
            print(
                f"PUBLISH_SKIP {platform} existing_job={existing['id']} "
                f"status={existing['status']}",
                flush=True,
            )
            results.append({"platform": platform, **existing, "skipped": True})
            continue

        if existing and existing["status"] == "queued":
            job_id = int(existing["id"])
        else:
            job = create_publish_job(
                database_path,
                article_id=article_id,
                platform=platform,
                images=[args.image] if platform in IMAGE_PLATFORMS else [],
                video=args.video if platform not in IMAGE_PLATFORMS else "",
                demo=False,
            )
            job_id = int(job["id"])

        print(f"PUBLISH_START {platform} job={job_id}", flush=True)
        result = execute_publish_job(
            database_path,
            job_id,
            publisher_factory=factory,
            media_root=media_root,
        )
        summary = {
            "platform": platform,
            "job_id": job_id,
            "status": result["status"],
            "message": result.get("message") or "",
            "url": result.get("result_url") or "",
        }
        results.append(summary)
        print(
            "PUBLISH_RESULT " + json.dumps(summary, ensure_ascii=False),
            flush=True,
        )

    print("PUBLISH_BATCH_RESULT " + json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
