# archive/ — persona material that is not loaded at runtime

Nothing in this directory is read by `agent_personas.py`. The loader only opens
folders named in `AGENT_FOLDERS`, and only under `committee/`. Files here cost
nothing per API call.

## knowledge-agent/

Kept, not deleted. It was a persona for a retrieval agent that this system
never instantiated — there is no `KnowledgeAgent` class in `app/agents/`, and
no entry in `AGENT_FOLDERS` ever pointed at it. It described a context engine
that would fetch precedent for the other agents and hand them a ranked context
pack with an explicit retrieval-confidence rating.

That job exists in the running system, but as plumbing rather than as an agent:

- `search_notes` and `read_note` are in `BaseAgent._base_tools`, so **every**
  agent retrieves for itself, and step 1 of every system prompt tells it to.
- `knowledge_context` is passed into agent context by the orchestrator.
- `app/knowledge/` holds the pgvector layer; `semantic_search` is reachable
  over HTTP but no agent calls it.

Moved here on 24 Aug 2026 rather than deleted for two reasons. First, if the
retrieval layer is ever given an agent of its own — the obvious candidate being
a front-loaded pass that runs before the eight data agents and wires up
`semantic_search`, which nothing currently does — this is the specification for
it, and its distinction between "absence of evidence" and "evidence of absence"
is worth keeping. Second, `TOOLS.md` here holds the only written guidance in
the repository on reading X for founder judgement and governance sentiment;
that material overlaps `memory/trusted_accounts.md` and should be reconciled
into `fed-intelligence/` before this folder is discarded.

To revive it: move the folder back under `committee/`, add the agent class, and
add the `AGENT_FOLDERS` entry. Do not add the map entry alone — a mapped folder
with no class is loaded by nothing, which is the state this folder was in.
