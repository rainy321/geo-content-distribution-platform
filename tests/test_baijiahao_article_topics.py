import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uploader.baijiahao_uploader.main import BaiJiaHaoArticle


class BaiJiaHaoArticleTopicTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _publisher(tags, *, enable_topic_picker=False):
        return BaiJiaHaoArticle(
            title="合规测试标题",
            body="合规测试正文",
            tags=tags,
            publish_date=0,
            account_file="synthetic-account.json",
            enable_topic_picker=enable_topic_picker,
        )

    @staticmethod
    def _page_and_editor():
        page = Mock()
        page.keyboard = Mock()
        page.keyboard.press = AsyncMock()
        page.keyboard.type = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        editor = Mock()
        editor.evaluate = AsyncMock(return_value=False)
        editor.inner_text = AsyncMock(return_value="合规测试正文")
        return page, editor

    @staticmethod
    def _stub_topic_setup(publisher, editor):
        publisher._get_body_editable = AsyncMock(return_value=editor)
        publisher._editor_contains_confirmed_topic = AsyncMock(return_value=False)
        publisher._move_caret_to_editor_end = AsyncMock()
        publisher._ensure_editor_focused = AsyncMock(return_value=True)
        publisher._count_confirmed_topics = AsyncMock(return_value=0)
        publisher._type_topic_query = AsyncMock()
        publisher._clear_partial_topic_input = AsyncMock()

    def test_only_first_cleaned_unique_topic_is_eligible(self):
        publisher = self._publisher([
            "  #首选话题  ",
            "备用话题",
            "#首选话题",
        ])

        self.assertEqual(publisher._normalize_tags(), ["首选话题"])

    async def test_default_skips_picker_without_typing_or_deleting(self):
        publisher = self._publisher(["首选话题", "备用话题"])
        page, editor = self._page_and_editor()
        publisher._attempt_single_topic = AsyncMock()
        publisher._clear_partial_topic_input = AsyncMock()

        outcome = await publisher.insert_topics_in_body(page, editor)

        self.assertEqual(outcome, "topic_skipped_disabled")
        publisher._attempt_single_topic.assert_not_awaited()
        publisher._clear_partial_topic_input.assert_not_awaited()
        page.keyboard.type.assert_not_awaited()
        page.keyboard.press.assert_not_awaited()

    async def test_no_match_does_not_retype_or_try_fallback_topics(self):
        publisher = self._publisher(
            ["首选话题", "备用话题", "第三话题"],
            enable_topic_picker=True,
        )
        page, editor = self._page_and_editor()
        self._stub_topic_setup(publisher, editor)
        publisher._click_topic_suggestion = AsyncMock(return_value=(False, ""))
        publisher._topic_click_succeeded = AsyncMock(return_value=False)

        outcome = await publisher.insert_topics_in_body(page, editor)

        self.assertEqual(outcome, "topic_skipped_no_match")
        publisher._type_topic_query.assert_awaited_once_with(page, editor, "首选话题")
        publisher._click_topic_suggestion.assert_awaited_once_with(
            page,
            "首选话题",
            editor=editor,
            before_nodes=0,
        )
        publisher._topic_click_succeeded.assert_not_awaited()
        publisher._clear_partial_topic_input.assert_awaited_once_with(
            page, editor, "首选话题"
        )

    async def test_total_budget_includes_editor_preparation_and_returns_control(self):
        publisher = self._publisher(
            ["首选话题", "备用话题"],
            enable_topic_picker=True,
        )
        page, editor = self._page_and_editor()
        self._stub_topic_setup(publisher, editor)

        async def never_finishes(*_args, **_kwargs):
            await asyncio.Event().wait()

        publisher._move_caret_to_editor_end = AsyncMock(side_effect=never_finishes)
        publisher._click_topic_suggestion = AsyncMock()
        continued_step = AsyncMock()

        async def topic_then_continue():
            outcome = await publisher.insert_topics_in_body(page, editor)
            await continued_step()
            return outcome

        with patch(
            "uploader.baijiahao_uploader.main.BAIJIAHAO_TOPIC_TOTAL_TIMEOUT_SECONDS",
            0.01,
        ):
            outcome = await asyncio.wait_for(topic_then_continue(), timeout=0.25)

        self.assertEqual(outcome, "topic_skipped_timeout")
        publisher._move_caret_to_editor_end.assert_awaited_once_with(page, editor)
        publisher._type_topic_query.assert_not_awaited()
        publisher._click_topic_suggestion.assert_not_awaited()
        publisher._clear_partial_topic_input.assert_awaited_once_with(
            page, editor, "首选话题"
        )
        continued_step.assert_awaited_once()

    async def test_cleanup_is_also_bounded_after_topic_timeout(self):
        publisher = self._publisher(["首选话题"], enable_topic_picker=True)
        page, editor = self._page_and_editor()

        async def never_finishes(*_args, **_kwargs):
            await asyncio.Event().wait()

        publisher._attempt_single_topic = AsyncMock(side_effect=never_finishes)
        publisher._clear_partial_topic_input = AsyncMock(side_effect=never_finishes)

        with (
            patch(
                "uploader.baijiahao_uploader.main.BAIJIAHAO_TOPIC_TOTAL_TIMEOUT_SECONDS",
                0.01,
            ),
            patch(
                "uploader.baijiahao_uploader.main.BAIJIAHAO_TOPIC_CLEANUP_TIMEOUT_SECONDS",
                0.01,
            ),
        ):
            outcome = await asyncio.wait_for(
                publisher.insert_topics_in_body(page, editor),
                timeout=0.25,
            )

        self.assertEqual(outcome, "topic_skipped_timeout")
        publisher._attempt_single_topic.assert_awaited_once_with(
            page, editor, "首选话题"
        )
        publisher._clear_partial_topic_input.assert_awaited_once_with(
            page, editor, "首选话题"
        )


if __name__ == "__main__":
    unittest.main()
