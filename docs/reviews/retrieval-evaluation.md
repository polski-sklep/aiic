# Retrieval evaluation — keyword vs semantic on the live corpus

**Branch:** `agent/retrieval`
**Measured:** 24 August 2026, against the live VPS (`100.95.239.105`), read-only.
**Question (handoff §5.3):** at tens of Notion pages, does semantic search
actually beat keyword?

**Verdict: partly — and the split is sharp enough to act on.** Semantic wins
decisively on paraphrased *concept* queries. Keyword wins on *named projects*
and is the only retriever that can reach the richest documents at all. They are
complementary, not competing. The tool ships **beside** `search_notes`, and
`search_notes` remains the instructed first call.

Confidence: **high** on the ranking comparison and on the corpus-coverage
finding (both directly measured); **medium** on how much the concept-query win
is worth in production, because no agent transcript records what agents actually
searched for — the query set is reconstructed from the system prompt, not
observed.

---

## 1. Corpus characterisation

```
$ docker exec committee-postgres psql -U committee -d committee -c \
  "SELECT source_type, count(*) AS chunks, count(embedding) AS embedded,
          avg(length(content))::int AS avg_len, min(length(content)) AS min_len,
          max(length(content)) AS max_len FROM knowledge_chunks GROUP BY source_type;"

 source_type | chunks | embedded | avg_len | min_len | max_len
-------------+--------+----------+---------+---------+---------
 learning    |     62 |       62 |     224 |     128 |     361
(1 row)
```

**The single most important fact in this document: all 62 chunks are
`source_type = 'learning'`.**

- Zero `project_evaluation` chunks. Zero `transcript` chunks.
- The `sync` endpoint's `db_map` has three entries; only the Learnings one has
  ever produced rows.
- Every chunk was written on **2026-06-24** and nothing since — a two-month-old
  snapshot, not a live index.
- Chunks are 128–361 chars, far under the 1000-char `chunk_text` target: each
  learning page is short enough to be a single chunk. There is no
  multi-chunk document in the index, so chunk-overlap behaviour is untested.
- Content shape is `[Project] [agent] <one risk statement>`, and `full_text` in
  `sync_database_to_pgvector` prefixes the title, so the title text is embedded
  twice per chunk.

Project spread (62 chunks): Chainlink 27, Plasma 10, Aave 5, Ethena 5,
GEODNET 5, Morpho 5, Pendle 5. Note **Chainlink dominates 44% of the index** but
is not one of the six projects in the calibration ledger (`CONTRACTS.md` §2.6),
so the index is skewed toward a project the committee is not tracking.

### 1.1 The corpora are not the same — and this is decisive

| | `search_notes` (keyword) | `semantic_search` (pgvector) |
|---|---|---|
| Backend | Notion API, live | Postgres `knowledge_chunks` |
| Reach | **all** shared pages — Learnings, Projects, Transcripts | Learnings snapshot only |
| Project evaluation pages (~9 KB of per-agent reasoning each) | **yes** | **no** |
| IC call transcripts | **yes** | **no** |
| Freshness | live | frozen at 2026-06-24 |
| Result cap | 5 (hardcoded in `search_notes`) | caller's `limit` |

`CONTRACTS.md` §2.5 establishes that for the 18 June cohort the committee's
reasoning **survives only on the Notion project pages**. Those pages are not in
the semantic index. Any proposal to replace `search_notes` with semantic search
would therefore delete the system's only access to its own reasoning history.
That is why this evaluation never treats replacement as an option.

---

## 2. Method

### 2.1 Query set

`BaseAgent.get_system_prompt` (`backend/app/agents/base.py:99`) instructs every
one of the fifteen agents:

> "FIRST, use search_notes to check if there are prior evaluations, IC call
> transcripts, or learnings about this project or related projects."

So realistic agent queries are project names and domain concepts, not invented
phrases. Fourteen queries in four categories:

| Category | Rationale |
|---|---|
| **A — exact name, in corpus** | The literal instructed first call. Keyword should be at least as good. |
| **B — exact name, NOT in corpus** | The common case: a new project. The correct answer is *nothing*. Tests false positives. |
| **C — related-project** | "or related projects" from the prompt. |
| **D — concept / paraphrase** | Where no page contains the literal words. The case semantic exists for. |

### 2.2 Retrievers

Both run in-process inside `committee-backend`, same query strings, top-5.

- **Keyword:** `search_notion(q, database_id=None, limit=5)` — byte-for-byte what
  `search_notes` does at its default `database="all"`.
