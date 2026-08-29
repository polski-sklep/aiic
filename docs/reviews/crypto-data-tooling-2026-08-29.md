# Six crypto data tools, assessed against what this committee actually needs

Researched 29 Aug 2026. Six tools were put forward as candidate data sources.
Five are not worth building against; one item pointed at a real gap that a
source we already integrate fills for free, and that is the only code this
review produced.

The list came from a screenshot and **was cut off partway through item 6**.
There may be further items nobody here has seen. Treat this as a review of six
named tools, not as a review of the list.

## The standard applied

Not "is this interesting". Three questions, in order:

1. **Is there a programmatic surface at all?** Several of these are X accounts
   fronting a product. A UI with no API is a finding, and the assessment stops
   there.
2. **Does it answer a question a committee agent asks?** The committee scores
   projects on tokenomics, governance, on-chain health, technical soundness,
   competitive position, sentiment, legal exposure and risk. A datum that does
   not bear on one of those is not a gap being filled.
3. **Does it duplicate something already wired?** `tools/defillama.py`,
   `tools/coingecko.py`, `tools/binance.py`, `tools/web_search.py`,
   `tools/twitter.py`, `tools/notion_tools.py` and `tools/semantic.py` are
   registered today. Protocol revenue, fees and TVL are already canonical
   baseline facts fetched on every run (`fetch_canonical_facts`).

Every figure below about an endpoint's behaviour was verified by calling it on
29 Aug 2026, not read off a marketing page.

## Verdicts at a glance

| # | Tool | Programmatic surface | Verdict |
|---|---|---|---|
| 1 | Valueverse (`@valueverse_ai`) | API/MCP advertised, gated behind an access form; no public docs, no published price | **Not now** — gated, and duplicates the DeFiLlama revenue baseline |
| 2 | Yieldz (`@Yieldzio`) | No published API. An undocumented internal JSON endpoint exists and works | **Not worth it** — no terms, no stability guarantee, and the read-side data is free on DeFiLlama |
| 3 | SoSoValue (`@SoSoValueCrypto`) | Real, documented REST API — **requires an API key** | **Worth it later** — the only source here with data we genuinely lack, but it needs an account |
| 4 | Spectre AI (`@Spectre__AI`) | API in preview, "goes live with V2 in Q3 2026"; access via Stripe, x402/USDC, or staking $SPECTRE | **Not worth it** — unshipped, and token-gated access is a dependency risk |
| 5 | Polymarket Analytics (`@poly_data`) | Polymarket's own API is public and keyless | **Not worth it** — measured; the coverage does not answer what this committee asks |
| 6 | Token Terminal + Jumper RWA | Token Terminal REST is key-only, plan is custom-priced; MCP needs OAuth. Jumper is a UI over LI.FI | **Not worth it as sources** — but the RWA/market-share question is real and DeFiLlama answers it free. **This is the one thing implemented.** |

---

## 1. Valueverse — `@valueverse_ai`

**What it is.** An analytics platform for tokenized assets. Three proprietary
models: a Token Value Attribution Model, an "Effective Revenue Multiplier"
(their P/FCF analogue), and a Deep Context Attribution Model. Coverage claimed:
protocol revenue and *token-specific* revenue capture, supply and vesting,
governance structure, emissions.

**Programmatic surface.** The site lists API, MCP and MPP as delivery modes and
provides no documentation for any of them. There is no developer portal, no
endpoint reference, no published price. Every route funnels to a "Request
Access" form. Terms of use are a PDF; there is no evidence of a self-serve tier.

**What it would add.** Genuinely one thing: *token-level* value capture — does
protocol revenue reach holders, and by what mechanism. That is a judgement, not
a measurement, and it is precisely what the Tokenomics Analyst is paid to form
on its own. Importing another party's verdict on it would not add evidence, it
would add a competing opinion the committee cannot interrogate.

**What it duplicates.** The measurable half — protocol revenue, fees, TVL — is
already fetched deterministically on every run by `fetch_canonical_facts` and
handed to every agent as baseline. That is the more defensible arrangement:
one named source, one dated figure, an explicit `unavailable` when it is
missing.

**Cost / licensing.** Unknown. Nothing is published; access requires an
application. Terms are a PDF that was not agreed to and should not be, since
nothing here needs it.

**Verdict: not now.** Gated behind a sales conversation, duplicative on the
half that is measurable, and an opinion on the half that is not.

## 2. Yieldz — `@Yieldzio`

**What it is.** `https://yieldz.io` — a lending and leveraged-yield frontend
that pulls markets from Morpho, Fluid, Aave v3/v4, Euler and Lista into one
interface, and can enter or unwind a looped position in a single transaction.
(`yieldz.xyz` is an unrelated fertiliser company. Do not confuse them.)

