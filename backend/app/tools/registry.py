from __future__ import annotations

import logging

from app.llm import ToolDefinition
from app.tools.contracts import ToolFunc
from app.utils.types import ToolArguments, ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Central registry for all tools available to agents."""

    def __init__(self):
        self._tools: dict[str, ToolFunc] = {}
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition, func: ToolFunc) -> None:
        self._tools[definition.name] = func
        self._definitions[definition.name] = definition
        logger.debug(f"Registered tool: {definition.name}")

    def get_func(self, name: str) -> ToolFunc | None:
        return self._tools.get(name)

    def get_definition(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def get_definitions(self, names: list[str] | None = None) -> list[ToolDefinition]:
        """Definitions for ``names``, or every registered tool when None.

        An unregistered name is skipped rather than raising — an agent must not
        fail to run because one of its tools is unavailable. But it is logged
        loudly: a typo in an agent's ``tool_names`` silently costs that agent a
        capability for the lifetime of the process, with nothing anywhere
        reporting it (QA-026).
        """
        if names is None:
            return list(self._definitions.values())

        unknown = [n for n in names if n not in self._definitions]
        if unknown:
            logger.warning(
                "Requested tool(s) not registered and will be unavailable to the "
                "caller: %s. Registered tools: %s",
                ", ".join(sorted(unknown)),
                ", ".join(sorted(self._definitions)),
            )
        return [self._definitions[n] for n in names if n in self._definitions]

    async def execute(self, name: str, arguments: ToolArguments) -> ToolResult:
        func = self._tools.get(name)
        if not func:
            return {"error": f"Unknown tool: {name}"}
        try:
            return await func(arguments)
        except Exception as e:
            logger.error(f"Tool {name} failed: {e}")
            return {"error": f"Tool execution failed: {str(e)}"}

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())


def validate_agent_tool_names() -> list[str]:
    """Check that every agent's declared ``tool_names`` actually resolves.

    ``tool_names`` is static class data, so a typo or a renamed tool is knowable
    at startup rather than discovered mid-evaluation. Raising from
    ``get_definitions`` would kill an agent part-way through a paid run for what
    is really a programming error; returning silently loses it entirely
    (QA-026). Checking here gets both: deterministic, loud, and before any
    money is spent.

    Returns a list of human-readable problems, empty when everything resolves.
    """
    from app.agents.orchestrator import Orchestrator

    registry = get_tool_registry()
    known = set(registry.tool_names)
    orchestrator = Orchestrator()

    agents = [
        *orchestrator.data_agents,
        orchestrator.maturation,
        orchestrator.devils_advocate,
        orchestrator.risk_officer,
        orchestrator.portfolio_manager,
        orchestrator.report_writer,
        orchestrator.chair,
        orchestrator.ray,
    ]

    problems: list[str] = []
    for agent in agents:
        declared = set(agent.tool_names) | set(agent._base_tools)
        missing = sorted(declared - known)
        if missing:
            problems.append(
                f"{agent.name} declares unregistered tool(s): {', '.join(missing)}"
            )
    return problems


_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_all_tools(_registry)
    return _registry


def _register_all_tools(registry: ToolRegistry) -> None:
    """Import and register all tool modules."""
    from app.tools.binance import register as register_binance
    from app.tools.coingecko import register as register_coingecko
    from app.tools.defillama import register as register_defillama
    from app.tools.twitter import register as register_twitter
    from app.tools.web_search import register as register_web_search
    from app.tools.notion_tools import register as register_notion
    from app.tools.semantic import register as register_semantic

    register_binance(registry)
    register_coingecko(registry)
    register_defillama(registry)
    register_web_search(registry)
    register_notion(registry)
    register_twitter(registry)
    register_semantic(registry)

    logger.info(f"Registered {len(registry.tool_names)} tools: {registry.tool_names}")
