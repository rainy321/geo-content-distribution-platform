import unittest

from uploader.douyin_uploader.main import DouYinNote
from uploader.ks_uploader.main import KSNote


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


if __name__ == "__main__":
    unittest.main()
