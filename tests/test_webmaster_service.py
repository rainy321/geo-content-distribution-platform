"""Synthetic Bing onboarding with no external account or network calls."""
import base64
import copy
import hashlib
import json
import os
import socket
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import Mock, patch

from services import webmaster_service as service
from services import geo_content_store as store
from tests.test_geo_research_probes import ProbeFixture

XML = b'<?xml version="1.0"?>\n<users><user>0123456789ABCDEF0123456789ABCDEF</user></users>'
ENCODED = base64.b64encode(XML).decode()
SITEMAP = b'''<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://synthetic.example.org/</loc></url>
<url><loc>https://synthetic.example.org/about</loc></url></urlset>'''


def fetch_ok(origin, path, maximum):
    return {'http_status': 200, 'content_type': 'application/xml'}, XML if path.endswith('BingSiteAuth.xml') else SITEMAP


class WebmasterFixture(ProbeFixture):
    def setUp(self):
        super().setUp()
        self.cycle_id = self.cycle['id']
        self.view = service.get_overview(self.database, self.cycle_id)

    def action(self, action, **values):
        view = service.get_overview(self.database, self.cycle_id)
        return service.perform_action(self.database, self.cycle_id,
            {'action': action, 'context_sha256': view['context_sha256'], **values})

    def upload(self, data=XML):
        return self.action('upload_xml', xml_base64=base64.b64encode(data).decode())


