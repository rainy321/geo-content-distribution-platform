"""IndexNow contract tests: temporary databases, synthetic HTTP, no real sends."""
import copy
import json
import os
import sqlite3
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from unittest.mock import MagicMock, patch

from services import geo_content_store as store
from services import geo_indexnow_service as service
from tests.test_geo_research_probes import ProbeFixture
from services.geo_test_service import plan_fingerprint

_REAL_POST = service._post_indexnow


class IndexNowFixture(ProbeFixture):
    def setUp(self):
        super().setUp()
        self.origin = 'https://synthetic.example.org'
        self.key = 'a1234567890b1234567890c1234567890d'
        self.key_location = self.origin + '/' + self.key + '.txt'
        self.urls = [self.origin + '/', self.origin + '/products/', self.origin + '/guide/']
        self.html = b'<!doctype html><html><head><title>Public product</title></head><body><h1>API guide</h1><p>Public API service details.</p></body></html>'
        self.responses = {}
        self.fetches = []
        self.fetch_patch = patch.object(service.public_http, '_fetch_bounded', side_effect=self.fetch)
        self.fetch_patch.start()
        self.addCleanup(self.fetch_patch.stop)
        self.post_patch = patch.object(service, '_post_indexnow', return_value=200)
        self.post = self.post_patch.start()
        self.addCleanup(self.post_patch.stop)
        # Any missed patch becomes a deterministic test failure, not a network
        # request to a real key file, search endpoint or synthetic domain.
        self.dns_patch = patch('socket.getaddrinfo', side_effect=AssertionError('tests must not resolve DNS'))
        self.dns_patch.start()
        self.addCleanup(self.dns_patch.stop)

    def fetch(self, origin, path, maximum, **kwargs):
        self.fetches.append((origin, path, maximum, kwargs))
        self.assertEqual(origin, self.origin)
        self.assertTrue(kwargs['include_headers'])
        self.assertGreater(kwargs['deadline'], 0)
        if path in self.responses:
            value = self.responses[path]
            if isinstance(value, BaseException):
                raise value
            return value
        if path.endswith('.txt'):
            return {'http_status': 200, 'content_type': 'text/plain; charset=utf-8'}, self.key.encode('ascii')
        return {'http_status': 200, 'content_type': 'text/html; charset=utf-8'}, self.html

    def view(self):
        return service.get_overview(self.database, self.cycle['id'])

    def preview_payload(self, **changes):
        return {'action': 'preview', 'context_sha256': self.view()['context_sha256'],
                'key_location': self.key_location, 'urls': self.urls, **changes}

    def prepare(self, **changes):
        return service.perform_action(self.database, self.cycle['id'], self.preview_payload(**changes))['preview']

    def submit_payload(self, preview, **changes):
        return {'action': 'submit', 'context_sha256': preview['context_sha256'], 'preview_id': preview['id'],
                'request_id': str(uuid.uuid4()), **changes}

    def submit(self, preview, **changes):
        return service.perform_action(self.database, self.cycle['id'], self.submit_payload(preview, **changes))

    def records(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute('SELECT status,record FROM geo_indexnow_notifications').fetchall()


class IndexNowTests(IndexNowFixture, unittest.TestCase):
    def test_get_is_read_only_and_never_networks(self):
        before = self.database.read_bytes()
        view = self.view()
        self.assertTrue(view['supported'], view)
        self.assertEqual(view['site_origin'], self.origin)
        self.assertEqual(view['page_scope'], self.origin + '/')
        self.assertEqual(view['notifications'], [])
        self.assertEqual(view['previews'], [])
        self.assertEqual(view['endpoint'], 'https://api.indexnow.org/indexnow')
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.fetches, [])
        self.post.assert_not_called()

    def test_preview_is_explicit_bounded_and_contains_only_public_content_hashes(self):
        view = self.view()
        preview = self.prepare()
        self.assertTrue(preview['ready'])
        self.assertEqual(preview['changed_urls'], sorted(self.urls))
        self.assertEqual(preview['blocked_urls'], [])
        self.assertIsNone(preview['retry_receipt_id'])
        self.assertEqual(preview['context_sha256'], self.view()['context_sha256'])
        self.assertEqual(preview['context_sha256'], view['context_sha256'])
        self.assertEqual(len(self.fetches), 4)
        self.assertEqual({row[3]['deadline'] for row in self.fetches}, {self.fetches[0][3]['deadline']})
        self.assertLessEqual(self.fetches[0][2], 512)
        self.assertTrue(all(row[2] <= service.MAX_PAGE_BYTES for row in self.fetches))
        self.assertNotIn('key', preview)
        self.assertNotIn('scope', preview)
        self.assertNotIn(self.html.decode(), json.dumps(preview))
        self.post.assert_not_called()
        self.assert_original_unchanged()

    def test_received_is_not_indexed_or_geo_effect_and_fixed_body_cannot_be_overridden(self):
        preview = self.prepare()
        result = self.submit(preview)
        receipt = result['receipt']
        self.assertEqual(receipt['status'], 'received')
        self.assertTrue(receipt['received'])
        self.assertFalse(receipt['indexing_verified'])
        self.assertFalse(receipt['geo_effect_verified'])
        body = json.loads(self.post.call_args.args[0])
        self.assertEqual(body, {'host': 'synthetic.example.org', 'key': self.key,
                               'keyLocation': self.key_location, 'urlList': sorted(self.urls)})
        self.assertEqual(self.view()['notifications'][0]['id'], receipt['id'])
        self.assertEqual(receipt['request_id'], self.view()['notifications'][0]['request_id'])
        for field in ('key', 'scope', 'body', 'request_sha256'):
            self.assertNotIn(field, receipt)
        self.assert_original_unchanged()

    def test_202_is_key_pending_not_verified(self):
        self.post.return_value = 202
        receipt = self.submit(self.prepare())['receipt']
        self.assertEqual(receipt['status'], 'key_pending')
        self.assertFalse(receipt['received'])
        self.assertTrue(receipt['key_validation_pending'])
        self.assertFalse(receipt['indexing_verified'])
        self.assertFalse(receipt['geo_effect_verified'])

    def test_same_request_id_is_read_only_even_after_expiry_and_cannot_bind_other_input(self):
        preview = self.prepare()
        payload = self.submit_payload(preview)
        first = service.perform_action(self.database, self.cycle['id'], payload)['receipt']
        before = self.database.read_bytes()
        with patch.object(service, '_now', return_value=service._now() + timedelta(days=1)):
            repeated = service.perform_action(self.database, self.cycle['id'], copy.deepcopy(payload))['receipt']
        self.assertTrue(repeated['idempotent'])
        self.assertEqual(first['id'], repeated['id'])
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual(self.post.call_count, 1)
        changed = dict(payload, preview_id='f' * 32)
        with self.assertRaises(ValueError):
            service.perform_action(self.database, self.cycle['id'], changed)

    def test_same_public_content_new_request_or_batch_cannot_resend(self):
        preview = self.prepare()
        self.submit(preview)
        with self.assertRaises(ValueError):
            self.submit(preview)
        duplicate = self.prepare(urls=[self.urls[0]])
        self.assertFalse(duplicate['ready'])
        self.assertEqual(duplicate['changed_urls'], [])
        self.assertEqual(duplicate['blocked_urls'][0]['status'], 'received')
        with self.assertRaises(ValueError):
            self.submit(duplicate)
        self.assertEqual(self.post.call_count, 1)

    def test_changed_real_page_content_allows_new_notification_same_json_body(self):
        self.submit(self.prepare())
        first = self.records()[0][1]
        self.html = self.html.replace(b'API guide', b'API guide updated')
        self.submit(self.prepare())
        rows = [json.loads(record) for _, record in self.records()]
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(rows[0]['body_sha256'], rows[1]['body_sha256'])
        self.assertNotEqual(rows[0]['content_sha256'], rows[1]['content_sha256'])
        self.assertIn(first, [record for _, record in self.records()])

    def test_mixed_old_and_new_content_only_sends_changed_page(self):
        self.submit(self.prepare())
        self.responses['/guide/'] = ({'http_status': 200, 'content_type': 'text/html'}, self.html + b' changed')
        preview = self.prepare()
        self.assertEqual(preview['changed_urls'], [self.origin + '/guide/'])
        self.assertEqual(len(preview['blocked_urls']), 2)
        self.submit(preview)
        self.assertEqual(json.loads(self.post.call_args.args[0])['urlList'], [self.origin + '/guide/'])

    def test_unknown_is_durable_and_blocks_new_intent(self):
        self.post.side_effect = TimeoutError('do not expose private exception text')
        preview = self.prepare()
        receipt = self.submit(preview)['receipt']
        self.assertEqual(receipt['status'], 'delivery_unknown')
        self.assertTrue(receipt['delivery_unknown'])
        self.assertNotIn('private exception text', json.dumps(receipt))
        self.assertEqual(self.records()[0][0], 'delivery_unknown')
        duplicate = self.prepare()
        self.assertFalse(duplicate['ready'])
        self.assertEqual(self.post.call_count, 1)

    def test_explicit_http_rejection_requires_preview_bound_manual_retry(self):
        self.post.return_value = 429
        receipt = self.submit(self.prepare())['receipt']
        self.assertEqual(receipt['status'], 'rejected')
        preview = self.prepare(urls=list(reversed(self.urls)))
        self.assertEqual(preview['retry_receipt_id'], receipt['id'])
        with self.assertRaises(ValueError):
            self.submit(preview)
        with self.assertRaises(ValueError):
            self.submit(preview, retry_receipt_id='f' * 32)
        self.post.return_value = 200
        retried = self.submit(preview, retry_receipt_id=receipt['id'])['receipt']
        self.assertEqual(retried['retry_receipt_id'], receipt['id'])
        self.assertEqual(self.post.call_count, 2)

    def test_rejected_content_cannot_bypass_retry_by_batch_regrouping(self):
        self.post.return_value = 403
        receipt = self.submit(self.prepare())['receipt']
        preview = self.prepare(urls=[self.urls[0]])
        self.assertEqual(preview['retry_receipt_id'], receipt['id'])
        with self.assertRaises(ValueError):
            self.submit(preview)
        self.assertEqual(self.post.call_count, 1)

    def test_5xx_is_unknown_and_no_auto_retry(self):
        self.post.return_value = 503
        receipt = self.submit(self.prepare())['receipt']
        self.assertEqual(receipt['status'], 'delivery_unknown')
        self.assertEqual(receipt['http_status'], 503)
        self.assertFalse(self.prepare()['ready'])
        self.assertEqual(self.post.call_count, 1)

    def test_reservation_is_saved_before_network_and_parallel_intents_send_once(self):
        preview = self.prepare()
        payload = self.submit_payload(preview)
        started, release = threading.Event(), threading.Event()

        def sending(*unused):
            self.assertEqual(self.records()[0][0], 'reserved')
            started.set()
            self.assertTrue(release.wait(4))
            return 200

        self.post.side_effect = sending
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(service.perform_action, self.database, self.cycle['id'], payload)
            self.assertTrue(started.wait(4))
            try:
                self.assertTrue(self.view()['notifications'][0]['delivery_unknown'])
                repeated = service.perform_action(self.database, self.cycle['id'], copy.deepcopy(payload))['receipt']
                self.assertEqual(repeated['status'], 'reserved')
                self.assertTrue(repeated['idempotent'])
                with self.assertRaises(ValueError):
                    self.submit(preview)
                self.assertFalse(self.prepare()['ready'])
            finally:
                release.set()
            self.assertEqual(first.result()['receipt']['status'], 'received')
        self.assertEqual(self.post.call_count, 1)

    def test_disk_failure_after_post_leaves_reserved_unknown_and_never_resends(self):
        preview = self.prepare()
        normal = service._connect_before
        calls = 0

        def connect(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise sqlite3.OperationalError('synthetic storage failure')
            return normal(*args, **kwargs)

        with patch.object(service, '_connect_before', side_effect=connect):
            with self.assertRaises(sqlite3.OperationalError):
                self.submit(preview)
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(self.records()[0][0], 'reserved')
        self.assertTrue(self.view()['notifications'][0]['delivery_unknown'])
        self.assertFalse(self.prepare()['ready'])

    def test_fixed_post_transport_is_pinned_single_request_and_never_follows_redirect(self):
        connection = MagicMock()
        connection.getresponse.return_value.status = 302
        pinned = ('synthetic-family', '203.0.113.23')
        with patch.object(service.public_http, '_resolve_public', return_value=pinned) as resolver, \
             patch.object(service.public_http, '_PinnedHTTPSConnection', return_value=connection) as factory:
            self.assertEqual(_REAL_POST(b'{"synthetic":true}', time.monotonic() + 10), 302)
        resolver.assert_called_once_with('api.indexnow.org')
        self.assertEqual(factory.call_args.args, ('api.indexnow.org', pinned))
        connection.request.assert_called_once()
        args, options = connection.request.call_args
        self.assertEqual(args, ('POST', '/indexnow'))
        self.assertEqual(options['body'], b'{"synthetic":true}')
        self.assertNotIn('Cookie', options['headers'])
        self.assertNotIn('Authorization', options['headers'])
        self.assertEqual(options['headers']['Content-Type'], 'application/json; charset=utf-8')
        connection.close.assert_called_once()
        self.post.assert_not_called()

    def test_scope_profile_and_public_content_cas_before_intent(self):
        preview = self.prepare()
        self.html += b' content changed after preview'
        with self.assertRaises(ValueError):
            self.submit(preview)
        self.post.assert_not_called()
        preview = self.prepare()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE projects SET company='Changed entity company'")
        with self.assertRaises(ValueError):
            self.submit(preview)
        self.post.assert_not_called()
        self.assertEqual(self.view()['notifications'], [])

    def test_scope_changed_during_preflight_cannot_send(self):
        preview = self.prepare()
        normal = self.fetch
        mutated = False

        def mutate(*args, **kwargs):
            nonlocal mutated
            result = normal(*args, **kwargs)
            if not mutated:
                mutated = True
                with closing(sqlite3.connect(self.database)) as connection, connection:
                    connection.execute("UPDATE projects SET description='Changed during public checks'")
            return result

        with patch.object(service.public_http, '_fetch_bounded', side_effect=mutate):
            with self.assertRaises(ValueError):
                self.submit(preview)
        self.post.assert_not_called()

    def test_expired_preview_cannot_send(self):
        preview = self.prepare()
        with patch.object(service, '_now', return_value=service._now() + timedelta(seconds=service.PREVIEW_SECONDS + 1)):
            with self.assertRaises(ValueError):
                self.submit(preview)
        self.post.assert_not_called()

    def test_all_nonpublic_or_ambiguous_urls_rejected_before_fetch(self):
        values = ['http://synthetic.example.org/', self.origin + '/?a=1', self.origin + '/#',
            self.origin + '/?', self.origin + '/%2e%2e/private', self.origin + '/%252e%252e/',
            self.origin + '/%2fadmin', self.origin + '/..;/', self.origin + '//guide',
            'https://127.0.0.1/', 'https://8.8.8.8/', 'https://internal.local/',
            'https://user@synthetic.example.org/', 'https://other.example.org/',
            self.origin + '/account/', self.origin + '/products/edit/', self.key_location,
            'https://SYNTHETIC.example.org/', self.origin + '/%zz']
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.prepare(urls=[value])
        self.assertEqual(self.fetches, [])
        self.post.assert_not_called()

    def test_ascii_percent_encoding_aliases_are_rejected_without_conversion(self):
        # 'a', '~' and '@' each have one accepted spelling. Encoded aliases and
        # literal reserved punctuation cannot open a second deduplication key.
        self.assertEqual(service._public_url(self.origin + '/a/~/%40/'), self.origin + '/a/~/%40/')
        aliases = ['/\u0061/%7E/', '/%61/~/','/%41/', '/%7e/', '/%3a/', '/raw@name/', '/raw:name/']
        for path in aliases:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.prepare(urls=[self.origin + path])
        self.assertEqual(self.fetches, [])
        self.post.assert_not_called()

    def test_utf8_percent_hex_case_cannot_bypass_received_content_deduplication(self):
        canonical = self.origin + '/%E4%BA%A7%E5%93%81/'
        self.submit(self.prepare(urls=[canonical]))
        self.assertFalse(self.prepare(urls=[canonical])['ready'])
        fetch_count = len(self.fetches)
        for path in ('/%e4%ba%a7%e5%93%81/', '/%E4%ba%A7%E5%93%81/', '/\u4ea7\u54c1/'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.prepare(urls=[self.origin + path])
        self.assertEqual(len(self.fetches), fetch_count)
        self.assertEqual(self.post.call_count, 1)

    def test_batch_limit_duplicates_and_key_location_range(self):
        for urls in ([], self.urls + [self.origin + '/four/'], [self.urls[0], self.urls[0]]):
            with self.assertRaises(ValueError):
                self.prepare(urls=urls)
        with self.assertRaises(ValueError):
            self.prepare(key_location=self.origin + '/guide/' + self.key + '.txt')
        with self.assertRaises(ValueError):
            self.prepare(key_location=self.origin + '/short.txt')
        self.assertEqual(self.fetches, [])

    def test_product_path_is_exact_frozen_scope_and_key_directory_covers_every_page(self):
        cycle = store.get_cycle(self.database, self.cycle['id'])
        website = self.origin + '/products/dongai'
        cycle['profile']['website'] = website
        with closing(store._connect(self.database)) as connection, connection:
            connection.execute('UPDATE projects SET website=?', (website,))
            scope = service._scope(connection, cycle)
        self.assertEqual(scope['page_scope'], website + '/')
        key_location = website + '/' + self.key + '.txt'
        valid = [website + '/', website + '/pricing/', website + '/guide/']
        key, urls = service._selection(scope, key_location, valid)
        self.assertEqual(key, self.key)
        self.assertEqual(urls, sorted(valid))
        for invalid in (self.origin + '/', self.origin + '/products/dongaix/',
                        self.origin + '/products/other/', self.origin + '/about/',
                        website + '/%2e%2e/other/', website):
            with self.subTest(url=invalid), self.assertRaises(ValueError):
                service._selection(scope, key_location, [invalid])
        with self.assertRaises(ValueError):
            service._selection(scope, self.key_location, valid)

    def test_key_raw_bytes_and_content_type_and_200_required(self):
        path = '/' + self.key + '.txt'
        for metadata, raw in (({'http_status': 200, 'content_type': 'text/html'}, self.key.encode()),
            ({'http_status': 200, 'content_type': 'text/plain'}, self.key.encode() + b'\n'),
            ({'http_status': 200, 'content_type': 'text/plain'}, b'other-key'),
            ({'http_status': 302, 'content_type': 'text/plain'}, self.key.encode()),
            ({'http_status': 200, 'content_type': 'text/plain', 'location': '/login'}, self.key.encode())):
            self.responses[path] = metadata, raw
            with self.assertRaises(ValueError):
                self.prepare()
        self.post.assert_not_called()

    def test_html_login_private_noindex_and_non200_rejected(self):
        variants = [({'http_status': 401, 'content_type': 'text/html'}, self.html),
            ({'http_status': 200, 'content_type': 'application/json'}, b'{}'),
            ({'http_status': 200, 'content_type': 'text/html', 'x_robots_tag': 'noindex'}, self.html),
            ({'http_status': 200, 'content_type': 'text/html', 'x_robots_tag_truncated': True}, self.html),
            ({'http_status': 200, 'content_type': 'text/html'}, self.html.replace(b'</head>', b'<meta name="robots" content="noindex"></head>')),
            ({'http_status': 200, 'content_type': 'text/html'}, b'<html><head><title>Login</title></head><body>Log in</body></html>'),
            ({'http_status': 200, 'content_type': 'text/html'}, self.html.replace(b'</body>', b'<input type="password"></body>')),
            ({'http_status': 200, 'content_type': 'text/html'}, b'<html><head><title>Empty</title></head><body></body></html>')]
        for metadata, raw in variants:
            with self.subTest(metadata=metadata, raw=raw):
                self.responses['/'] = metadata, raw
                with self.assertRaises(ValueError):
                    self.prepare()
        self.post.assert_not_called()

    def test_preflight_network_error_or_total_deadline_never_reserves(self):
        self.responses['/'] = TimeoutError('private message')
        with self.assertRaises(ValueError):
            self.prepare()
        self.responses.clear()
        with patch.object(service, 'TOTAL_SECONDS', 0):
            with self.assertRaises(ValueError):
                self.prepare()
        self.assertEqual(self.view()['notifications'], [])
        self.post.assert_not_called()

    def test_caller_cannot_fake_endpoint_outcome_key_or_receipt(self):
        for key, value in {'endpoint': 'https://attacker.example.org/', 'key': self.key,
                           'status': 'received', 'received': True, 'indexing_verified': True}.items():
            with self.assertRaises(ValueError):
                service.perform_action(self.database, self.cycle['id'], {**self.preview_payload(), key: value})
        self.post.assert_not_called()


class IndexNowAssetFixture(IndexNowFixture):
    def setUp(self):
        super().setUp()
        self.secondary = 'https://api.synthetic.example.org'
        self.secondary_key = 'b2345678901c2345678901d2345678901e'
        self.asset_key_location = self.secondary + '/' + self.secondary_key + '.txt'
        self.asset_urls = [self.secondary + '/docs', self.secondary + '/models']
        self.register_scopes([self.secondary])
        self.asset_id = next(item['id'] for item in self.view()['assets'] if not item['primary'])

    def register_scopes(self, values):
        self.plan = copy.deepcopy(self.plan)
        self.plan['profile']['official_urls'] = values
        self.plan['plan_sha256'] = plan_fingerprint(self.plan)
        self.baseline = copy.deepcopy(self.baseline)
        self.baseline.update(profile=self.plan['profile'], plan_sha256=self.plan['plan_sha256'])
        self.original_baseline = json.dumps(self.baseline, ensure_ascii=False)
        with closing(store._connect(self.database)) as connection, connection:
            connection.execute('UPDATE projects SET official_urls=? WHERE id=?',
                               (json.dumps(values), self.cycle['project_id']))
            connection.execute('UPDATE geo_content_cycles SET baseline=? WHERE id=?',
                               (self.original_baseline, self.cycle['id']))
        self.cycle = store.get_cycle(self.database, self.cycle['id'])

    def fetch(self, origin, path, maximum, **kwargs):
        self.fetches.append((origin, path, maximum, kwargs))
        self.assertIn(origin, (self.origin, self.secondary))
        self.assertTrue(kwargs['include_headers'])
        if (origin, path) in self.responses:
            return self.responses[(origin, path)]
        if path.endswith('.txt'):
            key = self.secondary_key if origin == self.secondary else self.key
            return {'http_status': 200, 'content_type': 'text/plain; charset=utf-8'}, key.encode()
        return {'http_status': 200, 'content_type': 'text/html; charset=utf-8'}, self.html

    def asset_view(self):
        return service.get_overview(self.database, self.cycle['id'], self.asset_id)

    def asset_payload(self, **changes):
        return {'action': 'preview', 'context_sha256': self.asset_view()['context_sha256'],
                'key_location': self.asset_key_location, 'urls': self.asset_urls, **changes}

    def asset_prepare(self, **changes):
        return service.perform_action(self.database, self.cycle['id'], self.asset_payload(**changes), self.asset_id)['preview']


class IndexNowAssetTests(IndexNowAssetFixture, unittest.TestCase):
    def test_registered_assets_are_read_only_candidates_and_primary_context_is_unchanged(self):
        before = self.database.read_bytes()
        view = self.view()
        self.assertEqual(len(view['assets']), 2)
        self.assertEqual([item['control_status'] for item in view['assets']], ['not_checked', 'not_checked'])
        self.assertEqual(view['selected_asset_id'], view['assets'][0]['id'])
        with closing(store._connect(self.database)) as connection:
            scope = service._scope(connection, self.cycle)
            current = service.profile_for_project(dict(connection.execute('SELECT * FROM projects').fetchone()))
        identity = {'cycle_id': self.cycle['id'], 'project_id': self.cycle['project_id'],
            'plan_sha256': self.cycle['test_plan']['plan_sha256'],
            'frozen_profile_sha256': service.profile_sha256(self.cycle['profile']),
            'current_profile_sha256': service.profile_sha256(current), 'site_origin': self.origin, 'path': '/'}
        self.assertEqual(scope['context_sha256'], service._digest(identity))
        self.assertNotIn('asset_id', scope)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.fetches, [])
        self.post.assert_not_called()

    def test_secondary_uses_its_own_key_host_and_explicit_public_pages(self):
        view = self.asset_view()
        self.assertEqual(view['site_origin'], self.secondary)
        self.assertEqual(view['page_scope'], self.secondary + '/')
        self.assertEqual(view['selected_asset_id'], self.asset_id)
        self.assertNotEqual(view['context_sha256'], self.view()['context_sha256'])
        preview = self.asset_prepare()
        self.assertEqual(preview['asset_id'], self.asset_id)
        self.assertEqual(preview['site_origin'], self.secondary)
        payload = self.submit_payload(preview)
        result = service.perform_action(self.database, self.cycle['id'], payload, self.asset_id)
        self.assertEqual(json.loads(self.post.call_args.args[0]), {'host': 'api.synthetic.example.org',
            'key': self.secondary_key, 'keyLocation': self.asset_key_location, 'urlList': self.asset_urls})
        self.assertEqual(result['receipt']['asset_id'], self.asset_id)
        self.assertEqual(result['receipt']['site_origin'], self.secondary)
        self.assertFalse(result['receipt']['indexing_verified'])
        self.assertFalse(result['receipt']['geo_effect_verified'])
        self.assert_original_unchanged()

    def test_unregistered_or_wrong_host_key_and_cross_host_pages_never_fetch(self):
        for changes in ({'key_location': self.key_location}, {'urls': self.urls},
                        {'urls': ['https://other.synthetic.example.org/docs']},
                        {'urls': ['https://attacker.example.org/docs']}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.asset_prepare(**changes)
        with self.assertRaises(ValueError):
            service.perform_action(self.database, self.cycle['id'], self.asset_payload())
        with self.assertRaises(ValueError):
            service.perform_action(self.database, self.cycle['id'], self.asset_payload(), '0' * 64)
        self.assertEqual(self.fetches, [])
        self.post.assert_not_called()

    def test_removal_or_narrowing_current_registration_invalidates_the_old_root(self):
        preview = self.asset_prepare()
        fetches = len(self.fetches)
        for current_urls in ([], [self.secondary + '/docs'], ['https://new.synthetic.example.org/']):
            with self.subTest(current_urls=current_urls):
                with closing(store._connect(self.database)) as connection, connection:
                    connection.execute('UPDATE projects SET official_urls=?', (json.dumps(current_urls),))
                self.assertEqual(len(self.view()['assets']), 1)
                self.assertFalse(self.asset_view()['supported'])
                with self.assertRaises(ValueError):
                    service.perform_action(self.database, self.cycle['id'], self.submit_payload(preview), self.asset_id)
        self.assertEqual(len(self.fetches), fetches)
        self.post.assert_not_called()
        self.assert_original_unchanged()

    def test_primary_preview_cannot_be_sent_as_secondary_and_UUID_is_bound_to_asset_route(self):
        primary = self.prepare()
        with self.assertRaises(ValueError):
            service.perform_action(self.database, self.cycle['id'], self.submit_payload(primary), self.asset_id)
        secondary = self.asset_prepare()
        payload = self.submit_payload(secondary)
        with self.assertRaises(ValueError):
            service.perform_action(self.database, self.cycle['id'], payload)
        first = service.perform_action(self.database, self.cycle['id'], payload, self.asset_id)['receipt']
        before = self.database.read_bytes()
        with patch.object(service, '_now', return_value=service._now() + timedelta(days=1)):
            again = service.perform_action(self.database, self.cycle['id'], copy.deepcopy(payload), self.asset_id)['receipt']
        self.assertEqual(first['id'], again['id'])
        self.assertTrue(again['idempotent'])
        self.assertEqual(self.database.read_bytes(), before)
        with self.assertRaises(ValueError):
            service.perform_action(self.database, self.cycle['id'], payload)
        self.assertEqual(self.post.call_count, 1)

    def test_secondary_duplicate_or_unknown_content_never_resends_and_does_not_block_primary(self):
        self.post.side_effect = TimeoutError('private exception')
        first = service.perform_action(self.database, self.cycle['id'], self.submit_payload(self.asset_prepare()), self.asset_id)
        self.assertEqual(first['receipt']['status'], 'delivery_unknown')
        self.assertFalse(self.asset_prepare()['ready'])
        self.assertTrue(self.prepare()['ready'])
        self.assertEqual(self.post.call_count, 1)

    def test_secondary_private_noindex_and_non_HTML_pages_stay_blocked(self):
        with self.assertRaises(ValueError):
            self.asset_prepare(urls=[self.secondary + '/admin/'])
        for metadata, body in (({'http_status': 401, 'content_type': 'application/json'}, b'{}'),
                               ({'http_status': 200, 'content_type': 'application/json'}, b'{}'),
                               ({'http_status': 200, 'content_type': 'text/html', 'x_robots_tag': 'noindex'}, self.html)):
            self.responses[(self.secondary, '/api/private')] = metadata, body
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                self.asset_prepare(urls=[self.secondary + '/api/private'])
        self.post.assert_not_called()

    def test_another_project_asset_id_or_an_unfrozen_new_current_address_is_not_eligible(self):
        other_project_id = service._asset(self.secondary, self.cycle['project_id'] + 1)['id']
        with self.assertRaises(ValueError):
            service.perform_action(self.database, self.cycle['id'], self.asset_payload(), other_project_id)
        with closing(store._connect(self.database)) as connection, connection:
            connection.execute('UPDATE projects SET official_urls=?',
                               (json.dumps([self.secondary + '/', 'https://new.synthetic.example.org/']),))
        view = self.view()
        self.assertEqual(len(view['assets']), 2)
        self.assertEqual(view['assets'][1]['id'], self.asset_id)
        self.assertNotIn('https://new.synthetic.example.org/', [item['url'] for item in view['assets']])
        self.assertEqual(self.fetches, [])
        self.post.assert_not_called()

    def test_a_registered_secondary_path_does_not_grant_the_whole_host_or_sibling_path(self):
        scoped = copy.deepcopy(self.cycle)
        scoped['profile']['official_urls'] = [self.secondary + '/guide/']
        with closing(store._connect(self.database)) as connection, connection:
            connection.execute('UPDATE projects SET official_urls=?', (json.dumps([self.secondary + '/guide']),))
            secondary_id = service._asset(self.secondary + '/guide', self.cycle['project_id'])['id']
            scope = service._scope(connection, scoped, secondary_id)
        key_url = self.secondary + '/guide/' + self.secondary_key + '.txt'
        service._selection(scope, key_url, [self.secondary + '/guide/models'])
        for key, urls in ((self.asset_key_location, [self.secondary + '/guide/models']),
                          (key_url, [self.secondary + '/models']),
                          (key_url, [self.secondary + '/guidex/models'])):
            with self.subTest(key=key, urls=urls), self.assertRaises(ValueError):
                service._selection(scope, key, urls)
        self.assertEqual(self.fetches, [])
        self.post.assert_not_called()

    def test_secondary_parallel_intents_reserve_once_before_external_send(self):
        preview = self.asset_prepare()
        payload = self.submit_payload(preview)
        started, release = threading.Event(), threading.Event()
        def sending(*unused):
            self.assertEqual(self.records()[0][0], 'reserved')
            started.set()
            self.assertTrue(release.wait(4))
            return 200
        self.post.side_effect = sending
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(service.perform_action, self.database, self.cycle['id'], payload, self.asset_id)
            self.assertTrue(started.wait(4))
            try:
                repeated = service.perform_action(self.database, self.cycle['id'], copy.deepcopy(payload), self.asset_id)['receipt']
                self.assertEqual(repeated['status'], 'reserved')
                self.assertTrue(repeated['idempotent'])
                self.assertFalse(self.asset_prepare()['ready'])
            finally:
                release.set()
            self.assertEqual(first.result()['receipt']['status'], 'received')
        self.assertEqual(self.post.call_count, 1)

    def test_overlapping_secondary_paths_and_later_narrowing_cannot_resend_same_content(self):
        self.register_scopes([self.secondary, self.secondary + '/docs'])
        url = self.secondary + '/docs/chapter'
        first = self.asset_prepare(urls=[url])
        service.perform_action(self.database, self.cycle['id'], self.submit_payload(first), self.asset_id)
        narrow_id = service._asset(self.secondary + '/docs', self.cycle['project_id'])['id']
        def narrow_preview():
            view = service.get_overview(self.database, self.cycle['id'], narrow_id)
            payload = {'action': 'preview', 'context_sha256': view['context_sha256'],
                       'key_location': self.secondary + '/docs/' + self.secondary_key + '.txt', 'urls': [url]}
            return service.perform_action(self.database, self.cycle['id'], payload, narrow_id)['preview']
        self.assertFalse(narrow_preview()['ready'])
        with closing(store._connect(self.database)) as connection, connection:
            connection.execute('UPDATE projects SET official_urls=?', (json.dumps([self.secondary + '/docs']),))
        self.assertFalse(self.asset_view()['supported'])
        self.assertFalse(narrow_preview()['ready'])
        self.assertEqual(self.post.call_count, 1)

    def test_legacy_primary_notification_also_blocks_an_overlapping_new_asset(self):
        nested = self.origin + '/products'
        self.register_scopes([self.secondary, nested])
        url = nested + '/guide'
        self.submit(self.prepare(urls=[url]))
        nested_id = service._asset(nested, self.cycle['project_id'])['id']
        view = service.get_overview(self.database, self.cycle['id'], nested_id)
        payload = {'action': 'preview', 'context_sha256': view['context_sha256'],
                   'key_location': nested + '/' + self.key + '.txt', 'urls': [url]}
        preview = service.perform_action(self.database, self.cycle['id'], payload, nested_id)['preview']
        self.assertFalse(preview['ready'])
        self.assertEqual(preview['blocked_urls'][0]['status'], 'received')
        self.assertEqual(self.post.call_count, 1)

    def test_a_new_narrow_cycle_reconciles_legacy_root_receipts_from_frozen_identity(self):
        first = service.perform_action(self.database, self.cycle['id'],
                                      self.submit_payload(self.asset_prepare(urls=[self.secondary + '/docs/chapter'])), self.asset_id)
        # Legacy records have no explicit asset/host metadata. Their original
        # cycle remains the authority for entity identity, not today's profile.
        with closing(store._connect(self.database)) as connection, connection:
            record = json.loads(connection.execute('SELECT record FROM geo_indexnow_notifications').fetchone()[0])
            for field in ('asset_id', 'site_origin', 'page_scope'):
                record.pop(field, None)
            connection.execute('UPDATE geo_indexnow_notifications SET record=?', (json.dumps(record),))
            connection.execute('UPDATE projects SET official_urls=?', (json.dumps([self.secondary + '/docs']),))
        plan, report = copy.deepcopy(self.plan), copy.deepcopy(self.baseline)
        plan['profile']['official_urls'] = [self.secondary + '/docs']
        report.update(profile=plan['profile'], plan_sha256=plan_fingerprint(plan), run_id='synthetic-narrow-next')
        cycle = store.create_cycle(self.database, report, 'synthetic-narrow-next', self.cycle['project_id'])
        view = service.get_overview(self.database, cycle['id'])
        self.assertEqual(len(view['assets']), 2)
        narrow = next(item for item in view['assets'] if not item['primary'])
        selected = service.get_overview(self.database, cycle['id'], narrow['id'])
        preview = service.perform_action(self.database, cycle['id'], {'action': 'preview',
            'context_sha256': selected['context_sha256'], 'key_location': self.secondary + '/docs/' + self.secondary_key + '.txt',
            'urls': [self.secondary + '/docs/chapter']}, narrow['id'])['preview']
        self.assertFalse(preview['ready'])
        self.assertEqual(preview['blocked_urls'][0]['receipt_id'], first['receipt']['id'])
        self.assertEqual(self.post.call_count, 1)

    def test_overlap_retains_accepted_unknown_and_explicit_rejection_retry_rules(self):
        self.register_scopes([self.secondary, self.secondary + '/docs'])
        narrow_id = service._asset(self.secondary + '/docs', self.cycle['project_id'])['id']
        for expected, http_status in (('received', 200), ('delivery_unknown', 503), ('rejected', 429)):
            with self.subTest(status=expected):
                self.html += expected.encode()
                self.post.return_value = http_status
                root = service.perform_action(self.database, self.cycle['id'],
                    self.submit_payload(self.asset_prepare(urls=[self.secondary + '/docs/chapter'])), self.asset_id)['receipt']
                self.assertEqual(root['status'], expected)
                view = service.get_overview(self.database, self.cycle['id'], narrow_id)
                narrow = service.perform_action(self.database, self.cycle['id'], {'action': 'preview',
                    'context_sha256': view['context_sha256'], 'key_location': self.secondary + '/docs/' + self.secondary_key + '.txt',
                    'urls': [self.secondary + '/docs/chapter']}, narrow_id)['preview']
                if expected == 'rejected':
                    self.assertTrue(narrow['ready'])
                    self.assertEqual(narrow['retry_receipt_id'], root['id'])
                    with self.assertRaises(ValueError):
                        service.perform_action(self.database, self.cycle['id'], self.submit_payload(narrow), narrow_id)
                else:
                    self.assertFalse(narrow['ready'])
        self.assertEqual(self.post.call_count, 3)

    def test_another_frozen_entity_or_project_does_not_inherit_a_content_block(self):
        service.perform_action(self.database, self.cycle['id'], self.submit_payload(self.asset_prepare()), self.asset_id)
        for same_project in (True, False):
            with self.subTest(same_project=same_project):
                plan, report = copy.deepcopy(self.plan), copy.deepcopy(self.baseline)
                name = '新合成树科技有限公司' if same_project else '另一合成树科技有限公司'
                plan['profile'].update(name=name, company=name)
                report.update(profile=plan['profile'], plan_sha256=plan_fingerprint(plan), run_id='synthetic-' + str(same_project))
                if same_project:
                    with closing(store._connect(self.database)) as connection, connection:
                        connection.execute('UPDATE projects SET name=?,company=? WHERE id=?',
                                           (name, name, self.cycle['project_id']))
                cycle = store.create_cycle(self.database, report, 'different-entity-' + str(same_project),
                                           self.cycle['project_id'] if same_project else None)
                asset = next(item for item in service.get_overview(self.database, cycle['id'])['assets'] if not item['primary'])
                view = service.get_overview(self.database, cycle['id'], asset['id'])
                preview = service.perform_action(self.database, cycle['id'], {'action': 'preview',
                    'context_sha256': view['context_sha256'], 'key_location': self.asset_key_location,
                    'urls': self.asset_urls}, asset['id'])['preview']
                self.assertTrue(preview['ready'])
        self.assertEqual(self.post.call_count, 1)


class IndexNowRouteTests(IndexNowFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with patch.dict(os.environ, DATABASE_PATH=str(self.database), DEMO_MODE='false', SEED_DEMO_DATA='false',
                        MEDIA_ROOT=str(self.root / 'media'), COOKIES_DIRECTORY=str(self.root / 'cookies')):
            from sau_backend import app
        config = patch.dict(app.config, DATABASE_PATH=self.database, TESTING=True, ACCESS_CONTROL_ENABLED=False)
        config.start()
        self.addCleanup(config.stop)
        self.client = app.test_client()
        self.url = f"/api/geo-content/cycles/{self.cycle['id']}/indexnow"

    def test_route_get_and_post_protect_origin_size_content_type_and_no_store(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertTrue(response.get_json()['data']['supported'])
        self.assertEqual(self.client.get(self.url, headers={'Origin': 'https://attacker.example.org'}).status_code, 403)
        self.assertEqual(self.client.get(self.url + '?site=https://other.example.org').status_code, 400)
        self.assertEqual(self.client.get(self.url, data='x').status_code, 400)
        self.assertEqual(self.client.post(self.url, data='{}').status_code, 415)
        self.assertEqual(self.client.post(self.url, json=self.preview_payload(),
            headers={'Origin': 'https://attacker.example.org'}).status_code, 403)
        self.assertEqual(self.client.post(self.url, data=json.dumps({'x': 'a' * 17000}),
            content_type='application/json').status_code, 413)
        self.assertEqual(self.client.post(self.url, data='{"action":"preview","action":"submit"}',
            content_type='application/json').status_code, 400)
        prepared = self.client.post(self.url, json=self.preview_payload())
        self.assertEqual(prepared.status_code, 200, prepared.get_json())
        self.assertEqual(prepared.headers['Cache-Control'], 'no-store')
        receipt = self.client.post(self.url, json=self.submit_payload(prepared.get_json()['data']['preview']))
        self.assertEqual(receipt.status_code, 200, receipt.get_json())
        self.assertEqual(receipt.get_json()['data']['receipt']['status'], 'received')
        self.assertEqual(self.post.call_count, 1)


class IndexNowAssetRouteTests(IndexNowAssetFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with patch.dict(os.environ, DATABASE_PATH=str(self.database), DEMO_MODE='false', SEED_DEMO_DATA='false',
                        MEDIA_ROOT=str(self.root / 'media'), COOKIES_DIRECTORY=str(self.root / 'cookies')):
            from sau_backend import app
        config = patch.dict(app.config, DATABASE_PATH=self.database, TESTING=True, ACCESS_CONTROL_ENABLED=False)
        config.start()
        self.addCleanup(config.stop)
        self.client = app.test_client()
        self.url = f'/api/geo-content/cycles/{self.cycle["id"]}/indexnow/assets/{self.asset_id}'

    def test_asset_route_preserves_origin_JSON_size_no_store_and_exact_asset_binding(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.get_json()['data']['selected_asset_id'], self.asset_id)
        self.assertEqual(self.client.get(self.url + '?host=https://attacker.example.org').status_code, 400)
        self.assertEqual(self.client.get(self.url, data='x').status_code, 400)
        self.assertEqual(self.client.get(self.url, headers={'Origin': 'https://attacker.example.org'}).status_code, 403)
        self.assertEqual(self.client.get(self.url.rsplit('/', 1)[0] + '/not-an-asset').status_code, 400)
        self.assertEqual(self.client.post(self.url, data='{}').status_code, 415)
        self.assertEqual(self.client.post(self.url, json=self.asset_payload(),
            headers={'Origin': 'https://attacker.example.org'}).status_code, 403)
        self.assertEqual(self.client.post(self.url, data=json.dumps({'x': 'a' * 17000}),
            content_type='application/json').status_code, 413)
        self.assertEqual(self.client.post(self.url, data='{"action":"preview","action":"submit"}',
            content_type='application/json').status_code, 400)
        prepared = self.client.post(self.url, json=self.asset_payload())
        self.assertEqual(prepared.status_code, 200, prepared.get_json())
        self.assertEqual(prepared.get_json()['data']['preview']['site_origin'], self.secondary)
        receipt = self.client.post(self.url, json=self.submit_payload(prepared.get_json()['data']['preview']))
        self.assertEqual(receipt.status_code, 200, receipt.get_json())
        self.assertEqual(receipt.headers['Cache-Control'], 'no-store')
        self.assertEqual(receipt.get_json()['data']['receipt']['asset_id'], self.asset_id)
        self.assertEqual(self.post.call_count, 1)


if __name__ == '__main__':
    unittest.main()
