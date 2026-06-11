"""
Manual end-to-end smoke harness for the current committee pipeline.

Run with:
    cd committee-orchestrator/backend
    ANTHROPIC_API_KEY=sk-... python -m tests.test_tokenomics_e2e

Or via the API:
    curl -X POST http://localhost:8100/api/evaluate \
      -H "Content-Type: application/json" \
      -d '{"project_name": "Chainlink", "coingecko_id": "chainlink", "category": "Infrastructure"}'
"""
import asyncio
import json
import os
import sys

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run_tool_smoke():
    """Test tools work independently."""
    from app.tools import get_tool_registry

    registry = get_tool_registry()
    print(f"\n=== Registered tools: {registry.tool_names} ===\n")

    # Test get_price
    print("--- get_price(chainlink) ---")
    result = await registry.execute("get_price", {"coin_id": "chainlink"})
    print(json.dumps(result, indent=2, default=str))

    # Test get_token_info
    print("\n--- get_token_info(chainlink) ---")
    result = await registry.execute("get_token_info", {"coin_id": "chainlink"})
    print(json.dumps(result, indent=2, default=str))

    # Test get_tvl
    print("\n--- get_tvl(aave) ---")
    result = await registry.execute("get_tvl", {"protocol": "aave"})
    print(json.dumps(result, indent=2, default=str))

    print("\n=== All tool tests passed ===\n")


async def run_tokenomics_agent():
    """Test the full TokenomicsAnalyst agent with LLM."""
    from app.agents.tokenomics import TokenomicsAnalyst

    agent = TokenomicsAnalyst()
    print(f"\n=== Running {agent.name} on Chainlink ===\n")

    result = await agent.run({
        "project_name": "Chainlink",
        "project_info": {
            "ticker": "LINK",
            "coingecko_id": "chainlink",
            "category": "Infrastructure",
            "chain": "Ethereum",
        },
    })

    print(f"Score: {result.score}")
    print(f"Model: {result.model_used}")
    print(f"Tokens: {result.tokens_input} in / {result.tokens_output} out")
    print(f"Latency: {result.latency_ms}ms")
    print(f"Tools used: {result.tool_calls_made}")
    print(f"Error: {result.error}")
    print(f"\nOutput:\n{json.dumps(result.output, indent=2, default=str)}")

    print(f"\n=== {agent.name} test complete ===")


async def run_orchestrator():
    """Test the full orchestrator pipeline."""
    from app.agents.orchestrator import Orchestrator

    orchestrator = Orchestrator()

    async def status_callback(event_type: str, agent_name: str, data: dict):
        print(f"  [{event_type}] {agent_name}: {data}")

    print("\n=== Running Orchestrator on Chainlink ===\n")

    result = await orchestrator.evaluate(
        project_name="Chainlink",
        project_info={
            "ticker": "LINK",
            "coingecko_id": "chainlink",
            "category": "Infrastructure",
        },
        on_status=status_callback,
    )

    print(f"\nOverall score: {result.get('overall_score')}")
    print(f"Recommendation: {result.get('recommendation')}")
    print(f"\nFull result:\n{json.dumps(result, indent=2, default=str)}")


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode in ("tools", "all"):
        await run_tool_smoke()

    if mode in ("agent", "all"):
        await run_tokenomics_agent()

    if mode in ("orchestrator", "all"):
        await run_orchestrator()


if __name__ == "__main__":
    asyncio.run(main())
