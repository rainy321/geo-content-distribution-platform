import inspect
import unittest

from uploader.douyin_uploader.main import DouYinNote
from uploader.ks_uploader.main import KUAISHOU_MANAGE_URL_PATTERN, KSNote


class P2NotePublishSafetyTests(unittest.TestCase):
    def test_douyin_note_reuses_complete_ai_declaration_helpers(self):
        for method_name in (
            "set_ai_generated_declaration",
            "_is_ai_declaration_dialog_open",
            "_wait_ai_declaration_dialog",
            "_select_ai_generated_radio",
            "_click_ai_declaration_confirm",
        ):
            self.assertTrue(callable(getattr(DouYinNote, method_name, None)))

    def test_kuaishou_note_reuses_complete_ai_declaration_helpers(self):
        for method_name in (
            "_locate_author_declaration_select",
            "set_ai_generated_declaration",
        ):
            self.assertTrue(callable(getattr(KSNote, method_name, None)))

    def test_note_uploaders_expose_page_variant_and_single_submit_helpers(self):
        for uploader_class, method_names in (
            (DouYinNote, ("_switch_to_image_post", "_upload_note_images")),
            (KSNote, ("_switch_to_note_publish", "_upload_note_images")),
        ):
            for method_name in method_names:
                self.assertTrue(callable(getattr(uploader_class, method_name, None)))

        self.assertEqual(KUAISHOU_MANAGE_URL_PATTERN, "**/article/manage/**")

    def test_douyin_note_repairs_missing_tab_with_bounded_direct_route(self):
        switch_source = inspect.getsource(DouYinNote._switch_to_image_post)
        upload_source = inspect.getsource(DouYinNote.upload_note_content)

        self.assertIn("/creator-micro/content/post/image?enter_from=publish_page", switch_source)
        self.assertIn('wait_until="domcontentloaded"', switch_source)
        self.assertIn("for wait_round in range(60)", upload_source)
        self.assertIn("尚未提交", upload_source)
        self.assertNotIn("while True", upload_source)


if __name__ == "__main__":
    unittest.main()
