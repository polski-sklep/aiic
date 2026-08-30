"""A literal newline inside a JSON string must not lose a whole report.

The Plasma evaluation of 2026-08-30 produced 73,956 characters of complete,
well-formed report and recorded `report_failure_reason: unparseable`, because
the Report Writer formatted a bullet list with real newlines inside a string
value. Strict JSON forbids that; the report was otherwise perfect.
"""

import unittest

from app.agents.base import _loads


class ControlCharactersInStrings(unittest.TestCase):
    def test_a_literal_newline_inside_a_string_parses(self):
        # The exact shape that failed, reduced.
        raw = '{"sections": {"3_tokenomics": "~$195M at spot [55].\n• 2026-09-25 tranche"}}'
        value, ok = _loads(raw)
        self.assertTrue(ok, "a real newline inside a string must not fail the report")
        self.assertIn("\n", value["sections"]["3_tokenomics"])

    def test_tab_and_carriage_return_too(self):
        value, ok = _loads('{"a": "x\ty\r\nz"}')
        self.assertTrue(ok)
        self.assertEqual(value["a"], "x\ty\r\nz")

    def test_genuinely_malformed_json_still_fails(self):
        # strict=False must not become "accept anything".
        for bad in ('{"a": }', '{"a" "b"}', '{"a": "unterminated', "{'a': 1}", "not json"):
            with self.subTest(bad=bad):
                _value, ok = _loads(bad)
                self.assertFalse(ok, f"{bad!r} should not parse")

    def test_escapes_are_still_validated(self):
        _value, ok = _loads('{"a": "bad escape \\q"}')
        self.assertFalse(ok)

    def test_normal_output_is_unaffected(self):
        value, ok = _loads('{"score": 31.6, "sections": {"1": "clean"}}')
        self.assertTrue(ok)
        self.assertEqual(value["score"], 31.6)


if __name__ == "__main__":
    unittest.main()
