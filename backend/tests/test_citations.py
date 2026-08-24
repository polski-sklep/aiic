"""Citation normalisation, merging and reindexing.

``app/utils/citations.py`` is what decides whether a footnote in a finished
report points at the source that actually supports the claim. It is pure, it is
375 lines, and before this file it had no tests at all.

Every ``expectedFailure`` below asserts the behaviour the module *should* have
and carries the defect id it is blocked on. Do not weaken them to make the run
green -- the run is already green; an expected failure is the record.
"""
from __future__ import annotations

import unittest

from app.utils.citations import (
    _normalize_url,
    build_source_catalog,
    dedupe_sources,
    extract_sources_from_tool_result,
    format_source_catalog_text,
    make_source,
    normalize_footnotes,
    reindex_citations,
    UNRESOLVED_CITATION,
)


def fn(local_id: int, url: str, label: str = "", supports: str = "") -> list[dict[str, object]]:
    """One raw footnote as an agent would emit it."""
    return [{"id": local_id, "url": url, "label": label or url, "supports": supports}]


class MergeAcrossAgentsTest(unittest.TestCase):
    """Two agents independently number their own footnotes from [1].

    The data agents are deliberately isolated from each other (CONTRACTS 4.2),
    so collisions in the local id space are the normal case, not the edge case.
    """

    def test_colliding_local_ids_are_renumbered_apart(self):
        """The happy path: both agents say [1], they end up as [1] and [2]."""
        merged: list[dict[str, object]] = []
        text_a, merged = reindex_citations(
            "Aave TVL is high [1].", normalize_footnotes(fn(1, "https://a.example/aave")), merged
        )
        text_b, merged = reindex_citations(
            "Risk is elevated [1].", normalize_footnotes(fn(1, "https://b.example/risk")), merged
        )

        self.assertEqual(text_a, "Aave TVL is high [1].")
        self.assertEqual(text_b, "Risk is elevated [2].")
        self.assertEqual([m["url"] for m in merged], ["https://a.example/aave", "https://b.example/risk"])

    def test_same_url_from_two_agents_collapses_to_one_footnote(self):
        merged: list[dict[str, object]] = []
        text_a, merged = reindex_citations("A [1].", normalize_footnotes(fn(1, "https://shared.example")), merged)
        text_b, merged = reindex_citations("B [1].", normalize_footnotes(fn(1, "https://shared.example")), merged)

        self.assertEqual(text_a, "A [1].")
        self.assertEqual(text_b, "B [1].")
        self.assertEqual(len(merged), 1)

    def test_supports_is_backfilled_when_the_first_agent_left_it_blank(self):
        merged: list[dict[str, object]] = []
        _, merged = reindex_citations("A [1].", normalize_footnotes(fn(1, "https://s.example")), merged)
        _, merged = reindex_citations(
            "B [1].", normalize_footnotes(fn(1, "https://s.example", supports="quarterly revenue")), merged
        )
        self.assertEqual(merged[0]["supports"], "quarterly revenue")

    def test_QA_001_citation_without_footnotes_must_not_inherit_another_agents_source(self):
        """QA-001 (HIGH): reindex_citations early-returns when footnotes is empty.

        reindex_citations() line 1: ``if not text or not footnotes: return text, merged``.
        An agent that emits prose containing [1] but omits or malforms its
        footnote list keeps the literal [1], which now resolves against the
        *merged* list -- i.e. against whatever the previous agent registered.
        The report links a claim to a source that never supported it.
        """
        merged: list[dict[str, object]] = []
        _, merged = reindex_citations(
            "Tokenomics: emissions are front-loaded [1].",
            normalize_footnotes(fn(1, "https://coingecko.example/aave")),
            merged,
        )
        orphan_text, merged = reindex_citations("Legal: the entity is offshore [1].", [], merged)

        self.assertNotIn("[1]", orphan_text)

    def test_QA_002_dangling_citation_must_not_become_a_valid_wrong_reference(self):
        """QA-002 (HIGH): unmapped ids are left verbatim and go on to resolve.

        ``replace()`` returns ``match.group(0)`` when the local id is not in the
        mapping. Agent A cites [3] but only supplied 2 footnotes, so [3] stays.
        Once agent B appends a third entry to ``merged``, agent A's [3] silently
        starts pointing at agent B's source.
        """
        merged: list[dict[str, object]] = []
        text_a, merged = reindex_citations(
            "Audited [1], insured [2], and treasury-backed [3].",
            normalize_footnotes(fn(1, "https://one.example") + fn(2, "https://two.example")),
            merged,
        )
        _, merged = reindex_citations("Unrelated claim [1].", normalize_footnotes(fn(1, "https://three.example")), merged)

        # [3] in agent A's text now indexes merged[2] == https://three.example,
        # a source agent A never saw.
        self.assertNotIn("[3]", text_a)

    def test_QA_003_duplicate_local_ids_must_not_silently_repoint_the_citation(self):
        """QA-003 (MED): ``mapping[footnote["id"]]`` is overwritten by the last duplicate.

        A model that emits two footnotes both numbered 1 gets both of them into
        the source catalog, but every [1] in the prose resolves to the *second*
        URL. The first source is registered and orphaned.
        """
        merged: list[dict[str, object]] = []
        raw = fn(1, "https://first.example") + fn(1, "https://second.example")
        text, merged = reindex_citations("Claim [1].", normalize_footnotes(raw), merged)

        self.assertEqual(text, "Claim [1].", "citation was repointed at the duplicate")

    def test_QA_006_bracketed_numbers_in_prose_must_not_be_rewritten(self):
        """QA-006 (MED): INLINE_CITATION_RE matches any [digits].

        Agents write prose like "only [2] of five audits are public" or "the top
        [10] holders". Any bracketed integer that collides with a local footnote
        id is rewritten to that footnote's *global* number -- so the prose figure
        silently changes value and becomes a citation to an unrelated source.

        Here two earlier agents have already filled slots 1 and 2, so this
        agent's local 2 maps to global 4: the sentence "only [2] of the five
        audits" is rendered to the reader as "only [4] of the five audits".
        """
        merged: list[dict[str, object]] = []
        _, merged = reindex_citations("prior [1]", normalize_footnotes(fn(1, "https://p1.example")), merged)
        _, merged = reindex_citations("prior [1]", normalize_footnotes(fn(1, "https://p2.example")), merged)

        raw = fn(1, "https://one.example") + fn(2, "https://two.example")
        text, merged = reindex_citations(
            "Only [2] of the five audits are public [1].", normalize_footnotes(raw), merged
        )
        self.assertIn("Only [2] of the five audits", text)


