"""Newest-first ordering of evaluation runs on a project page.

Jacob's report on a page carrying two runs was that it "seemed like two reports
mashed into one": appending put the oldest at the top and buried the result he
wanted at the bottom.

Notion has no prepend. `PATCH /v1/blocks/{block_id}/children` takes `after`,
naming an existing child; there is no `before`, no move endpoint, and no way to
address position zero. The fake client here reproduces the two behaviours of
the live API (verified 25 Aug 2026) that the implementation depends on:

* an append with `after` returns the created blocks *followed by every sibling
  that already came after them*, so `results[-1]` is the last block on the
  page, not the last block written;
* nested children are not returned at all.

Both are why the multi-run and multi-batch cases below exist. Two runs look
correct under several wrong implementations; three do not.
"""
from __future__ import annotations

import asyncio
import unittest

from app.tools.notion import (
    HISTORY_HEADER_TEXT,
    NOTION_CHILDREN_LIMIT,
    NOTION_TOTAL_BLOCKS_LIMIT,
    _last_created_block_id,
    batch_blocks,
    block_weight,
    bullet_blocks,
    divider_block,
    heading_block,
    history_anchor_id,
    history_header_block,
    is_history_header,
    paragraph_blocks,
    prepend_blocks,
    rich_text,
    toggle_block,
)

PAGE = "3c70a58c-96ec-8192-9dd7-f2ce9ece180e"


def label(block: dict) -> str:
    """A block's type and text, the way a reader sees the page."""
    block_type = block["type"]
    text = "".join(
        obj.get("text", {}).get("content", "")
        for obj in block.get(block_type, {}).get("rich_text", [])
    )
    return f"{block_type}:{text}" if text else block_type


class FakeChildren:
    """The subset of blocks.children this module uses, with live semantics."""

    def __init__(self) -> None:
        self.pages: dict[str, list[dict]] = {PAGE: []}
        self.appends: list[dict] = []
        self._next = 0

    def _id(self) -> str:
        self._next += 1
        return f"blk-{self._next:04d}"

    async def list(self, block_id: str, page_size: int = 100, start_cursor=None):
        children = self.pages.setdefault(block_id, [])
        start = int(start_cursor or 0)
        window = children[start:start + page_size]
        end = start + len(window)
        return {
            "results": window,
            "has_more": end < len(children),
            "next_cursor": str(end) if end < len(children) else None,
        }

    async def append(self, block_id: str, children: list[dict], after: str | None = None):
        siblings = self.pages.setdefault(block_id, [])
        self.appends.append({"after": after, "count": len(children)})

        if len(children) > NOTION_CHILDREN_LIMIT:
            raise AssertionError("children array exceeds Notion's 100-entry limit")
        total = sum(block_weight(child) for child in children)
        if total > NOTION_TOTAL_BLOCKS_LIMIT:
            raise AssertionError(f"request of {total} blocks exceeds the 1000 limit")

        if after is None:
            index = len(siblings)
        else:
            ids = [block["id"] for block in siblings]
            if after not in ids:
                # The live 400: "Block ID (…) to append children after is not
                # parented by (…)". Notably what `after=<the page id>` returns.
                raise AssertionError(f"after={after!r} is not a child of {block_id}")
            index = ids.index(after) + 1

        created = []
        for child in children:
            stored = dict(child)
            stored["id"] = self._id()
            stored["parent"] = {"type": "page_id", "page_id": block_id}
            created.append(stored)
        siblings[index:index] = created

        # Live shape: the created blocks, then everything already after them.
        return {"results": created + siblings[index + len(created):]}


class FakeClient:
    def __init__(self) -> None:
        self.children = FakeChildren()
        self.blocks = self

    def order(self, page_id: str = PAGE) -> list[str]:
        return [label(block) for block in self.children.pages[page_id]]

    def headers(self, page_id: str = PAGE) -> int:
        return sum(is_history_header(b) for b in self.children.pages[page_id])

    def seed(self, blocks: list[dict], page_id: str = PAGE) -> None:
        """Put blocks on the page the way the append-only code left them."""
        run(self.children.append(block_id=page_id, children=blocks))
        self.children.appends.clear()


def run(coro):
    return asyncio.run(coro)


def evaluation(stamp: str) -> list[dict]:
    """The shape orchestrator._notion_blocks emits: divider, then the run."""
    return [
        divider_block(),
        heading_block(f"Evaluation — {stamp}", 1),
        *paragraph_blocks(f"body {stamp}"),
    ]


