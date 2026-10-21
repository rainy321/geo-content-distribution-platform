import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from services import browser_runtime


class BrowserSessionChoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name) / 'profile'
        self.alternate = self.profile.with_name('profile-chrome')
        self.marker = self.profile / browser_runtime.SELECTION_FILE
        self.options = {'executable_path': 'edge', 'headless': False, 'args': ['--example']}

    def playwright(self, effects):
        launch = AsyncMock(side_effect=effects)
        return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch)), launch

    async def choose_chrome(self):
        context = SimpleNamespace(close=AsyncMock())
        playwright, launch = self.playwright([RuntimeError('synthetic startup failure'), context])
        result = await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome')
        self.assertIs(result, context)
        return context, launch

    async def test_primary_success_does_not_choose_existing_suffix_or_write_marker(self):
        self.profile.mkdir()
        self.alternate.mkdir()
        primary_data, alternate_data = self.profile / 'Cookies', self.alternate / 'Cookies'
        primary_data.write_bytes(b'primary session sentinel')
        alternate_data.write_bytes(b'fallback session sentinel')
        context = object()
        playwright, launch = self.playwright([context])
        self.assertIs(await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome'), context)
        self.assertEqual(launch.await_count, 1)
        self.assertEqual(launch.call_args.args[0], str(self.profile))
        self.assertEqual(primary_data.read_bytes(), b'primary session sentinel')
        self.assertEqual(alternate_data.read_bytes(), b'fallback session sentinel')
        self.assertFalse(self.marker.exists())

    async def test_successful_fallback_is_remembered_without_paths_and_preferred_later(self):
        original_options = {**self.options, 'args': list(self.options['args'])}
        _, first = await self.choose_chrome()
        self.assertEqual(first.await_count, 2)
        marker = json.loads(self.marker.read_text())
        expected_identity = hashlib.sha256(os.fsencode(os.path.normcase(os.path.abspath('chrome')))).hexdigest()
        self.assertEqual(marker, {'schema_version': 1, 'engine': 'chrome', 'profile_suffix': '-chrome',
                                  'executable_sha256': expected_identity})
        self.assertNotIn(str(self.profile), self.marker.read_text())
        self.assertEqual(self.options, original_options)
        primary_data, alternate_data = self.profile / 'Cookies', self.alternate / 'Cookies'
        primary_data.write_bytes(b'edge remains independent')
        alternate_data.write_bytes(b'chrome stays logged in')
        next_context = object()
        playwright, launch = self.playwright([next_context])
        self.assertIs(await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome'), next_context)
        launch.assert_awaited_once_with(str(self.alternate), **{**self.options, 'executable_path': 'chrome'})
        self.assertEqual(primary_data.read_bytes(), b'edge remains independent')
        self.assertEqual(alternate_data.read_bytes(), b'chrome stays logged in')

    async def test_marker_is_not_created_until_fallback_launch_succeeds(self):
        seen = []
        context = SimpleNamespace(close=AsyncMock())

        async def launch(path, **options):
            self.assertFalse(self.marker.exists())
            seen.append(path)
            if len(seen) == 1:
                raise RuntimeError('primary failed')
            return context

        playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=AsyncMock(side_effect=launch)))
        self.assertIs(await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome'), context)
        self.assertTrue(self.marker.exists())
        self.assertEqual(seen, [str(self.profile), str(self.alternate)])

    async def test_failed_fallback_leaves_no_choice(self):
        failure = RuntimeError('fallback failed')
        playwright, launch = self.playwright([RuntimeError('primary failed'), failure])
        with self.assertRaises(RuntimeError) as caught:
            await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome')
        self.assertIs(caught.exception, failure)
        self.assertEqual(launch.await_count, 2)
        self.assertFalse(self.marker.exists())

    async def test_missing_or_identical_fallback_keeps_original_launch_failure(self):
        for fallback in (None, 'edge'):
            with self.subTest(fallback=fallback):
                failure = RuntimeError('primary failed')
                playwright, launch = self.playwright([failure])
                with self.assertRaises(RuntimeError) as caught:
                    await browser_runtime.launch_context(playwright, self.profile, self.options, fallback)
                self.assertIs(caught.exception, failure)
                self.assertEqual(launch.await_count, 1)
                self.assertFalse(self.marker.exists())

    async def test_remembered_chrome_failure_never_starts_primary(self):
        await self.choose_chrome()
        original_marker = self.marker.read_bytes()
        failure = RuntimeError('selected browser failed')
        playwright, launch = self.playwright([failure])
        with self.assertRaises(RuntimeError) as caught:
            await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome')
        self.assertIs(caught.exception, failure)
        launch.assert_awaited_once_with(str(self.alternate), **{**self.options, 'executable_path': 'chrome'})
        self.assertEqual(self.marker.read_bytes(), original_marker)

    async def test_changed_or_missing_fallback_configuration_pauses_without_launch(self):
        await self.choose_chrome()
        original_marker = self.marker.read_bytes()
        for options, fallback in ((self.options, 'another-chrome'), (self.options, None),
                                  ({**self.options, 'executable_path': 'chrome'}, 'chrome')):
            with self.subTest(fallback=fallback):
                playwright, launch = self.playwright([])
                with self.assertRaisesRegex(browser_runtime.BrowserSessionSelectionError, '配置已变化'):
                    await browser_runtime.launch_context(playwright, self.profile, options, fallback)
                launch.assert_not_awaited()
                self.assertEqual(self.marker.read_bytes(), original_marker)

    async def test_corrupt_or_untrusted_marker_pauses_without_opening_a_profile(self):
        self.profile.mkdir()
        valid = {'schema_version': 1, 'engine': 'chrome', 'profile_suffix': '-chrome', 'executable_sha256': '0' * 64}
        cases = ['invalid JSON private-value', 'x' * 1025, '[]',
                 json.dumps({**valid, 'schema_version': True}),
                 json.dumps({**valid, 'engine': 'edge'}),
                 json.dumps({**valid, 'profile_suffix': '../other-profile'}),
                 json.dumps({**valid, 'executable_sha256': 'private-value'}),
                 json.dumps({**valid, 'executable_path': 'private-value'})]
        for content in cases:
            with self.subTest(content=content[:30]):
                self.marker.write_text(content)
                playwright, launch = self.playwright([])
                with self.assertRaises(browser_runtime.BrowserSessionSelectionError) as caught:
                    await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome')
                self.assertNotIn('private-value', str(caught.exception))
                self.assertNotIn(str(self.profile), str(caught.exception))
                launch.assert_not_awaited()
                self.assertEqual(self.marker.read_text(), content)

    async def test_marker_read_failure_is_conservative_and_does_not_expose_paths(self):
        self.profile.mkdir()
        self.marker.write_text('{}')
        playwright, launch = self.playwright([])
        with patch.object(Path, 'open', side_effect=PermissionError('private-value')):
            with self.assertRaises(browser_runtime.BrowserSessionSelectionError) as caught:
                await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome')
        self.assertNotIn('private-value', str(caught.exception))
        launch.assert_not_awaited()

    async def test_marker_write_failure_closes_started_context_and_cleans_temporary_file(self):
        context = SimpleNamespace(close=AsyncMock())
        playwright, launch = self.playwright([RuntimeError('primary failed'), context])
        with patch('services.browser_runtime.os.link', side_effect=PermissionError('private-value')):
            with self.assertRaises(browser_runtime.BrowserSessionSelectionError) as caught:
                await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome')
        self.assertNotIn('private-value', str(caught.exception))
        context.close.assert_awaited_once()
        self.assertEqual(launch.await_count, 2)
        self.assertFalse(self.marker.exists())
        self.assertEqual(list(self.profile.glob('.geo-browser-selection-*.tmp')), [])

    async def test_close_failure_after_marker_error_does_not_return_context_or_expose_errors(self):
        context = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError('private-close-value')))
        playwright, _ = self.playwright([RuntimeError('primary failed'), context])
        with patch('services.browser_runtime.os.link', side_effect=PermissionError('private-write-value')):
            with self.assertRaises(browser_runtime.BrowserSessionSelectionError) as caught:
                await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome')
        self.assertNotIn('private-close-value', str(caught.exception))
        self.assertNotIn('private-write-value', str(caught.exception))
        context.close.assert_awaited_once()

    async def test_unknown_concurrent_marker_is_not_overwritten(self):
        context = SimpleNamespace(close=AsyncMock())
        playwright, _ = self.playwright([RuntimeError('primary failed'), context])
        unknown = b'{"executable_path":"private-value","engine":"unexpected"}'

        def appeared(source, destination):
            Path(destination).write_bytes(unknown)
            raise FileExistsError()

        with patch('services.browser_runtime.os.link', side_effect=appeared):
            with self.assertRaises(browser_runtime.BrowserSessionSelectionError):
                await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome')
        self.assertEqual(self.marker.read_bytes(), unknown)
        context.close.assert_awaited_once()

    async def test_identical_concurrent_marker_is_reused_without_replacing_it(self):
        context = SimpleNamespace(close=AsyncMock())
        playwright, _ = self.playwright([RuntimeError('primary failed'), context])
        appeared_bytes = []

        def appeared(source, destination):
            data = Path(source).read_bytes()
            Path(destination).write_bytes(data)
            appeared_bytes.append(data)
            raise FileExistsError()

        with patch('services.browser_runtime.os.link', side_effect=appeared):
            self.assertIs(await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome'), context)
        self.assertEqual(self.marker.read_bytes(), appeared_bytes[0])
        context.close.assert_not_awaited()

    async def test_missing_remembered_profile_is_not_recreated_as_empty_session(self):
        await self.choose_chrome()
        self.alternate.rmdir()
        playwright, launch = self.playwright([])
        with self.assertRaisesRegex(browser_runtime.BrowserSessionSelectionError, '会话目录无法确认'):
            await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome')
        self.assertFalse(self.alternate.exists())
        launch.assert_not_awaited()

    async def test_remembered_profile_alias_is_not_opened(self):
        await self.choose_chrome()
        playwright, launch = self.playwright([])
        with patch.object(Path, 'is_symlink', return_value=True):
            with self.assertRaisesRegex(browser_runtime.BrowserSessionSelectionError, '会话目录无法确认'):
                await browser_runtime.launch_context(playwright, self.profile, self.options, 'chrome')
        launch.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
