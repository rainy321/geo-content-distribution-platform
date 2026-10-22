import unittest
from services.publish_messages import public_publish_message


class PublishMessagesTests(unittest.TestCase):
    def test_legacy_preflight_browser_error_is_readable_without_private_path(self):
        raw = "知乎公开文章幂等检查失败：BrowserType.launch: Executable doesn't exist at C:\\Users\\private-user\\app\\chromium_headless_shell-1169\\headless_shell.exe; playwright install"
        result = public_publish_message(raw, 'zhihu')
        self.assertIn('发文前检查未完成', result)
        self.assertIn('尚未进入填稿或发布', result)
        for marker in ('private-user', 'BrowserType', 'headless_shell', 'playwright install'):
            self.assertNotIn(marker, result)
        self.assertIn('private-user', raw)

    def test_post_submission_technical_error_does_not_claim_no_submission(self):
        result = public_publish_message('知乎发布失败：BrowserType.launch: private diagnostics', 'zhihu')
        self.assertIn('最终状态尚未确认', result)
        self.assertIn('避免重复发布', result)
        self.assertNotIn('尚未进入', result)

    def test_regular_human_action_and_other_platform_messages_are_preserved(self):
        for text, platform in [('请在原知乎窗口完成安全验证', 'zhihu'), ('账号检查未完成', 'toutiao'), ('', 'zhihu')]:
            self.assertEqual(public_publish_message(text, platform), text)

    def test_projection_does_not_change_job_or_status(self):
        job = {'platform':'zhihu', 'status':'failed', 'message':'知乎公开文章幂等检查失败：BrowserType.launch: private'}
        before = dict(job)
        public_publish_message(job['message'], job['platform'])
        self.assertEqual(job, before)

    def test_legacy_page_and_locator_diagnostics_are_not_user_messages(self):
        for raw in ('Page.goto: net::ERR_FAILED', 'Locator.fill: private text', 'Timeout 30000ms exceeded', 'Target page, context or browser has been closed'):
            result = public_publish_message('知乎发布失败：' + raw, 'zhihu')
            self.assertNotIn(raw, result)
            self.assertIn('最终状态尚未确认', result)


if __name__ == '__main__':
    unittest.main()
