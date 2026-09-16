import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_sohu_policy_uses_current_content_management_page(self):
        self.assertEqual(
            _POLICIES["sohu"].manage_url,
            "https://mp.sohu.com/mpfe/v4/contentManagement/first/page",
        )
        self.assertTrue(_POLICIES["sohu"].read_rejection_details)

    def test_dynamic_management_list_waits_for_exact_title_status(self):
        class Page:
            def __init__(self):
                self.waits = []

            def wait_for_timeout(self, milliseconds):
                self.waits.append(milliseconds)

        page = Page()
        final_rows = [{"text": "精确标题 未通过 2026-09-16 10:10", "urls": ()}]
        with patch.object(
            PlatformReviewVerifier,
            "_exact_title_rows",
            side_effect=[[], [{"text": "精确标题", "urls": ()}], final_rows],
        ):
            rows = self.verifier._wait_for_exact_title_rows(
                page,
                "精确标题",
                _POLICIES["sohu"].manage_url,
                _POLICIES["sohu"].public_url_patterns,
                read_rejection_details=False,
            )

        self.assertEqual(rows, final_rows)
        self.assertEqual(len(page.waits), 2)

    def test_title_row_prefers_deeper_status_over_shallow_url_only_ancestor(self):
        class Locator:
            def __init__(self, nodes=()):
                self.nodes = list(nodes)

            def count(self):
                return len(self.nodes)

            def nth(self, index):
                return self.nodes[index]

        class Anchor:
            def __init__(self, href):
                self.href = href

            def get_attribute(self, name):
                return self.href if name == "href" else None

        class Node:
            def __init__(self, text, *hrefs):
                self.text = text
                self.hrefs = hrefs

            def inner_text(self):
                return self.text

            def locator(self, selector):
                if selector == "a[href]":
                    return Locator([Anchor(href) for href in self.hrefs])
                return Locator()

        class Candidate(Node):
            def __init__(self, text, ancestors):
                super().__init__(text)
                self.ancestors = ancestors

            def is_visible(self):
                return True

            def locator(self, selector):
                if selector.startswith("xpath="):
                    return self.ancestors[selector.count("/..")]
                return super().locator(selector)

        class Page:
            def __init__(self, candidate):
                self.candidate = candidate

            def get_by_text(self, text, exact):
                if text == "精确标题" and exact:
                    return Locator([self.candidate])
                return Locator()

        ancestors = {
            1: Node("精确标题", "/mpfe/v4/edit/123"),
            2: Node(
                "精确标题 未通过 2026-09-16 10:10",
                "/mpfe/v4/edit/123",
            ),
            3: Node("精确标题"),
            4: Node(""),
            5: Node(""),
            6: Node(""),
        }
        rows = PlatformReviewVerifier._exact_title_rows(
            Page(Candidate("精确标题", ancestors)),
            "精确标题",
            _POLICIES["sohu"].manage_url,
            _POLICIES["sohu"].public_url_patterns,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "精确标题 未通过 2026-09-16 10:10")

    def test_sohu_hidden_reason_text_is_read_without_visibility_requirement(self):
        class Locator:
            def __init__(self, nodes=()):
                self.nodes = list(nodes)

            def count(self):
                return len(self.nodes)

            def nth(self, index):
                return self.nodes[index]

        class HiddenReason:
            def text_content(self):
                return "未通过原因：标题营销推广倾向明显"

        class Row:
            def get_attribute(self, _name):
                return None

            def locator(self, selector):
                if selector == ".article-reson-text, .article-reason-text":
                    return Locator([HiddenReason()])
                return Locator()

            def get_by_text(self, _text, exact):
                return Locator()

        class Page:
            def locator(self, _selector):
                return Locator()

        reason = PlatformReviewVerifier._read_rejection_reason(
            Page(),
            Row(),
            title="精确标题",
            row_text="精确标题 未通过 2026-09-16 10:10",
        )

        self.assertEqual(reason, "标题营销推广倾向明显")

    def test_sohu_rejection_reason_is_returned_after_attempt_binding(self):
        policy = _POLICIES["sohu"]
        result = _resolve_exact_title_rows(
            {
                "started_at": "2026-09-16 02:00:00",
                "observed_at": "2026-09-16 02:30:00",
            },
            [
                {
                    "text": "精确标题 未通过 2026-09-16 10:10",
                    "urls": (),
                    "rejection_reason": "标题营销推广倾向明显",
                }
            ],
            platform_name=policy.name,
            public_url_patterns=policy.public_url_patterns,
            display_timezone=policy.display_timezone,
        )

        self.assertEqual(result["state"], "rejected")
        self.assertIn("标题营销推广倾向明显", result["message"])
        self.assertIn("未通过原因", result["evidence"])

    def test_sohu_rejection_tooltip_is_read_by_hover_without_click(self):
        class Locator:
            def __init__(self, nodes=()):
                self.nodes = list(nodes)

            def count(self):
                return len(self.nodes)

            def nth(self, index):
                return self.nodes[index]

        class Element:
            def __init__(self, *, page=None, text="", attributes=None):
                self.page = page
                self.text = text
                self.attributes = attributes or {}

            def get_attribute(self, name):
                return self.attributes.get(name)

            def is_visible(self):
                return True

            def hover(self, timeout):
                self.page.hovered = True

            def inner_text(self):
                return self.text

        class Row(Element):
            def __init__(self, page, target):
                super().__init__(page=page)
                self.target = target

            def locator(self, selector):
                if selector == '[class*="question"]':
                    return Locator([self.target])
                return Locator()

            def get_by_text(self, text, exact):
                if text == "未通过" and exact:
                    return Locator([self.target])
                return Locator()

        class Page:
            def __init__(self):
                self.hovered = False
                self.tooltip = Element(
                    page=self,
                    text="未通过原因：正文营销推广倾向明显",
                )

            def locator(self, _selector):
                return Locator([self.tooltip]) if self.hovered else Locator()

            def wait_for_timeout(self, _milliseconds):
                return None

        page = Page()
        target = Element(page=page, text="未通过")
        reason = PlatformReviewVerifier._read_rejection_reason(
            page,
            Row(page, target),
            title="精确标题",
            row_text="精确标题 未通过 2026-09-16 10:10",
        )

        self.assertTrue(page.hovered)
        self.assertEqual(reason, "正文营销推广倾向明显")

    def test_weibo_reviewer_requires_returned_public_url_not_only_status_id(self):
        marker = self.cookies_dir / "weibo.json"
        marker.write_text("{}", encoding="utf-8")

        class Connector:
            def __init__(self, rows):
                self.rows = rows

            def verify_marker(self, _marker):
                return True

            def recent_status_rows(self, _title, *, count):
                self.count = count
                return self.rows

        snapshot = {
            "platform": "weibo",
            "title": "精确标题",
            "account_file": marker.name,
            "account_status": 1,
            "started_at": "2026-09-15 08:00:00",
            "observed_at": "2026-09-15 08:30:00",
        }
        interval = (("2026-09-15T08:10:00+00:00", "2026-09-15T08:10:01+00:00"),)
        id_only = PlatformReviewVerifier(
            self.cookies_dir,
            navigation_timeout_ms=1_000,
            settle_ms=0,
            weibo_connector_factory=lambda: Connector(
                [
                    {
                        "text": "精确标题 已发布 status_id=1234567890",
                        "urls": (),
                        "external_ids": ("1234567890",),
                        "time_intervals": interval,
                        "public_visibility_confirmed": True,
                        "title_match_confirmed": True,
                    }
                ]
            ),
        )(snapshot)
        public = PlatformReviewVerifier(
            self.cookies_dir,
            navigation_timeout_ms=1_000,
            settle_ms=0,
            weibo_connector_factory=lambda: Connector(
                [
                    {
                        "text": "精确标题 已发布 status_id=1234567890",
                        "urls": ("https://m.weibo.cn/detail/1234567890",),
                        "external_ids": ("1234567890",),
                        "time_intervals": interval,
                        "public_visibility_confirmed": True,
                        "title_match_confirmed": True,
                    }
                ]
            ),
        )(snapshot)

        self.assertEqual(id_only["state"], "processing")
        self.assertNotIn("result_url", id_only)
        self.assertEqual(public["state"], "published")
        self.assertEqual(
            public["result_url"],
            "https://m.weibo.cn/detail/1234567890",
        )

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