- **Semantic:** `generate_embedding` + the cosine query from
  `knowledge/__init__.py`, with `threshold=0.0` so raw similarities are visible.

The deployed image is at `8432cf4`, which predates the
`CAST(:embedding AS vector)` fix (`5d3c033`), so the corrected SQL was supplied
inline rather than deploying. No writes, no restarts, no sync.

### 2.3 Relevance criteria (stated up front, judged by hand)

A result is **relevant** if an agent reading it would gain applicable prior
context on the thing asked about:

- **A/B (named project):** the result concerns that project.
- **C/D (concept):** the result substantively addresses that risk or mechanism.
  Merely sharing a word ("risk", "governance") without addressing the concept is
  **not** relevant.
- **B specifically:** returning *any* result is a **false positive**. The corpus
  has never seen the project; the correct output is an empty list.

Metrics: **P@5** (relevant of five returned) and **RR** (reciprocal rank of the
first relevant result). Denominator is fixed at 5 throughout so the two are
comparable even where keyword returned fewer than five.

---

## 3. Head-to-head results

P@5 / RR, keyword vs semantic. **Bold** = winner.

| # | Cat | Query | Keyword P@5 | Sem P@5 | KW RR | Sem RR | Winner |
|---|-----|-------|------------|---------|-------|--------|--------|
| 1 | A | Ethena | **1.00** | 1.00 | 1.0 | 1.0 | **Keyword** — ties on learnings but its #1 hit is the *project page* |
| 2 | A | Morpho | **1.00** | 1.00 | 1.0 | 1.0 | **Keyword** — same, reaches the project page |
| 3 | A | GEODNET | **1.00** | 0.80 | 1.0 | 1.0 | **Keyword** |
| 4 | B | Lido | **0.00 (0 results)** | 0.00 (5 false positives) | — | — | **Keyword** |
| 5 | B | EigenLayer | **0.00 (0 results)** | 0.00 (5 false positives) | — | — | **Keyword** |
| 6 | C | lending protocols similar to Aave | 0.40 | **0.60** | 0.25 | **1.0** | **Semantic** |
| 7 | C | yield tokenization protocols | 0.20 | 0.20 | 1.0 | 1.0 | tie |
| 8 | D | delta-neutral stablecoin risk | 0.20 | **0.40** | 0.20 | **1.0** | **Semantic** |
| 9 | D | DePIN hardware incentive design | 0.20 | 0.20 | 1.0 | 1.0 | tie |
| 10 | D | governance capture by insiders | 0.80 | **1.00** | 1.0 | 1.0 | **Semantic** |
| 11 | D | risk of team tokens being dumped on the market | 0.40 | **0.60** | 0.50 | **1.0** | **Semantic** |
| 12 | D | protocol that never turns on revenue sharing to holders | 0.60 | **0.80** | 0.50 | 0.50 | **Semantic** |
| 13 | D | supply overhang from cliff vesting schedules | 0.40 | **1.00** | 1.0 | 1.0 | **Semantic** |
| 14 | D | oracle competition eroding market share | 0.60 | **0.80** | 0.50 | **1.0** | **Semantic** |

**Totals — 12 in-corpus queries (excluding B, where the target is an empty result):**

| | Keyword | Semantic |
|---|---|---|
| mean P@5 | 0.567 | **0.700** |
| mean RR | 0.746 | **0.958** |
| wins | 3 | **7** |
| ties | 2 | 2 |

**Category B (2 queries, projects the corpus has never seen):** keyword 2–0.

### 3.1 The cases that decide it

**Semantic finds what keyword cannot — query 11, zero literal overlap:**

```
QUERY: risk of team tokens being dumped on the market
  SEMANTIC
    0.5535  [Chainlink] [tokenomics_analyst] Perpetual selling pressure from 650M+ team-controlled token
    0.5285  [Plasma]    [tokenomics_analyst] Synchronized insider unlock wave (5B XPL, team + investors,
    0.5122  [GEODNET]   [tokenomics_analyst] Insider concentration: 50% of supply (500M GEOD) allocated
  KEYWORD
            [Pendle] veTokenomics model creates governance centralization risk
            [Pendle] Yield tokenization is a complex and niche product
            [Morpho] Regulatory risk: Activating a fee switch that distributes r
```

"dumped" appears nowhere in the corpus. Semantic retrieves *selling pressure*,
*unlock wave* and *insider concentration* — exactly the prior risk analysis an
agent should see. Keyword's top three are unrelated.

**Keyword's ranking is genuinely weak even on literal matches — query 8:**

