"""Synthetic official HTML and temporary audit state; no live site or account."""
import copy
import hashlib
import io
import json
import os
import socket
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from services import geo_content_store as store
from services import geo_website_audit as audit
from services import geo_website_html as html
from services import webmaster_service as webmaster
from services.geo_test_service import plan_fingerprint
from tests.test_geo_research_probes import ProbeFixture


def document(name='澄星软件有限公司', *, extra_head='', body=None):
    return ('''<!doctype html><html><head><title>''' + name + ''' | 公开资料</title>
    <meta name="description" content="公开的产品说明与适用边界。">
    <link rel="canonical" href="/">
    <script type="application/ld+json">''' + json.dumps({'@context': 'https://schema.org',
        '@type': 'Organization', 'name': name, 'url': 'https://synthetic.example.org/'}, ensure_ascii=False) + '''</script>
    ''' + extra_head + '''</head><body>''' + (body if body is not None else '<h1>' + name + '</h1><p>公开说明与文档。</p>') + '''</body></html>''').encode()


def response(data=None, *, status=200, content_type='text/html; charset=utf-8', **headers):
    return {'http_status': status, 'content_type': content_type, 'location': '', 'x_robots_tag': '', **headers}, document() if data is None else data


class HTMLObservationTests(unittest.TestCase):
    def inspect(self, data, **metadata):
        return html.inspect_html(data, {'content_type': 'text/html; charset=utf-8', **metadata},
            'https://synthetic.example.org/', '澄星软件有限公司', lambda value: value == 'https://synthetic.example.org/')

    def test_title_description_canonical_body_name_and_JSON_LD_remain_observations(self):
        result = self.inspect(document())
        self.assertEqual(result['title'], '澄星软件有限公司 | 公开资料')
        self.assertEqual(result['meta_description'], '公开的产品说明与适用边界。')
        self.assertEqual(result['canonical_urls'], ['https://synthetic.example.org/'])
        self.assertTrue(result['name_text_match']['body_text_present'])
        self.assertFalse(result['name_text_match']['identity_verified'])
        self.assertEqual(result['json_ld']['entries'][0]['types'], ['Organization'])
        self.assertTrue(result['json_ld']['entries'][0]['configured_name_equal'])
        self.assertFalse(result['json_ld']['remote_contexts_fetched'])
        self.assertFalse(result['json_ld']['standards_validation_performed'])
        self.assertFalse(result['browser_rendered'])
        self.assertNotIn('indexed', result)
        self.assertNotIn('score', result)

    def test_near_company_names_hidden_scripts_and_longer_name_prefixes_do_not_match(self):
        for name in ('澄新软件有限公司', '北京澄星软件有限公司', '澄星软件有限责任公司'):
            with self.subTest(name=name):
                result = self.inspect(document(name, body='<h1>' + name + '</h1><script>"澄星软件有限公司"</script>'))
                self.assertFalse(result['name_text_match']['body_text_present'])
                self.assertFalse(result['name_text_match']['title_text_present'])
                self.assertFalse(result['json_ld']['entries'][0]['configured_name_equal'])
        self.assertFalse(html.name_present('Orbit SDKX', 'Orbit SDK'))
        self.assertFalse(html.name_present('SuperOrbit SDK', 'Orbit SDK'))

    def test_literal_name_text_and_strict_identity_boundaries_are_separate_for_Chinese_sentences_and_near_names(self):
        for name, literal in (('澄星软件有限公司面向企业提供公开说明', True),
                              ('北京澄星软件有限公司', True), ('澄新软件有限公司', False)):
            with self.subTest(name=name):
                result = self.inspect(document(name, body='<p>' + name + '</p>'))
                observed = result['name_text_match']
                self.assertIs(observed['body_literal_substring_present'], literal)
                self.assertIs(observed['title_literal_substring_present'], literal)
                self.assertFalse(observed['body_text_present'])
                self.assertFalse(observed['title_text_present'])
                self.assertFalse(observed['identity_verified'])
                codes = [item['code'] for item in result['findings']]
                self.assertIn('configured_name_context_ambiguous' if literal else 'configured_name_not_observed', codes)
                if literal:
                    self.assertNotIn('configured_name_not_observed', codes)

    def test_noindex_keeps_crawler_scopes_and_does_not_claim_actual_index(self):
        result = self.inspect(document(extra_head='<meta name="Googlebot" content="noindex, nofollow">'), x_robots_tag='bingbot: index')
        self.assertTrue(result['robots_noindex_observed'])
        self.assertEqual(result['robots_meta'][0]['agent'], 'googlebot')
        self.assertEqual(result['x_robots_tag'], 'bingbot: index')
        self.assertTrue(self.inspect(document(), x_robots_tag='noindex')['robots_noindex_observed'])
        self.assertTrue(self.inspect(document(extra_head='<meta name="robots" content="none">'))['robots_noindex_observed'])
        self.assertIsNone(self.inspect(document(), x_robots_tag='index', x_robots_tag_truncated=True)['robots_noindex_observed'])

    def test_links_base_and_external_canonical_are_reported_without_fetching_or_executing(self):
        raw = document(extra_head='<base href="https://docs.example.org/base/"><link rel="canonical" href="../canonical">',
            body='<a href="guide">文档</a><a href="javascript:alert(1)">忽略</a><a href="http://127.0.0.1/">忽略</a><a href="https://[">无效</a>')
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No HTTP')), \
             patch('socket.getaddrinfo', side_effect=AssertionError('No DNS')):
            result = self.inspect(raw)
        self.assertEqual(result['page_links'][0]['url'], 'https://docs.example.org/base/guide')
        self.assertEqual(len(result['page_links']), 1)
        self.assertFalse(result['page_links'][0]['automatically_fetched'])
        self.assertIn('canonical_outside_configured_scope', [row['code'] for row in result['findings']])

    def test_JSON_LD_remote_contexts_bad_JSON_graphs_and_sparse_HTML_are_bounded(self):
        graph = {'@context': 'http://169.254.169.254/latest/meta-data', '@graph': [
            {'@type': 'Product', 'name': '另一款产品'}] * 60}
        raw = b'<html><head><script type="application/ld+json">' + json.dumps(graph).encode() + \
            b'</script><script type="application/ld+json">{"x":NaN}</script></head><body><div id="app"></div></body></html>'
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No context retrieval')):
            result = self.inspect(raw)
        self.assertLessEqual(len(result['json_ld']['entries']), 20)
        self.assertEqual(result['json_ld']['invalid_script_count'], 1)
        self.assertIsNone(result['name_text_match']['body_text_present'])
        self.assertIsNone(result['title'])

    def test_parser_structure_and_output_text_link_limits(self):
        raw = document(body=''.join('<a href="/p' + str(index) + '">' + 'a' * 300 + '</a>' for index in range(100)))
        result = self.inspect(raw)
        self.assertLessEqual(len(result['page_links']), 30)
        self.assertTrue(all(len(row['text']) <= 200 for row in result['page_links']))
        with self.assertRaisesRegex(ValueError, 'html_structure_limit'):
            self.inspect(document(body='<i>x</i>' * 10000))