class WebmasterServiceTests(WebmasterFixture, unittest.TestCase):
    def test_read_is_network_free_and_safe_before_migration(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('DROP TABLE geo_webmaster_records')
        with patch.object(service, '_fetch_public') as fetch, patch.object(socket, 'getaddrinfo') as dns:
            with closing(sqlite3.connect(self.database)) as connection:
                result = service.overview(connection, self.cycle)
                self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='geo_webmaster_records'").fetchone())
            self.assertTrue(result['supported'])
            self.assertIsNone(result['record'])
            fetch.assert_not_called()
            dns.assert_not_called()

    def test_upload_preserves_original_bytes_and_reopens_only_new_table(self):
        before = {}
        with closing(sqlite3.connect(self.database)) as connection:
            names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='geo_webmaster_records'")]
            for name in names:
                before[name] = connection.execute('SELECT * FROM "' + name + '"').fetchall()
        view = self.upload()
        file = view['record']['verification_file']
        self.assertEqual(base64.b64decode(file['xml_base64']), XML)
        self.assertEqual(file['sha256'], hashlib.sha256(XML).hexdigest())
        self.assertEqual(file['name'], 'BingSiteAuth.xml')
        self.assertEqual(service.get_overview(self.database, self.cycle_id)['record'], view['record'])
        self.assertEqual(view['record']['ownership']['status'], 'not_confirmed')
        with closing(sqlite3.connect(self.database)) as connection:
            for name, rows in before.items():
                self.assertEqual(connection.execute('SELECT * FROM "' + name + '"').fetchall(), rows, name)

    def test_upload_rejects_oversize_path_xxe_multiple_keys_and_malformed_xml(self):
        invalid = [b'x' * (service.MAX_XML_BYTES + 1), b'<html>home</html>',
            b'<!DOCTYPE users [<!ENTITY x "0123456789ABCDEF0123456789ABCDEF">]><users><user>&x;</user></users>',
            b'<users><user>0123456789ABCDEF0123456789ABCDEF</user><user>0123456789ABCDEF0123456789ABCDEF</user></users>',
            XML.replace(b'<users>', b'<users path="/secret">'), XML.replace(b'</user>', b'<child/></user>'),
            b'<users><user>short</user></users>', XML.decode().encode('utf-16')]
        for data in invalid:
            with self.subTest(data=data[:40]), self.assertRaises(ValueError):
                self.upload(data)
        for payload in ({'xml_base64': 'C:/secret.xml'}, {'xml_base64': ENCODED, 'file_path': 'C:/secret.xml'}):
            with self.assertRaises(ValueError):
                self.action('upload_xml', **payload)
        self.assertIsNone(service.get_overview(self.database, self.cycle_id)['record'])

    def test_public_match_is_not_bing_ownership_index_or_geo_success(self):
        self.upload()
        with patch.object(service, '_fetch_public', side_effect=fetch_ok):
            result = self.action('check_public')
        record = result['record']
        self.assertEqual(record['public_check']['status'], 'matches')
        self.assertEqual(record['public_check']['evidence_source'], 'software_public_http')
        self.assertEqual(record['sitemap_check']['entries'], 2)
        self.assertEqual(record['ownership']['status'], 'not_confirmed')
        self.assertEqual(record['sitemap_submission']['status'], 'not_reported')
        self.assertEqual(record['inspections'], [])
        cycle = store.get_cycle(self.database, self.cycle_id)
        self.assertEqual(cycle['webmaster'], result)
        self.assertEqual(cycle['publications'], [])
        self.assertEqual(cycle['reported_publications'], [])
        self.assert_original_unchanged()

    def test_ownership_map_and_inspection_reports_keep_independent_provenance(self):
        self.action('confirm_ownership', confirmed=True)
        self.action('record_sitemap', status='pending', discovered_urls=0, note='平台显示 Processing，最后抓取时间为空。')
        view = self.action('record_inspection', url='https://synthetic.example.org/about', status='unknown')
        record = view['record']
        self.assertEqual(record['ownership']['status'], 'confirmed')
        self.assertEqual(record['ownership']['evidence_source'], 'user_reported_platform_result')
        self.assertEqual(record['public_check']['status'], 'not_checked')
        self.assertEqual(record['sitemap_submission']['status'], 'pending')
        self.assertEqual(record['sitemap_submission']['discovered_urls'], 0)
        self.assertEqual(record['inspections'][0]['status'], 'unknown')
        self.assertEqual(record['inspections'][0]['evidence_source'], 'user_reported_platform_result')
        self.assert_original_unchanged()

    def test_different_xml_invalidates_old_matches_and_ownership_identical_keeps_them(self):
        self.upload()
        with patch.object(service, '_fetch_public', side_effect=fetch_ok):
            self.action('check_public')
        first = self.action('confirm_ownership', confirmed=True)['record']
        same = self.upload()['record']
        self.assertEqual(same['ownership'], first['ownership'])
        self.assertEqual(same['public_check'], first['public_check'])
        # Reformatting is a different original even with the same public key.
        changed = self.upload(XML.replace(b'<users>', b'<users>\n'))['record']
        self.assertEqual(changed['ownership']['status'], 'not_confirmed')
        self.assertEqual(changed['public_check']['status'], 'not_checked')
        self.assertIsNone(changed['public_check']['exact_source_bytes'])

    def test_html_redirect_404_timeout_and_no_original_never_count_as_match(self):
        for response, expected in (({'http_status': 200}, 'invalid_xml'), ({'http_status': 302}, 'redirect'),
                                   ({'http_status': 404}, 'not_found')):
            with self.subTest(response=response), patch.object(service, '_fetch_public', return_value=(response, b'<!DOCTYPE html><html>SPA</html>')):
                view = self.action('check_public')
            self.assertEqual(view['record']['public_check']['status'], expected)
            self.assertIsNone(view['record']['public_check']['matches_key'])
        with patch.object(service, '_fetch_public', side_effect=TimeoutError):
            view = self.action('check_public')
        self.assertEqual(view['record']['public_check']['status'], 'unreachable')
        self.assertIsNone(view['record']['sitemap_check']['entries'])
        with patch.object(service, '_fetch_public', side_effect=fetch_ok):
            view = self.action('check_public')
        self.assertEqual(view['record']['public_check']['status'], 'xml_present')
        self.assertIsNone(view['record']['public_check']['matches_key'])

    def test_same_key_reformatted_public_xml_is_not_exact_file_match(self):
        self.upload()
        def reformatted(origin, path, maximum):
            metadata, data = fetch_ok(origin, path, maximum)
            return metadata, data.replace(b'<users>', b'<users>\n') if path.endswith('BingSiteAuth.xml') else data
        with patch.object(service, '_fetch_public', side_effect=reformatted):
            view = self.action('check_public')
        self.assertEqual(view['record']['public_check']['status'], 'mismatch')
        self.assertTrue(view['record']['public_check']['matches_key'])
        self.assertFalse(view['record']['public_check']['exact_source_bytes'])

    def test_xml_index_entries_do_not_claim_discovered_pages(self):
        index = b'<sitemapindex><sitemap><loc>https://elsewhere.example.org/part.xml</loc></sitemap></sitemapindex>'
        with patch.object(service, '_fetch_public', return_value=({'http_status': 200}, index)) as fetch:
            view = self.action('check_public')
        self.assertEqual(fetch.call_count, 2, 'never follows sitemap entry URLs')
        self.assertEqual(view['record']['sitemap_check']['kind'], 'sitemapindex')
        self.assertEqual(view['record']['sitemap_check']['entries'], 1)
        self.assertTrue(all(value is None for value in view['record']['sitemap_check']['target_urls_present'].values()))
        self.assertNotIn('discovered_urls', view['record']['sitemap_check'])

    def test_stale_context_and_network_race_cannot_override_concurrent_owner_report(self):
        stale = self.view['context_sha256']
        self.upload()
        with self.assertRaises(ValueError):
            service.perform_action(self.database, self.cycle_id, {'action': 'confirm_ownership', 'confirmed': True, 'context_sha256': stale})
        def racing_check(view):
            self.action('confirm_ownership', confirmed=True)
            return ({'status': 'matches'}, {'status': 'ok'})
        with patch.object(service, '_public_checks', side_effect=racing_check), self.assertRaisesRegex(ValueError, '已变更'):
            self.action('check_public')
        record = service.get_overview(self.database, self.cycle_id)['record']
        self.assertEqual(record['ownership']['status'], 'confirmed')
        self.assertEqual(record['public_check']['status'], 'not_checked')

    def test_origin_change_hides_old_verification_but_preserves_its_record(self):
        old = self.action('confirm_ownership', confirmed=True)
        changed = copy.deepcopy(self.cycle)
        changed['profile']['website'] = 'https://other.example.net/products/'
        with patch.object(store, '_cycle', return_value=changed):
            fresh = service.get_overview(self.database, self.cycle_id)
            self.assertIsNone(fresh['record'])
            self.assertNotEqual(fresh['context_sha256'], old['context_sha256'])
            self.action('record_sitemap', status='pending')
        with closing(sqlite3.connect(self.database)) as connection:
            stored = json.loads(connection.execute('SELECT record FROM geo_webmaster_records').fetchone()[0])
        self.assertEqual(len(stored['sites']), 2)
        self.assertEqual(stored['sites'][old['site_key']]['ownership']['status'], 'confirmed')

    def test_invalid_manual_inputs_cannot_inject_platform_or_foreign_page(self):
        for action, values in (('confirm_ownership', {'confirmed': 'true'}),
            ('record_sitemap', {'status': 'indexed'}), ('record_sitemap', {'status': 'pending', 'discovered_urls': True}),
            ('record_inspection', {'status': 'indexed', 'url': 'https://other.example.net/'}),
            ('record_inspection', {'status': 'indexed', 'url': 'https://synthetic.example.org.evil.test/'}),
            ('record_inspection', {'status': 'indexed', 'url': 'https://synthetic.example.org/#fragment'}),
            ('record_inspection', {'status': 'unknown', 'url': 'https://synthetic.example.org/', 'note': 'x' * 501}),
            ('check_public', {'url': 'http://127.0.0.1/'}),
            ('confirm_ownership', {'confirmed': True, 'evidence_source': 'native_verified'})):
            with self.subTest(action=action, values=values), self.assertRaises(ValueError):
                self.action(action, **values)
        self.assertIsNone(service.get_overview(self.database, self.cycle_id)['record'])


class PublicFetchSafetyTests(unittest.TestCase):
    def test_private_origin_credentials_port_and_ambiguous_hosts_are_rejected(self):
        for url in ('http://example.com', 'https://127.0.0.1', 'https://[::1]', 'https://10.0.0.1',
                    'https://169.254.169.254/', 'https://localhost/', 'https://host.local/',
                    'https://owner:secret@example.com/', 'https://example.com:8443/',
                    'https://example.com\\@private/', 'https://%31%32%37.0.0.1/', 'https://example.com./'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                service._origin(url)

    def test_dns_answers_are_all_checked_and_connection_is_not_re_resolved(self):
        good = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))
        with patch.object(socket, 'getaddrinfo', return_value=[good, private]), self.assertRaises(ValueError):
            service._resolve_public('synthetic.example.org')
        with patch.object(socket, 'getaddrinfo', return_value=[good]) as dns:
            endpoint = service._resolve_public('synthetic.example.org')
        self.assertEqual(endpoint, (socket.AF_INET, '8.8.8.8'))
        raw, wrapped, context = Mock(), Mock(), Mock()
        context.wrap_socket.return_value = wrapped
        with patch.object(socket, 'socket', return_value=raw), patch.object(ssl_module(), 'create_default_context', return_value=context), patch.object(socket, 'getaddrinfo') as dns_again:
            connection = service._PinnedHTTPSConnection('synthetic.example.org', endpoint)
            connection.connect()
        raw.connect.assert_called_once_with(('8.8.8.8', 443))
        context.wrap_socket.assert_called_once_with(raw, server_hostname='synthetic.example.org', do_handshake_on_connect=False)
        wrapped.do_handshake.assert_called_once()
        dns_again.assert_not_called()

    def test_fetch_does_not_follow_redirect_and_only_accepts_fixed_paths(self):
        connection = Mock()
        connection.getresponse.return_value.status = 302
        connection.getresponse.return_value.getheader.return_value = 'https://127.0.0.1/'
        with patch.object(service, '_resolve_public', return_value=(socket.AF_INET, '8.8.8.8')), patch.object(service, '_PinnedHTTPSConnection', return_value=connection):
            metadata, data = service._fetch_public('https://synthetic.example.org', '/BingSiteAuth.xml', 100)
        self.assertEqual(metadata['http_status'], 302)
        self.assertEqual(data, b'')
        self.assertEqual(connection.request.call_count, 1)
        connection.close.assert_called_once()
        with self.assertRaises(ValueError):
            service._fetch_public('https://synthetic.example.org', '/arbitrary', 100)


