import unittest

from services.geo_content_research import _json_output


class JsonRenderingTests(unittest.TestCase):
    def test_exact_observed_language_badge_preserves_complete_json(self):
        self.assertEqual(_json_output('json\n{"title":"核验清单","body":"第一段\\n第二段"}'),
                         {'title': '核验清单', 'body': '第一段\n第二段'})

    def test_badge_does_not_allow_partial_or_multiple_payloads_or_prose(self):
        for raw in ('json\n{"a":', 'json\n{"a":1}\n继续操作',
                    'json\n{"a":1}\n{"b":2}', '说明\njson\n{"a":1}',
                    'json\n["不是对象"]', 'json\njson\n{"a":1}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                _json_output(raw)
