# Per-project: named variable vs. realised driver

Verdict scale: **HIT** — the committee named the variable that moved the price,
with the right sign. **PARTIAL** — named the right variable, wrong sign or
missed the trigger. **MISS** — the price moved on something no agent named, or
on something an agent named and actively dismissed. **UNRESOLVED** — no
separable price effect to grade.

## Scoreboard

| Project | Rec | Score | Conf | α @30d | α @61d | α @67d | Sign stable? | Verdict |
| --- | --- | ---: | --- | ---: | ---: | ---: | --- | --- |
| Aave | PASS | 77.2 | high | **+49.9** | +39.2 | +100.1 | yes | **MISS** |
| Plasma | PASS | 34.3 | high | **−19.6** | −28.9 | −26.4 | yes | **HIT** |
| GEODNET | WATCH | 62.6 | medium | −7.6 | −10.4 | −13.0 | yes | **PARTIAL** |
| Ethena | WATCH | 53.2 | medium | −13.1 | −12.7 | **+49.4** | **no** | **PARTIAL** |
| Morpho | WATCH | 65.6 | medium | −1.2 | +5.8 | +23.5 | no (near 0) | **MISS** |
| Pendle | WATCH | 62.3 | medium | +7.6 | −8.2 | **+8.1** | **no** | **UNRESOLVED** |

α = asset return − BTC return, both from entry. 30d = 11 July for Aave,
18 July for the cohort. All prices at 00:00 UTC. Source:
`source/prices/checkpoints-0000utc.json`.

Read the 30-day column first. It is the only one the product will ever write,
it is the one the 429-degraded runs were actually forecasting into, and it is
not contaminated by the 19–24 August repricing. At 30 days the picture is
unambiguous and rather flattering to the WATCHes: the four WATCHes cluster in
a −13 to +8 band around zero — nothing happened, which is what a WATCH asserts —
while the two high-confidence PASSes are the two extremes, one maximally right
and one maximally wrong.

---

## Aave — MISS

**PASS, score 77.2 (above the ≥75 INVEST threshold), chair confidence high,
11 June 2026. 30-day alpha +49.9. 73-day alpha +100.1.**

### What the committee said would matter

The Chair's decision is in `source/pg-aave-1a94e47d-agent-outputs.txt` and is
unambiguous — one variable, stated as a hard rule:

> "the severe governance concentration (80% whale control) violates our
> investment mandate's exclusion criteria for projects where top holders control
> >60% of supply"