class FootnoteNormalisationTest(unittest.TestCase):
    def test_non_dict_and_urlless_entries_are_dropped(self):
        raw = ["nonsense", 42, {"id": 1}, {"id": 1, "url": ""}, {"url": "https://x.example"}]
        self.assertEqual(normalize_footnotes(raw), [])

    def test_non_list_input_is_tolerated(self):
        for value in (None, {}, "abc", 7):
            self.assertEqual(normalize_footnotes(value), [])

    def test_string_ids_are_accepted_and_sorted(self):
        out = normalize_footnotes(
            [{"id": "3", "url": "https://c.example"}, {"id": 1, "url": "https://a.example"}]
        )
        self.assertEqual([item["id"] for item in out], [1, 3])

    def test_QA_009_float_and_bool_ids_must_not_be_coerced_into_collisions(self):
        """QA-009 (LOW): ``int(item.get("id"))`` truncates 1.9 to 1 and True to 1.

        normalize_footnotes manufactures duplicate ids out of distinct inputs,
        which then feeds QA-003.
        """
        out = normalize_footnotes(
            [
                {"id": 1, "url": "https://a.example"},
                {"id": 1.9, "url": "https://b.example"},
                {"id": True, "url": "https://c.example"},
            ]
        )
        ids = [item["id"] for item in out]
        self.assertEqual(len(set(ids)), len(ids), f"duplicate ids manufactured: {ids}")