class SharedPublicTransportTests(unittest.TestCase):
    def connection(self, *, data=b'<html/>', headers=None, status=200):
        headers = {'Content-Type': 'text/html', **(headers or {})}
        response = Mock(status=status)
        response.getheader.side_effect = lambda key, default=None: headers.get(key, default)
        chunks = [data, b'']
        response.read.side_effect = lambda maximum: chunks.pop(0)
        connection = Mock()
        connection.getresponse.return_value = response
        return connection

    def fetch(self, connection, maximum=100, **kwargs):
        with patch.object(webmaster, '_resolve_public', return_value=(socket.AF_INET, '8.8.8.8')), \
             patch.object(webmaster, '_PinnedHTTPSConnection', return_value=connection):
            return webmaster._fetch_bounded('https://synthetic.example.org', '/about', maximum,
                accept='text/html,application/xhtml+xml', include_headers=True, **kwargs)

    def test_shared_transport_retains_pinning_no_cookie_no_proxy_no_redirect_behavior(self):
        connection = self.connection(status=302, headers={'Location': 'https://127.0.0.1/'})
        metadata, data = self.fetch(connection)
        self.assertEqual(metadata['http_status'], 302)
        self.assertEqual(data, b'')
        self.assertEqual(connection.request.call_count, 1)
        sent = connection.request.call_args.kwargs['headers']
        self.assertEqual(sent['Accept-Encoding'], 'identity')
        self.assertNotIn('Cookie', sent)
        self.assertNotIn('Authorization', sent)
        connection.close.assert_called_once()

    def test_content_length_streamed_size_and_compression_are_all_bounded(self):
        for headers, data, message in (({'Content-Length': '101'}, b'x', 'response_too_large'),
            ({'Content-Length': 'invalid'}, b'x', 'response_too_large'),
            ({}, b'x' * 101, 'response_too_large'), ({'Content-Encoding': 'gzip'}, b'x', 'unsupported_content_encoding')):
            with self.subTest(headers=headers):
                connection = self.connection(headers=headers, data=data)
                with self.assertRaisesRegex(ValueError, message):
                    self.fetch(connection)
                connection.close.assert_called_once()

    def test_truncated_redirect_header_cannot_become_an_allowed_prefix_URL(self):
        connection = self.connection(status=302, headers={'Location': 'https://synthetic.example.org/' + 'x' * 2200})
        metadata, _ = self.fetch(connection)
        self.assertTrue(metadata['location_truncated'])
        self.assertEqual(metadata['location'], '')

    def test_bad_paths_and_private_origins_stop_before_any_resolver_or_socket(self):
        for origin, path in (('https://127.0.0.1', '/'), ('https://169.254.169.254', '/'),
                             ('https://synthetic.example.org', '//other/'), ('https://synthetic.example.org', '/bad\r\nHost:x'),
                             ('https://synthetic.example.org', 'https://other.example.org')):
            with self.subTest(origin=origin, path=path), patch.object(webmaster, '_resolve_public') as dns:
                with self.assertRaises(ValueError):
                    webmaster._fetch_bounded(origin, path, 100)
                dns.assert_not_called()

    def test_deadline_after_DNS_does_not_open_a_connection_and_old_XML_guard_still_holds(self):
        with patch.object(webmaster, '_resolve_public', return_value=(socket.AF_INET, '8.8.8.8')), \
             patch.object(webmaster.time, 'monotonic', return_value=11), patch.object(webmaster, '_PinnedHTTPSConnection') as connect:
            with self.assertRaises(TimeoutError):
                webmaster._fetch_bounded('https://synthetic.example.org', '/', 100, deadline=10)
            connect.assert_not_called()
        with self.assertRaises(ValueError):
            webmaster._fetch_public('https://synthetic.example.org', '/about', 100)


