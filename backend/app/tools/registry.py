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
        if names is None:
            return list(self._definitions.values())
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

    register_binance(registry)
    register_coingecko(registry)
    register_defillama(registry)
    register_web_search(registry)
    register_notion(registry)
    register_twitter(registry)

    logger.info(f"Registered {len(registry.tool_names)} tools: {registry.tool_names}")
