"""Shared contracts for the tool layer.

This module exists to invert the dependency between `app.tools.registry` and
the individual tool modules. The registry imports every tool module in order to
register it; if the tool modules in turn imported the registry for their types,
the two would be mutually dependent and only the registry's import being
function-local would keep it from being an import-time cycle.

Nothing here imports a tool module or the registry, so it is safe for both
sides to depend on it.

`ToolArguments` and `ToolResult` live in `app.utils.types` and are imported
from there directly; they are not tool-layer concepts.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from app.llm import ToolDefinition
from app.utils.types import ToolArguments, ToolResult


ToolFunc = Callable[[ToolArguments], Awaitable[ToolResult]]


class ToolRegistrar(Protocol):
    """The only part of the registry a tool module is allowed to depend on.

    `ToolRegistry` satisfies this structurally, so tool modules annotate their
    `register(registry)` parameter with this Protocol instead of importing the
    concrete class.
    """

    def register(self, definition: ToolDefinition, func: ToolFunc) -> None:
        """Register a tool definition and its async implementation."""
        ...
