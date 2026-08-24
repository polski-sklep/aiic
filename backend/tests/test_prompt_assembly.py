"""Prompt assembly helpers, and one load-bearing constraint they leak.

app/agents/prompt_utils.py builds the sections that carry one agent's output
into another agent's prompt. It is pure and cheap to test, and it is where
CONTRACTS section 4.1 turns out to be enforced in only one of the two places it
needs to be.
"""
from __future__ import annotations

import unittest

from app.agents.prompt_utils import (
    combine_prompt_sections,
    format_prior_outputs_section,
    load_trusted_accounts_section,
)


class CombineSectionsTest(unittest.TestCase):
    def test_empty_and_whitespace_sections_are_dropped(self):
        self.assertEqual(combine_prompt_sections("a", "", None, "   ", "\n\n", "b"), "a\n\nb")

    def test_sections_are_stripped_and_blank_line_separated(self):
        self.assertEqual(combine_prompt_sections("  a  ", "\nb\n"), "a\n\nb")

    def test_no_sections_gives_an_empty_string(self):
        self.assertEqual(combine_prompt_sections(), "")
        self.assertEqual(combine_prompt_sections("", None), "")


class PriorOutputsSectionTest(unittest.TestCase):
    def test_empty_input_produces_no_heading(self):
        self.assertEqual(format_prior_outputs_section({}, "HEADING", (("s", "S", None),)), "")

    def test_a_heading_with_no_content_is_suppressed(self):
        """Never emit a bare heading -- an empty section invites the model to
        invent content under it."""
        self.assertEqual(
            format_prior_outputs_section({"a": {"other": 1}}, "HEADING", (("s", "S", None),)), ""
        )

    def test_non_mapping_agent_outputs_are_skipped(self):
        self.assertEqual(
            format_prior_outputs_section({"a": "errored", "b": None}, "H", (("s", "S", None),)), ""
        )

    def test_list_fields_are_truncated_to_the_limit_and_semicolon_joined(self):
        out = format_prior_outputs_section(
            {"a": {"risks": ["r1", "r2", "r3", "r4"]}}, "H", (("risks", "Risks", 2),)
        )
        self.assertEqual(out, "H:\n[a]\n  Risks: r1; r2")

    def test_zero_is_rendered_but_none_is_omitted(self):
        out = format_prior_outputs_section(
            {"a": {"score": None}, "b": {"score": 0}}, "H", (("score", "Score", None),)
        )
        self.assertEqual(out, "H:\n[b]\n  Score: 0")

    def test_mappings_are_rendered_as_sorted_json(self):
        out = format_prior_outputs_section(
            {"a": {"dq": {"b": 2, "a": 1}}}, "H", (("dq", "DQ", None),)
        )
        self.assertIn('{"a": 1, "b": 2}', out)

    def test_trusted_accounts_file_is_present_and_non_empty(self):
        """base.py and prompt_utils both load this by path; a rename breaks the
        social agents silently (both call sites swallow the miss)."""
        self.assertGreater(len(load_trusted_accounts_section()), 100)

    @unittest.expectedFailure
    def test_QA_043_the_limit_argument_must_bound_string_fields(self):
        """QA-043 (MED): ``limit`` is silently a no-op for strings.

        _stringify_prompt_value excludes str from its Sequence branch and falls
        through to ``str(value)`` with no truncation. Every caller that passes a
        limit for a string field believes it is bounded and is not:

            synthesis_agents.py:38  ("summary", "Summary", 3)
            risk_officer.py:38      ("summary", "Summary", 3)

        Those read as "3 items" but render the full summary for up to eight data
        agents into the Risk Officer's and Maturation Scorer's prompts. Prompt
        size is unbounded by agent verbosity, and the veto-holding agent is the
        one carrying the load.
        """
        out = format_prior_outputs_section(
            {"a": {"summary": "x" * 500}}, "H", (("summary", "Summary", 3),)
        )
        self.assertLess(len(out), 100)

    @unittest.expectedFailure
    def test_QA_044_an_agent_with_no_renderable_fields_must_still_be_visible(self):
        """QA-044 (LOW/MED): a failed agent vanishes rather than being reported.

        If every requested field is missing or None -- which is exactly what a
        crashed or unparseable agent produces -- the ``[agent_name]`` header is
        never emitted. The synthesis agent is not told that agent ran and
        returned nothing; it simply sees a shorter list and reasons as though
        the roster were smaller.
        """
        out = format_prior_outputs_section(
            {"onchain_analyst": {"score": None, "error": "Max tool rounds exceeded"}},
            "PRIOR AGENT SCORES",
            (("score", "Score", None),),
        )
        self.assertIn("onchain_analyst", out)


