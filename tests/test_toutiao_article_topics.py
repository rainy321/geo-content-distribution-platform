import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from uploader.toutiao_uploader.main import (
    TOUTIAO_TOPIC_NO_MATCH_TEXT,
    TouTiaoArticle,
)


class ToutiaoArticleTopicMatchingTests(unittest.TestCase):
    def setUp(self):
        self.publisher = TouTiaoArticle(
            title="测试标题",
            body="测试正文",
            tags=[],
            publish_date=0,
            account_file="unused.json",
        )

    def test_accepts_exact_topic_candidate(self):
        self.assertTrue(
            self.publisher._is_real_topic_dropdown_item(
                "#内容分发# 13讨论",
                "内容分发",
            )
        )

    def test_accepts_exact_topic_when_spacing_differs(self):
        self.assertTrue(
            self.publisher._is_real_topic_dropdown_item(
                "#AI内容# 20讨论",
                "AI 内容",
            )
        )

    def test_rejects_candidate_that_only_contains_requested_topic(self):
        self.assertFalse(
            self.publisher._is_real_topic_dropdown_item(
                "#心脏功能测试# 16讨论",
                "功能测试",
            )
        )
        self.assertFalse(
            self.publisher._is_real_topic_dropdown_item(
                "#今日头条微头条# 36万讨论",
                "今日头条",
            )
        )

    def test_ranking_drops_non_exact_topics(self):
        candidates = [
            {"text": "#心脏功能测试# 16讨论", "score": 99},
            {"text": "#功能测试# 3讨论", "score": 10},
        ]

        ranked = self.publisher._rank_topic_candidates(candidates, "功能测试")

        self.assertEqual(["#功能测试# 3讨论"], [item["text"] for item in ranked])

    def test_only_first_cleaned_topic_is_eligible(self):
        self.publisher.tags = ["  ### AI  接入 ###  ", "第二个话题"]

        self.assertEqual(["AI 接入"], self.publisher._normalize_tags())


class _FakeTopicPage:
    def __init__(self):
        self.keyboard = SimpleNamespace(type=AsyncMock(), press=AsyncMock())
        self.wait_for_timeout = AsyncMock()
        self.evaluate = AsyncMock(return_value=False)


class _FakeTopicEditor:
    def __init__(self, text="测试正文"):
        self.inner_text = AsyncMock(return_value=text)
        self.evaluate = AsyncMock(return_value="")