def ssl_module():
    # The standard module is shared, keeping the test's only patch explicit.
    return service.ssl


class WebmasterAPITests(WebmasterFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with patch.dict(os.environ, {'DATABASE_PATH': str(self.database), 'DEMO_MODE': 'false'}):
            from sau_backend import app
        self.configuration = patch.dict(app.config, DATABASE_PATH=self.database, ACCESS_CONTROL_ENABLED=False, TESTING=True)
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
        self.app, self.client = app, app.test_client()
        self.endpoint = '/api/geo-content/cycles/' + self.cycle_id + '/webmaster'

    def test_route_persists_one_owner_report_and_embeds_same_record_in_cycle(self):
        view = self.client.get('/backend' + self.endpoint).json['data']
        result = self.client.post(self.endpoint, json={'action': 'confirm_ownership', 'confirmed': True, 'context_sha256': view['context_sha256']})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['data']['record']['ownership']['evidence_source'], 'user_reported_platform_result')
        cycle = self.client.get('/api/geo-content/cycles/' + self.cycle_id).json['data']
        self.assertEqual(cycle['webmaster'], result.json['data'])

    def test_bad_payloads_missing_cycle_and_existing_access_controls(self):
        for value in ([], None, {'action': []}, {'action': 'check_public', 'context_sha256': self.view['context_sha256'], 'url': 'https://example.com'}):
            self.assertEqual(self.client.post(self.endpoint, json=value).status_code, 400)
        self.assertEqual(self.client.get('/api/geo-content/cycles/missing/webmaster').status_code, 404)
        with patch.dict(self.app.config, ACCESS_CONTROL_ENABLED=True):
            self.assertEqual(self.client.post(self.endpoint, json={}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