class TechnicalAnalystExclusionTest(unittest.TestCase):
    """CONTRACTS section 4.1 / handoff section 3.

    "Technical Analyst never influences conviction. It is in
    exclude_from_scores. Its output reaches the Chair only as
    technical_entry_context. Any scoring change must preserve this."
    """

    @staticmethod
    def _result(name: str, score):
        from app.agents.base import AgentResult

        return AgentResult(agent_name=name, output={}, score=score)

    def test_technical_analyst_score_does_not_move_the_weighted_average(self):
        """The half that is enforced -- tested as behaviour, not by grepping for
        the exclude_from_scores literal (handoff 14.2)."""
        from app.agents.orchestrator import Orchestrator

        orchestrator = Orchestrator()
        without = orchestrator._calc_score({"tokenomics_analyst": self._result("tokenomics_analyst", 70)})
        with_ta = orchestrator._calc_score(
            {
                "tokenomics_analyst": self._result("tokenomics_analyst", 70),
                "technical_analyst": self._result("technical_analyst", 0),
            }
        )
        self.assertEqual(without, 70.0)
        self.assertEqual(with_ta, without, "technical_analyst moved the conviction score")

    def test_a_failed_agent_is_renormalised_out_rather_than_scored_zero(self):
        from app.agents.orchestrator import Orchestrator

        orchestrator = Orchestrator()
        score = orchestrator._calc_score(
            {
                "tokenomics_analyst": self._result("tokenomics_analyst", 80),
                "risk_officer": self._result("risk_officer", None),
            }
        )
        self.assertEqual(score, 80.0)

    def test_no_scores_at_all_gives_none_not_zero(self):
        from app.agents.orchestrator import Orchestrator

        self.assertIsNone(Orchestrator()._calc_score({}))

    @unittest.expectedFailure
    def test_QA_013_a_nan_score_from_one_agent_must_not_poison_the_committee_score(self):
        """QA-013 (HIGH), consequence at the point of use.

        extract_score does float(value) with no finiteness check, and json.loads
        accepts the bare literal NaN. One agent emitting {"score": NaN} makes
        _calc_score return NaN for the whole committee. Every threshold
        comparison against NaN is False, so the >=75 and >=60 bands both miss and
        the recommendation lands on PASS -- an unexplainable rejection that looks
        like a considered verdict.
        """
        from app.agents.orchestrator import Orchestrator

        score = Orchestrator()._calc_score(
            {
                "tokenomics_analyst": self._result("tokenomics_analyst", 80),
                "onchain_analyst": self._result("onchain_analyst", float("nan")),
            }
        )
        self.assertEqual(score, 80.0)

    @unittest.expectedFailure
    def test_QA_045_technical_analyst_score_must_not_reach_the_portfolio_manager(self):
        """QA-045 (HIGH): the exclusion is enforced in the arithmetic only.

        PortfolioManager.get_system_prompt (synthesis_agents.py:177) renders
        ``("score", "Score", None)`` over the whole of prior_agent_outputs with
        no filter. When technical_analyst is present its score is printed under
        "PRIOR AGENT SCORES" -- immediately above the section headed "TECHNICAL
        ENTRY CONTEXT (timing only, not conviction)". Observed output:

            PRIOR AGENT SCORES:
            [tokenomics_analyst]
              Score: 70
            [technical_analyst]
              Score: 42

        The Portfolio Manager carries a 0.05 conviction weight and its judgment
        is a conviction input, so a technical score is influencing conviction
        through the prompt even though it is excluded from the average. The
        constraint is about influence, not about one formula.
        """
        from app.agents.synthesis_agents import PortfolioManager

        prior = {
            "tokenomics_analyst": {"score": 70, "summary": "t"},
            "technical_analyst": {"score": 42, "current_price_entry_quality": "poor"},
        }
        prompt = PortfolioManager().get_system_prompt(
            {"project_name": "Aave", "prior_agent_outputs": prior}
        )
        scores_section = prompt.split("PRIOR AGENT SCORES:", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn("technical_analyst", scores_section)

    def test_technical_entry_context_is_still_delivered_separately(self):
        """The channel that is supposed to exist must keep existing -- fixing
        QA-045 must not delete the timing context."""
        from app.agents.synthesis_agents import PortfolioManager

        prompt = PortfolioManager().get_system_prompt(
            {
                "project_name": "Aave",
                "prior_agent_outputs": {
                    "technical_analyst": {"score": 42, "current_price_entry_quality": "poor"}
                },
            }
        )
        self.assertIn("TECHNICAL ENTRY CONTEXT (timing only, not conviction)", prompt)
        self.assertIn("poor", prompt)


if __name__ == "__main__":
    unittest.main()
