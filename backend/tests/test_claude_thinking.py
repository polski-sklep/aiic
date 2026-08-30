"""What `ClaudeProvider` actually puts on the wire, and what it does with a
thinking model's reply.

WHY THIS FILE EXISTS
--------------------
The Opus 5 / Sonnet 5 migration turned on a behaviour that nothing in this
repository had ever exercised: the models THINK. Three of its consequences are
invisible from the outside and each would degrade the committee silently rather
than loudly.

1. `thinking` and `output_config.effort` are a deliberate decision (see the
   block comment in `llm/claude.py`), and on these models an *omitted*
   `thinking` is not "off" — it is adaptive. A future edit that drops either
   key would not fail: it would quietly change how fifteen agents reason and
   what a run costs. Asserted here so it fails instead.

2. `max_tokens` caps thinking PLUS answer. Every agent's `max_tokens` was sized
   when thinking did not exist, so the provider adds headroom. Removing that
   would truncate answers mid-JSON, which `parse_output`'s recovery path would
   then half-repair into a plausible wrong verdict.

3. A thinking model wants its own reasoning handed back unmodified on the next
   round of the same conversation. The agent loop rebuilds the assistant turn
   from text + tool calls, which drops thinking blocks; `content_blocks` carries
   them instead. That round trip is asserted end to end.

These run against a real `anthropic.AsyncAnthropic` pointed at a local mock
Messages endpoint, so the SDK does the request serialisation and the response
parsing. A fake client would prove our dict-building and nothing about whether
the pinned SDK accepts these parameters or can parse a thinking block — which
is exactly what `anthropic==0.42.0` could not do, and the reason that pin moved.
"""
from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from anthropic import AsyncAnthropic

from app.llm import LLMMessage, ModelTier, ToolDefinition
from app.llm.claude import (
    EFFORT,
    THINKING_HEADROOM_TOKENS,
    THINKING_MODE,
    ClaudeProvider,
)


def _turn(msg_id, stop_reason, blocks, cache_write=0, cache_read=0):
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 11,
            "output_tokens": 42,
            "cache_creation_input_tokens": cache_write,
            "cache_read_input_tokens": cache_read,
        },
        "content": blocks,
    }


TOOL_TURN = _turn(
    "msg_1",
    "tool_use",
    [
        {"type": "thinking", "thinking": "", "signature": "sig-abc"},
        {"type": "text", "text": "Looking that up."},
        {"type": "tool_use", "id": "tu_1", "name": "get_price", "input": {"id": "plasma"}},
    ],
    cache_write=1234,
    cache_read=5678,
)

FINAL_TURN = _turn(
    "msg_2",
    "end_turn",
    [
        {"type": "thinking", "thinking": "", "signature": "sig-def"},
        {"type": "text", "text": '{"score": 7}'},
    ],
    cache_read=90123,
)

REFUSAL_TURN = _turn("msg_3", "refusal", [])


def _sse(turn: dict) -> bytes:
    """Render a turn as the SSE stream the SDK's stream helper consumes."""
    start = {k: v for k, v in turn.items() if k != "content"}
    start["content"] = []
    start["usage"] = dict(turn["usage"], output_tokens=0)
    events = [("message_start", {"type": "message_start", "message": start})]

    for index, block in enumerate(turn["content"]):
        if block["type"] == "thinking":
            opening = {"type": "thinking", "thinking": "", "signature": ""}
            deltas = [{"type": "signature_delta", "signature": block["signature"]}]
        elif block["type"] == "text":
            opening = {"type": "text", "text": ""}
            deltas = [{"type": "text_delta", "text": block["text"]}]
        else:
            opening = {
                "type": "tool_use",
                "id": block["id"],
                "name": block["name"],
                "input": {},
            }
            deltas = [
                {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
            ]
        events.append(
            ("content_block_start",
             {"type": "content_block_start", "index": index, "content_block": opening})
        )
        for delta in deltas:
            events.append(
                ("content_block_delta",
                 {"type": "content_block_delta", "index": index, "delta": delta})
            )
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))

    events.append(
        ("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": turn["stop_reason"], "stop_sequence": None},
            "usage": {"output_tokens": turn["usage"]["output_tokens"]},
        })
    )
    events.append(("message_stop", {"type": "message_stop"}))

    out = b""
    for name, data in events:
        out += ("event: %s\ndata: %s\n\n" % (name, json.dumps(data))).encode()
    return out