class WebsiteFixture(ProbeFixture):
    def setUp(self):
        super().setUp()
        report = copy.deepcopy(self.baseline)
        report['run_id'] = 'website-official-pages-fixture'
        report['profile']['official_urls'] = ['https://synthetic.example.org/' + name
            for name in ('about', 'docs', 'products', 'pricing', 'contact')]
        plan = copy.deepcopy(self.plan)
        plan['profile'] = report['profile']
        report['plan_sha256'] = plan_fingerprint(plan)
        self.cycle = store.create_cycle(self.database, report, 'website-official-pages-fixture')
        self.view = audit.get_overview(self.database, self.cycle['id'])
        self.raw = document(self.cycle['profile']['name'])
        self.original_transport = webmaster._fetch_bounded
        forbidden_network = patch.object(webmaster, '_fetch_bounded', side_effect=AssertionError('No real network in fixtures'))
        forbidden_network.start()
        self.addCleanup(forbidden_network.stop)

    def payload(self, *, view=None, page_ids=None, **changes):
        view = view or audit.get_overview(self.database, self.cycle['id'])
        return {'project_id': self.cycle['project_id'], 'expected_plan_sha256': self.cycle['test_plan']['plan_sha256'],
            'expected_revision': self.cycle['revision'], 'expected_context_sha256': view['context_sha256'],
            'expected_audit_revision': view['audit_revision'],
            'page_ids': page_ids or [view['allowed_pages'][0]['id']], **changes}

    def run_audit(self, payload=None, **patch_kwargs):
        with patch.object(webmaster, '_fetch_bounded', return_value=response(self.raw), **patch_kwargs) as fetch:
            result = audit.perform_audit(self.database, self.cycle['id'], payload or self.payload())
        return result, fetch

    def stored(self):
        with store._connect(self.database) as connection:
            return webmaster._stored(connection, self.cycle['id'])


