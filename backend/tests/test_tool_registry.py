"""ToolRegistry: registration, lookup, and exception containment.

``execute`` is the only thing standing between a misbehaving tool and the agent
loop in base.py. Its contract is "always return a dict, never raise".
"""
from __future__ import annotations

import asyncio
import json
import unittest

from app.llm import ToolDefinition
from app.tools.registry import ToolRegistry


def definition(name: str) -> ToolDefinition:
    return ToolDefinition(name=name, description="d", parameters={"type": "object", "properties": {}})


class ContainmentTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = ToolRegistry()

    async def test_unknown_tool_returns_an_error_dict(self):
        self.assertEqual(await self.registry.execute("nope", {}), {"error": "Unknown tool: nope"})

    async def test_ordinary_exceptions_are_contained(self):
        async def boom(_args):
            raise ValueError("kaboom")

        self.registry.register(definition("boom"), boom)
        result = await self.registry.execute("boom", {})
        self.assertIn("error", result)
        self.assertIn("kaboom", result["error"])

    async def test_every_exception_type_is_contained(self):
        """No exception class escapes -- including ones raised from deep in httpx."""

        class Weird(Exception):
            pass

        for exc in (KeyError("k"), TypeError("t"), RuntimeError("r"), ZeroDivisionError(), Weird("w")):

            async def raiser(_args, _exc=exc):
                raise _exc

            self.registry.register(definition("raiser"), raiser)
            result = await self.registry.execute("raiser", {})
            self.assertIsInstance(result, dict, repr(exc))
            self.assertIn("error", result)

    async def test_cancellation_still_propagates(self):
        """Deliberate and correct: CancelledError is a BaseException in 3.12.

        ``except Exception`` does not swallow it, so asyncio.gather can still
        cancel a stalled agent. A well-meaning "harden the registry" change to
        ``except BaseException`` would break cancellation -- this test exists to
        stop that.
        """

        async def cancelled(_args):
            raise asyncio.CancelledError()

        self.registry.register(definition("cancelled"), cancelled)
        with self.assertRaises(asyncio.CancelledError):
            await self.registry.execute("cancelled", {})

    async def test_a_hanging_tool_is_cancellable(self):
        """A tool with no timeout of its own must still be interruptible."""

        async def hangs(_args):
            await asyncio.sleep(3600)
            return {}

        self.registry.register(definition("hangs"), hangs)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.registry.execute("hangs", {}), timeout=0.05)


class ResultContractTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = ToolRegistry()

    @unittest.expectedFailure
    async def test_QA_024_a_tool_returning_none_must_not_reach_the_model(self):
        """QA-024 (MED): execute is typed -> ToolResult but does not enforce it.

        A tool with a missing return statement -- an easy edit slip -- yields
        None. base.py then does json.dumps(None, default=str) and feeds the agent
        the literal string "null" as a tool result, with no error anywhere. The
        agent has no way to know the call failed and will reason over the absence.
        """

        async def forgot_to_return(_args):
            return None

        self.registry.register(definition("forgot"), forgot_to_return)
        result = await self.registry.execute("forgot", {})
        self.assertIsInstance(result, dict)

    @unittest.expectedFailure
    async def test_QA_025_unserialisable_results_must_be_contained_here(self):
        """QA-025 (MED): containment ends one line too early.

        execute returns the object untouched; the serialisation that actually
        fails happens in base.py::run at
        ``json.dumps(result, default=str)`` -- outside the registry's try. That
        raises, is caught by run's blanket handler, and the *entire agent* is
        recorded as errored because one tool returned an odd object. The registry
        should have degraded that to a single failed tool call.

        ``default=str`` does not help: it is consulted for values, not for dict
        keys, and never for circular references.
        """

        async def circular(_args):
            payload = {}
            payload["self"] = payload
            return payload

        async def tuple_keys(_args):
            return {(1, 2): "x"}

        for name, func in (("circular", circular), ("tuple_keys", tuple_keys)):
            self.registry.register(definition(name), func)
            result = await self.registry.execute(name, {})
            json.dumps(result, default=str)

    @unittest.expectedFailure
    async def test_QA_027_registering_a_non_coroutine_must_fail_at_registration(self):
        """QA-027 (LOW): register() accepts anything and the failure surfaces later.

        A plain def tool registers cleanly and only breaks mid-evaluation, as
        {"error": "Tool execution failed: object dict can't be used in 'await'
        expression"} -- a message that tells nobody the real cause.
        """

        def sync_tool(_args):
            return {"ok": 1}

        with self.assertRaises(TypeError):
            self.registry.register(definition("sync"), sync_tool)


