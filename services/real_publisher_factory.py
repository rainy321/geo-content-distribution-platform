from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from services.publish_job_executor import PublisherNotConfiguredError
from services.publisher_adapter import (
    PublisherAdapter,
    SohuPublisherAdapter,
    ToutiaoPublisherAdapter,
    ZhihuPublisherAdapter,
)


REAL_PUBLISH_PLATFORMS = frozenset({"zhihu", "toutiao", "sohu"})
_PLATFORM_ACCOUNT_TYPES = {"zhihu": 9, "toutiao": 7, "sohu": 8}


class RealPublisherFactory:
    """Resolve an explicitly enabled real job to a local account adapter."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        cookies_directory: str | Path,
    ):
        self.database_path = Path(database_path)
        self.cookies_directory = Path(cookies_directory).resolve()

    def __call__(self, job: dict[str, Any]) -> PublisherAdapter:
        platform = str(job.get("platform") or "").strip().lower()
        account_type = _PLATFORM_ACCOUNT_TYPES.get(platform)
        if account_type is None:
            raise PublisherNotConfiguredError(
                f"{platform or '未知平台'} 尚未接入真实文章发布器"
            )

        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, filePath, userName
                FROM user_info
                WHERE type = ? AND status = 1
                ORDER BY COALESCE(last_checked_at, '') DESC, id ASC
                """,
                (account_type,),
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
) -> RealPublisherFactory:
    return RealPublisherFactory(
        database_path,
        cookies_directory=cookies_directory,
    )
