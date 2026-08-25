"""Notion block construction and API-limit handling.

For five of the six projects in the calibration corpus the Notion page is the
only surviving record of the committee's reasoning (docs/CONTRACTS.md 2.5).
Two properties therefore matter more than looks:

* markdown must become annotations, not literal characters — the live pages
  were full of `**agent_name**` because Notion does not interpret markdown in a
  rich-text `content` field;
* nothing may be dropped without saying so in the page.

Every limit asserted here was hit for real against the live API on 25 Aug 2026,
including the 1,000-blocks-per-request one, which counts nested children and so
fires on a payload whose `children` array is a perfectly legal 100 entries.
"""
from __future__ import annotations

import unittest

from app.tools.notion import (
    NOTION_CHILDREN_LIMIT,
    NOTION_RICH_TEXT_LIMIT,
    NOTION_TEXT_LIMIT,
    NOTION_TOTAL_BLOCKS_LIMIT,
    _text_to_blocks,
    batch_blocks,
    block_weight,
    bullet_blocks,
    callout_block,
    divider_block,
    heading_block,
    inline_rich_text,
    paragraph_blocks,
    rich_text,
    split_text,
    toggle_block,
)


def plain(objects) -> str:
    return "".join(o["text"]["content"] for o in objects)


def walk(blocks):
    for block in blocks:
        yield block
        yield from walk(block[block["type"]].get("children", []))


def all_rich_text(blocks):
    for block in walk(blocks):
        yield from block[block["type"]].get("rich_text", [])


class SplitTextTest(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(split_text("hello"), ["hello"])

    def test_split_is_exactly_lossless(self):
        text = ("word " * 3000).strip()
        pieces = split_text(text)
        self.assertGreater(len(pieces), 1)
        self.assertEqual("".join(pieces), text)

    def test_every_piece_is_within_the_limit(self):
        text = "lorem ipsum dolor sit amet " * 900
        for piece in split_text(text):
            self.assertLessEqual(len(piece), NOTION_TEXT_LIMIT)

    def test_text_with_no_break_point_is_still_split(self):
        text = "x" * 5000
        pieces = split_text(text)
        self.assertEqual("".join(pieces), text)
        self.assertTrue(all(len(p) <= NOTION_TEXT_LIMIT for p in pieces))


class InlineMarkdownTest(unittest.TestCase):
    """The actual defect: `**name**` rendered as four literal characters."""

    def test_bold_becomes_an_annotation_not_asterisks(self):
        objects = inline_rich_text("**tokenomics_analyst** (score: 74.0): Pendle is…")
        self.assertNotIn("**", plain(objects))
        bold = [o for o in objects if o.get("annotations", {}).get("bold")]
        self.assertEqual([o["text"]["content"] for o in bold], ["tokenomics_analyst"])

    def test_links_become_hrefs(self):
        objects = inline_rich_text("see [the report](https://example.com/r/1) for detail")
        linked = [o for o in objects if "link" in o["text"]]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["text"]["link"]["url"], "https://example.com/r/1")
        self.assertEqual(linked[0]["text"]["content"], "the report")

    def test_code_and_italic(self):
        objects = inline_rich_text("run `pytest` on the _whole_ suite")
        self.assertTrue(any(o.get("annotations", {}).get("code") for o in objects))
        self.assertTrue(any(o.get("annotations", {}).get("italic") for o in objects))
        self.assertNotIn("`", plain(objects))
        self.assertNotIn("_", plain(objects))

    def test_plain_text_survives_unchanged(self):
        text = "no markup here at all"
        self.assertEqual(plain(inline_rich_text(text)), text)

    def test_underscores_inside_identifiers_are_not_italics(self):
        objects = inline_rich_text("tokenomics_analyst and onchain_analyst")
        self.assertEqual(plain(objects), "tokenomics_analyst and onchain_analyst")


class TextToBlocksTest(unittest.TestCase):
    def test_markdown_maps_onto_block_types(self):
        blocks = _text_to_blocks(
            "# Title\n\n## Section\n\n- first\n- second\n\n1. one\n\n> quoted\n\n---\n\nbody"
        )
        types = [b["type"] for b in blocks]
        self.assertEqual(
            types,
            [
                "heading_1",
                "heading_2",
                "bulleted_list_item",
                "bulleted_list_item",
                "numbered_list_item",
                "quote",
                "divider",
                "paragraph",
            ],
        )

    def test_no_block_is_a_paragraph_dump_of_markdown(self):
        blocks = _text_to_blocks("**agent** (score: 74.0): summary text")
        self.assertNotIn("**", "".join(o["text"]["content"] for o in all_rich_text(blocks)))

    def test_fenced_code_becomes_a_code_block(self):
        blocks = _text_to_blocks("intro\n\n```python\nx = 1\n```")
        self.assertEqual([b["type"] for b in blocks], ["paragraph", "code"])
        self.assertEqual(blocks[1]["code"]["language"], "python")

    def test_long_paragraph_is_split_into_valid_blocks(self):
        blocks = _text_to_blocks("word " * 4000)
        for obj in all_rich_text(blocks):
            self.assertLessEqual(len(obj["text"]["content"]), NOTION_TEXT_LIMIT)
        for block in blocks:
            self.assertLessEqual(
                len(block[block["type"]]["rich_text"]), NOTION_RICH_TEXT_LIMIT
            )