class HistoryHeaderTest(unittest.TestCase):
    def test_the_header_identifies_itself(self):
        self.assertTrue(is_history_header(history_header_block()))

    def test_other_blocks_are_not_mistaken_for_the_header(self):
        self.assertFalse(is_history_header(divider_block()))
        self.assertFalse(is_history_header(heading_block(HISTORY_HEADER_TEXT, 1)))
        self.assertFalse(is_history_header(paragraph_blocks("anything")[0]))

    def test_the_header_is_recognised_as_read_back_from_the_api(self):
        """A block off the wire carries plain_text, not text.content."""
        block = history_header_block()
        as_read = {
            "type": "callout",
            "callout": {
                "rich_text": [
                    {"plain_text": obj["text"]["content"]}
                    for obj in block["callout"]["rich_text"]
                ]
            },
        }
        self.assertTrue(is_history_header(as_read))

    def test_the_header_says_which_end_is_newest(self):
        text = "".join(
            o["text"]["content"] for o in history_header_block()["callout"]["rich_text"]
        )
        self.assertIn("newest first", text)


class LastCreatedBlockIdTest(unittest.TestCase):
    """`results[-1]` is a trap: the response runs past the blocks it created."""

    def test_trailing_siblings_are_not_mistaken_for_new_blocks(self):
        response = {
            "results": [
                {"id": "new-1", "parent": {"type": "page_id", "page_id": PAGE}},
                {"id": "new-2", "parent": {"type": "page_id", "page_id": PAGE}},
                {"id": "old-1", "parent": {"type": "page_id", "page_id": PAGE}},
                {"id": "old-2", "parent": {"type": "page_id", "page_id": PAGE}},
            ]
        }
        self.assertEqual(_last_created_block_id(response, 2, PAGE), "new-2")

    def test_ids_compare_equal_dashed_or_undashed(self):
        response = {
            "results": [{"id": "new-1", "parent": {"type": "page_id", "page_id": PAGE}}]
        }
        self.assertEqual(
            _last_created_block_id(response, 1, PAGE.replace("-", "")), "new-1"
        )

    def test_nested_children_are_never_used_as_an_anchor(self):
        response = {
            "results": [
                {"id": "new-1", "parent": {"type": "page_id", "page_id": PAGE}},
                {"id": "kid", "parent": {"type": "block_id", "block_id": "new-1"}},
            ]
        }
        self.assertEqual(_last_created_block_id(response, 1, PAGE), "new-1")

    def test_an_empty_response_is_an_error_not_a_wrong_anchor(self):
        with self.assertRaises(RuntimeError):
            _last_created_block_id({"results": []}, 1, PAGE)


class AnchorTest(unittest.TestCase):
    def test_an_empty_page_gains_the_header(self):
        client = FakeClient()
        anchor = run(history_anchor_id(PAGE, client=client))
        self.assertEqual(len(client.children.pages[PAGE]), 1)
        self.assertTrue(is_history_header(client.children.pages[PAGE][0]))
        self.assertEqual(client.children.pages[PAGE][0]["id"], anchor)

    def test_an_existing_header_is_reused_not_duplicated(self):
        client = FakeClient()
        first = run(history_anchor_id(PAGE, client=client))
        second = run(history_anchor_id(PAGE, client=client))
        self.assertEqual(first, second)
        self.assertEqual(client.headers(), 1)

    def test_a_legacy_page_gains_a_header_without_losing_anything(self):
        """No move endpoint, so the header lands after the page's first block.

        A page written by the append-only code has no header and there is no
        way to put one at position zero. Its leading divider stays stranded on
        top; everything below the header is newest-first from then on.
        """
        client = FakeClient()
        client.seed(evaluation("2026-01-01") + evaluation("2026-04-01"))
        before = list(client.order())
        self.assertEqual(before[0], "divider")

        run(prepend_blocks(PAGE, evaluation("2026-07-01"), client=client))

        after = client.order()
        self.assertEqual(after[0], "divider")  # the stranded legacy block
        self.assertTrue(is_history_header(client.children.pages[PAGE][1]))
        self.assertEqual(
            after[2:5],
            [
                "divider",
                "heading_1:Evaluation — 2026-07-01",
                "paragraph:body 2026-07-01",
            ],
        )
        self.assertEqual(after[5:], before[1:])  # nothing old moved or vanished

    def test_an_adopted_page_gains_exactly_one_header_ever(self):
        """The live regression: the header sits at index 1, not index 0.

        Looking only at the first child found nothing there on every later run
        and minted a second header each time. One write hid this; the live page
        showed two headers after two.
        """
        client = FakeClient()
        client.seed(evaluation("2026-01-01"))

        for stamp in ("2026-04-01", "2026-07-01", "2026-10-01"):
            run(prepend_blocks(PAGE, evaluation(stamp), client=client))

        self.assertEqual(client.headers(), 1)
        self.assertTrue(is_history_header(client.children.pages[PAGE][1]))
        self.assertEqual(
            [line for line in client.order() if line.startswith("heading_1:")],
            [
                "heading_1:Evaluation — 2026-10-01",
                "heading_1:Evaluation — 2026-07-01",
                "heading_1:Evaluation — 2026-04-01",
                "heading_1:Evaluation — 2026-01-01",
            ],
        )

    def test_a_header_pushed_down_by_a_hand_written_note_is_still_found(self):
        client = FakeClient()
        run(prepend_blocks(PAGE, evaluation("2026-01-01"), client=client))
        client.children.pages[PAGE][:0] = [
            {"id": "note", "type": "paragraph",
             "paragraph": {"rich_text": [{"text": {"content": "Jacob's note"}}]}}
        ]

        run(prepend_blocks(PAGE, evaluation("2026-04-01"), client=client))

        self.assertEqual(client.headers(), 1)
        self.assertEqual(client.order()[0], "paragraph:Jacob's note")  # untouched
        self.assertTrue(is_history_header(client.children.pages[PAGE][1]))
        self.assertEqual(client.order()[3], "heading_1:Evaluation — 2026-04-01")