class ToutiaoArticleTopicFlowTests(unittest.IsolatedAsyncioTestCase):
    def _publisher(self, tags=None, body="测试正文"):
        return TouTiaoArticle(
            title="测试标题",
            body=body,
            tags=tags or [],
            publish_date=0,
            account_file="unused.json",
        )

    @staticmethod
    def _prepare_attempt(publisher):
        publisher._move_caret_to_editor_end = AsyncMock()
        publisher._count_topic_nodes_in_editor = AsyncMock(return_value=0)

    async def test_no_match_stops_after_one_query_and_does_not_rotate_tags(self):
        publisher = self._publisher(["###首选话题###", "备用话题", "第三话题"])
        page = _FakeTopicPage()
        editor = _FakeTopicEditor()
        self._prepare_attempt(publisher)
        publisher._topic_no_match_visible = AsyncMock(return_value=True)
        publisher._find_topic_dropdown_candidates = AsyncMock(return_value=[])
        publisher._clear_partial_topic_input = AsyncMock()
        publisher._close_topic_picker = AsyncMock()

        outcome = await publisher.insert_topics_in_body(page, editor)

        self.assertEqual("topic_skipped_no_match", outcome)
        typed_queries = [
            call.args[0]
            for call in page.keyboard.type.await_args_list
            if call.args and call.args[0].startswith("#")
        ]
        self.assertEqual(["#首选话题"], typed_queries)
        publisher._topic_no_match_visible.assert_awaited_once()
        publisher._find_topic_dropdown_candidates.assert_not_awaited()
        publisher._clear_partial_topic_input.assert_awaited_once_with(
            page, editor, "首选话题"
        )
        publisher._close_topic_picker.assert_awaited_once_with(page, editor)

    async def test_exact_match_types_once_and_clicks_one_candidate_once(self):
        publisher = self._publisher(["首选话题", "备用话题"])
        page = _FakeTopicPage()
        editor = _FakeTopicEditor()
        self._prepare_attempt(publisher)
        publisher._wait_for_topic_dropdown = AsyncMock(
            return_value=(
                "match",
                [{"text": "#首选话题# 3讨论", "x": 10.0, "y": 20.0}],
            )
        )
        publisher._mouse_click_xy = AsyncMock()
        publisher._topic_click_succeeded = AsyncMock(return_value=True)
        publisher._clear_partial_topic_input = AsyncMock()
        publisher._close_topic_picker = AsyncMock()

        outcome = await publisher.insert_topics_in_body(page, editor)

        self.assertEqual("topic_selected", outcome)
        typed_queries = [
            call.args[0]
            for call in page.keyboard.type.await_args_list
            if call.args and call.args[0].startswith("#")
        ]
        self.assertEqual(["#首选话题"], typed_queries)
        publisher._wait_for_topic_dropdown.assert_awaited_once_with(page, "首选话题")
        publisher._mouse_click_xy.assert_awaited_once_with(page, 10.0, 20.0)
        publisher._topic_click_succeeded.assert_awaited_once()
        publisher._clear_partial_topic_input.assert_not_awaited()
        publisher._close_topic_picker.assert_not_awaited()

    async def test_unknown_dropdown_state_stops_topic_work_and_continues(self):
        publisher = self._publisher(["首选话题", "备用话题"])
        page = _FakeTopicPage()
        editor = _FakeTopicEditor()
        self._prepare_attempt(publisher)
        publisher._wait_for_topic_dropdown = AsyncMock(return_value=("unknown", []))
        publisher._mouse_click_xy = AsyncMock()
        publisher._clear_partial_topic_input = AsyncMock()
        publisher._close_topic_picker = AsyncMock()

        outcome = await publisher.insert_topics_in_body(page, editor)

        self.assertEqual("topic_skipped_unknown", outcome)
        publisher._wait_for_topic_dropdown.assert_awaited_once()
        publisher._mouse_click_xy.assert_not_awaited()
        publisher._clear_partial_topic_input.assert_awaited_once()
        publisher._close_topic_picker.assert_awaited_once()

    async def test_topic_flow_has_hard_total_timeout(self):
        publisher = self._publisher(["首选话题", "备用话题"])
        page = _FakeTopicPage()
        editor = _FakeTopicEditor()

        async def never_finishes(*_args):
            await asyncio.Event().wait()

        publisher._attempt_single_topic = AsyncMock(side_effect=never_finishes)
        publisher._clear_partial_topic_input = AsyncMock()
        publisher._close_topic_picker = AsyncMock()

        with patch(
            "uploader.toutiao_uploader.main.TOUTIAO_TOPIC_TOTAL_TIMEOUT_SECONDS",
            0.01,
        ):
            outcome = await asyncio.wait_for(
                publisher.insert_topics_in_body(page, editor), timeout=0.25
            )

        self.assertEqual("topic_skipped_timeout", outcome)
        publisher._attempt_single_topic.assert_awaited_once_with(
            page, editor, "首选话题"
        )
        publisher._clear_partial_topic_input.assert_awaited_once()
        publisher._close_topic_picker.assert_awaited_once()

    async def test_no_match_detector_uses_platform_message(self):
        publisher = self._publisher(["首选话题"])
        page = _FakeTopicPage()
        page.evaluate.return_value = True

        self.assertTrue(await publisher._topic_no_match_visible(page))
        self.assertEqual(
            TOUTIAO_TOPIC_NO_MATCH_TEXT,
            page.evaluate.await_args.args[1],
        )

    async def test_tags_do_not_replace_required_article_body(self):
        publisher = self._publisher(["只有话题"], body="")

        with self.assertRaisesRegex(ValueError, "文章正文不能为空"):
            await publisher.fill_body(_FakeTopicPage())


if __name__ == "__main__":
    unittest.main()