**Programmatic surface — and this needs stating precisely.** There is **no
published API**. There are no developer docs; the app is a single-page bundle
and every unknown path, including `/docs`, `/terms` and `/tos`, returns the
same HTML shell, so a 200 from those paths means nothing.

But the frontend feeds itself from `GET https://yieldz.io/api/markets`, which
is open, keyless, CORS-enabled and returned **3.7 MB / 1,229 markets** when
called on 29 Aug 2026 — 602 Morpho, 280 Fluid, 169 Aave v3, 71 Aave v4, 60
Euler, 47 Lista, across eleven chains. Per market it carries `lltv`,
`supply_apy`, `borrow_apy`, `utilization`, `liquidity_usd`, `oracle_providers`,
borrow caps and per-collateral borrow-yield trends.

That is richer on lending mechanics than anything we have. It is also an
**internal endpoint with no documentation, no terms granting its use, no
versioning promise and no rate-limit policy**. It can change shape or start
refusing us on any deploy, with no notice and no recourse, and there is no
published licence under which the numbers may be reproduced in a report.

**What it duplicates.** The read-side is largely covered free and documented:
`https://yields.llama.fi/pools` is keyless and returned **17,329 pools, of
which 820 are Aave v3 / Morpho**, with `apy`, `apyBase`, `apyReward`,
`apyMean30d`, `tvlUsd`, `sigma`, `stablecoin` and IL-risk flags. What DeFiLlama
does not give is per-market LLTV, utilisation and oracle provider.

**What it would add, honestly.** For *this* committee, little. The unique
fields serve someone sizing a leveraged position. No committee agent takes
positions; the execution half — one-transaction looping — is irrelevant to a
research pipeline by construction.

**Cost / licensing.** No published terms at all, which is the problem rather
than a cost.

**Verdict: not worth it.** Uniquely detailed, uniquely unstable, and pointed at
a decision this system does not make. If lending-market depth ever becomes a
committee question, use `yields.llama.fi` — documented, free, already a trusted
source here — and accept the thinner schema.

## 3. SoSoValue — `@SoSoValueCrypto`

**What it is.** ETF flow data across US and HK spot products, plus BTC
treasuries, fundraising, crypto-equity, index and macro-calendar data.

**Programmatic surface — real, and the best-documented of the six.** Open API
launched April 2025. Base URL `https://openapi.sosovalue.com/openapi/v1`.
Authentication is a single header, `x-soso-api-key`. Ten modules: Currency &
Pairs, **ETF** (summary, list, market snapshot, historical net inflow), Index,
Crypto Stocks, **BTC Treasuries**, Feeds, **Fundraising**, Macro, Analysis
Charts, Market Overview. Documented rate limits: **20 requests/minute and
100,000/month per key**, `429` on breach. Time-series endpoints paginate on
`timestamp + 1`; klines are daily only and capped at three months.

**What it would add — and this is the one real gap on the list.** Nothing we
have carries ETF flows, corporate BTC treasury holdings, or fundraising
history. All three bear on questions the committee already asks and currently
answers with `web_search`:

- **ETF flows** are the cleanest available proxy for institutional
  participation, which Ray's macro/cycle pass and the Portfolio Manager both
  reason about today with no data behind them.
- **BTC treasury purchase history** is dated, discrete and citable — the shape
  of fact this system prefers.
- **Fundraising** — who funded a project, at what stage — is a standing
  question for the Tokenomics Analyst, and web search answers it inconsistently.

**What it duplicates.** Currency, klines and market-overview modules duplicate
CoinGecko and Binance, both already wired. Only the ETF / treasuries /
fundraising modules are additive; a build should use those three and ignore the
rest.

**Cost / licensing.** A key requires a SoSoValue account and an approval step
("Apply your Key", then a pending/approved review). No price is published on
the documented tiers; the launch promotion was free for the first 1,000
applicants. **No account was created and no application was submitted** — that
is outside what this run may do.

**Verdict: worth it later, blocked on a credential.** This is the single
candidate that adds data we do not have and cannot derive. It needs Jacob to
create an account and obtain a key. Once a key exists, the build is small: one
`tools/sosovalue.py` in the shape of `tools/twitter.py` — `NOT_CONFIGURED` when
the key is absent, which is the established pattern for optional keys, so the
tool can be merged and simply report itself unavailable until a key appears.

## 4. Spectre AI — `@Spectre__AI`

**What it is.** A market-intelligence platform — Research Zone, AI charts, whale
alerts, a liquidation heatmap, and narrative/mindshare tagging synthesised from
X, Telegram and on-chain sources. Beta opened 28 May 2026.

