"""Synthetic native observations; no test reads an existing user window."""
import base64
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
import uuid
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import quote

from services import bing_inspection_reader as reader
from services import webmaster_service as webmaster
from services import geo_content_store as store
from services.desktop_geo_jobs import DesktopGeoJobs
from tests.test_geo_research_probes import ProbeFixture


def observed(**changes):
    return {'status': 'observed', 'reason': 'current_visible_page_read', 'index_status': 'indexed',
            'last_crawl_text': '20 Oct 2026 at 10:55', 'page_fetch_text': 'Successful',
            'observed_at': '2026-11-03T01:02:03.0000000Z', **changes}


class NativeTransportTests(unittest.TestCase):
    def invoke(self, stdout=None, **kwargs):
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(observed()).encode() if stdout is None else stdout,
                                    stderr=b'not returned private diagnostic')
        with patch.object(reader.sys, 'platform', 'win32'), \
             patch.object(reader, '_powershell_executable', return_value=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'), \
             patch.object(reader.subprocess, 'run', return_value=completed, **kwargs) as run:
            result = reader.read_existing_page('https://synthetic.example.org/product/')
        return result, run

    def test_full_system_executable_fixed_script_stdin_only_hidden_timeout_and_whitelist(self):
        result, run = self.invoke()
        self.assertEqual(result, observed())
        args, options = run.call_args
        command = args[0]
        self.assertEqual(command[:4], [r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive'])
        self.assertEqual(command[4], '-EncodedCommand')
        self.assertEqual(base64.b64decode(command[5]).decode('utf-16-le'), reader.POWERSHELL_READER)
        self.assertLess(len(' '.join(command)), 32767)
        self.assertEqual(json.loads(options['input']), {'expected_url': 'https://synthetic.example.org/product/'})
        self.assertEqual(options['timeout'], 8)
        self.assertEqual(options['creationflags'], 0x08000000)
        self.assertTrue(options['capture_output'])
        self.assertNotIn('shell', options)
        self.assertNotIn('env', options)
        self.assertNotIn('not returned', json.dumps(result))

    def test_no_windows_is_explicit_unsupported_without_spawn_or_environment(self):
        with patch.object(reader.sys, 'platform', 'linux'), patch.object(reader.subprocess, 'run') as run:
            result = reader.read_existing_page('https://synthetic.example.org/')
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['reason'], 'windows_only')
        self.assertEqual(result['index_status'], 'unknown')
        run.assert_not_called()

    def test_timeout_and_process_failure_are_unknown_not_not_indexed(self):
        result, _ = self.invoke(side_effect=subprocess.TimeoutExpired('fixed program', 8))
        self.assertEqual(result['reason'], 'reader_timeout')
        self.assertEqual(result['index_status'], 'unknown')
        with patch.object(reader.sys, 'platform', 'win32'), patch.object(reader, '_powershell_executable', side_effect=OSError):
            self.assertEqual(reader.read_existing_page('https://synthetic.example.org/')['reason'], 'powershell_unavailable')

    def test_only_whitelisted_fields_and_known_states_can_cross_process_boundary(self):
        invalid = [b'not json', b'x' * 9000, b'{"status":"need_action","status":"observed"}',
            json.dumps({**observed(), 'body': 'synthetic private text'}).encode(),
            json.dumps(observed(index_status='success')).encode(),
            json.dumps(observed(last_crawl_text='synthetic account name')).encode(),
            json.dumps(observed(last_crawl_text='20 Oct 2026 at 10:55Page FetchSuccessful')).encode(),
            json.dumps(observed(page_fetch_text='synthetic entire document')).encode(),
            json.dumps(observed(page_fetch_text='successful')).encode(),
            json.dumps(observed(observed_at='no time')).encode(),
            json.dumps(observed(status='need_action', reason='bing_login_required')).encode()]
        for raw in invalid:
            with self.subTest(raw=raw[:45]):
                result, _ = self.invoke(raw)
                self.assertEqual(result['reason'], 'invalid_reader_result')
                self.assertEqual(result['index_status'], 'unknown')
        for reason in ('bing_login_required', 'bing_verification_required', 'inspection_input_mismatch',
                       'multiple_bing_inspection_windows', 'index_result_not_visible'):
            expected = reader._failure(reason)
            result, _ = self.invoke(json.dumps(expected).encode())
            self.assertEqual(result, expected)

    def test_fixed_script_contains_no_desktop_mutation_or_data_export(self):
        code = reader.POWERSHELL_READER
        for forbidden in ('InvokePattern', '.Invoke(', '.SetValue(', 'SendKeys', 'SetForegroundWindow',
                          'Start-Process', 'Set-Content', 'Out-File', '$env:', 'Cookie', 'localStorage'):
            self.assertNotIn(forbidden, code)
        self.assertIn('GetText(20480)', code)
        self.assertIn("'urlInspect'", code)
        self.assertIn("'urlToInspect'", code)
        self.assertNotIn('page_document_name=', code)
        self.assertNotIn('browser_url=', code)


@unittest.skipUnless(sys.platform == 'win32', 'Pure URI PowerShell contract is Windows-only; no UIA is loaded')
class OfficialAddressContractTests(unittest.TestCase):
    def address_results(self, expected, cases):
        start = reader.POWERSHELL_READER.index('function TargetMatches')
        end = reader.POWERSHELL_READER.index('function Field')
        # Execute only the exact pure URI functions from the production script.
        # No UIAutomation, Get-Process, browser, network or persistent profile.
        program = "$ErrorActionPreference='Stop';" + reader.POWERSHELL_READER[start:end] + r'''
        $data=[Console]::In.ReadToEnd()|ConvertFrom-Json
        $results=@(foreach($row in $data.cases){OfficialAddress $row[0] ([Uri]$data.expected)})
        [Console]::Write(($results|ConvertTo-Json -Compress))
        '''
        command = [reader._powershell_executable(), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                   base64.b64encode(program.encode('utf-16-le')).decode()]
        completed = subprocess.run(command, input=json.dumps({'expected': expected, 'cases': cases}).encode(),
            capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        return json.loads(completed.stdout)

    def test_actual_double_encoded_bing_query_and_strict_alias_conflict_boundaries(self):
        expected = 'https://synthetic.example.org/products/dongai/'
        site = quote('https://synthetic.example.org/', safe='')
        single, double, triple = [expected], None, None
        single = quote(expected, safe='')
        double = quote(single, safe='')
        triple = quote(double, safe='')
        base = 'https://www.bing.com/webmasters/urlinspection?siteUrl=' + site
        cases = [
            (base + '&urlInspect=' + single + '&urlToInspect=' + double, True),
            # Observed normal Bing shape: both independent aliases are encoded twice.
            (base + '&urlInspect=' + double + '&urlToInspect=' + double, True),
            (base + '&urlInspect=' + double, True),
            (base + '&urlInspect=' + expected, True),
            (base + '&urlToInspect=' + expected, True),
            (base + '&urlToInspect=' + single, True),
            (base + '&urlToInspect=' + double, True),
            (base.removeprefix('https://') + '&urlToInspect=' + double, True),
            (base + '&urlInspect=' + single, True),
            (base + '&urlInspect=' + triple, False),
            (base + '&urlToInspect=' + triple, False),
            (base + '&urlInspect=' + triple + '&urlToInspect=' + double, False),
            (base + '&urlInspect=' + quote(expected + 'other/', safe='') + '&urlToInspect=' + double, False),
            (base + '&urlInspect=' + single + '&urlToInspect=' + quote(quote(expected + 'other/', safe=''), safe=''), False),
            (base + '&urlInspect=' + double + '&urlToInspect=' + quote(quote(expected + 'other/', safe=''), safe=''), False),
            (base + '&urlInspect=' + quote(expected.replace('dongai', '%64ongai'), safe=''), False),
            (base + '&urlInspect=' + quote(quote(expected.replace('dongai', '%64ongai'), safe=''), safe=''), False),
            (base + '&urlToInspect=' + quote(expected.replace('dongai', '%64ongai'), safe=''), False),
            (base.replace('www.bing.com', 'www.bing.com.evil.example') + '&urlInspect=' + single, False),
            (base.replace('https://www.bing.com', 'http://www.bing.com') + '&urlInspect=' + double, False),
            (base.replace('www.bing.com', 'www.bing.com:444') + '&urlInspect=' + double, False),
            (base.replace('www.bing.com', 'user@www.bing.com') + '&urlInspect=' + double, False),
            (base.replace('/urlinspection?', '/urlinspection-other?') + '&urlInspect=' + single, False),
            (base.replace(site, quote('https://other.example.org/', safe='')) + '&urlInspect=' + single, False),
            (base.replace(site, quote('http://synthetic.example.org/', safe='')) + '&urlInspect=' + double, False),
            (base.replace(site, quote('https://synthetic.example.org/?x=1', safe='')) + '&urlInspect=' + double, False),
            (base + '&urlInspect=' + single + '&urlInspect=' + single, False),
            (base + '&urlInspect=' + double + '&url%49nspect=' + double, False),
            (base + '&urlInspect=' + double + '&siteUrl=' + site, False),
            (base + '&urlInspect=' + double + '#fragment', False),
            (base, False),
        ]
        actual = self.address_results(expected, cases)
        self.assertEqual(len(actual), len(cases))
        for (address, expected_value), actual_value in zip(cases, actual):
            with self.subTest(address=address):
                self.assertIs(actual_value, expected_value)

    def test_target_matching_stops_at_first_complete_url_and_has_a_two_layer_limit(self):
        expected = 'https://synthetic.example.org/products/dongai/'
        encoded = quote(expected, safe='')
        double = quote(encoded, safe='')
        triple = quote(double, safe='')
        escaped_path = expected.replace('dongai', '%64ongai')
        cases = [
            (expected, expected, 2, True),
            (encoded, expected, 2, True),
            (double, expected, 2, True),
            (triple, expected, 2, False),
            (double, expected, 1, False),
            (encoded, expected, 0, False),
            (escaped_path, expected, 2, False),
            (quote(escaped_path, safe=''), expected, 2, False),
            (quote(quote(escaped_path, safe=''), safe=''), expected, 2, False),
            # A deliberate encoded canonical path remains literal after unwrapping.
            (quote(quote(escaped_path, safe=''), safe=''), escaped_path, 2, True),
            (expected + 'other/', expected, 2, False),
        ]
        start = reader.POWERSHELL_READER.index('function TargetMatches')
        end = reader.POWERSHELL_READER.index('function OfficialAddress')
        program = "$ErrorActionPreference='Stop';" + reader.POWERSHELL_READER[start:end] + r'''
        $data=[Console]::In.ReadToEnd()|ConvertFrom-Json
        $results=@(foreach($row in $data.cases){TargetMatches $row[0] $row[1] $row[2]})
        [Console]::Write(($results|ConvertTo-Json -Compress))
        '''
        command = [reader._powershell_executable(), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                   base64.b64encode(program.encode('utf-16-le')).decode()]
        completed = subprocess.run(command, input=json.dumps({'cases': cases}).encode(),
            capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        actual = json.loads(completed.stdout)
        self.assertEqual(len(actual), len(cases))
        for (raw, target, layers, expected_value), actual_value in zip(cases, actual):
            with self.subTest(raw=raw, target=target, layers=layers):
                self.assertIs(actual_value, expected_value)


@unittest.skipUnless(sys.platform == 'win32', 'Pure PowerShell field contract is Windows-only; no UIA is loaded')
class FieldContractTests(unittest.TestCase):
    def fields(self, texts):
        code = reader.POWERSHELL_READER
        start = code.index('function Field(')
        end = code.index('\ntry {', start)
        validation_start = code.index("    $crawl = Field $lines 'Last crawl attempted'")
        validation_end = code.index("    $index = if ($indexed)", validation_start)
        # Run the exact production Field function and its actual allowlists.
        # Fixture text is stdin JSON data, never interpolated PowerShell source.
        program = "$ErrorActionPreference='Stop';" + code[start:end] + r'''
        [Console]::InputEncoding=[System.Text.UTF8Encoding]::new($false)
        [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
        $data=[Console]::In.ReadToEnd()|ConvertFrom-Json
        $results=@(foreach($text in $data.texts){
            $lines=@($text.Split([char]10)|ForEach-Object{$_.Trim()}|Where-Object{$_})
        ''' + code[validation_start:validation_end] + r'''
            @{last_crawl_text=$crawl;page_fetch_text=$fetch}
        })
        [Console]::Write(($results|ConvertTo-Json -Compress))
        '''
        command = [reader._powershell_executable(), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                   base64.b64encode(program.encode('utf-16-le')).decode()]
        completed = subprocess.run(command, input=json.dumps({'texts': texts}).encode(), capture_output=True,
            timeout=8, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        return json.loads(completed.stdout)

    def test_joined_card_newline_colon_and_space_layouts_share_the_production_contract(self):
        actual_card = ('Discover\nDiscovered on20 Oct 2026\nCrawl\n'
            'Last crawl attempted20 Oct 2026 at 10:55\nCrawl allowed?Yes\n'
            'Page FetchSuccessful\nIndexing allowed?Yes\nIndex\nCanonical URL- -\nView Indexed page\n')
        texts = [actual_card,
            'Last crawl attempted\n20 Oct 2026 at 10:55\nPage Fetch\nSuccessful',
            'Last crawl attempted:20 Oct 2026 at 10:55\nPage Fetch: Successful',
            'Last crawl attempted 20 Oct 2026 at 10:55\nPage Fetch Successful',
            'last crawl attempted20 Oct 2026 at 10:55\npage fetchSuccessful']
        self.assertEqual(self.fields(texts), [
            {'last_crawl_text': '20 Oct 2026 at 10:55', 'page_fetch_text': 'Successful'}] * len(texts))
        self.assertEqual(self.fields([
            'Last crawl attemptedToday at 10:21\nPage FetchNot available',
            'Last crawl attempted 2026-11-05 10:11:12\nPage Fetch: Timed out']), [
            {'last_crawl_text': 'Today at 10:21', 'page_fetch_text': 'Not available'},
            {'last_crawl_text': '2026-11-05 10:11:12', 'page_fetch_text': 'Timed out'}])

    def test_duplicate_labels_remain_unknown_instead_of_choosing_one_value(self):
        self.assertEqual(self.fields([
            'Last crawl attempted20 Oct 2026 at 10:55\nLast crawl attempted: 21 Oct 2026 at 11:55\nPage FetchSuccessful',
            'Last crawl attempted\n20 Oct 2026 at 10:55\nLast crawl attempted20 Oct 2026 at 10:55\nPage FetchSuccessful',
            'Last crawl attempted20 Oct 2026 at 10:55\nPage FetchSuccessful\nPage Fetch: Failed',
            'Last crawl attempted20 Oct 2026 at 10:55\nLast crawl attemptedprivate value\nPage FetchSuccessful']), [
            {'last_crawl_text': None, 'page_fetch_text': 'Successful'},
            {'last_crawl_text': None, 'page_fetch_text': 'Successful'},
            {'last_crawl_text': '20 Oct 2026 at 10:55', 'page_fetch_text': None},
            {'last_crawl_text': None, 'page_fetch_text': 'Successful'}])

    def test_unknown_or_invalid_values_do_not_cross_the_field_boundary(self):
        texts = [
            'Last crawl attemptedprivate account text\nPage FetchSuccessful but private text',
            'Last crawl attempted20 Oct 2026 at 10:55 extra\nPage Fetchsuccessful',
            'Last crawl attempted2026-11-05+00:00\nPage Fetch$(throw "fixture must remain data")',
            'Unrelated Last crawl attempted20 Oct 2026 at 10:55\nUnrelated Page FetchSuccessful',
            'Last crawl attempted:\nPage Fetch:',
            'Last crawl attempted',
            'Page Fetching']
        self.assertEqual(self.fields(texts), [
            {'last_crawl_text': None, 'page_fetch_text': None}] * len(texts))


class NativeObservationTests(ProbeFixture, unittest.TestCase):
    def payload(self):
        return (str(self.database), self.cycle['id'], 'https://synthetic.example.org/',
                webmaster.get_overview(self.database, self.cycle['id'])['context_sha256'], str(uuid.uuid4()))

    def test_webmaster_get_is_read_only_and_does_not_read_any_window_or_create_table(self):
        before = self.database.read_bytes()
        with patch.object(reader, 'read_existing_page') as read:
            view = webmaster.get_overview(self.database, self.cycle['id'])
        self.assertEqual(view['native_inspections'], [])
        self.assertEqual(self.database.read_bytes(), before)
        read.assert_not_called()

    def test_native_read_is_explicit_preserves_original_bing_user_record_and_metrics(self):
        with closing(store._connect(self.database)) as connection, connection:
            connection.execute(webmaster.CREATE_TABLE)
            record = {'sites': {'synthetic-original': {'preserve': 'original user confirmation'}}}
            connection.execute('INSERT INTO geo_webmaster_records VALUES(?,?,?)',
                               (self.cycle['id'], json.dumps(record), 'synthetic'))
        before = self.database.read_bytes()
        with patch.object(reader, 'read_existing_page', return_value=observed()) as read:
            result = reader.read_for_cycle(*self.payload())
        inspection = result['inspection']
        self.assertEqual(inspection['status'], 'observed')
        self.assertEqual(inspection['last_crawl_text'], '20 Oct 2026 at 10:55')
        self.assertEqual(inspection['source'], 'native_browser_ui')
        for field in ('independent', 'content_version_verified', 'geo_effect_verified', 'browser_opened',
                      'navigation_performed', 'inspect_invoked', 'indexing_requested'):
            self.assertIs(inspection[field], False)
        self.assertEqual(inspection['page_freshness'], 'unknown')
        self.assertEqual(inspection['timezone'], 'unknown')
        self.assertEqual(result['overview']['native_inspections'][0]['request_id'], inspection['request_id'])
        with closing(store._connect(self.database)) as connection:
            self.assertEqual(json.loads(connection.execute('SELECT record FROM geo_webmaster_records').fetchone()[0]), record)
        self.assertNotEqual(self.database.read_bytes(), before)
        self.assert_original_unchanged()
        read.assert_called_once_with('https://synthetic.example.org/')

    def test_unique_request_id_is_idempotent_and_cannot_change_project_or_url(self):
        payload = self.payload()
        with patch.object(reader, 'read_existing_page', return_value=observed()) as read:
            first = reader.read_for_cycle(*payload)
            before = self.database.read_bytes()
            repeated = reader.read_for_cycle(*payload)
            self.assertTrue(repeated['inspection']['idempotent'])
            self.assertEqual(first['inspection']['id'], repeated['inspection']['id'])
            self.assertEqual(self.database.read_bytes(), before)
            changed = list(payload);changed[2] += 'other/'
            with self.assertRaises(ValueError):
                reader.read_for_cycle(*changed)
        self.assertEqual(read.call_count, 1)

    def test_intent_exists_before_uia_and_same_request_while_reading_never_repeats(self):
        payload = self.payload()
        def capture(url):
            repeated = reader.read_for_cycle(*payload)['inspection']
            self.assertEqual(repeated['status'], 'reading')
            self.assertTrue(repeated['idempotent'])
            return observed()
        with patch.object(reader, 'read_existing_page', side_effect=capture) as read:
            self.assertEqual(reader.read_for_cycle(*payload)['inspection']['status'], 'observed')
        self.assertEqual(read.call_count, 1)

    def test_wrong_scope_noncanonical_or_stale_context_rejected_without_ui_or_table(self):
        payload = self.payload()
        for url in ('http://synthetic.example.org/', 'https://other.example.org/',
                    'https://127.0.0.1/', 'https://synthetic.example.org/?secret=x',
                    'https://synthetic.example.org/%61lias/'):
            changed = list(payload);changed[2] = url
            with patch.object(reader, 'read_existing_page') as read, self.assertRaises(ValueError):
                reader.read_for_cycle(*changed)
            read.assert_not_called()
        changed = list(payload);changed[3] = 'f' * 64
        with patch.object(reader, 'read_existing_page') as read, self.assertRaises(ValueError):
            reader.read_for_cycle(*changed)
        with closing(store._connect(self.database)) as connection:
            self.assertFalse(reader._exists(connection))
        read.assert_not_called()

    def test_scope_change_during_ui_read_discards_fields_but_preserves_original_intent(self):
        payload = self.payload()
        def capture(url):
            with closing(store._connect(self.database)) as connection, connection:
                connection.execute("UPDATE projects SET description='Changed during read'")
            return observed()
        with patch.object(reader, 'read_existing_page', side_effect=capture):
            inspection = reader.read_for_cycle(*payload)['inspection']
        self.assertEqual(inspection['reason'], 'scope_changed')
        self.assertEqual(inspection['status'], 'need_action')
        self.assertEqual(inspection['index_status'], 'unknown')
        self.assertIsNone(inspection['last_crawl_text'])

    def test_unknown_or_exception_is_need_action_never_notindexed(self):
        for reason in ('bing_login_required', 'inspection_input_mismatch', 'reader_timeout'):
            with patch.object(reader, 'read_existing_page', return_value=reader._failure(reason)):
                inspection = reader.read_for_cycle(*self.payload())['inspection']
            self.assertEqual(inspection['status'], 'need_action')
            self.assertEqual(inspection['index_status'], 'unknown')
        with patch.object(reader, 'read_existing_page', side_effect=OSError('synthetic private diagnostic')):
            result = reader.read_for_cycle(*self.payload())
        self.assertEqual(result['inspection']['reason'], 'reader_unavailable')
        self.assertNotIn('private diagnostic', json.dumps(result))

    def test_native_bridge_exposes_only_fixed_cycle_url_context_request_contract(self):
        fake = SimpleNamespace(data=Path('/synthetic/data'))
        args = ('a' * 32, 'https://synthetic.example.org/', 'b' * 64, 'synthetic-request-id')
        with patch.object(reader, 'read_for_cycle', return_value={'synthetic': True}) as read:
            self.assertEqual(DesktopGeoJobs.read_bing_inspection(fake, *args), {'synthetic': True})
        read.assert_called_once_with(fake.data / 'database.db', *args)


if __name__ == '__main__':
    unittest.main()
