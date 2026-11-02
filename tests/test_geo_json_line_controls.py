import json
import unittest
from services.geo_content_research import _json_output


class JsonLineControlTests(unittest.TestCase):
    def test_literal_line_controls_keep_the_exact_string_content(self):
        value='{"title":"Example","content":"first\nsecond\r\nthird\tend"}'
        self.assertEqual(_json_output(value),{'title':'Example','content':'first\nsecond\r\nthird\tend'})
        self.assertIn('\nsecond',value)

    def test_valid_escaped_sequences_and_newlines_outside_strings_stay_valid(self):
        data={'title':'A "quoted" title','content':'line 1\nline 2\t\\n literal'}
        for raw in [json.dumps(data),json.dumps(data,indent=2),'```json\n'+json.dumps(data)+'\n```']:
            self.assertEqual(_json_output(raw),data)

    def test_brackets_in_strings_are_data_and_not_fragment_boundaries(self):
        self.assertEqual(_json_output('{"content":"{first}\n} next [data]"}'),{'content':'{first}\n} next [data]'})

    def test_unrelated_corruption_surrounding_prose_and_ambiguous_escapes_still_fail(self):
        for raw in ['prefix {"content":"a\nb"}', '{"content":"a\nb"} trailing',
                    '{"content":"a\nb"', '{"content":"a\nb",}',
                    '{"content":"a\x00\nb"}', '{"content":"a\\\nb"}',
                    '{"content":"a\nb"}{"other":1}', '["a\nb"]']:
            with self.subTest(raw=repr(raw)),self.assertRaises(ValueError):_json_output(raw)



if __name__ == '__main__':unittest.main()
