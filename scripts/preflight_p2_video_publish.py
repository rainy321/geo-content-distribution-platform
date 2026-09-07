from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from uploader.douyin_uploader.main import DouYinVideo
from uploader.ks_uploader.main import KSVideo
from uploader.tencent_uploader.main import TencentVideo
from uploader.tk_uploader.main_chrome import TiktokVideo


PLATFORM_TYPES = {
    "channels": 2,
    "douyin": 3,
    "kuaishou": 4,
    "bilibili": 6,
    "tiktok": 10,
}
TITLE = "GEO 内容生成与分发链路测试说明"
DESCRIPTION = "中性功能测试素材，仅用于验证账号授权、视频上传与发布表单链路。"
TAGS = ["GEO", "内容分发", "功能测试"]


def _load_account(database_path: Path, platform: str) -> Path:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT filePath
            FROM user_info
            WHERE type = ? AND status = 1
            ORDER BY id DESC
            LIMIT 1
            """,
            (PLATFORM_TYPES[platform],),
        ).fetchone()
    if not row:
        raise RuntimeError(f"{platform} 没有可用的本地登录账号")
    return Path(row[0])


async def _preflight_browser_platform(
    platform: str,
    video_path: Path,
    account_path: Path,
    preview_seconds: int,
) -> None:
    common = {
        "title": TITLE,
        "file_path": str(video_path),
        "tags": TAGS,
        "publish_date": 0,
        "account_file": account_path,
        "headless": False,
        "dry_run": True,
    }
    if platform == "douyin":
        publisher = DouYinVideo(
            **common,
            desc=DESCRIPTION,
            ai_generated=False,
        )
        await publisher.main()
    elif platform == "kuaishou":
        publisher = KSVideo(
            **common,
            desc=DESCRIPTION,
            ai_generated=False,
        )
        await publisher.main()
    elif platform == "channels":
        publisher = TencentVideo(
            **common,
            desc=DESCRIPTION,
            preview_seconds=preview_seconds,
        )
        await publisher.main()
    elif platform == "tiktok":
        publisher = TiktokVideo(
            **common,
            preview_seconds=preview_seconds,
        )
        await publisher.main()
    else:
        raise ValueError(f"不支持的浏览器预检平台: {platform}")


def _preflight_bilibili(video_path: Path, account_path: Path) -> None:
    from uploader.bilibili_uploader.runtime import run_biliup_command

    if not video_path.is_file():
        raise FileNotFoundError(video_path)
    result = run_biliup_command(["-u", str(account_path), "renew"])
    if result.returncode != 0:
        raise RuntimeError("Bilibili 登录凭据校验失败")
    # biliup has no browser draft page. Do not invoke `upload` during preflight:
    # that command starts an external transfer immediately.
    print("PREFLIGHT_RESULT bilibili ready (credential/video/metadata only; no upload)", flush=True)


async def main_async(args: argparse.Namespace) -> None:
    database_path = args.database.expanduser().resolve()
    cookies_directory = args.cookies.expanduser().resolve()
    video_path = args.video.expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(video_path)

    selected = args.platform or list(PLATFORM_TYPES)
    for platform in selected:
        account_file = _load_account(database_path, platform)
        account_path = account_file if account_file.is_absolute() else cookies_directory / account_file
        if not account_path.is_file():
            raise FileNotFoundError(f"{platform} 登录文件不存在: {account_path}")
        print(f"PREFLIGHT_START {platform}", flush=True)
        if platform == "bilibili":
            await asyncio.to_thread(_preflight_bilibili, video_path, account_path)
        else:
            await _preflight_browser_platform(
                platform,
                video_path,
                account_path,
                args.preview_seconds,
            )
            print(f"PREFLIGHT_RESULT {platform} ready (no publish click)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="对五个 P2 视频渠道执行只填表、不点击发布的安全预检。"
    )
    parser.add_argument(
        "--platform",
        action="append",
        choices=list(PLATFORM_TYPES),
        help="只预检指定平台；可重复传入。默认依次预检全部平台。",
    )
    parser.add_argument("--database", type=Path, default=Path("db/database.db"))
    parser.add_argument("--cookies", type=Path, default=Path("cookiesFile"))
    parser.add_argument(
        "--video",
        type=Path,
        default=Path("videoFile/geo-p2-neutral-test.mp4"),
    )
    parser.add_argument("--preview-seconds", type=int, default=120)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