class LimitTest(unittest.TestCase):
    def test_rich_text_array_never_exceeds_the_limit(self):
        blocks = paragraph_blocks("word " * 60000)
        self.assertGreater(len(blocks), 1)
        for block in blocks:
            self.assertLessEqual(len(block["paragraph"]["rich_text"]), NOTION_RICH_TEXT_LIMIT)

    def test_block_weight_counts_nested_children(self):
        toggle = toggle_block(rich_text("t"), bullet_blocks("a") + bullet_blocks("b"))
        self.assertEqual(block_weight(toggle), 3)

    def test_batches_respect_the_children_array_limit(self):
        blocks = [divider_block() for _ in range(250)]
        for batch in batch_blocks(blocks):
            self.assertLessEqual(len(batch), NOTION_CHILDREN_LIMIT)
        self.assertEqual(sum(len(b) for b in batch_blocks(blocks)), 250)

    def test_batches_respect_the_total_block_limit(self):
        """A legal 100-entry array can still be an illegal 1,500-block request."""
        heavy = [
            toggle_block(rich_text("agent"), [b for _ in range(15) for b in bullet_blocks("x")])
            for _ in range(100)
        ]
        batches = batch_blocks(heavy)
        self.assertGreater(len(batches), 1)
        for batch in batches:
            self.assertLessEqual(len(batch), NOTION_CHILDREN_LIMIT)
            self.assertLessEqual(
                sum(block_weight(b) for b in batch), NOTION_TOTAL_BLOCKS_LIMIT
            )
        self.assertEqual(sum(len(b) for b in batches), 100)

    def test_batching_preserves_order_and_loses_nothing(self):
        blocks = [heading_block(f"h{i}", 2) for i in range(305)]
        flat = [b for batch in batch_blocks(blocks) for b in batch]
        self.assertEqual(flat, blocks)

    def test_oversized_toggle_children_are_marked_not_dropped(self):
        children = [b for i in range(400) for b in bullet_blocks(f"item {i}")]
        toggle = toggle_block(rich_text("agent"), children)
        kept = toggle["toggle"]["children"]
        self.assertEqual(len(kept), NOTION_CHILDREN_LIMIT)
        tail = kept[-1]
        self.assertEqual(tail["type"], "callout")
        self.assertIn(
            "Truncated",
            "".join(o["text"]["content"] for o in tail["callout"]["rich_text"]),
        )

    def test_callout_rich_text_is_capped_with_a_visible_marker(self):
        objects = [o for i in range(300) for o in rich_text(f"run {i} ", bold=bool(i % 2))]
        block = callout_block(objects)
        kept = block["callout"]["rich_text"]
        self.assertLessEqual(len(kept), NOTION_RICH_TEXT_LIMIT)
        self.assertIn("truncated", kept[-1]["text"]["content"])


class ReportBaseTest(unittest.TestCase):
    def test_loopback_backend_url_is_not_linked(self):
        """The VPS runs BACKEND_URL=http://localhost:8100 — dead as a hyperlink."""
        from app.tools import notion as notion_module

        for url in ("http://localhost:8100", "http://127.0.0.1:8100", "http://0.0.0.0:8100"):
            with self.subTest(url=url):
                self.assertNotEqual(self._resolve(notion_module, url), url)

    def test_reachable_backend_url_is_used_as_given(self):
        from app.tools import notion as notion_module

        self.assertEqual(
            self._resolve(notion_module, "https://reports.example.com/"),
            "https://reports.example.com",
        )

    @staticmethod
    def _resolve(notion_module, backend_url: str) -> str:
        from app.config import get_settings

        settings = get_settings()
        original = settings.backend_url
        try:
            object.__setattr__(settings, "backend_url", backend_url)
            return notion_module.resolve_report_base()
        finally:
            object.__setattr__(settings, "backend_url", original)


if __name__ == "__main__":
    unittest.main()
