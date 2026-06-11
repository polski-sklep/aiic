from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.tools import ToolArguments, get_tool_registry

router = APIRouter(prefix="/api/tools", tags=["tools"])


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
    """Execute a specific tool with given arguments."""
    registry = get_tool_registry()
    definition = registry.get_definition(tool_name)
    if not definition:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    result = await registry.execute(tool_name, req.arguments)
    error = result.get("error")
    if error is not None:
        raise HTTPException(status_code=400, detail=str(error))

    return result