class RegistrationTest(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()

    def test_register_then_lookup(self):
        async def tool(_args):
            return {}

        self.registry.register(definition("t"), tool)
        self.assertIs(self.registry.get_func("t"), tool)
        self.assertEqual(self.registry.get_definition("t").name, "t")
        self.assertEqual(self.registry.tool_names, ["t"])

    def test_unknown_lookups_return_none(self):
        self.assertIsNone(self.registry.get_func("nope"))
        self.assertIsNone(self.registry.get_definition("nope"))

    def test_get_definitions_with_no_argument_returns_everything(self):
        async def tool(_args):
            return {}

        self.registry.register(definition("a"), tool)
        self.registry.register(definition("b"), tool)
        self.assertEqual({d.name for d in self.registry.get_definitions()}, {"a", "b"})

    @unittest.expectedFailure
    def test_QA_026_requesting_an_unknown_tool_name_must_not_be_silent(self):
        """QA-026 (MED): get_definitions filters unknown names away without a word.

        Agents declare their capabilities as ``tool_names: list[str]``. A typo,
        or a tool that was renamed, means the agent quietly runs with one fewer
        tool for every evaluation from then on. There is no log line, no error,
        and the agent's own output will not mention a tool it was never offered.
        This is precisely the kind of silent capability loss handoff section 14.3
        warns about.
        """

        async def tool(_args):
            return {}

        self.registry.register(definition("get_price"), tool)
        with self.assertRaises(KeyError):
            self.registry.get_definitions(["get_price", "get_pirce"])

    @unittest.expectedFailure
    def test_QA_027_duplicate_registration_must_not_silently_overwrite(self):
        """QA-027 (LOW): the second register() wins with no warning.

        Two modules claiming the same tool name is a merge accident that would
        otherwise never be noticed -- the registry count stays plausible and the
        wrong implementation runs.
        """

        async def v1(_args):
            return {"v": 1}

        async def v2(_args):
            return {"v": 2}

        self.registry.register(definition("dup"), v1)
        with self.assertRaises(ValueError):
            self.registry.register(definition("dup"), v2)


class LiveRegistryTest(unittest.TestCase):
    """The live tool roster.

    CONTRACTS section 3.6 documents eleven. agent/retrieval added a twelfth,
    ``semantic_search_notes``, and wired it into BaseAgent._base_tools so every
    agent gets it -- this test is the thing that noticed, and CONTRACTS 3.6
    still says eleven.

    A thirteenth, ``get_category_peers``, was added on 29 Aug 2026 after the
    data-source review in docs/reviews/crypto-data-tooling-2026-08-29.md.
    CompetitiveIntel is scored on "market share within category" and every tool
    it had took a single protocol slug, so it had no peer set to compare
    against -- see the comment above the tool in tools/defillama.py.

    Pure registration check: constructs no clients and makes no calls.
    """

    CONTRACTED_ELEVEN = {
        "get_price",
        "get_token_info",
        "get_tvl",
        "get_protocol_fees",
        "get_klines",
        "get_orderbook_depth",
        "compute_technical_levels",
        "web_search",
        "search_twitter",
        "search_notes",
        "read_note",
    }
    EXPECTED = CONTRACTED_ELEVEN | {"semantic_search_notes", "get_category_peers"}

    def test_exactly_the_registered_roster_is_the_expected_one(self):
        from app.tools import get_tool_registry

        self.assertEqual(set(get_tool_registry().tool_names), self.EXPECTED)

    def test_the_original_eleven_all_survived_the_retrieval_merge(self):
        from app.tools import get_tool_registry

        self.assertTrue(self.CONTRACTED_ELEVEN <= set(get_tool_registry().tool_names))

    def test_semantic_search_is_offered_to_every_agent(self):
        """Handoff section 5: semantic_search existed but no agent could call it.

        The fix is that it is in BaseAgent._base_tools, not merely registered.
        Testing reachability, not the presence of the registration line.
        """
        from app.agents.base import BaseAgent
        from app.tools import get_tool_registry

        self.assertIn("semantic_search_notes", BaseAgent._base_tools)

        class BareAgent(BaseAgent):
            name = "bare"
            tool_names: list[str] = []

        offered = {d.name for d in BareAgent().get_tools()}
        self.assertIn("semantic_search_notes", offered)
        self.assertIn("search_notes", offered)

    def test_tool_registry_satisfies_the_registrar_protocol(self):
        """ADR 0001: tool modules depend on the Protocol, not the concrete class.

        A structural check, so a signature drift on ToolRegistry.register that
        silently breaks every tool module's type contract shows up here.
        """
        import inspect

        from app.tools.contracts import ToolRegistrar

        # ToolRegistrar is not @runtime_checkable, so isinstance() is unavailable.
        # Compare the signature the tool modules are typed against with the one
        # they will actually call.
        self.assertEqual(
            inspect.signature(ToolRegistry.register),
            inspect.signature(ToolRegistrar.register),
        )

    def test_tool_argument_types_come_from_utils_not_the_tool_layer(self):
        """ADR 0001: ToolArguments/ToolResult are not tool-layer concepts."""
        from app.tools import contracts
        from app.utils import types

        self.assertIs(contracts.ToolArguments, types.ToolArguments)
        self.assertIs(contracts.ToolResult, types.ToolResult)

    def test_unbuilt_integrations_are_not_registered(self):
        """CONTRACTS section 3.6: these are named in older docs and do not exist."""
        from app.tools import get_tool_registry

        names = set(get_tool_registry().tool_names)
        for absent in ("etherscan", "dune", "github", "snapshot", "tally", "safe", "token_terminal"):
            self.assertFalse(any(absent in n for n in names), absent)

    def test_every_definition_has_a_json_schema_object(self):
        from app.tools import get_tool_registry

        for defn in get_tool_registry().get_definitions():
            self.assertEqual(defn.parameters.get("type"), "object", defn.name)
            self.assertIn("properties", defn.parameters, defn.name)
            self.assertTrue(defn.description.strip(), defn.name)


if __name__ == "__main__":
    unittest.main()
