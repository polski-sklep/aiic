from __future__ import annotations
from fastapi import APIRouter
from app.memory import load_all, reload_memory, load_mandates, load_risk_policy, load_thesis

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("")
async def get_memory():
    """View all institutional memory files."""
    return load_all()


@router.get("/mandates")
async def get_mandates():
    return {"content": load_mandates()}


@router.get("/risk_policy")
async def get_risk_policy():
    return {"content": load_risk_policy()}


@router.get("/thesis")
async def get_thesis():
    return {"content": load_thesis()}


@router.post("/reload")
async def reload():
    """Reload memory files from disk. Call after editing files via SSH."""
    reload_memory()
    memory = load_all()
    return {
        "status": "reloaded",
        "files": {k: f"{len(v)} chars" for k, v in memory.items()},
    }

@router.get("/personas")
async def get_personas():
    from app.memory.agent_personas import list_personas
    return list_personas()


@router.get("/trusted_accounts")
async def get_trusted_accounts():
    import os
    ta_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "memory", "trusted_accounts.md")
    if os.path.exists(ta_path):
        with open(ta_path) as f:
            return {"content": f.read()}
    return {"content": "not configured"}
