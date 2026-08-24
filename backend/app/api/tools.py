from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.tools import ToolArguments, get_tool_registry

router = APIRouter(prefix="/api/tools", tags=["tools"])


# --- Execution gate -------------------------------------------------------
#
# `POST /api/tools/{tool_name}` is arbitrary tool execution over HTTP with no
# authentication, on a service that has none anywhere (SEC-03). It has been
# returning 500 for weeks because of the `ToolArguments` forward-reference bug,
# which the security review noted "accidentally shrinks the SEC-03 attack
# surface". Fixing that bug in app/utils/types.py — which had to be fixed,
# because the same cause made `/openapi.json` unusable — would otherwise switch
# this endpoint back on as a side effect. It is not switched back on.
#
# What it would expose if enabled today: all twelve registered tools are
# read-only (no Notion write function is registered), so this is not remote
# code execution. It is an unauthenticated ability to spend Brave, CoinGecko,
# X and embedding quota, to use the host as a web/Twitter search proxy, and to
# read institutional Notion notes through `read_note` / `search_notes`. Per
# SEC-02 the only control keeping that off the internet is an external Hetzner
# firewall with no host-level fallback, which the reviewer could not inspect.
#
# So: schema fixed, endpoint deliberately left disabled. Re-enabling it is a
# decision to take with SEC-03, not a side effect of a schema repair. Listing
# (`GET /api/tools`) is unaffected and still works.
#
# To enable properly rather than by flipping this constant, add to
# `config.py::Settings` and `.env.example` (CONTRACTS §3.5) —
#     tools_execute_enabled: bool = False
# — and read it through `get_settings()` here. Both files belong to other
# owners, so this branch does not add the setting itself.
TOOL_EXECUTION_OVER_HTTP_ENABLED = False


class ToolExecuteRequest(BaseModel):
    arguments: ToolArguments = Field(default_factory=dict)


@router.get("")
async def list_tools():
    """List all available tools."""
    registry = get_tool_registry()
    return {
        "tools": [
            {
                "name": d.name,
                "description": d.description,
                "parameters": d.parameters,
            }
            for d in registry.get_definitions()
        ]
    }


@router.post("/{tool_name}")
async def execute_tool(tool_name: str, req: ToolExecuteRequest):
    """Execute a specific tool with given arguments.

    **Disabled by default.** See `TOOL_EXECUTION_OVER_HTTP_ENABLED` above: this
    is unauthenticated arbitrary tool execution and the service has no auth
    (SEC-03). Returns 403 until it is enabled deliberately.
    """
    if not TOOL_EXECUTION_OVER_HTTP_ENABLED:
        raise HTTPException(
            status_code=403,
            detail=(
                "Tool execution over HTTP is disabled. This endpoint runs "
                "arbitrary registered tools and the API has no authentication; "
                "see docs/reviews/security-review.md SEC-03."
            ),
        )

    registry = get_tool_registry()
    definition = registry.get_definition(tool_name)
    if not definition:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    result = await registry.execute(tool_name, req.arguments)
    error = result.get("error")
    if error is not None:
        raise HTTPException(status_code=400, detail=str(error))

    return result
