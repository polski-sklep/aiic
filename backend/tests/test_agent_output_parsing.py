"""The untrusted-text boundary: BaseAgent.parse_output / extract_score.

Everything a model writes crosses into structured data here. These are pure
functions, so every case below is a literal string an LLM can and does emit.
"""
from __future__ import annotations

import json
import unittest

from app.agents.base import BaseAgent


class ParseOutputTest(unittest.TestCase):
    def setUp(self):
        self.agent = BaseAgent()

    def parse(self, raw: str) -> dict:
        return self.agent.parse_output(raw)

    # --- cases the parser already handles; regression cover -------------------

    def test_plain_json_object(self):
        self.assertEqual(self.parse('{"score": 85}'), {"score": 85})

    def test_fenced_block_with_language_tag(self):
        self.assertEqual(self.parse('```json\n{\n "score": 85\n}\n```'), {"score": 85})

    def test_fenced_block_without_language_tag(self):
        self.assertEqual(self.parse('```\n{"score": 85}\n```'), {"score": 85})

    def test_leading_and_trailing_whitespace(self):
        self.assertEqual(self.parse('\n\n  {"score": 85}  \n'), {"score": 85})

    def test_braces_inside_string_values_survive(self):
        out = self.parse('{"summary": "we use {curly} braces", "score": 85}')
        self.assertEqual(out["score"], 85)
        self.assertEqual(out["summary"], "we use {curly} braces")

    def test_unicode_and_emoji_survive(self):
        out = self.parse('{"summary": "你好 🚀 café", "score": 85}')
        self.assertEqual(out["summary"], "你好 🚀 café")

    def test_empty_string_degrades_to_parse_error(self):
        out = self.parse("")
        self.assertEqual(out["parse_error"], "Could not parse structured JSON from agent output")
        self.assertEqual(out["summary"], "")

    def test_truncated_json_degrades_to_parse_error_and_keeps_the_text(self):
        raw = '{"score": 85, "summary": "the protocol has'
        out = self.parse(raw)
        self.assertIn("parse_error", out)
        self.assertEqual(out["raw_output"], raw)

    def test_json_array_is_rejected_as_not_an_object(self):
        out = self.parse('[{"score": 85}]')
        self.assertEqual(out["parse_error"], "Agent output was valid JSON but not an object")

    def test_json_scalar_is_rejected_as_not_an_object(self):
        for raw in ("85", '"just a string"', "null", "true"):
            self.assertEqual(
                self.parse(raw)["parse_error"], "Agent output was valid JSON but not an object", raw
            )

    def test_prose_only_output_is_summarised_and_flagged(self):
        out = self.parse("I could not complete this analysis because the tools returned no data.")
        self.assertIn("parse_error", out)
        self.assertTrue(out["summary"].startswith("I could not complete"))

    def test_summary_is_capped_at_500_chars_but_raw_output_is_not(self):
        raw = "x" * 10_000
        out = self.parse(raw)
        self.assertEqual(len(out["summary"]), 500)
        self.assertEqual(len(out["raw_output"]), 10_000)

    def test_ten_megabytes_of_prose_does_not_blow_up(self):
        out = self.parse("y" * (10 * 1024 * 1024))
        self.assertEqual(len(out["summary"]), 500)

    # --- defects --------------------------------------------------------------

    def test_QA_010_unterminated_code_fence_must_not_destroy_the_payload(self):
        """QA-010 (HIGH): ``lines[1:-1]`` assumes a closing fence exists.

        base.py::parse_output strips the first *and last* line of anything
        starting with ```. When the model is cut off by max_tokens the closing
        fence is missing, so the slice eats the final line of real JSON -- the
        closing brace. The find("{")/rfind("}") fallback then finds no "}" at all
        (rfind returns -1, end becomes 0, ``end > start`` is False) and a fully
        recoverable payload is thrown away as unparseable.
        """
        out = self.parse('```json\n{\n "score": 85,\n "confidence": "high"\n}')
        self.assertEqual(out.get("score"), 85)

    def test_QA_011_preamble_containing_a_brace_must_not_defeat_recovery(self):
        """QA-011 (MED): recovery spans find("{") .. rfind("}") over the whole text.

        Any brace before the object -- a model explaining its format, a template
        placeholder -- widens the slice to something that is not JSON.
        """
        out = self.parse('I will respond in the shape {field: value}: {"score": 85}')
        self.assertEqual(out.get("score"), 85)

    def test_QA_011_trailing_prose_containing_a_brace_must_not_defeat_recovery(self):
        """QA-011 (MED), the common half.

        The JSON object here is complete and valid. One brace in the sign-off
        sentence pushes rfind("}") past the object and the whole assessment is
        discarded.
        """
        out = self.parse('{"score": 85}\nNote: I used {search_notes} for prior context.')
        self.assertEqual(out.get("score"), 85)

    def test_QA_011_two_objects_recover_the_first_complete_one(self):
        """QA-011 (was MED, fixed): a model that emits an example then the answer.

        rfind used to span both, so neither was recovered. The replacement scans
        for balanced objects and yields them first-one-first, so recovery is
        deterministic. Asserting *which* object comes back, not merely that one
        does -- the first is what _balanced_object_candidates documents, and a
        change to last-wins would otherwise pass silently.
        """
        out = self.parse('{"score": 0, "summary": "example"}\n{"score": 85, "summary": "real"}')
        self.assertEqual(out, {"score": 0, "summary": "example"})

    def test_QA_012_deeply_nested_input_must_not_raise(self):
        """QA-012 (MED): only json.JSONDecodeError is caught.

        json.loads raises RecursionError on deep nesting. It escapes
        parse_output, is caught by BaseAgent.run's blanket handler, and the agent
        is recorded as errored rather than as having produced unparseable text --
        which is a different and more alarming failure mode for the orchestrator.
        """
        out = self.parse("[" * 200_000)
        self.assertIn("parse_error", out)

    def test_QA_012_non_string_input_must_not_raise(self):
        """QA-012 (MED): ``raw_text.strip()`` assumes str.

        Providers that return structured content blocks rather than a flat string
        take down the agent with an AttributeError.
        """
        out = self.parse(None)
        self.assertIn("parse_error", out)


