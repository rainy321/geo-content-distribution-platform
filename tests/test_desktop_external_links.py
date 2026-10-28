"""Search links use the normal OS browser without collector or progress writes."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.desktop_external_links import open_search_portal
from services.desktop_geo_jobs import DesktopGeoJobs


class DesktopExternalLinkTests(unittest.TestCase):
    def test_every_official_purpose_is_opened_as_a_url_without_browser_arguments(self):
        urls = {
            ('baidu', 'entry'): 'https://ziyuan.baidu.com/site/index',
            ('baidu', 'help'): 'https://ziyuan.baidu.com/college/articleinfo?id=1003',
            ('baidu', 'submit'): 'https://ziyuan.baidu.com/linksubmit/index',
            ('toutiao', 'entry'): 'https://zhanzhang.toutiao.com/',
            ('toutiao', 'help'): 'https://zhanzhang.toutiao.com/page/outer/docs',
            ('toutiao', 'submit'): 'https://zhanzhang.toutiao.com/',
        }
        with patch('services.desktop_external_links.os.name', 'nt'), \
                patch('services.desktop_external_links.os.startfile', create=True) as opener, \
                patch('services.desktop_external_links.webbrowser.open_new_tab') as other:
            for (provider, purpose), url in urls.items():
                with self.subTest(provider=provider, purpose=purpose):
                    result = open_search_portal(provider, purpose)
                    opener.assert_called_with(url, 'open')
                    self.assertEqual(result, {'launch_requested': True, 'provider': provider,
                                             'purpose': purpose, 'url': url})
                    self.assertNotIn('success', result)
            self.assertEqual(opener.call_count, 6)
            other.assert_not_called()

    def test_supplied_urls_commands_and_non_string_identifiers_never_reach_the_os(self):
        invalid = [None, True, 1, [], {}, 'unknown', 'https://example.org/', 'javascript:alert(1)',
                   'file:///C:/Windows/System32/cmd.exe', 'baidu & calc.exe', '__proto__']
        with patch('services.desktop_external_links.os.startfile', create=True) as opener, \
                patch('services.desktop_external_links.webbrowser.open_new_tab') as other:
            for value in invalid:
                for provider, purpose in [(value, 'entry'), ('baidu', value)]:
                    with self.subTest(provider=provider, purpose=purpose):
                        result = open_search_portal(provider, purpose)
                        self.assertIs(result['launch_requested'], False)
                        self.assertEqual(result['reason'], 'invalid_search_portal')
            opener.assert_not_called()
            other.assert_not_called()

    def test_os_failure_returns_readable_error_without_claiming_page_or_login_success(self):
        with patch('services.desktop_external_links.os.name', 'nt'), \
                patch('services.desktop_external_links.os.startfile', create=True, side_effect=OSError('synthetic failure')), \
                self.assertLogs(level='ERROR') as logs:
            result = open_search_portal('baidu', 'entry')
        self.assertIs(result['launch_requested'], False)
        self.assertEqual(result['reason'], 'browser_request_rejected')
        self.assertNotIn('synthetic failure', str(result))
        self.assertEqual(len(logs.records), 1)
        self.assertIn('synthetic failure', logs.output[0])

    def test_unexpected_native_details_are_logged_once_and_never_returned_to_the_bridge(self):
        detail = 'Traceback: C:/SyntheticCustomer/private/profile.json\nRuntimeError: native failure'
        with patch('services.desktop_external_links.os.name', 'nt'), \
                patch('services.desktop_external_links.os.startfile', create=True, side_effect=RuntimeError(detail)), \
                self.assertLogs(level='ERROR') as logs:
            result = open_search_portal('baidu', 'entry')
        self.assertEqual(result, {'launch_requested': False, 'reason': 'browser_request_unconfirmed'})
        self.assertEqual(len(logs.records), 1)
        self.assertIn(detail, logs.output[0])
        self.assertNotIn('SyntheticCustomer', str(result))

    def test_extra_url_or_browser_arguments_cannot_reach_the_os(self):
        with patch('services.desktop_external_links.os.startfile', create=True) as opener, \
                patch('services.desktop_external_links.webbrowser.open_new_tab') as other:
            with self.assertRaises(TypeError):
                open_search_portal('baidu', 'entry', 'https://example.org/')
            with self.assertRaises(TypeError):
                open_search_portal('baidu', 'entry', browser_args=['--profile-directory=private'])
            opener.assert_not_called()
            other.assert_not_called()

    def test_non_windows_browser_rejection_is_not_a_success_receipt(self):
        with patch('services.desktop_external_links.os.name', 'posix'), \
                patch('services.desktop_external_links.webbrowser.open_new_tab', return_value=False) as opener:
            result = open_search_portal('toutiao', 'help')
        opener.assert_called_once_with('https://zhanzhang.toutiao.com/page/outer/docs')
        self.assertIs(result['launch_requested'], False)

    def test_public_bridge_preserves_running_task_and_does_not_write_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            jobs = DesktopGeoJobs(data)
            jobs.job = {'id': 'current-task', 'status': 'running', 'kind': 'run', 'waiting_for_user': True}
            before = copy.deepcopy(jobs.job)
            paths = set(data.rglob('*'))
            with patch.object(jobs, '_start') as start, \
                    patch('services.desktop_external_links.open_search_portal', return_value={'launch_requested': True}) as opener:
                result = jobs.open_search_portal('baidu', 'entry')
            self.assertEqual(result, {'launch_requested': True})
            opener.assert_called_once_with('baidu', 'entry')
            start.assert_not_called()
            self.assertEqual(jobs.job, before)
            self.assertEqual(set(data.rglob('*')), paths)
            self.assertFalse((data / 'database.db').exists())