with `mandate_flags: ["HOLDER_CONCENTRATION_VIOLATION"]` and an
`adjudication_trace` recording `threshold_crossed: "Top holder concentration
exceeding 60% mandate limit"`. Ray concurred at score 45: *"No margin of safety
when 80% of tokens can vote through anything."* The named top-five risks are
three tokenomics items (safety-module slashing, governance centralisation,
competition) and two governance items (governance capture via the "Aave Will
Win" proposal; the ACI / Chaos Labs delegate exodus).

The Chair's five signposts were: whale concentration below 60%; governance
reform with vesting for large holders; return of exited delegates; competitors
faltering; institutional diversification of the token base. **None of the five
fired. The price doubled anyway.**

### What actually moved the price

- **27–28 June 2026 — Aavenomics 3.0 goes live.** An automated, non-discretionary
  buyback engine routing all Aave and GHO protocol revenue (~$400M annualised)
  into open-market AAVE purchases, ~292 AAVE/day removed. Sixteen days after the
  evaluation.
- **25 June** — Standard Chartered initiates coverage; **25–26 June** — Kraken
  reported in talks for a 15% stake in Aave Group; **2 July** — new Aave market
  on Monad takes >$100M deposits in 48 hours.
- 19–24 August — market-wide repricing (see `00-method.md`, contamination 1).

Price path corroborates: 63.09 at entry, 73.5 by 17 June, 84 by 1 July, 96 by
15 July, and a monotone climb with a maximum drawdown from entry of **−0.08%**
across the whole window. This asset never traded below its entry price.

### Verdict: MISS — and the sharpest kind

Aavenomics 3.0 was the delivery vehicle of the **Aave Will Win** framework,
which passed governance in April 2026. The committee named AWW. It named it
twice, in two agents, and analysed exclusively its legitimacy dimension —
$51M extracted, delegates left, governance captured. Not one of the fifteen
agents asked what AWW was going to *do*. The `tokenomics_analyst`, which scored
Aave 85 and owns value accrual, does not mention AWW at all.

It is worse than an omission. The Devil's Advocate had the variable in hand and
argued it away:

> claim: *"Fee switch activation and buyback mechanisms could create
> deflationary pressure"*
> counter: *"Fee switches have historically failed to drive token value in DeFi
> (see UNI), and the governance crisis makes implementing such changes
> politically impossible"*

Both halves were falsified within sixteen days. And the second half inverts the
causal relationship the whole committee was built around: the governance
concentration that the Chair treated as disqualifying is precisely what let a
contested value-accrual upgrade pass and ship on schedule. Concentration was
the *enabling* condition, not the brake.

The mandate rule is the proximate cause. It is a hard, unweighted exclusion —
`risk_officer_approved_override: true`, `report_writer_recommendation: "WATCH"`
at 73.5 — and it fired on a metric (holder concentration) with no established
link to forward return, against a catalyst that was already public.

One mechanical correction, because the obvious reading of "scored 77.2, returned
PASS" is wrong. **The Chair never saw 77.2.** `_calc_score` runs *after* the
Chair in `orchestrator.py`; the weighted score did not exist when the decision
was taken, and the Chair's own score was parsed and discarded. What it had was
the report writer's 73.5, which survived `chair.py`'s 6,000-character truncation
at offset 2,446 and which it read and quoted. So this is not a judgment agent
overruling a number — it is a number computed afterwards and filed in the ledger
beside a decision it never touched. Six of the report's twenty-four sections
were cut before the Chair read it, including the competitive landscape, on a
case whose loudest bear argument was Morpho competition. See F10.

**One caution, stated plainly: n = 1.** One override being wrong is not evidence
that overrides are wrong. What this instance does establish is narrower and
still actionable: the disagreement was real, it was reasoned, it was recorded in
`agent_outputs` — and it is invisible in the calibration ledger, which stores
77.2 and "PASS" with `evaluation_id` NULL and no link back to the trace. The
system cannot learn from this event because the event was never written down
anywhere the ledger can see.

---

## Plasma — HIT

**PASS, score 34.3, chair confidence high, 18 June 2026. 30-day alpha −19.6.
67-day alpha −26.4. Sign stable at every checkpoint.**

### What the committee said would matter

Named risk #2, with a date, a size and a direction:

> "US public sale cliff unlock (1B XPL, July 28 2026) equals ~39.8% of current
> circulating supply in a single event — high price impact risk"

Reinforced independently by `onchain_analyst` ("a massive, imminent unlock event
on July 28, 2026… ~40% of current circulating supply"), `legal_regulatory` ("a
12-month lockup expiring July 28, 2026 — a date now approximately 40 days away
that represents a material legal and market event"), `tech_infra_analyst`,
`maturation_scorer` and `risk_officer`. Six agents, one date, same sign.

### What actually happened

1 billion XPL were released to US public-sale participants on 28 July 2026 as
scheduled, roughly a 37% increase in circulating supply. The price series:
local maximum $0.1152 on 4 July, then a continuous slide to $0.0724 on
6 August — **−31.7% from entry**, the deepest drawdown in the cohort — with only
the market-wide August rally lifting it back to −5%.

### Verdict: HIT

This is the corpus's one clean result and the only one robust to every
methodological choice: right variable, right date, right direction, right
magnitude class, confirmed by the price path. It is also the only project where
a named risk carried a date and a quantity. That is not a coincidence — see
`02-findings.md` F1.

---

## GEODNET — PARTIAL

**WATCH, score 62.6, chair confidence medium, 18 June 2026. 30-day alpha −7.6.
67-day alpha −13.0.**

### What the committee said would matter

Insider concentration (50% of supply), quarterly unlock velocity, DePIN
execution risk, absent audits, and — the one the committee itself flagged as
decisive and unknowable — the burn/emission balance:

> "Revenue opacity: No public revenue figures available to quantify the actual
> magnitude of the 80% buyback-burn relative to new emissions, making net
> inflation/deflation balance unverifiable"

The Devil's Advocate went further: *"the much-hyped 'net-deflationary' flywheel
is mathematically marginal… the deflation narrative is mostly aspirational."*
Separately, `technical_analyst` reported it could produce no analysis at all —
GEOD is not on Binance — and concluded: *"illiquidity and limited exchange
presence significantly elevate execution risk."*

### What actually moved the price

- **27 July 2026 — Upbit listing**, reported as a ~54.6% surge. The cached
  series independently puts GEOD's window maximum at $0.2540 on exactly
  27 July, +52.5% above entry. Two sources, same day, same magnitude.
- **1 August 2026 — GEOD reaches net-deflationary status.** The unverifiable
  quantity resolved, in the bulls' favour.
- Reversion through early August to a −20% drawdown by 7 August; the August
  rally then took it back to roughly flat in alpha-negative terms.

### Verdict: PARTIAL

The committee identified the decisive structural variable — burn versus
emissions — correctly, explicitly, and honestly labelled it unverifiable. It
resolved eleven days later and the committee had no mechanism to look again.

The larger single move was the Upbit listing, and here the committee had the
underlying fact and used it upside-down. It knew GEOD was on few venues; it
recorded that solely as *execution risk to us* and never as *the largest
available catalyst for the asset*. Same observation, one sign, and the sign it
chose was the one that does not pay. That pattern recurs — `02-findings.md` F3.

---

## Ethena — PARTIAL, and the clearest demonstration of endpoint fragility

**WATCH, score 53.2, chair confidence medium, 18 June 2026. Alpha −13.1 at 30
days, −12.7 at 61 days, +49.4 at 67 days.**

### What the committee said would matter

Funding rates, from five agents independently — the strongest convergence
anywhere in the corpus. Named risk #3:

> "Delta-neutral strategy risk: USDe yield depends on perpetual futures funding
> rates remaining positive — sustained negative funding could erode collateral
> and collapse USDe yield, reducing protocol revenue below activation
> thresholds"

`tech_infra_analyst`: dependence on "positive funding rate regimes—none of which
are onchain-native". `field_intel` (score 38): "sUSDe yields have compressed to
high-single-digits from peak levels of 20–30%, driven by cooler funding
markets". `maturation_scorer`: "revenue has compressed sharply alongside falling
funding rates". `devils_advocate`: "a pure governance/reflexivity token on a
yield engine whose yield is gone". The fee switch was named as risk #1 —
specifically, the risk that it would *not* activate.

### What actually moved the price

Flat-to-down for eight weeks, exactly as described — then from ~$0.082 on
18 August:

- **~19–21 August — FalconX $1B secured warehouse facility** for USDe, with
  FalconX as originator, servicer and collateral manager. Institutional credit
  distribution.
- **21 August — Arthur Hayes** publicly calls a 5x on ENA.
- **August protocol revenue ~$61M** — the funding-driven revenue base recovering
  hard, giving the move a fundamental hook.
- Fee-switch speculation, plus the market-wide rally.

### Verdict: PARTIAL

The committee named the correct central variable and, for sixty-one of
sixty-seven days, was correct about it. The thing that ultimately repriced ENA
*was* protocol revenue — the exact quantity funding rates drive. But it modelled
that variable as a one-way decay: `field_intel` gave the cohort's joint-lowest
score (38) on the reasoning that compressed funding was a structural headwind,
with no scenario in which it recovers. And it identified none of the proximate
triggers: an institutional credit facility and a reflexive influencer bid are
not variables any of the fifteen agents track.

State the fragility explicitly. **Graded on 18 August, this project is a clean
HIT** — alpha −12.7, thesis confirmed, funding decay depressed the token exactly
as forecast. Graded six days later on identical reasoning it is a PARTIAL at
best. Nothing about the committee's analysis changed; only the measurement date
did. Any calibration process that reads one number off one date will make this
mistake systematically.

---

## Morpho — MISS (weak)

**WATCH, score 65.6, chair confidence medium, 18 June 2026. 30-day alpha −1.2.
67-day alpha +23.5.**

### What the committee said would matter

One variable, stated as load-bearing for the entire valuation — the fee switch:

> "Fee switch activation risk: Governance may never activate direct fee
> distribution… leaving MORPHO as a permanently hollow governance token."

`competitive_intel`: "MORPHO currently captures $0 in protocol revenue…
making the value accrual thesis entirely forward-looking".
`maturation_scorer`: "value accrual entirely forward-looking and
governance-dependent". `devils_advocate`: "the bull case rests almost entirely
on a hypothetical fee switch". Also named: the Stream/xUSD curator contagion,
insider concentration at 47.6%, a 5/9 multisig with no timelock, and named risk
#2 — "October 2025 Cohort 2 unlock… creates immediate, material sell pressure",
an event **eight months in the past** at the time of writing.

### What actually happened

The fee switch did **not** activate. As of August 2026 MORPHO still captures
zero of ~$257M cumulative fees; the first real commercial revenue, a Berachain
licensing fee, was routed to the Association rather than the DAO because the
legal plumbing is unfinished. Meanwhile: a Pendle USDC vault co-curated with
Wintermute went live 12 August; Pendle added PT-USD3-on-Morpho to its
PT-looping incentives on 17 August; Markets App early exits shipped 19 August;
and ~3.9% of supply (~$22.7M) unlocked on 21 August without arresting the rally.

### Verdict: MISS

The variable the committee said the whole valuation depended on stayed exactly
where it was, and the token returned +23.5 points of alpha regardless. Whatever
repriced MORPHO, it was not fee-switch resolution. That is a falsification of the
framing, not of a forecast: "priced entirely on speculative optionality" was
offered as a criticism and turned out to be a correct description of an asset
that then went up on optionality.

Weakened by two things and both should be said. The driver attribution is
genuinely thin — integrations and product ships of that size do not usually
explain +23.5 alpha, and most of this move is the 19–24 August market. And at
30 days the alpha is −1.2, i.e. nothing to grade at all. **Driver not
established beyond the sector-wide repricing.** The graded finding is narrower
and solid: the named central variable did not resolve, and the price moved
anyway.

---

## Pendle — UNRESOLVED

**WATCH, score 62.3, chair confidence medium, 18 June 2026. Alpha +7.6 at 30
days, −8.2 at 61 days, +8.1 at 67 days.**

### What the committee said would matter

TVL decline ("down ~86% from $8.9B August 2025 peak", with a −17% intraweek
acceleration), revenue collapse, terminal 2% inflation from April 2026, Boros
unproven at "~0.1% perps penetration", and counterparty concentration —
"~75% of deposits" in Ethena (`competitive_intel`, `maturation_scorer`,
`risk_officer`; `devils_advocate` says ~41%).

### What actually happened

TVL ~$1.068B in August against ~$1.25B at evaluation — continued mild decline,
direction correct. Boros passed $14B cumulative volume and added crude, gold,
silver and equity perpetuals in July 2026; the H2 roadmap turned toward RWA and
institutional markets. Ethena, the named 75% counterparty, rose 71%.

### Verdict: UNRESOLVED

Alpha is +7.6, −8.2, +8.1 depending on which of three defensible dates you pick.
It is inside the noise. The named variables did move as described — TVL down,
Boros scaled — and produced no separable price effect over the window.

There is nothing to grade here and the honest output is to say so rather than
manufacture a verdict from an 8-point number that changes sign twice. Two
observations are worth banking for the 90-day checkpoint, neither of them a
finding yet: named risk #3 elevated a **seven-day TVL wiggle** ($1.18B → $960M)
into a top-five structural risk, which is a category error regardless of
outcome; and the largest idiosyncratic variable the committee identified —
Ethena concentration — resolved strongly positive without carrying Pendle with
it, which is at least evidence that the 75% figure is not the transmission
mechanism the committee assumed.