class ExtractScoreTest(unittest.TestCase):
    def setUp(self):
        self.agent = BaseAgent()

    def score(self, value):
        return self.agent.extract_score({"score": value})

    # --- coercions that are correct -------------------------------------------

    def test_numeric_string_is_coerced(self):
        self.assertEqual(self.score("85"), 85.0)
        self.assertEqual(self.score("  85  "), 85.0)

    def test_float_and_int_pass_through(self):
        self.assertEqual(self.score(85), 85.0)
        self.assertEqual(self.score(85.0), 85.0)

    def test_missing_none_and_unparseable_give_none(self):
        self.assertIsNone(self.agent.extract_score({}))
        self.assertIsNone(self.score(None))
        self.assertIsNone(self.score("high"))
        self.assertIsNone(self.score("85%"))
        self.assertIsNone(self.score([85]))
        self.assertIsNone(self.score({"value": 85}))

    def test_zero_is_a_real_score_not_a_missing_one(self):
        """0 is falsy; it must still be returned, not swallowed."""
        self.assertEqual(self.score(0), 0.0)

    # --- defects --------------------------------------------------------------

    def test_QA_013_nan_must_not_be_accepted_as_a_score(self):
        """QA-013 (HIGH): NaN reaches the weighted average and poisons it.

        Python's json.loads accepts the non-standard literal NaN, extract_score
        does float(nan) without a finiteness check, and NaN propagates through
        orchestrator._calc_score. Every threshold comparison against NaN is
        False, so the committee silently lands on the bottom band.
        """
        self.assertIsNone(self.score(json.loads('{"score": NaN}')["score"]))

    def test_QA_013_infinity_must_not_be_accepted_as_a_score(self):
        """QA-013 (HIGH): 1e400 in JSON parses to inf; "infinity" as a string does too."""
        self.assertIsNone(self.score(json.loads('{"score": 1e400}')["score"]))
        self.assertIsNone(self.score("infinity"))

    def test_QA_013_out_of_range_scores_must_be_rejected_or_clamped(self):
        """QA-013 (HIGH): the prompt says 0-100; nothing enforces it.

        A model that answers on a 0-1 scale, in basis points, or with a negative
        penalty score silently reweights the whole committee. 8500 against a 0.15
        weight moves the overall score by more than 1200 points.
        """
        for value in (-1, 101, 8500, -100.0):
            self.assertIsNone(self.score(value), f"{value} was accepted as a 0-100 score")

    def test_QA_013_booleans_must_not_become_scores(self):
        """QA-013 (HIGH): float(True) is 1.0, float(False) is 0.0.

        ``"score": true`` -- a plausible mis-generation -- becomes a score of 1.0,
        a maximally bearish vote, instead of being rejected as unparseable and
        renormalised out of the average.
        """
        self.assertIsNone(self.score(True))
        self.assertIsNone(self.score(False))


if __name__ == "__main__":
    unittest.main()