class NewestFirstTest(unittest.TestCase):
    def test_three_runs_read_newest_to_oldest(self):
        """Three, not two: two runs look right under several wrong orderings."""
        client = FakeClient()
        for stamp in ("2026-01-01", "2026-04-01", "2026-07-01"):
            run(prepend_blocks(PAGE, evaluation(stamp), client=client))

        headings = [
            line for line in client.order() if line.startswith("heading_1:")
        ]
        self.assertEqual(
            headings,
            [
                "heading_1:Evaluation — 2026-07-01",
                "heading_1:Evaluation — 2026-04-01",
                "heading_1:Evaluation — 2026-01-01",
            ],
        )

    def test_the_header_stays_at_the_top(self):
        client = FakeClient()
        for stamp in ("2026-01-01", "2026-04-01", "2026-07-01"):
            run(prepend_blocks(PAGE, evaluation(stamp), client=client))
        self.assertTrue(is_history_header(client.children.pages[PAGE][0]))
        self.assertEqual(client.headers(), 1)

    def test_each_run_stays_internally_in_order(self):
        client = FakeClient()
        for stamp in ("2026-01-01", "2026-04-01"):
            run(prepend_blocks(PAGE, evaluation(stamp), client=client))
        self.assertEqual(
            client.order()[1:],
            [
                "divider",
                "heading_1:Evaluation — 2026-04-01",
                "paragraph:body 2026-04-01",
                "divider",
                "heading_1:Evaluation — 2026-01-01",
                "paragraph:body 2026-01-01",
            ],
        )

    def test_nothing_already_on_the_page_is_moved_or_deleted(self):
        client = FakeClient()
        run(prepend_blocks(PAGE, evaluation("2026-01-01"), client=client))
        first_ids = [b["id"] for b in client.children.pages[PAGE]]

        run(prepend_blocks(PAGE, evaluation("2026-04-01"), client=client))
        surviving = [b["id"] for b in client.children.pages[PAGE]]
        for block_id in first_ids:
            self.assertIn(block_id, surviving)

    def test_empty_input_writes_nothing_and_creates_no_header(self):
        client = FakeClient()
        self.assertEqual(run(prepend_blocks(PAGE, [], client=client)), 0)
        self.assertEqual(client.order(), [])


class MultiBatchTest(unittest.TestCase):
    """The failure a single batch hides: chaining on the wrong response entry."""

    def test_a_run_spanning_several_batches_lands_in_order(self):
        client = FakeClient()
        long_run = [heading_block(f"h{i}", 3) for i in range(250)]
        self.assertGreater(len(batch_blocks(long_run)), 2)

        written = run(prepend_blocks(PAGE, long_run, client=client))
        self.assertEqual(written, 250)
        self.assertEqual(
            client.order()[1:], [f"heading_3:h{i}" for i in range(250)]
        )

    def test_a_later_run_still_lands_above_a_multi_batch_run(self):
        client = FakeClient()
        run(prepend_blocks(PAGE, [heading_block(f"old{i}", 3) for i in range(250)], client=client))
        run(prepend_blocks(PAGE, [heading_block("new", 3)], client=client))
        self.assertEqual(client.order()[1], "heading_3:new")

    def test_batching_still_respects_both_limits_when_prepending(self):
        """Weight, not array length: 100 toggles of 15 children is 1,600 blocks.

        FakeChildren raises on either violation, so reaching the assertions at
        all is the check; the counts confirm nothing was dropped to get there.
        """
        client = FakeClient()
        heavy = [
            toggle_block(
                rich_text(f"agent {i}"),
                [b for _ in range(15) for b in bullet_blocks("finding")],
            )
            for i in range(100)
        ]
        written = run(prepend_blocks(PAGE, heavy, client=client))
        self.assertEqual(written, 100)
        self.assertGreater(len(client.children.appends), 2)  # header + >1 batch
        self.assertEqual(len(client.children.pages[PAGE]), 101)  # header + 100
        for call in client.children.appends:
            self.assertLessEqual(call["count"], NOTION_CHILDREN_LIMIT)


if __name__ == "__main__":
    unittest.main()