**Programmatic surface.** Advertised: 400+ endpoints, WebSocket streams, an MCP
server. Three access routes — Stripe subscription with an API key; x402
pay-per-call in USDC on Base; or staking `$SPECTRE` for tiered quotas (free
"Explorer" read-only, up to 7,000 tokens for unlimited). The site's own label on
the API is **"Preview · Goes live with V2 in Q3 2026"**. No public endpoint
reference was found.

**What it would add.** Mindshare and narrative-phase tagging is the only
non-duplicative piece, and it is the least verifiable thing on this list — a
model's opinion about attention, with no way for an agent to check it. Handing
that to Field Intel as data would put an unfalsifiable score into a report
alongside measured figures.

**What it duplicates.** Liquidations and market data overlap Binance, already
wired. Sentiment overlaps `search_twitter` and `web_search`.

**Cost / licensing.** All three routes are payment. The staking route is worse
than a price: it makes a data dependency contingent on holding a token whose
own value we might one day be evaluating, which is a conflict this committee
should not create.

**Verdict: not worth it.** The API is not shipped; its distinctive output is
unverifiable; and its cheapest access route buys a governance problem.

## 5. Polymarket Analytics — `@poly_data`

**What it is.** Analytics over Polymarket — whale trades, wallet-level P&L, win
rates, open interest, historical positioning. The account is Polymarket's own
analytics surface; several third-party sites (polydata.pro,
polymarketanalytics.com) sit on the same underlying data.

**Programmatic surface — genuinely open.** Polymarket's APIs are public and
keyless. Verified 29 Aug 2026: `gamma-api.polymarket.com/markets` → 200,
`data-api.polymarket.com/trades` → 200, `gamma-api.polymarket.com/public-search`
→ 200. Markets carry `outcomePrices`, `volumeNum`, `liquidityNum`, `endDate`,
price changes over one week and one month. No key, no signup, no cost.

**So this was measured rather than argued about.** Searching the live API for
the things a committee agent would ask:

| Query | What came back |
|---|---|
| `Pendle` | **nothing at all** |
| `Chainlink` | two events, both "What price will Chainlink hit in 2026 / August" |
| `Solana` | four events, all price-threshold markets |
| `SEC` | "SEC removes quarterly reporting requirement?", then NCAA football conference markets |
| `stablecoin` | four issuer-launch markets (Revolut, X, Meta) |
| `ETF approval` | daily "Bitcoin ETF Flows on <date>" markets |

**What it would add.** For a mid-cap protocol evaluation — the common case —
nothing. Pendle returns zero. Where coverage exists it is overwhelmingly
**price-threshold markets**, and this architecture already decided what to do
with price-shaped inputs: the Technical Analyst is excluded from scoring on the
stated ground that a good chart is not a reason to own something. A
market-implied price probability is the same class of input and would have to
be excluded for the same reason — leaving a tool whose main output no agent may
score on.

The one defensible slot was the Legal/Regulatory analyst, which has only
`web_search` today. A dated, market-implied probability on a regulatory outcome
would suit it well. The `SEC` query above is what that idea actually returns.

**Cost / licensing.** Free, keyless, no account. Cost is not the objection.

**Verdict: not worth it.** Free and easy is not the same as useful. Coverage is
absent for mid-caps, price-shaped where it exists, and a keyword search that
answers "SEC" with college football is not a source to put in front of an agent
that will cite it. Worth revisiting only if Polymarket's regulatory and
protocol-event coverage deepens — the integration would be a couple of hours
whenever that becomes true.

## 6. Token Terminal + Jumper — RWAs and tokenized stocks

**Token Terminal.** REST API at `https://api.tokenterminal.com/v2/`,
Bearer-token auth, **no endpoint accessible without a key**, 1,000 req/min.
Plans: Free ($0, dashboards and CSV export, no REST), Pro ($350/month, still no
REST), **API (custom pricing, sales contact)**, Data Room (custom). There is
also an MCP server at `https://mcp.tokenterminal.com/mcp` available on every
plan including free, but it authenticates by **OAuth 2.1 with PKCE** — an
interactive browser flow that cannot be completed in an unattended run, and
which is a research surface for a human rather than a tool an agent calls.

**Jumper.** `rwa.jumper.xyz` is a browsing UI over LI.FI's routing
infrastructure. LI.FI's API is a *routing and execution* API — quotes, routes,
bridges — not a market-data API. There is no RWA market-data endpoint to
consume. This is a place to look at assets, not a source to read from.

**What they would add — and the thing worth noticing.** The genuine question
behind item 6 is *market-level structure*: who the issuers are and how the
category divides. That is not an RWA question, it is the **Competitive Intel
Analyst's entire job description** — "market share within category, comparable
protocol metrics (TVL, revenue, users)" — and that agent's tool list is
`get_price`, `get_tvl`, `get_protocol_fees`, `web_search`. Every one of those
takes a single protocol slug. **It has no way to see a peer set at all.** Every
market-share figure in every report to date was produced by an agent from
memory or from web search prose.

