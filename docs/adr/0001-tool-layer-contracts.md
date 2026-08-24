# ADR 0001 — Tool-layer contracts and the registry dependency inversion

- **Status:** Accepted
- **Date:** 2026-08-24
- **Branch:** `agent/architecture`
- **Commit:** `3eff02f`
- **Supersedes / relates to:** `PROJECT_DECISIONS.md` D3, handoff §0 pass 4,
  `docs/triage-tree4.md` pass 4

---

## Context

The tool layer had a mutual dependency between the registry and the modules it
registers.

`tools/registry.py::_register_all_tools` imports every tool module in order to
call its `register(registry)` function:

```python
def _register_all_tools(registry: ToolRegistry) -> None:
    from app.tools.binance import register as register_binance
    from app.tools.coingecko import register as register_coingecko
    ...
```

Each tool module imported back, at module level:

```python
from app.tools.registry import ToolArguments

if TYPE_CHECKING:
    from app.tools.registry import ToolRegistry
```

`ToolArguments` is not a registry concept — it is
`app.utils.types.ToolArguments`, re-exported by `registry.py` as a convenience.
Six modules took a hard runtime dependency on the registry to obtain a type
alias that lives two packages away.

This did not break, for one reason only: the registry's imports are inside a
function body, so they run on first `get_tool_registry()` call rather than at
import time. The cycle was real; it was deferred, not absent. Hoisting those six
imports to the top of `registry.py` — an edit that looks like tidying — turns
`import app.tools.binance` into an `ImportError`. That is a trap sitting under a
file that anyone adding a twelfth tool will open.

An AST scan of the 49 modules under `backend/app` confirmed the shape before any
change: zero cycles among module-level imports, six cycles once function-local
imports are counted, all six between `tools.registry` and a tool module.

## Decision

Introduce `backend/app/tools/contracts.py`, holding the tool-layer types that
both sides of the boundary need, and depending on neither side.

```python
ToolFunc = Callable[[ToolArguments], Awaitable[ToolResult]]

class ToolRegistrar(Protocol):
    def register(self, definition: ToolDefinition, func: ToolFunc) -> None: ...
```

Then:

1. Tool modules import `ToolArguments` from `app.utils.types`, where it is
   defined, instead of from the registry.
2. Tool modules annotate `register()` with the `ToolRegistrar` Protocol instead
   of importing the concrete `ToolRegistry`. `ToolRegistry` satisfies it
   structurally; no registration, no base class, no runtime cost. The
   `TYPE_CHECKING` block in all six modules disappears with it.
3. `registry.py` takes `ToolFunc` from `contracts.py` rather than defining it.
4. `tools/__init__.py` keeps all five previous re-exports —
   `ToolArguments`, `ToolFunc`, `ToolRegistry`, `ToolResult`,
   `get_tool_registry` — sourced from their new homes, and adds `ToolRegistrar`.
   `app/main.py`, `agents/base.py`, `agents/orchestrator.py` and
   `api/tools.py` import through this package and were not touched.

The dependency now runs one way: `tool modules → contracts ← registry`. The
registry's function-local imports stay function-local, because lazy loading of
plugins is the right shape for a registry — but they are now a choice, not a
load-bearing workaround.

### What was deliberately not ported

Tree 4's `contracts.py` also re-exports a `ToolError` alias from
`app.utils.types`. This base has no such type: each of the six tool modules
declares its own local `ToolError` TypedDict, and `app/utils/types.py` does not
define one. Inventing it here would have been a second, unrelated change riding
in on a refactor. It is triaged separately as pass 2a in
`docs/triage-tree4.md`, and `contracts.py` is the obvious place to re-export it
from when that lands.

This is a reapplication of tree 4's intent onto the current base, not a copy of
tree 4's file — per `PROJECT_DECISIONS.md` D3 and handoff §14.6. Tree 4 predates
`tools/binance.py` entirely; a file-level copy would have deleted three live
tools.

## Consequences

**Good.**

- Adding a tool no longer requires importing the registry. The template is
  `from app.tools.contracts import ToolRegistrar` and a `register(registry:
  ToolRegistrar)` function — nothing else.
- The latent cycle is gone, so the "tidy up the imports" edit is now safe.
- The tool modules are independently importable and independently testable: a
  test can pass any object with a `register` method and assert on what the
  module tried to register, without constructing a `ToolRegistry` or triggering
  the registration of the other ten tools.
- `CONTRACTS.md` §3.6's registration protocol is unchanged and is now expressed
  in the type system rather than in prose.

**Costs.**

- One more file in `app/tools/`. Small, and it is the file a new tool author
  should read first.
- `ToolRegistrar` is not `@runtime_checkable`, so `isinstance` checks against it
  raise `TypeError`. This is intentional — structural conformance is a static
  concern and a runtime check would be a false comfort — but it will surprise
  someone eventually.
- Tool modules now import from two places (`app.utils.types` for the JSON
  aliases, `app.tools.contracts` for the registrar) where they previously
  imported from one. That is the honest picture of the dependency; the previous
  single import was hiding it.

**Neutral.**

- No behaviour change. The same eleven tools register, in the same order, with
  the same definitions.

## Verification

Run from `/Users/Jacob/Projects/aiic-worktrees/architecture` with a
worktree-local compose project (`-p aiic-arch`), per `CONTRACTS.md` §5 — host
Python on the Mac is 3.14.5 and proves nothing about the pinned dependencies.

AST import-graph check, before and after:

```
BEFORE (800b6e1)                       AFTER (3eff02f)
modules parsed: 49                     modules parsed: 50

[A] import-time graph                  [A] import-time graph
  0 cycles                               0 cycles

[B] static graph                       [B] static graph
  app.tools.registry ->                  0 cycles
    app.tools.binance -> app.tools.registry
  ... and 5 more, one per tool module

RESULT: PASS on [A], but 6 latent      RESULT: PASS - 0 cycles in both graphs
        cycle(s) in [B]
```

Container (Python 3.12.13):

```
IMPORT app.main OK
IMPORT app.tools re-exports OK -> ['ToolArguments', 'ToolFunc', 'ToolRegistrar',
                                   'ToolRegistry', 'ToolResult', 'get_tool_registry']
tools registered: 11 ['compute_technical_levels', 'get_klines', 'get_orderbook_depth',
                      'get_price', 'get_protocol_fees', 'get_token_info', 'get_tvl',
                      'read_note', 'search_notes', 'search_twitter', 'web_search']
app.tools.binance alone OK
app.tools.registry after OK
compileall OK
```

The eleven names match `CONTRACTS.md` §3.6 exactly.

The checker builds two graphs. Graph A counts only imports that execute at
module import time; a cycle there is an `ImportError` waiting to happen. Graph B
additionally counts function-local and `TYPE_CHECKING`-guarded imports; a cycle
there is latent — it does not break today and breaks the moment someone hoists
the import. Implicit parent-package edges (`from app.tools.contracts import X`
inside `app.tools.binance` also initialising `app.tools`) are excluded when the
target is an ancestor of the importer, since a submodule reaching its own
already-initialised package is how Python packages work; explicit
`from app.tools import X` edges are **not** excluded, because those are real.
