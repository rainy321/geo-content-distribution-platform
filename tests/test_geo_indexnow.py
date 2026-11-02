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


if __name__ == '__main__':
    unittest.main()
