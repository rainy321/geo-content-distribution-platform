from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from services.desktop_backup_service import (
    DesktopBackupError,
    create_verified_database_backup,
)


class DesktopBackupServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "data" / "geo.db"
        self.database.parent.mkdir()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO evidence (value) VALUES ('synthetic-only')")
            connection.commit()

    def test_creates_atomic_integrity_checked_snapshot(self):
        result = create_verified_database_backup(
            self.database,
            self.root / "backups",
        )

        backup = Path(result.path)
        self.assertTrue(backup.is_file())
        self.assertGreater(result.bytes, 0)
        self.assertEqual(result.integrity_check, "ok")
        with closing(sqlite3.connect(backup)) as connection:
            value = connection.execute("SELECT value FROM evidence").fetchone()[0]
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(value, "synthetic-only")
        self.assertEqual(integrity, "ok")
        self.assertEqual(list(backup.parent.glob("*.partial")), [])

    def test_retention_removes_only_old_managed_snapshots(self):
        backup_root = self.root / "backups"
        unmanaged = backup_root / "keep-me.db"
        for index in range(4):
            with closing(sqlite3.connect(self.database)) as connection:
                connection.execute(
                    "INSERT INTO evidence (value) VALUES (?)", (f"row-{index}",)
                )
                connection.commit()
            create_verified_database_backup(self.database, backup_root, keep=2)
        unmanaged.touch()

        managed = list(backup_root.glob("geo-backup-*.db"))
        self.assertEqual(len(managed), 2)
        self.assertTrue(unmanaged.is_file())

    def test_missing_database_fails_without_creating_fake_snapshot(self):
        backup_root = self.root / "backups"
        with self.assertRaises(DesktopBackupError):
            create_verified_database_backup(self.root / "missing.db", backup_root)
        self.assertFalse(backup_root.exists())


if __name__ == "__main__":
    unittest.main()