That is not a hypothetical weakness. It is the documented cause of the worst
figure defect this repo has recorded: the ~44% vs 70-80% market-share split in
`tools/defillama.py`'s canonical-facts comment, and the Aave `$25.7B` vs
`$61.9B` TVL split in D15.

**What it duplicates — DeFiLlama already answers it, free.** Verified 29 Aug
2026: `GET https://api.llama.fi/protocols` is keyless, returned 8.7 MB in
0.5 s, and carries **8,148 protocols across 102 categories** with `tvl`,
`category`, `mcap`, `chains`, `change_1d/7d` and `gecko_id` on each. The RWA
category alone holds **154 protocols** — BlackRock BUIDL $3.60B, Tether Gold
$3.15B, Circle USYC $2.78B, Ondo $2.53B, Spiko $2.48B. That is item 6's
market-level view: issuers ranked, with a published denominator, from a source
already trusted here, at no cost and with no credential.

**Cost / licensing.** Token Terminal REST is a sales conversation with no
published price — not proceeded with. **No account was created anywhere.**
DeFiLlama's public API is the one we already build on.

**Verdict: the tools, not worth it. The gap they exposed, worth closing — and
it was closed, on DeFiLlama.** See below.

---

## What was implemented

One tool: **`get_category_peers`**, in `tools/defillama.py`, added to
`CompetitiveIntel`'s tool list. Given a DeFiLlama category it returns that
category's protocols ranked by TVL, with the category total and the count.

Three constraints were deliberate.

**It names its denominator and refuses to compute a share.** The comment block
in `fetch_canonical_facts` is explicit that share is "not a published number but
an arithmetic result whose value is decided by a denominator we would be
choosing", and that publishing one we computed would convert a contested
estimate into an unchallengeable one. So the tool returns the peer TVLs and the
category total as *separate* figures, states in prose exactly which set the
total is over, and leaves the division to the agent — which must then say what
it divided by. A canonical share stays out of the baseline, where that rule
applies. Evidence with a stated denominator is the opposite of the failure the
rule guards against.

**It never guesses the category.** An unknown category returns `NOT_FOUND` with
the available names listed, rather than a fuzzy match. A peer set silently
drawn from the wrong category is the market-share defect again, one layer down.

**The payload is cached in-process for five minutes.** `/protocols` is 8.7 MB;
several agents asking for peers in the same run would otherwise refetch it each
time. The cache is per-process and short — long enough for one evaluation, too
short to serve a stale figure to the next one.

Six tests in `tests/test_tools_http.py::CategoryPeersTest`: the
unknown-category refusal, case-insensitive matching reported back exactly, the
denominator being stated rather than divided, a protocol with no published TVL
counted but never zeroed, one fetch per run rather than one per agent, and
DeFiLlama being down reading as a gap rather than an empty category.
`tests/test_tool_registry.py::LiveRegistryTest` asserts the exact roster and was
updated to thirteen.

Verified live against `api.llama.fi` on 29 Aug 2026: `RWA` returns 154
protocols, a total of $27.6B over the 144 that publish a TVL, BlackRock BUIDL
top at $3.60B; `Lending` returns 636 protocols and $49.0B; `rwas` is refused
with the category list.

**One stale claim was corrected while here.** `README.md` and `CONTRACTS.md`
§3.6 both said eleven tools were registered. That had been wrong since
`agent/retrieval` merged `semantic_search_notes` — twelve, not eleven. Both now
say thirteen and name all of them. Adding a tool while leaving a known-wrong
count in the two documents people read first would have been worse than not
adding it.

## What was deliberately left

- **No accounts were created, no keys applied for, no plans purchased, no terms
  accepted.** SoSoValue, Token Terminal and Spectre all require one of those.
- **SoSoValue is the one build worth queuing**, and it is blocked only on Jacob
  creating an account and requesting a key at
  `https://sosovalue.com/developer/dashboard`. ETF flows, BTC treasuries and
  fundraising are the three modules worth wiring; the rest duplicates CoinGecko
  and Binance. The `NOT_CONFIGURED` pattern in `tools/twitter.py` means the tool
  can land before the key does.
- **The Yieldz internal endpoint was not built against**, despite working. No
  terms, no docs, no stability promise.
- **`yields.llama.fi` was not wired.** It is free and documented and would cover
  lending-market APYs, but no current agent asks a question it answers. It is
  noted here so the next person does not re-derive it.
- **DeFiLlama's derivatives endpoints remain paid** (`/overview/derivatives`
  returns HTTP 402, re-confirmed 29 Aug 2026). Perp volume is still not
  fetchable, and none of the six tools fixes that. Spectre claims liquidation
  data, but behind the objections in §4.