class UrlNormalisationTest(unittest.TestCase):
    def test_at_handles_and_bare_x_paths_become_urls(self):
        self.assertEqual(_normalize_url("@vitalikbuterin"), "https://x.com/vitalikbuterin")
        self.assertEqual(_normalize_url("x.com/aave"), "https://x.com/aave")
        self.assertEqual(_normalize_url("twitter.com/aave"), "https://twitter.com/aave")

    def test_empty_values_normalise_to_empty(self):
        for value in (None, "", 0, False, []):
            self.assertEqual(_normalize_url(value), "")

    def test_QA_004_non_url_footnote_targets_must_be_rejected(self):
        """QA-004 (MED): any non-empty string survives _normalize_url.

        A model that writes ``"url": "N/A"`` or ``"internal knowledge"`` gets a
        real entry in the source catalog. Worse, the dedupe key is the lowercased
        string, so "N/A" from the tokenomics agent and "n/a" from the legal agent
        merge into a single footnote carrying only the first agent's label --
        two unrelated unsourced claims now share one fabricated citation.
        """
        for junk in ("N/A", "n/a", "internal knowledge", "none", "TBD"):
            self.assertEqual(_normalize_url(junk), "", f"{junk!r} was accepted as a URL")

    def test_QA_004_unsourced_claims_from_two_agents_stay_separate_and_unsourced(self):
        """QA-004 end to end, now fixed.

        This was a characterisation test asserting the defect: "N/A" and "n/a"
        from two different agents merged into a single footnote carrying only
        the first agent's label, so two unrelated unsourced claims shared one
        fabricated citation. Rewritten to assert the behaviour that replaced it.

        Neither junk target registers a footnote, and each agent's marker
        becomes UNRESOLVED_CITATION -- so the reader still sees that evidence
        was claimed, and no number is produced that a later merge could resolve.
        """
        merged: list[dict[str, object]] = []
        text_a, merged = reindex_citations(
            "a [1]", normalize_footnotes(fn(1, "N/A", label="tokenomics reasoning")), merged
        )
        text_b, merged = reindex_citations(
            "b [1]", normalize_footnotes(fn(1, "n/a", label="legal reasoning")), merged
        )

        self.assertEqual(merged, [])
        self.assertEqual(text_a, f"a {UNRESOLVED_CITATION}")
        self.assertEqual(text_b, f"b {UNRESOLVED_CITATION}")


class DedupeSourcesTest(unittest.TestCase):
    def test_non_dict_and_urlless_sources_are_skipped(self):
        out = dedupe_sources(["x", None, {"label": "a"}, {"label": "b", "url": ""}])
        self.assertEqual(out, [])

    def test_input_sources_are_not_mutated(self):
        original = {"label": "", "url": "https://s.example", "kind": "web"}
        dedupe_sources([original, {"label": "filled", "url": "https://s.example", "kind": "web"}])
        self.assertEqual(original["label"], "", "dedupe_sources mutated its input")

    def test_scheme_and_host_case_differences_are_one_source(self):
        out = dedupe_sources(
            [
                {"label": "a", "url": "https://Example.com/x", "kind": "web"},
                {"label": "b", "url": "https://example.com/x", "kind": "web"},
            ]
        )
        self.assertEqual(len(out), 1)

    def test_QA_005_dedupe_key_must_not_lowercase_the_path(self):
        """QA-005 (MED): ``key = url.lower()`` lowercases the whole URL.

        Host and scheme are case-insensitive; the path is not. Two distinct
        GitHub / IPFS / Notion resources that differ only in path case collapse
        into one source and one of the two citations silently retargets.
        """
        out = dedupe_sources(
            [
                {"label": "a", "url": "https://github.com/Aave/aave-v3-core", "kind": "web"},
                {"label": "b", "url": "https://github.com/aave/aave-v3-core", "kind": "web"},
            ]
        )
        self.assertEqual(len(out), 2, "distinct case-sensitive paths were merged")

    def test_QA_005_dedupe_must_collapse_trailing_slash_and_fragment(self):
        """QA-005 (MED), other direction: no path/fragment normalisation.

        The same page cited as ``/x``, ``/x/`` and ``/x#section`` becomes three
        footnotes, inflating the reference list and splitting the evidence for
        one claim across three numbers.
        """
        out = dedupe_sources(
            [
                {"label": "a", "url": "https://e.example/x", "kind": "web"},
                {"label": "b", "url": "https://e.example/x/", "kind": "web"},
                {"label": "c", "url": "https://e.example/x#section", "kind": "web"},
            ]
        )
        self.assertEqual(len(out), 1, "the same page produced multiple footnotes")


class MergedListInvariantTest(unittest.TestCase):
    def test_QA_007_reindex_must_not_assume_merged_ids_equal_position(self):
        """QA-007 (LOW/MED): the function has an undocumented precondition.

        ``existing_by_url`` is built as ``idx + 1`` and new ids as
        ``len(merged) + 1``. Both assume merged[i]["id"] == i + 1. Hand it a
        merged list that does not satisfy that -- a resumed render, a filtered
        list, anything -- and the id space becomes inconsistent with the
        rendered numbering.
        """
        merged = [{"id": 7, "label": "pre", "url": "https://pre.example", "kind": "source", "supports": ""}]
        _, merged = reindex_citations("x [1]", normalize_footnotes(fn(1, "https://new.example")), merged)
        self.assertEqual([m["id"] for m in merged], list(range(1, len(merged) + 1)))

    def test_QA_008_raw_footnotes_must_not_raise(self):
        """QA-008 (LOW): reindex_citations does ``footnote["url"]`` unguarded.

        It is only safe on output from normalize_footnotes. Any caller that
        forgets that gets a KeyError in the middle of report assembly rather
        than a degraded-but-rendered report.
        """
        try:
            reindex_citations("x [1]", [{"id": 1}], [])
        except KeyError as exc:  # pragma: no cover - the defect
            self.fail(f"raised KeyError {exc}")


