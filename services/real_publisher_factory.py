from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from services.publish_job_executor import PublisherNotConfiguredError
from services.media_publisher_adapters import (
    BilibiliPublisherAdapter,
    ChannelsPublisherAdapter,
    DouyinPublisherAdapter,
    KuaishouPublisherAdapter,
    TiktokPublisherAdapter,
)
from services.platform_capability_service import (
    BILIBILI_PLATFORM_KEY,
    BILIBILI_RUNTIME_DISABLED_MESSAGE,
    PLATFORM_BY_KEY,
    SUPPORTED_PUBLISH_PLATFORMS,
)
from services.publisher_adapter import (
    BaijiahaoPublisherAdapter,
    PublisherAdapter,
    SohuPublisherAdapter,
    ToutiaoPublisherAdapter,
    XiaohongshuPublisherAdapter,
    ZhihuPublisherAdapter,
)


REAL_PUBLISH_PLATFORMS = SUPPORTED_PUBLISH_PLATFORMS
_PLATFORM_ACCOUNT_TYPES = {
    key: value["account_type"] for key, value in PLATFORM_BY_KEY.items()
}


class RealPublisherFactory:
    """Resolve an explicitly enabled real job to a local account adapter."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        cookies_directory: str | Path,
        enable_bilibili_runtime: bool = False,
    ):
        self.database_path = Path(database_path)
        self.cookies_directory = Path(cookies_directory).resolve()
        self.enable_bilibili_runtime = bool(enable_bilibili_runtime)

    def __call__(self, job: dict[str, Any]) -> PublisherAdapter:
        platform = str(job.get("platform") or "").strip().lower()
        if (
            platform == BILIBILI_PLATFORM_KEY
            and not self.enable_bilibili_runtime
        ):
            raise PublisherNotConfiguredError(BILIBILI_RUNTIME_DISABLED_MESSAGE)
        account_type = _PLATFORM_ACCOUNT_TYPES.get(platform)
        if account_type is None:
            raise PublisherNotConfiguredError(
                f"{platform or '未知平台'} 尚未接入真实文章发布器"
            )

        raw_account_id = job.get("account_id")
        bound_account_id: int | None = None
        if raw_account_id not in {None, ""}:
            try:
                bound_account_id = int(raw_account_id)
            except (TypeError, ValueError) as exc:
                raise PublisherNotConfiguredError("发布任务绑定的账号 ID 无效") from exc
            if bound_account_id <= 0:
                raise PublisherNotConfiguredError("发布任务绑定的账号 ID 无效")

        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.row_factory = sqlite3.Row
            if bound_account_id is None:
                rows = conn.execute(
                    """
                    SELECT id, filePath, userName
                    FROM user_info
                    WHERE type = ? AND status = 1
                    ORDER BY COALESCE(last_checked_at, '') DESC, id DESC
                    """,
                    (account_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, filePath, userName
                    FROM user_info
                    WHERE id = ? AND type = ? AND status = 1
                    """,
                    (bound_account_id, account_type),
                ).fetchall()

        for row in rows:
            account_file = self._safe_cookie_path(row["filePath"])
            if account_file is not None and account_file.is_file():
                if platform == "zhihu":
                    return ZhihuPublisherAdapter(account_file)
                if platform == "toutiao":
                    return ToutiaoPublisherAdapter(account_file)
                if platform == "sohu":
                    return SohuPublisherAdapter(account_file)
                if platform == "baijiahao":
                    return BaijiahaoPublisherAdapter(account_file)
                if platform == "xiaohongshu":
                    return XiaohongshuPublisherAdapter(account_file)
                if platform == "douyin":
                    return DouyinPublisherAdapter(account_file)
                if platform == "kuaishou":
                    return KuaishouPublisherAdapter(account_file)
                if platform == "bilibili":
                    return BilibiliPublisherAdapter(account_file)
                if platform == "channels":
                    return ChannelsPublisherAdapter(account_file)
                if platform == "tiktok":
                    return TiktokPublisherAdapter(account_file)

        if bound_account_id is not None:
            raise PublisherNotConfiguredError(
                f"{platform} 任务绑定的账号 {bound_account_id} 当前不可用，"
                "请重新登录该账号或创建绑定新账号的任务"
            )
        raise PublisherNotConfiguredError(
            f"{platform} 没有可用的已连接账号，请先在媒体账号页登录并检测状态"
        )

    def _safe_cookie_path(self, value: str) -> Path | None:
        raw_value = str(value or "").strip()
        if not raw_value:
            return None
        candidate = (self.cookies_directory / raw_value).resolve()
        try:
            candidate.relative_to(self.cookies_directory)
        except ValueError:
            return None
        return candidate


def create_real_publisher_factory(
    database_path: str | Path,
    *,
    cookies_directory: str | Path,
    enable_bilibili_runtime: bool = False,
) -> RealPublisherFactory:
    return RealPublisherFactory(
        database_path,
        cookies_directory=cookies_directory,
        enable_bilibili_runtime=enable_bilibili_runtime,
    )