```
QUERY: delta-neutral stablecoin risk
  SEMANTIC  0.5861  [Ethena] Delta-neutral strategy risk: USDe yield depends on perpetua...
  KEYWORD   does not return that chunk at all in its top 5
```

The phrase "Delta-neutral" is *in the title* of the chunk keyword failed to
return. Notion's relevance ranking, not just its matching, is the problem.

**Semantic hallucinates relevance on unseen projects — query 4:**

```
QUERY: Lido        (never evaluated; nothing in the corpus)
  KEYWORD   0 results                                    <- correct
  SEMANTIC  0.1915 [Chainlink] Centralization concerns around committee-based DON selection
            0.1699 [GEODNET]   No audit links found via DeFiLlama
            ...5 confident-looking rows, all irrelevant
```

Cosine search always returns *something*. Below §4 shows the threshold that
makes it abstain correctly.

### 3.2 Why keyword did better than expected

`search_notion` with `database_id=None` calls Notion's global `client.search()`,
which does index page bodies and applies its own relevance ranking — it is not
the title-substring match that the `databases.query` branch performs. It is a
real keyword engine. The honest finding is that keyword is a solid baseline that
semantic beats on paraphrase, not a straw man that semantic demolishes.

---

## 4. Defect: the 0.7 similarity threshold made the whole system return nothing

`semantic_search(threshold=0.7)` and `SearchRequest.threshold = 0.7`.
Across all 14 queries x 5 results = 70 retrieved rows, **the highest cosine
similarity observed anywhere was 0.6317.**

| Query | top-1 sim | ≥0.70 | ≥0.50 | ≥0.40 | ≥0.30 | ≥0.25 |
|---|---|---|---|---|---|---|
| Ethena | 0.3710 | **0** | 0 | 0 | 4 | 5 |
| Morpho | 0.3988 | **0** | 0 | 0 | 4 | 5 |
| GEODNET | 0.3542 | **0** | 0 | 0 | 1 | 2 |
| Lido *(should be 0)* | 0.1915 | **0** | 0 | 0 | **0** | **0** |
| EigenLayer *(should be 0)* | 0.2131 | **0** | 0 | 0 | **0** | **0** |
| lending protocols similar to Aave | 0.6317 | **0** | 2 | 3 | 5 | 5 |
| yield tokenization protocols | 0.5057 | **0** | 1 | 5 | 5 | 5 |
| delta-neutral stablecoin risk | 0.5861 | **0** | 1 | 5 | 5 | 5 |
| DePIN hardware incentive design | 0.4786 | **0** | 0 | 1 | 5 | 5 |
| governance capture by insiders | 0.5452 | **0** | 3 | 5 | 5 | 5 |
| risk of team tokens being dumped | 0.5535 | **0** | 5 | 5 | 5 | 5 |
| never turns on revenue sharing | 0.5217 | **0** | 1 | 5 | 5 | 5 |
| supply overhang from cliff vesting | 0.4826 | **0** | 0 | 5 | 5 | 5 |
| oracle competition eroding share | 0.5911 | **0** | 2 | 4 | 5 | 5 |

**At the shipped default, `semantic_search` returned zero rows for every single
realistic query.** `text-embedding-3-small` produces much lower absolute cosine
similarities than 0.7 assumes.

This is exactly the failure the handoff warns about in §8: an empty result from
a working, funded system is indistinguishable from an auth failure. Anyone who
had wired this tool at its defaults would have concluded "semantic search finds
nothing" and been wrong about why.

**New default: 0.30.** It is the widest clean separator in the measured data —
noise ceiling is 0.2131 (`EigenLayer`), weakest true positive is 0.3006
(`Ethena` rank 4). Both out-of-corpus queries correctly return nothing; every
in-corpus query keeps its true hits. 0.25 also separates but with less margin.

---

## 5. Defect: `sync_notion_to_pgvector` duplicates on re-run

`sync_database_to_pgvector` (`tools/notion.py:298`) only ever calls
`session.add(...)`. There is no upsert and no dedupe:

```sql
CREATE TABLE knowledge_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT NOT NULL,
    source_id UUID,             -- sync writes NULL, so nothing to upsert on
    ...
);                              -- no unique constraint anywhere
```

Pressing sync a second time yields **124 rows, every learning duplicated** — and
because duplicates embed identically they occupy adjacent ranks, so a top-5
query would surface 2–3 distinct learnings instead of 5. Retrieval quality
degrades silently with each press.