class ToolResultSourceExtractionTest(unittest.TestCase):
    def test_error_results_yield_no_sources(self):
        self.assertEqual(
            extract_sources_from_tool_result("get_price", {"coin_id": "aave"}, {"error": "boom"}), []
        )

    def test_non_dict_results_yield_no_sources(self):
        for result in (None, [], "text", 7):
            self.assertEqual(extract_sources_from_tool_result("get_price", {}, result), [])

    def test_web_search_results_become_sources(self):
        out = extract_sources_from_tool_result(
            "web_search",
            {"query": "aave"},
            {"query": "aave", "results": [{"title": "Aave docs", "url": "https://docs.aave.com", "description": "d"}]},
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["url"], "https://docs.aave.com")
        self.assertEqual(out[0]["kind"], "web_search")

    def test_tweets_without_ids_are_skipped(self):
        out = extract_sources_from_tool_result(
            "search_twitter", {}, {"tweets": [{"id": "", "text": "x"}, {"id": "  ", "text": "y"}]}
        )
        self.assertEqual(out, [])

    def test_QA_031_all_null_price_result_must_not_produce_a_citation(self):
        """QA-031 (MED): the only failure signal checked is ``result.get("error")``.

        ``get_price`` returns a full success envelope with every field None when
        CoinGecko has no quote in the requested currency (see
        test_tools_http.CoinGeckoTest). extract_sources_from_tool_result happily
        attaches a CoinGecko source record to it, so the report cites CoinGecko
        as evidence for a number that does not exist.
        """
        empty_price = {
            "coin_id": "aave",
            "price": None,
            "market_cap": None,
            "volume_24h": None,
            "change_24h_pct": None,
            "currency": "eur",
        }
        self.assertEqual(extract_sources_from_tool_result("get_price", {"coin_id": "aave"}, empty_price), [])


class SourceCatalogTest(unittest.TestCase):
    def test_agent_name_is_stamped_from_the_key_when_absent(self):
        catalog = build_source_catalog(
            {"tokenomics_analyst": {"sources": [{"label": "a", "url": "https://a.example", "kind": "web"}]}}
        )
        self.assertEqual(catalog[0]["agent_name"], "tokenomics_analyst")

    def test_existing_agent_name_wins_over_the_key(self):
        catalog = build_source_catalog(
            {"chair": {"sources": [{"label": "a", "url": "https://a.example", "kind": "web", "agent_name": "risk_officer"}]}}
        )
        self.assertEqual(catalog[0]["agent_name"], "risk_officer")

    def test_limit_is_applied_after_dedupe(self):
        sources = [{"label": str(i), "url": f"https://e.example/{i}", "kind": "web"} for i in range(10)]
        self.assertEqual(len(build_source_catalog({"a": {"sources": sources}}, limit=3)), 3)

    def test_agent_results_with_no_sources_attribute_are_skipped(self):
        self.assertEqual(build_source_catalog({"a": "not a result", "b": {"sources": "not a list"}}), [])

    def test_format_handles_empty_and_missing_fields(self):
        self.assertEqual(format_source_catalog_text([]), "No source catalog available.")
        line = format_source_catalog_text([{"label": "", "url": "https://a.example", "kind": "web"}])
        self.assertIn("unknown_agent", line)
        self.assertIn("unknown_tool", line)


class MakeSourceTest(unittest.TestCase):
    def test_urlless_source_is_none(self):
        self.assertIsNone(make_source(label="x", url=""))
        self.assertIsNone(make_source(label="x", url=None))

    def test_supports_is_truncated_and_whitespace_collapsed(self):
        source = make_source(label="x", url="https://a.example", supports="a" * 400)
        self.assertEqual(len(source["supports"]), 220)
        self.assertTrue(source["supports"].endswith("..."))

        source = make_source(label="x", url="https://a.example", supports="a\n\n  b\tc")
        self.assertEqual(source["supports"], "a b c")


if __name__ == "__main__":
    unittest.main()