class WebsiteAuditServiceTests(WebsiteFixture, unittest.TestCase):
    def test_overview_is_network_free_and_does_not_create_or_write_a_table(self):
        before = self.database.read_bytes()
        with patch.object(webmaster, '_fetch_bounded', side_effect=AssertionError('No fetch')), \
             patch('socket.getaddrinfo', side_effect=AssertionError('No DNS')):
            view = audit.get_overview(self.database, self.cycle['id'])
        self.assertTrue(view['supported'])
        self.assertEqual(len(view['allowed_pages']), 6)
        self.assertEqual(view['audit_revision'], 0)
        self.assertIsNone(view['latest'])
        self.assertEqual(self.database.read_bytes(), before)

    def test_HTTP_unsupported_and_private_credential_port_traversal_URLs_never_enter_the_allowlist(self):
        for url in ('http://synthetic.example.org', 'https://127.0.0.1/', 'https://owner:secret@example.org',
                    'https://synthetic.example.org:8443/', 'https://synthetic.example.org/%2fprivate',
                    'https://synthetic.example.org/../private', 'https://synthetic.example.org/?url=http://127.0.0.1'):
            with self.subTest(url=url):
                cycle = copy.deepcopy(self.cycle)
                cycle['profile'].update(website=url, official_urls=[])
                with store._connect(self.database) as connection:
                    view = audit.overview(connection, cycle)
                self.assertFalse(view['supported'])
                self.assertEqual(view['allowed_pages'], [])
                self.assertIn('HTTP', view['reason'])

    def test_audit_records_HTML_without_effect_claims_and_preserves_all_business_rows_and_webmaster_sites(self):
        webmaster.perform_action(self.database, self.cycle['id'], {'action': 'confirm_ownership', 'confirmed': True,
            'context_sha256': webmaster.get_overview(self.database, self.cycle['id'])['context_sha256']})
        before_sites = copy.deepcopy(self.stored()['sites'])
        with sqlite3.connect(self.database) as connection:
            names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before = {name: connection.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall()
                      for name in names if name != 'geo_webmaster_records'}
        result, fetch = self.run_audit()
        record = result['audit']
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['pages'][0]['status'], 'html_observed')
        self.assertEqual(record['pages'][0]['sha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(record['evidence_source'], 'software_public_http')
        self.assertEqual(record['index_status'], 'unknown')
        self.assertEqual(record['ai_adoption'], 'unknown')
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.stored()['sites'], before_sites)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(before, {name: connection.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in before})
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['website_audit'], result['overview'])

    def test_retry_after_unknown_HTTP_receipt_returns_same_reservation_without_fetching_again(self):
        payload = self.payload()
        first, _ = self.run_audit(payload)
        before = self.database.read_bytes()
        with patch.object(webmaster, '_fetch_bounded', side_effect=AssertionError('No retry')):
            repeated = audit.perform_audit(self.database, self.cycle['id'], payload)
        self.assertEqual(repeated, first)
        self.assertEqual(self.database.read_bytes(), before)

    def test_running_duplicate_and_expired_pending_are_inspectable_and_only_explicit_new_revision_refetches(self):
        payload = self.payload()
        _, _, reserved, _ = audit._reserve(self.database, self.cycle['id'], payload)
        with patch.object(webmaster, '_fetch_bounded', side_effect=AssertionError('Pending must not retry')):
            self.assertEqual(audit.perform_audit(self.database, self.cycle['id'], payload)['audit']['status'], 'running')
            with self.assertRaises(ValueError):
                audit.perform_audit(self.database, self.cycle['id'], self.payload())
            future = (datetime.fromisoformat(reserved['expires_at']) + timedelta(seconds=1)).isoformat()
            before = self.database.read_bytes()
            with patch.object(audit, '_now', return_value=future):
                expired = audit.get_overview(self.database, self.cycle['id'])
                self.assertEqual(expired['latest']['status'], 'interrupted')
                self.assertEqual(audit.perform_audit(self.database, self.cycle['id'], payload)['audit']['status'], 'interrupted')
                self.assertEqual(self.database.read_bytes(), before)
        with patch.object(audit, '_now', return_value=future):
            result, fetch = self.run_audit(self.payload())
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result['overview']['audit_revision'], 2)
        self.assertEqual([row['status'] for row in result['overview']['history']], ['completed', 'interrupted'])

    def test_multiple_pages_and_history_are_bounded_and_stale_old_revision_cannot_replay(self):
        oldest = self.payload()
        for _ in range(7):
            self.run_audit()
        view = audit.get_overview(self.database, self.cycle['id'])
        self.assertEqual(view['audit_revision'], 7)
        self.assertEqual(len(view['history']), audit.HISTORY_LIMIT)
        with patch.object(webmaster, '_fetch_bounded', side_effect=AssertionError('Stale cannot replay')):
            with self.assertRaises(ValueError):
                audit.perform_audit(self.database, self.cycle['id'], oldest)
        payload = self.payload(page_ids=[row['id'] for row in view['allowed_pages']])
        with self.assertRaises(ValueError):
            audit.perform_audit(self.database, self.cycle['id'], payload)

    def test_fetch_does_not_hold_a_DB_write_lock_and_CAS_keeps_scope_changed_receipt_out_of_current(self):
        def fetch(origin, path, maximum, **kwargs):
            store.save_facts(self.database, self.cycle['id'], [
                {'text': '合成用户在检测期间确认的新资料。', 'source_url': '', 'confirmed': True}], self.cycle['revision'])
            return response(self.raw)
        with patch.object(webmaster, '_fetch_bounded', side_effect=fetch):
            result = audit.perform_audit(self.database, self.cycle['id'], self.payload())
        self.assertEqual(result['audit']['status'], 'stale_context')
        self.assertFalse(result['audit']['current_scope'])
        self.assertIsNone(result['overview']['latest'])
        self.assertEqual(len(result['overview']['history']), 1)
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['facts'][0]['text'], '合成用户在检测期间确认的新资料。')

    def test_CAS_merge_preserves_concurrent_webmaster_owner_record(self):
        def fetch(origin, path, maximum, **kwargs):
            current = webmaster.get_overview(self.database, self.cycle['id'])
            webmaster.perform_action(self.database, self.cycle['id'], {'action': 'record_sitemap', 'status': 'pending',
                'context_sha256': current['context_sha256']})
            return response(self.raw)
        with patch.object(webmaster, '_fetch_bounded', side_effect=fetch):
            result = audit.perform_audit(self.database, self.cycle['id'], self.payload())
        self.assertEqual(result['audit']['status'], 'completed')
        self.assertEqual(webmaster.get_overview(self.database, self.cycle['id'])['record']['sitemap_submission']['status'], 'pending')

    def test_failure_receipt_is_safe_and_is_not_automatically_retried(self):
        payload = self.payload()
        with patch.object(audit, 'inspect_pages', side_effect=RuntimeError('private failure details')):
            result = audit.perform_audit(self.database, self.cycle['id'], payload)
        self.assertEqual(result['audit']['status'], 'failed')
        self.assertNotIn('private failure details', json.dumps(result))
        with patch.object(webmaster, '_fetch_bounded', side_effect=AssertionError('No automatic retry')):
            self.assertEqual(audit.perform_audit(self.database, self.cycle['id'], payload)['audit']['status'], 'failed')

    def test_redirects_only_follow_registered_pages_and_bound_loops_and_limit(self):
        selected = self.payload()
        pages = self.view['allowed_pages']
        for location in ('https://127.0.0.1/', 'https://169.254.169.254/', 'https://foreign.example.org/',
                         'http://synthetic.example.org/', '/unregistered', '//synthetic.example.org.evil.test/'):
            with self.subTest(location=location), patch.object(webmaster, '_fetch_bounded', return_value=response(
                    b'', status=302, location=location)) as fetch:
                result = audit.inspect_pages(self.cycle, self.view, {'selected_page_ids': selected['page_ids']})
                self.assertEqual(result[0]['status'], 'redirect_blocked')
                self.assertEqual(fetch.call_count, 1)
        with patch.object(webmaster, '_fetch_bounded', side_effect=[response(b'', status=301, location='/about'), response(self.raw)]) as fetch:
            result = audit.inspect_pages(self.cycle, self.view, {'selected_page_ids': selected['page_ids']})
        self.assertEqual(result[0]['status'], 'html_observed')
        self.assertEqual(result[0]['final_url'], 'https://synthetic.example.org/about')
        self.assertEqual(fetch.call_count, 2)
        with patch.object(webmaster, '_fetch_bounded', return_value=response(b'', status=302, location='/')) as fetch:
            self.assertEqual(audit.inspect_pages(self.cycle, self.view, {'selected_page_ids': selected['page_ids']})[0]['status'], 'redirect_loop')
        chain = [response(b'', status=302, location='/' + name) for name in ('about', 'docs', 'products')]
        with patch.object(webmaster, '_fetch_bounded', side_effect=chain) as fetch:
            self.assertEqual(audit.inspect_pages(self.cycle, self.view, {'selected_page_ids': selected['page_ids']})[0]['status'], 'redirect_limit')
            self.assertEqual(fetch.call_count, 3)

    def test_each_redirect_hop_reuses_full_DNS_validation_and_blocks_rebinding_before_second_socket(self):
        good = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))
        connection = SharedPublicTransportTests().connection(status=302, headers={'Location': '/about'})
        reservation = {'selected_page_ids': [self.view['allowed_pages'][0]['id']]}
        with patch.object(socket, 'getaddrinfo', side_effect=[[good], [good, private]]) as dns, \
             patch.object(webmaster, '_PinnedHTTPSConnection', return_value=connection) as connect, \
             patch.object(webmaster, '_fetch_bounded', side_effect=self.original_transport):
            page = audit.inspect_pages(self.cycle, self.view, reservation)[0]
        self.assertEqual(page['status'], 'address_or_html_invalid')
        self.assertEqual(dns.call_count, 2)
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(len(page['redirects']), 1)
        self.assertEqual(connection.request.call_count, 1)

    def test_product_redirect_cannot_expand_to_host_root_or_sibling_product(self):
        cycle = copy.deepcopy(self.cycle)
        cycle['profile'].update(entity_type='product', name='Orbit SDK',
            website='https://synthetic.example.org/products/orbit',
            official_urls=['https://synthetic.example.org/products/orbit/docs'])
        with store._connect(self.database) as connection:
            view = audit.overview(connection, cycle)
        for path in ('/', '/products/other'):
            with self.subTest(path=path), patch.object(webmaster, '_fetch_bounded', return_value=response(b'', status=302, location=path)) as fetch:
                page = audit.inspect_pages(cycle, view, {'selected_page_ids': [view['allowed_pages'][0]['id']]})[0]
                self.assertEqual(page['status'], 'redirect_blocked')
                self.assertEqual(fetch.call_count, 1)

    def test_CAS_finalization_cannot_overwrite_a_newer_explicit_run(self):
        original_payload = self.payload()
        newer = []
        def inspect(cycle, view, reservation):
            if reservation['revision'] == 1:
                future = (datetime.fromisoformat(reservation['expires_at']) + timedelta(seconds=1)).isoformat()
                with patch.object(audit, '_now', return_value=future):
                    newer.append(audit.perform_audit(self.database, self.cycle['id'], self.payload()))
            return [{**audit._page_result(view['allowed_pages'][0]), 'status': 'html_observed'}]
        with patch.object(audit, 'inspect_pages', side_effect=inspect):
            original = audit.perform_audit(self.database, self.cycle['id'], original_payload)
        self.assertEqual(original['audit']['status'], 'stale_context')
        self.assertFalse(original['audit']['current_scope'])
        self.assertEqual(original['overview']['latest']['id'], newer[0]['audit']['id'])
        self.assertEqual(original['overview']['latest']['status'], 'completed')
        self.assertEqual(original['overview']['audit_revision'], 2)

    def test_non_HTML_large_compressed_timeout_and_budget_exhaustion_keep_unknown_fields(self):
        selected = {'selected_page_ids': [self.view['allowed_pages'][0]['id']]}
        for value, status in ((response(b'{}', content_type='application/json'), 'non_html'),
                              (ValueError('response_too_large'), 'response_too_large'),
                              (ValueError('unsupported_content_encoding'), 'unsupported_content_encoding'),
                              (TimeoutError(), 'request_timeout')):
            with self.subTest(status=status), patch.object(webmaster, '_fetch_bounded',
                    **({'side_effect': value} if isinstance(value, Exception) else {'return_value': value})):
                page = audit.inspect_pages(self.cycle, self.view, selected)[0]
                self.assertEqual(page['status'], status)
                self.assertIsNone(page['name_text_match'])
                self.assertIsNone(page['json_ld'])
        clock = [100.0]
        def timed_fetch(*args, **kwargs):
            clock[0] += 44
            return response(self.raw)
        reservation = {'selected_page_ids': [row['id'] for row in self.view['allowed_pages'][:5]]}
        with patch.object(audit.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(webmaster, '_fetch_bounded', side_effect=timed_fetch) as fetch:
            pages = audit.inspect_pages(self.cycle, self.view, reservation)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(pages[1]['status'], 'not_checked_budget')


class WebsiteAuditAPITests(WebsiteFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with patch.dict(os.environ, DATABASE_PATH=str(self.database), DEMO_MODE='false', SEED_DEMO_DATA='false',
                        MEDIA_ROOT=str(self.root / 'media'), COOKIES_DIRECTORY=str(self.root / 'cookies')):
            from sau_backend import app
        self.configuration = patch.dict(app.config, DATABASE_PATH=self.database, TESTING=True, ACCESS_CONTROL_ENABLED=False)
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
        self.app, self.client = app, app.test_client()
        self.endpoint = '/api/geo-content/cycles/' + self.cycle['id'] + '/website-audit'

    def test_route_returns_current_pages_and_performs_only_selected_server_bound_pages(self):
        initial = self.client.get(self.endpoint)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.headers['Cache-Control'], 'no-store')
        with patch.object(webmaster, '_fetch_bounded', return_value=response(self.raw)) as fetch:
            result = self.client.post(self.endpoint, json=self.payload())
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        self.assertEqual(result.get_json()['data']['audit']['status'], 'completed')
        self.assertEqual(fetch.call_count, 1)

    def test_route_auth_origin_JSON_limits_and_rejects_arbitrary_fetch_controls(self):
        for key in ('url', 'urls', 'path', 'headers', 'cookies', 'proxy', 'method', 'index_status'):
            with self.subTest(key=key):
                self.assertEqual(self.client.post(self.endpoint, json=self.payload(**{key: 'https://127.0.0.1/'})).status_code, 400)
        for changes in ({'project_id': self.cycle['project_id'] + 1}, {'expected_revision': True},
                        {'expected_plan_sha256': '0' * 64}, {'expected_context_sha256': '0' * 64},
                        {'page_ids': ['forged-id']}, {'expected_audit_revision': True}):
            self.assertEqual(self.client.post(self.endpoint, json=self.payload(**changes)).status_code, 400)
        self.assertEqual(self.client.get(self.endpoint + '?url=https://127.0.0.1/').status_code, 400)
        with patch.dict(self.app.config, ACCESS_CONTROL_ENABLED=True):
            self.assertEqual(self.client.get(self.endpoint).status_code, 401)
            self.assertEqual(self.client.post(self.endpoint, json=self.payload()).status_code, 401)
        for method in (self.client.get, self.client.post):
            kwargs = {'json': self.payload()} if method == self.client.post else {}
            rejected = method(self.endpoint, headers={'Origin': 'https://foreign.example.org'}, **kwargs)
            self.assertEqual(rejected.status_code, 403)
            self.assertEqual(rejected.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.client.post(self.endpoint, data='{}', content_type='text/plain').status_code, 415)
        self.assertEqual(self.client.post(self.endpoint, data='{"x":NaN}', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(self.endpoint, data=b' ' * (64 * 1024 + 1), content_type='application/json').status_code, 413)
        chunked = self.client.open(self.endpoint, method='POST', input_stream=io.BytesIO(b'x' * (64 * 1024 + 1)),
            content_type='application/json', environ_overrides={'CONTENT_LENGTH': '', 'wsgi.input_terminated': True})
        self.assertEqual(chunked.status_code, 413)
        self.assertEqual(self.client.get(self.endpoint.replace(self.cycle['id'], 'missing')).status_code, 404)

if __name__ == '__main__':
    unittest.main()