Not fixed at source: `tools/notion.py` is outside this branch's ownership.
Fixed at the endpoint, which this branch does own — `POST /api/knowledge/sync`
now skips a `source_type` that already holds chunks and says so, unless called
with `replace=true`, which deletes that `source_type`'s chunks first and
rebuilds. Default-safe: a double press is now a no-op with an explanatory
message rather than silent corruption.

---

## 6. `semantic_search` executes cleanly against real embeddings

The `CAST(:embedding AS vector)` fix (`5d3c033`) had never been executed. It has
now. The deployed VPS image still carries the broken `:embedding::vector`:

```
$ curl -s -X POST http://localhost:8100/api/knowledge/search \
    -H 'Content-Type: application/json' -d '{"query":"Ethena","limit":5,"threshold":0.0}'
Internal Server Error

# docker logs committee-backend
asyncpg.exceptions.PostgresSyntaxError: syntax error at or near ":"
[SQL: ... 1 - (embedding <=> :embedding::vector) as similarity ...]
[parameters: (0.0, 5)]
```

Two conclusions from that traceback:

1. The `::vector` bug is real and the fix is required. SQLAlchemy consumes
   `:embedding` as a bind param and leaves a bare `::vector` in the statement.
2. **The OpenAI key is funded and working.** The request reached the *SQL* stage,
   which means `generate_embedding` had already returned 1536 floats from
   `text-embedding-3-small`. No auth or quota error. Handoff §8's warning is
   discharged — this is not the empty-key scenario.

With the corrected SQL, all 14 queries executed and returned rows against the
live 62-chunk corpus (§3). The new tool handler also runs end to end there:

```
QUERY: risk of team tokens being dumped on the market -> 3 results
   0.5535  [Chainlink] [tokenomics_analyst] Perpetual selling pressure from 650M+ team-cont
           page=3720a58c-96ec-8152-ae62-dc138cba87eb
QUERY: governance capture by insiders -> 3 results
   0.5452  [Chainlink] No token holder governance rights creates potential for governance c
           page=33e0a58c-96ec-811c-ae88-c9c993fe5fc1
QUERY: Lido -> 0 results
```

`notion_page_id` comes back on every hit, so `read_note` chains off it directly.

Local verification (`docker compose -p aiic-retr`, seeded fake 1536-dim vectors,
no API keys) covers the SQL path, the abstention behaviour, the table
allow-list, the tool handler, the missing-key error path, registration, and the
sync guard — all passing.

---

## 7. Decision

**Ship the tool, beside `search_notes`, with guidance.**

The concept-query win is real, repeatable and explainable: seven wins to three
with better rank ordering, on the exact queries the system prompt tells agents
to run. It is not marginal.

But it is narrow. Semantic loses on named projects, loses badly on unseen
projects, and structurally cannot reach the project evaluation pages that hold
the committee's actual reasoning. A tool description that did not say so would
produce worse retrieval than no tool at all, because an agent facing two
overlapping retrievers with no guidance picks by name. So
`semantic_search_notes` states its scope limits in the description itself:
`search_notes` first for named projects, this tool for cross-project patterns,
and an empty result means "not in the snapshot", not "never considered".

### 7.1 The strongest argument against shipping

The index covers 62 short risk statements from the *Learnings* database. It does
not cover the material an agent most needs. Fixing the **sync coverage** —
indexing the project evaluation pages and transcripts — would likely deliver
more retrieval value than adding this tool does, and it needs no new tool at
all, because `search_notes` already reaches those pages live.

The counter, and why the tool still ships: the concept-query gap is a gap
`search_notes` cannot close at any corpus size, because it is a ranking and
vocabulary limitation, not a coverage one. Query 11 fails for keyword whether
the corpus holds 62 chunks or 62,000.

### 7.2 Recommended follow-ups (not done here, out of ownership)

1. **Index the projects database.** `POST /api/knowledge/sync?database=projects`
   would add `project_evaluation` chunks and remove the single largest asymmetry
   in §1.1. Costs cents. Needs the deployed image to carry `5d3c033` first,
   otherwise search still 500s.
2. **Deploy `5d3c033`.** The VPS runs `8432cf4`; semantic search is broken in
   production until it is pulled.
3. **Re-run this evaluation after (1).** The verdict here is measured against a
   learnings-only corpus and does not transfer unchanged to a corpus that
   includes 9 KB project write-ups — chunking behaviour is untested at that size,
   and the 0.30 threshold should be re-derived.
4. **Raise `search_notes`' hardcoded `limit=5`** (`notion_tools.py:54`) — 5 is
   tight when a single project has 27 learnings.
