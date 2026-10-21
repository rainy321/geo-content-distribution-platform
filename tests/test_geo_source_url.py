"""Regression identities; all inputs are synthetic and no URL is requested."""
import json
import shutil
import subprocess
import unittest

from services.geo_source_url import trusted_source_url


class TrustedSourceURLTests(unittest.TestCase):
    def test_fifteen_observed_catalogue_regressions(self):
        # These are the exact 15 synthetic URLs from the 2026-10-21 read-only
        # catalogue probe. A safe URL is not itself proof of an article platform.
        cases = [
            ('https://cloud.tencent.com/developer/article/1006434', True),
            ('https://cloud.tencent.com.evil.example/developer/article/1', False),
            ('https://evil.example/cloud.tencent.com/developer/article/1', False),
            ('https://cloud.tencent.com@evil.example/developer/article/1', False),
            ('https://evil.example@cloud.tencent.com/developer/article/1', False),
            ('https://evil.example\\@cloud.tencent.com/developer/article/1', False),
            ('https://cloud.tencent.com:444/developer/article/1', False),
            ('http://cloud.tencent.com/developer/article/1', False),
            ('https://cloud.tencent.com/developer/../document/product/1', False),
            ('https://developer.aliyun.com/article/1639804', True),
            ('https://developer.aliyun.com/article/new', True),
            ('https://blog.csdn.net/blogdevteam/article/details/113122012', True),
            ('https://blog.csdn.net/other/non-article', True),
            ('https://www.cnblogs.com/user/p/12345678.html', True),
            ('https://www.cnblogs.com/cate/ai/', True),
        ]
        for value, accepted in cases:
            with self.subTest(value=value):
                self.assertEqual(trusted_source_url(value), value if accepted else None)

    def test_exact_public_host_is_not_inferred_from_a_name_or_suffix(self):
        # Ordinary lookalike public domains remain themselves; a caller must
        # compare the parsed exact hostname against its own known-platform list.
        for value in ('https://cloud.tencent.com.other-domain.com/developer/article/1',
                      'https://other-domain.com/cloud.tencent.com/developer/article/1'):
            with self.subTest(value=value):
                self.assertEqual(trusted_source_url(value), value)

    def test_query_and_fragment_keep_their_original_meaning_and_bytes(self):
        for suffix in ('?url=https%3A%2F%2Fother-domain.com%2F..%2Fa#section',
                       '?value=%252f%20%0A&next=/../a#%2F%5C', '?', '#', '?#',
                       '?lang=中文&x=1+2#说明'):
            value = 'https://cloud.tencent.com/developer/article/1' + suffix
            with self.subTest(value=value):
                self.assertEqual(trusted_source_url(value), value)

    def test_only_identity_components_are_canonicalized(self):
        self.assertEqual(trusted_source_url('HTTPS://CLOUD.TENCENT.COM.:443/develo%70er/article/%31?x=%2f#A'),
                         'https://cloud.tencent.com/developer/article/1?x=%2f#A')
        self.assertEqual(trusted_source_url('https://developer.aliyun.com?x=1#A'),
                         'https://developer.aliyun.com/?x=1#A')
        self.assertEqual(trusted_source_url('https://developer.aliyun.com'),
                         'https://developer.aliyun.com/')

    def test_standard_utf8_paths_and_reserved_escape_semantics_are_preserved(self):
        for path in ('/article/中文', '/article/%E4%B8%AD%E6%96%87',
                     '/article/filename%3Bpart%40name%3Av1', '/article/version.1',
                     '/article/.../1', '/article//1'):
            value = 'https://developer.aliyun.com' + path
            with self.subTest(value=value):
                self.assertEqual(trusted_source_url(value), value)

    def test_dot_segments_and_encoded_component_boundaries_are_refused(self):
        paths = ('/./article/1', '/../article/1', '/article/.', '/article/..',
                 '/%2e/article/1', '/%2E%2e/article/1', '/.%2e/article/1',
                 '/%2e./article/1', '/article/%2fother', '/article/%5Cother',
                 '/article/%3fnext=1', '/article/%23fragment',
                 '/article/%252e%252e/1', '/article/%25', '/article/%',
                 '/article/%GG', '/article/%FF', '/article/%C0%AE',
                 '/article/%00', '/article/%0A', '/article/%20',
                 '/article/%E3%80%80', '/article/%E2%80%8B')
        for path in paths:
            with self.subTest(path=path):
                self.assertIsNone(trusted_source_url('https://cloud.tencent.com' + path))

    def test_raw_controls_whitespace_and_backslashes_are_never_trimmed_or_repaired(self):
        for character in (' ', '\t', '\r', '\n', '\x00', '\x7f', '\x85', '\u200b', '\u202e', '\\'):
            for value in (character + 'https://cloud.tencent.com/developer/article/1',
                          'https://cloud.tencent.com/developer/' + character + 'article/1',
                          'https://cloud.tencent.com/developer/article/1?q=' + character):
                with self.subTest(value=repr(value)):
                    self.assertIsNone(trusted_source_url(value))

    def test_nonpublic_or_ambiguous_authorities_are_refused(self):
        authorities = ('localhost', 'localhost.localdomain', 'server.internal',
                       'server.local', 'server.test', 'server.invalid', 'server.example',
                       'server.onion', 'server.alt', 'server.home.arpa', 'server.lan',
                       '127.0.0.1', '127.1', '0x7f000001', '0177.0.0.1',
                       '[::1]', '[2001:db8::1]', '1.2.3.0x4',
                       'cloud%2etencent.com', 'cloud.tencent.com%2fevil.com',
                       'cloud.tencent.com@other-domain.com', 'user:pass@cloud.tencent.com',
                       '@cloud.tencent.com', 'cloud.tencent.com:', 'cloud.tencent.com:80',
                       'cloud.tencent.com:0443', 'cloud.tencent.com:999999',
                       'cloud.tencent.com:invalid', 'cloud.tencent.com..',
                       '-bad.tencent.com', 'bad-.tencent.com', 'bad_name.tencent.com',
                       '全角.tencent.com', 'ｃloud.tencent.com', 'cloud.tencent.com：443',
                       'xn--invalid-.com', 'x' * 64 + '.com')
        for authority in authorities:
            with self.subTest(authority=authority):
                self.assertIsNone(trusted_source_url('https://' + authority + '/article/1'))

    def test_valid_alabels_are_retained_without_guessing_unicode_hosts(self):
        self.assertEqual(trusted_source_url('https://xn--caf-dma.fr/article/1'),
                         'https://xn--caf-dma.fr/article/1')

    def test_relative_and_non_https_values_are_not_source_identities(self):
        for value in (None, 1, False, {}, b'https://cloud.tencent.com/', '',
                      '/developer/article/1', '//cloud.tencent.com/developer/article/1',
                      'http://cloud.tencent.com/developer/article/1',
                      'javascript:https://cloud.tencent.com/developer/article/1',
                      'https:cloud.tencent.com/developer/article/1',
                      'https:////cloud.tencent.com/developer/article/1'):
            with self.subTest(value=repr(value)):
                self.assertIsNone(trusted_source_url(value))

    @unittest.skipUnless(shutil.which('node'), 'Node required only for independent WHATWG URL comparison')
    def test_independent_whatwg_parser_confirms_the_reproduced_interpretation_gap(self):
        # The standard's special-scheme host/path states treat backslash as a
        # separator and remove ASCII/percent-encoded dot segments. Run Node's
        # WHATWG implementation, not an imitation of our Python checks.
        # https://url.spec.whatwg.org/#host-state
        # https://url.spec.whatwg.org/#path-state
        values = ['https://other-domain.com\\@cloud.tencent.com/developer/article/1',
                  'https://cloud.tencent.com/developer/../document/product/1',
                  'https://cloud.tencent.com/developer/%2e%2e/document/product/1']
        program = "process.stdout.write(JSON.stringify(JSON.parse(process.argv[1]).map(value=>{const u=new URL(value);return {host:u.hostname,path:u.pathname}})))"
        result = subprocess.run([shutil.which('node'), '-e', program, json.dumps(values)],
                                check=True, capture_output=True, text=True, timeout=10)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed[0], {'host': 'other-domain.com', 'path': '/@cloud.tencent.com/developer/article/1'})
        self.assertEqual(parsed[1:], [{'host': 'cloud.tencent.com', 'path': '/document/product/1'}] * 2)
        self.assertEqual([trusted_source_url(value) for value in values], [None] * 3)


if __name__ == '__main__':
    unittest.main()