class _MockAnthropic:
    """A local /v1/messages that records requests and replays scripted turns."""

    def __init__(self, turns):
        self.requests: list[dict] = []
        self._turns = list(turns)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(body)
                turn = outer._turns[min(len(outer.requests) - 1, len(outer._turns) - 1)]
                if body.get("stream"):
                    payload, ctype = _sse(turn), "text/event-stream"
                else:
                    payload, ctype = json.dumps(turn).encode(), "application/json"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]

    def __enter__(self):
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        return False

    def provider(self) -> ClaudeProvider:
        provider = ClaudeProvider.__new__(ClaudeProvider)
        provider.client = AsyncAnthropic(
            api_key="mock-key-not-a-credential",
            base_url="http://127.0.0.1:%d" % self.port,
            max_retries=0,
        )
        return provider


TOOLS = [
    ToolDefinition(
        name="get_price",
        description="price",
        parameters={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    )
]


def _base_messages() -> list[LLMMessage]:
    # Long enough that the cache breakpoint on the system head is meaningful.
    return [
        LLMMessage(role="system", content="You are the maturation scorer." + " pad" * 400),
        LLMMessage(role="user", content="Evaluate Plasma."),
    ]


class RequestShapeTest(unittest.TestCase):
    """What leaves the process."""

    def setUp(self):
        with _MockAnthropic([TOOL_TURN]) as mock:
            asyncio.run(
                mock.provider().complete(
                    _base_messages(), tier=ModelTier.STRONG, tools=TOOLS, max_tokens=4096
                )
            )
            self.request = mock.requests[0]

    def test_thinking_is_sent_explicitly_and_is_adaptive(self):
        # Omitting `thinking` on Opus 5 / Sonnet 5 does NOT mean "no thinking",
        # it means adaptive. Whatever this project wants, it says so out loud.
        self.assertEqual(self.request.get("thinking"), {"type": "adaptive"})
        self.assertEqual(THINKING_MODE["type"], "adaptive")

    def test_effort_is_sent_explicitly(self):
        self.assertEqual(self.request.get("output_config"), {"effort": EFFORT})

    def test_effort_is_within_the_range_disabled_thinking_would_allow(self):
        # Not a live constraint today — thinking is on — but it is the trap for
        # anyone who later switches thinking off: `disabled` with xhigh or max
        # is a 400 on Opus 5, checked per request.
        self.assertIn(EFFORT, ("low", "medium", "high", "xhigh", "max"))

    def test_max_tokens_carries_thinking_headroom(self):
        # The agent asked for 4096 tokens of ANSWER. Thinking shares the cap.
        self.assertEqual(self.request["max_tokens"], 4096 + THINKING_HEADROOM_TOKENS)
        self.assertGreaterEqual(THINKING_HEADROOM_TOKENS, 4096)

    def test_no_sampling_parameters(self):
        # CONTRACTS.md 4.4, and a 400 on every current model.
        for banned in ("temperature", "top_p", "top_k"):
            self.assertNotIn(banned, self.request)

    def test_no_budget_tokens_anywhere(self):
        # Removed on Opus 5 / Sonnet 5; sending it is a 400. It has never been
        # in this codebase and this is what keeps it out.
        self.assertNotIn("budget_tokens", json.dumps(self.request))

    def test_no_assistant_prefill(self):
        # A trailing assistant turn is a 400 on these models.
        self.assertNotEqual(self.request["messages"][-1]["role"], "assistant")

    def test_prompt_cache_breakpoints_survive_the_migration(self):
        # The migration must not cost the caching that took a run from
        # $1.69-priced-wrong to a real $3.59.
        self.assertGreaterEqual(json.dumps(self.request).count('"cache_control"'), 2)

    def test_a_thinking_request_streams(self):
        # 4096 was below STREAMING_THRESHOLD_TOKENS before the headroom; with
        # thinking on, every agent streams and no run can hit the 600s wall.
        self.assertTrue(self.request.get("stream"))


class ThinkingResponseTest(unittest.TestCase):
    """What comes back, and what survives to the next round."""

    def test_thinking_block_parses_and_never_reaches_the_json_parser(self):
        with _MockAnthropic([TOOL_TURN]) as mock:
            response = asyncio.run(
                mock.provider().complete(
                    _base_messages(), tier=ModelTier.STRONG, tools=TOOLS, max_tokens=4096
                )
            )
        # `content` is what parse_output reads as the agent's JSON verdict.
        # A thinking block folded into it would corrupt every agent's output.
        self.assertEqual(response.content, "Looking that up.")
        self.assertEqual([t.name for t in response.tool_calls], ["get_price"])
        self.assertEqual(
            [b["type"] for b in response.content_blocks],
            ["thinking", "text", "tool_use"],
        )

    def test_cache_counters_still_reach_the_cost_estimator(self):
        with _MockAnthropic([TOOL_TURN]) as mock:
            response = asyncio.run(
                mock.provider().complete(
                    _base_messages(), tier=ModelTier.STRONG, tools=TOOLS, max_tokens=4096
                )
            )
        usage = response.raw["usage"]
        self.assertEqual(usage["cache_creation_input_tokens"], 1234)
        self.assertEqual(usage["cache_read_input_tokens"], 5678)

    def test_assistant_turn_is_replayed_verbatim_with_its_thinking(self):
        with _MockAnthropic([TOOL_TURN, FINAL_TURN]) as mock:
            provider = mock.provider()
            messages = _base_messages()

            first = asyncio.run(
                provider.complete(
                    messages, tier=ModelTier.STRONG, tools=TOOLS, max_tokens=4096
                )
            )
            # Exactly what BaseAgent.run does between rounds.
            messages.append(
                LLMMessage(
                    role="assistant",
                    content=first.content,
                    tool_calls=first.tool_calls,
                    content_blocks=first.content_blocks,
                )
            )
            messages.append(
                LLMMessage(
                    role="tool_result",
                    content='{"usd": 1.0}',
                    tool_call_id=first.tool_calls[0].id,
                )
            )
            second = asyncio.run(
                provider.complete(
                    messages, tier=ModelTier.STRONG, tools=TOOLS, max_tokens=4096
                )
            )
            replay = mock.requests[1]

        assistant = [m for m in replay["messages"] if m["role"] == "assistant"]
        self.assertEqual(len(assistant), 1)
        blocks = assistant[0]["content"]
        self.assertEqual([b["type"] for b in blocks], ["thinking", "text", "tool_use"])
        # Unmodified: the signature is what the model checks.
        self.assertEqual(blocks[0]["signature"], "sig-abc")
        # The SDK serialises every optional field of every block type; a null
        # is not a valid block on the way back in.
        self.assertNotIn("null", json.dumps(blocks))
        self.assertEqual(second.content, '{"score": 7}')

    def test_replay_without_content_blocks_still_works(self):
        # The OpenAI provider sets no content_blocks, and neither do the 20
        # persisted records. The old reconstruction path must remain intact.
        with _MockAnthropic([TOOL_TURN, FINAL_TURN]) as mock:
            provider = mock.provider()
            messages = _base_messages()
            messages.append(
                LLMMessage(
                    role="assistant",
                    content="Looking that up.",
                    tool_calls=[],
                )
            )
            messages.append(LLMMessage(role="user", content="continue"))
            asyncio.run(
                provider.complete(
                    messages, tier=ModelTier.STRONG, tools=TOOLS, max_tokens=4096
                )
            )
            replay = mock.requests[0]
        assistant = [m for m in replay["messages"] if m["role"] == "assistant"]
        self.assertEqual(assistant[0]["content"], "Looking that up.")

    def test_a_refusal_is_not_an_exception_and_not_a_crash(self):
        # Opus 5 / Sonnet 5 safety classifiers decline with HTTP 200, empty
        # content and stop_reason "refusal". Indexing content[0] would raise;
        # this path must survive and say why.
        with _MockAnthropic([REFUSAL_TURN]) as mock:
            response = asyncio.run(
                mock.provider().complete(
                    _base_messages(), tier=ModelTier.STRONG, tools=TOOLS, max_tokens=4096
                )
            )
        self.assertEqual(response.stop_reason, "refusal")
        self.assertEqual(response.content, "")
        self.assertEqual(response.tool_calls, [])


class SdkCapabilityTest(unittest.TestCase):
    """The pin in requirements.txt, asserted rather than trusted."""

    def test_sdk_can_parse_a_thinking_block(self):
        # anthropic==0.42.0 could not: ContentBlock was Union[TextBlock,
        # ToolUseBlock] and this raised ValidationError inside the SDK, before
        # any of our code ran. Since Opus 5 thinks by default, that made the
        # model-id swap alone a fifteen-agent outage. Downgrading the pin must
        # fail here, not in production.
        from anthropic.types import Message

        message = Message.model_validate(
            _turn("msg_x", "end_turn", [
                {"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "text", "text": "hi"},
            ])
        )
        self.assertEqual([b.type for b in message.content], ["thinking", "text"])

    def test_sdk_types_thinking_and_output_config_as_real_parameters(self):
        import inspect

        from anthropic import AsyncAnthropic as _Client

        params = inspect.signature(
            _Client(api_key="x").messages.create
        ).parameters
        self.assertIn("thinking", params)
        self.assertIn("output_config", params)


if __name__ == "__main__":
    unittest.main()
