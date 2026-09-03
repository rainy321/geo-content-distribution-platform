import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from services.publish_job_executor import PublisherNotConfiguredError
from services.media_publisher_adapters import (
    BilibiliPublisherAdapter,
    ChannelsPublisherAdapter,
    DouyinPublisherAdapter,
    KuaishouPublisherAdapter,
    TiktokPublisherAdapter,
)
from services.publisher_adapter import (
    BaijiahaoPublisherAdapter,
    SohuPublisherAdapter,
    ToutiaoPublisherAdapter,
    XiaohongshuPublisherAdapter,
    ZhihuPublisherAdapter,
)
from services.real_publisher_factory import RealPublisherFactory


class RealPublisherFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "factory.db"
        self.cookies_dir = self.root / "cookies"
        self.cookies_dir.mkdir()
        initialize_database(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _insert_account(
        self,
        file_path,
        *,
        account_type=9,
        account_name="知乎账号",
        status=1,
        checked_at=None,
    ):
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO user_info (
                        type, filePath, userName, status, last_checked_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (account_type, file_path, account_name, status, checked_at),
                )

    def test_builds_zhihu_adapter_from_connected_local_cookie(self):
        cookie_file = self.cookies_dir / "zhihu.json"
        cookie_file.write_text("{}", encoding="utf-8")
        self._insert_account("zhihu.json", checked_at="2026-08-31 14:30:00")
        factory = RealPublisherFactory(
            self.db_path,
            cookies_directory=self.cookies_dir,
        )

        publisher = factory({"platform": "zhihu"})

        self.assertIsInstance(publisher, ZhihuPublisherAdapter)
        self.assertEqual(Path(publisher.account_file), cookie_file)

    def test_builds_toutiao_adapter_from_connected_local_cookie(self):
        cookie_file = self.cookies_dir / "toutiao.json"
        cookie_file.write_text("{}", encoding="utf-8")
        self._insert_account(
            "toutiao.json",
            account_type=7,
            account_name="头条账号",
            checked_at="2026-09-01 10:00:00",
        )
        factory = RealPublisherFactory(
            self.db_path,
            cookies_directory=self.cookies_dir,
        )

        publisher = factory({"platform": "toutiao"})

        self.assertIsInstance(publisher, ToutiaoPublisherAdapter)
        self.assertEqual(Path(publisher.account_file), cookie_file)

    def test_builds_sohu_adapter_from_connected_local_cookie(self):
        cookie_file = self.cookies_dir / "sohu.json"
        cookie_file.write_text("{}", encoding="utf-8")
        self._insert_account(
            "sohu.json",
            account_type=8,
            account_name="搜狐账号",
            checked_at="2026-09-01 11:00:00",
        )
        factory = RealPublisherFactory(
            self.db_path,
            cookies_directory=self.cookies_dir,
        )

        publisher = factory({"platform": "sohu"})

        self.assertIsInstance(publisher, SohuPublisherAdapter)
        self.assertEqual(Path(publisher.account_file), cookie_file)

    def test_builds_baijiahao_adapter_from_connected_local_cookie(self):
        cookie_file = self.cookies_dir / "baijiahao.json"
        cookie_file.write_text("{}", encoding="utf-8")
        self._insert_account("baijiahao.json", account_type=5)
        publisher = RealPublisherFactory(
            self.db_path,
            cookies_directory=self.cookies_dir,
        )({"platform": "baijiahao"})

        self.assertIsInstance(publisher, BaijiahaoPublisherAdapter)

    def test_builds_xiaohongshu_adapter_from_connected_local_cookie(self):
        cookie_file = self.cookies_dir / "xiaohongshu.json"
        cookie_file.write_text("{}", encoding="utf-8")
        self._insert_account("xiaohongshu.json", account_type=1)
        publisher = RealPublisherFactory(
            self.db_path,
            cookies_directory=self.cookies_dir,
        )({"platform": "xiaohongshu"})

        self.assertIsInstance(publisher, XiaohongshuPublisherAdapter)

    def test_builds_p2_media_adapters_from_connected_local_cookies(self):
        cases = (
            ("douyin", 3, DouyinPublisherAdapter),
            ("kuaishou", 4, KuaishouPublisherAdapter),
            ("bilibili", 6, BilibiliPublisherAdapter),
            ("channels", 2, ChannelsPublisherAdapter),
            ("tiktok", 10, TiktokPublisherAdapter),
        )
        factory = RealPublisherFactory(
            self.db_path,
            cookies_directory=self.cookies_dir,
        )
        for platform, account_type, expected_class in cases:
            with self.subTest(platform=platform):
                filename = f"{platform}.json"
                (self.cookies_dir / filename).write_text("{}", encoding="utf-8")
                self._insert_account(filename, account_type=account_type)
                self.assertIsInstance(factory({"platform": platform}), expected_class)

    def test_rejects_unsupported_platform(self):
        factory = RealPublisherFactory(
            self.db_path,
            cookies_directory=self.cookies_dir,
        )

        with self.assertRaisesRegex(PublisherNotConfiguredError, "尚未接入"):
            factory({"platform": "mastodon"})

    def test_refuses_missing_expired_and_path_traversal_credentials(self):
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        self._insert_account("missing.json", status=1)
        self._insert_account("../outside.json", status=1)
        valid_but_expired = self.cookies_dir / "expired.json"
        valid_but_expired.write_text("{}", encoding="utf-8")
        self._insert_account("expired.json", status=0)
        factory = RealPublisherFactory(
            self.db_path,
            cookies_directory=self.cookies_dir,
        )

        with self.assertRaisesRegex(PublisherNotConfiguredError, "没有可用"):
            factory({"platform": "zhihu"})


if __name__ == "__main__":
    unittest.main()
