"""Verified local SQLite backups for the Windows desktop product."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_BACKUP_RETENTION = 5


class DesktopBackupError(RuntimeError):
    """Raised when a desktop database snapshot cannot be trusted."""


@dataclass(frozen=True)
class DesktopBackupResult:
    path: str
    bytes: int
    integrity_check: str
    created_at: str

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def create_verified_database_backup(
    database_path: str | os.PathLike[str],
    backup_directory: str | os.PathLike[str],
    *,
    keep: int = DEFAULT_BACKUP_RETENTION,
) -> DesktopBackupResult:
    """Create an atomic SQLite snapshot, verify it, then apply retention."""

    source = Path(database_path).expanduser().resolve()
    destination_root = Path(backup_directory).expanduser().resolve()
    if isinstance(keep, bool) or not isinstance(keep, int) or not 1 <= keep <= 100:
        raise ValueError("backup retention must be between 1 and 100")
    if not source.is_file():
        raise DesktopBackupError(f"本地数据库不存在：{source}")

    destination_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    basename = timestamp.strftime("geo-backup-%Y%m%dT%H%M%S%fZ.db")
    destination = (destination_root / basename).resolve()
    temporary = (destination_root / f".{basename}.partial").resolve()
    if destination.parent != destination_root or temporary.parent != destination_root:
        raise DesktopBackupError("备份目标超出本机备份目录")

    try:
        with closing(sqlite3.connect(source)) as source_connection:
            with closing(sqlite3.connect(temporary)) as backup_connection:
                source_connection.backup(backup_connection)
                backup_connection.commit()
        with closing(
            sqlite3.connect(f"file:{temporary.as_posix()}?mode=ro", uri=True)
        ) as check:
            rows = check.execute("PRAGMA integrity_check").fetchall()
        integrity = "\n".join(str(row[0]) for row in rows)
        if rows != [("ok",)]:
            raise DesktopBackupError(f"数据库备份完整性检查失败：{integrity}")
        os.replace(temporary, destination)
        _prune_old_backups(destination_root, keep=keep)
        return DesktopBackupResult(
            path=str(destination),
            bytes=destination.stat().st_size,
            integrity_check="ok",
            created_at=timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        )
    except DesktopBackupError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise DesktopBackupError("无法创建经过校验的本地数据库备份") from exc
    finally:
        try:
            if temporary.is_file() and temporary.parent == destination_root:
                temporary.unlink()
        except OSError:
            pass


def _prune_old_backups(backup_directory: Path, *, keep: int) -> None:
    candidates = sorted(
        (
            path.resolve()
            for path in backup_directory.glob("geo-backup-*.db")
            if path.is_file()
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for stale in candidates[keep:]:
        if stale.parent != backup_directory:
            raise DesktopBackupError("拒绝删除备份目录之外的文件")
        stale.unlink()


__all__ = [
    "DEFAULT_BACKUP_RETENTION",
    "DesktopBackupError",
    "DesktopBackupResult",
    "create_verified_database_backup",
]
