import inspect
import tempfile
import unittest
from pathlib import Path

from services.platform_review_verifier import (
    PlatformReviewVerifier,
    _POLICIES,
    _contains_positive_published,
    _dedupe_rows_by_stable_identity,
    _public_url,
    _resolve_exact_title_rows,
)


class PlatformReviewVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cookies_dir = Path(self.temp_dir.name) / "cookies"
        self.cookies_dir.mkdir()
        self.verifier = PlatformReviewVerifier(
            self.cookies_dir,
            navigation_timeout_ms=1_000,
            settle_ms=0,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_cookie_is_reported_without_loading_browser_runtime(self):
        result = self.verifier(
            {
                "platform": "toutiao",
                "title": "精确标题",
                "account_file": "missing.json",
                "account_status": 1,
            }
        )

        self.assertEqual(result["state"], "risk")
        self.assertIn("Cookie 文件不存在", result["message"])
        self.assertIn("重新登录", result["evidence"])

    def test_disabled_account_is_rejected_before_any_browser_navigation(self):
        cookie = self.cookies_dir / "account.json"
        cookie.write_text("{}", encoding="utf-8")

        result = self.verifier(
            {
                "platform": "zhihu",
                "title": "精确标题",
                "account_file": cookie.name,
                "account_status": 0,
            }
        )

        self.assertEqual(result["state"], "risk")
        self.assertIn("会话已失效", result["message"])

    def test_cookie_path_cannot_escape_configured_directory(self):
        outside = Path(self.temp_dir.name) / "outside.json"
        outside.write_text("{}", encoding="utf-8")

        result = self.verifier(
            {
                "platform": "sohu",
                "title": "精确标题",
                "account_file": "../outside.json",
                "account_status": 1,
            }
        )

        self.assertEqual(result["state"], "risk")
        self.assertIn("Cookie 文件不存在", result["message"])

    def test_unsupported_platform_does_not_claim_remote_verification(self):
        channels = self.verifier({"platform": "channels", "title": "精确标题"})
        unknown = self.verifier({"platform": "unknown", "title": "精确标题"})

        self.assertEqual(channels["state"], "unsupported")
        self.assertIn("人工核验", channels["message"])
        self.assertEqual(unknown["state"], "unsupported")

    def test_implementation_exposes_only_navigation_and_read_operations(self):
        source = inspect.getsource(PlatformReviewVerifier)

        for forbidden in (
            ".click(",
            ".fill(",
            ".press(",
            ".type(",
            "keyboard.",
            "request.post(",
            "request.put(",
            "request.patch(",
            "request.delete(",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("publisher_adapter", source)

    def test_negated_status_words_never_count_as_published(self):
        for text in (
            "未公开",
            "尚未发布",
            "设为公开",
            "改为公开",
            "公开设置",
            "公开范围",
            "not live",
            "not published",
            "unpublished",
            "private",
            "draft",
            "delivery pending",
        ):
            with self.subTest(text=text):
                self.assertFalse(_contains_positive_published(text))

        for text in ("已公开", "已发布", "审核通过", "Published", "LIVE"):
            with self.subTest(text=text):
                self.assertTrue(_contains_positive_published(text))

    def test_visibility_controls_with_valid_work_link_are_not_terminal(self):
        policy = _POLICIES["toutiao"]
        work_url = "https://www.toutiao.com/article/123456789/"
        self.assertTrue(_public_url(work_url, policy.public_url_patterns))

        for control_text in ("设为公开", "改为公开", "公开设置", "公开范围"):
            with self.subTest(control_text=control_text):
                row_text = f"精确标题 {control_text} {work_url}"
                self.assertFalse(
                    _public_url(work_url, policy.public_url_patterns)
                    and _contains_positive_published(row_text)
                )

    def test_old_public_and_new_processing_same_title_never_claims_old_work(self):
        result = self._resolve(
            {
                "started_at": "2026-09-12 10:00:00",
                "observed_at": "2026-09-12 10:30:00",
            },
            [
                self._row(
                    "精确标题 已发布 2026-09-01 09:00",
                    "https://www.toutiao.com/article/100000001/",
                ),
                self._row("精确标题 审核中 2026-09-12 10:10"),
            ],
        )

        self.assertEqual(result["state"], "processing")
        self.assertIn("2 个同标题候选", result["message"])
        self.assertNotIn("result_url", result)

    def test_old_failure_and_new_success_need_identity_before_terminal_result(self):
        ambiguous = self._resolve(
            {
                "started_at": "2026-09-12 10:00:00",
                "observed_at": "2026-09-12 10:30:00",
            },
            [
                {
                    **self._row("精确标题 未通过 2026-09-01 09:00"),
                    "external_ids": ("old-work-1",),
                },
                {
                    **self._row(
                        "精确标题 已发布 2026-09-12 10:10",
                        "https://www.toutiao.com/article/200000002/",
                    ),
                    "external_ids": ("new-work-2",),
                },
            ],
        )
        bound = self._resolve(
            {
                "started_at": "2026-09-12 10:00:00",
                "observed_at": "2026-09-12 10:30:00",
                "submission_id": "new-work-2",
            },
            [
                {
                    **self._row("精确标题 未通过 2026-09-01 09:00"),
                    "external_ids": ("old-work-1",),
                },
                {
                    **self._row(
                        "精确标题 已发布 2026-09-12 10:10",
                        "https://www.toutiao.com/article/200000002/",
                    ),
                    "external_ids": ("new-work-2",),
                },
            ],
        )

        self.assertEqual(ambiguous["state"], "processing")
        self.assertEqual(bound["state"], "published")
        self.assertEqual(
            bound["result_url"],
            "https://www.toutiao.com/article/200000002/",
        )

    def test_repeated_candidates_remain_processing_even_if_both_look_public(self):
        result = self._resolve(
            {
                "started_at": "2026-09-12 10:00:00",
                "observed_at": "2026-09-12 10:30:00",
            },
            [
                self._row(
                    "精确标题 已发布 2026-09-12 10:10",
                    "https://www.toutiao.com/article/300000003/",
                ),
                self._row(
                    "精确标题 已发布 2026-09-12 10:11",
                    "https://www.toutiao.com/article/400000004/",
                ),
            ],
        )

        self.assertEqual(result["state"], "processing")
        self.assertNotIn("result_url", result)

    def test_identical_anonymous_rows_are_not_deduplicated(self):
        policy = _POLICIES["toutiao"]
        row = self._row("精确标题 未通过 2026-09-12 18:10")

        rows = _dedupe_rows_by_stable_identity(
            [dict(row), dict(row)],
            policy.public_url_patterns,
        )
        result = self._resolve(
            {
                "started_at": "2026-09-12 10:00:00",
                "observed_at": "2026-09-12 10:30:00",
            },
            rows,
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(result["state"], "processing")
        self.assertIn("2 个同标题候选", result["message"])

    def test_duplicate_dom_rows_with_same_public_identity_may_be_collapsed(self):
        policy = _POLICIES["toutiao"]
        row = self._row(
            "精确标题 已发布 2026-09-12 18:10",
            "https://www.toutiao.com/article/333333333/",
        )

        rows = _dedupe_rows_by_stable_identity(
            [dict(row), dict(row)],
            policy.public_url_patterns,
        )

        self.assertEqual(len(rows), 1)

    def test_same_public_identity_with_conflicting_status_stays_processing(self):
        policy = _POLICIES["toutiao"]
        public_url = "https://www.toutiao.com/article/333333333/"
        rows = _dedupe_rows_by_stable_identity(
            [
                self._row("精确标题 已发布 2026-09-12 18:10", public_url),
                self._row("精确标题 审核中 2026-09-12 18:10", public_url),
            ],
            policy.public_url_patterns,
        )
        result = self._resolve(
            {
                "result_url": public_url,
                "started_at": "2026-09-12 10:00:00",
                "observed_at": "2026-09-12 10:30:00",
            },
            rows,
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(result["state"], "processing")
        self.assertNotIn("result_url", result)

    def test_unique_title_requires_reliable_attempt_time_attribution(self):
        no_row_time = self._resolve(
            {
                "started_at": "2026-09-12 02:00:00",
                "observed_at": "2026-09-12 02:30:00",
            },
            [
                self._row(
                    "精确标题 已发布",
                    "https://www.toutiao.com/article/500000005/",
                )
            ],
        )
        old_row = self._resolve(
            {
                "started_at": "2026-09-12 02:00:00",
                "observed_at": "2026-09-12 02:30:00",
            },
            [
                self._row(
                    "精确标题 已发布 2026-09-01 09:00",
                    "https://www.toutiao.com/article/500000005/",
                )
            ],
        )
        current_row = self._resolve(
            {
                "started_at": "2026-09-12 02:00:00",
                "observed_at": "2026-09-12 02:30:00",
            },
            [
                self._row(
                    "精确标题 已发布 2026-09-12 10:10",
                    "https://www.toutiao.com/article/500000005/",
                )
            ],
        )

        self.assertEqual(no_row_time["state"], "processing")
        self.assertEqual(old_row["state"], "processing")
        self.assertEqual(current_row["state"], "published")

    def test_old_title_from_eight_hours_before_utc_attempt_is_not_current(self):
        old_row = self._resolve(
            {
                # SQLite CURRENT_TIMESTAMP: 10:00 UTC = 18:00 China time.
                "started_at": "2026-09-12 10:00:00",
                "observed_at": "2026-09-12 10:30:00",
            },
            [
                self._row(
                    # 12:00 China time = 04:00 UTC, six hours before attempt.
                    "精确标题 已发布 2026-09-12 12:00",
                    "https://www.toutiao.com/article/800000008/",
                )
            ],
        )
        current_row = self._resolve(
            {
                "started_at": "2026-09-12 10:00:00",
                "observed_at": "2026-09-12 10:30:00",
            },
            [
                self._row(
                    # 18:10 China time = 10:10 UTC, inside this attempt.
                    "精确标题 已发布 2026-09-12 18:10",
                    "https://www.toutiao.com/article/900000009/",
                )
            ],
        )

        self.assertEqual(old_row["state"], "processing")
        self.assertNotIn("result_url", old_row)
        self.assertEqual(current_row["state"], "published")

    def test_unknown_foreign_page_timezone_never_uses_naive_display_time(self):
        policy = _POLICIES["tiktok"]
        result = _resolve_exact_title_rows(
            {
                "started_at": "2026-09-12 10:00:00",
                "observed_at": "2026-09-12 10:30:00",
            },
            [
                {
                    "text": "精确标题 Published 2026-09-12 10:10",
                    "urls": ("https://www.tiktok.com/@tester/video/1234567890123456789",),
                }
            ],
            platform_name=policy.name,
            public_url_patterns=policy.public_url_patterns,
            display_timezone=policy.display_timezone,
        )

        self.assertEqual(result["state"], "processing")
        self.assertNotIn("result_url", result)

    def test_stable_result_url_binds_only_the_matching_duplicate(self):
        result = self._resolve(
            {
                "result_url": "https://www.toutiao.com/article/700000007/",
                "started_at": "2026-09-12 10:00:00",
            },
            [
                self._row(
                    "精确标题 未通过",
                    "https://www.toutiao.com/article/600000006/",
                ),
                self._row(
                    "精确标题 已发布",
                    "https://www.toutiao.com/article/700000007/",
                ),
            ],
        )

        self.assertEqual(result["state"], "published")
        self.assertEqual(
            result["result_url"],
            "https://www.toutiao.com/article/700000007/",
        )

    def _resolve(self, snapshot, rows):
        policy = _POLICIES["toutiao"]
        return _resolve_exact_title_rows(
            snapshot,
            rows,
            platform_name=policy.name,
            public_url_patterns=policy.public_url_patterns,
            display_timezone=policy.display_timezone,
        )

    @staticmethod
    def _row(text, *urls):
        return {"text": text, "urls": urls}


if __name__ == "__main__":
    unittest.main()
