# Risk Policy

## Hard Guardrails (Non-Negotiable)

### What the veto is for
The veto protects against being **trapped**, not against being **wrong**. It
fires on two mechanisms only: funds frozen or seizable by design
(mechanism-irrecoverable), and inability to exit at any size
(liquidity-irrecoverable).

Thesis-death — the asset goes to zero but the exit stays open — is **not** a
veto condition. That is the rest of the committee's job, priced through the
score and the recommendation.

The veto fires on **presence of danger**, never on **absence of evidence**.
Missing, thin or unobtainable information produces a flag, not a stop.

### Automatic VETO Triggers
Each names a mechanism by which capital becomes irrecoverable. If one fires on
verified evidence, the Risk Officer vetoes. If it cannot be determined, it is a
flag. Thresholds marked *[D4]* are the adopted defaults from
`PROJECT_DECISIONS.md` D4 and are open to revision.

1. **Unaudited contract that funds enter** — veto only where the unaudited
   contract is the one the position's capital actually enters. An unaudited
   peripheral, governance or incentive contract is a flag. *[D4]*
2. **Upgradeable contract with single-key or unbounded admin** — no timelock,
   or under 24h, vetoes. 24h or more with a publicly visible upgrade queue is a
   severe flag, because the exit survives the notice period. *[D4]*
3. **Single unbonded custodian of user assets** — automatic, no threshold.
4. **Live, uncapped, single-key mint authority with no timelock** — all four
   together veto. Renounced, capped, timelocked, or behind a threshold
   multisig is a flag. *[D4]*
5. **No withdraw path, or one-way deposits** — automatic, definitional.
6. **Verified prior rug by the same team** — verified attribution only: an
   on-chain link, a doxxed identity, or an admission. Credible allegation is a
   severe flag. *[D4]* This condition predicts people rather than naming a
   mechanism, and is unfalsifiable for calibration in a way 1–5 are not: the
   position is never taken, so nothing is ever measured against it.
7. **Degenerate liquidity** — veto only where exit fails at *any* size: no
   venue, no depth at all, or a transfer restriction that blocks selling. Thin
   but functional depth is never a veto; it is a maximum position size, and it
   is handed to the Portfolio Manager.

### Open clause
A veto outside this list requires all three, in writing: a **named verified
fact**; a **stated mechanism** running from that fact to irrecoverable loss
with no step assumed; and the marker `open_clause`. Anything short of all three
is a flag. Open-clause vetoes are reviewed separately, and a mechanism that
recurs is promoted onto the list above.

### Severe Flags (escalate, do not stop)
These were previously listed as automatic vetoes. They are real and they belong
in the report and in the score, but each leaves the exit open, so under the
policy above none is a stop on its own.

1. **Active SEC/DOJ investigation** — regulatory risk, not a trap. Escalates to
   a veto only if enforcement actually freezes, blacklists or blocks transfer
   of the asset, which is condition 1 or 7 above.
2. **Token classified as a security with active enforcement** — same treatment.
   A delisting severe enough to leave no venue is condition 7.
3. **Rug-pull pattern** (anonymous team + concentrated supply + no timelock +
   recent launch) — an inference about intent. Where it is real it almost
   always also trips condition 2 or 4 on mechanism, which is the ground the
   veto should stand on.
4. **Wash trading >50% of volume** — the reported depth is fake. Re-test real
   exitable depth against condition 7 rather than vetoing on the wash trading
   itself.
5. **Known scammer connections** short of verified attribution — see 6 above.

### Automatic WATCH Downgrade Triggers
These downgrade a BUY to WATCH until resolved.

1. **FDV/MCap ratio >10x** — massive dilution ahead
2. **Top 5 wallets hold >40% circulating** — concentration risk
3. **No revenue for 12+ months** — product-market fit unproven
4. **Team departure** — key technical or business leads leaving
5. **Smart contract upgrade with <72h timelock** — governance risk. This nests
   with veto condition 2 rather than competing with it: under 24h is a veto,
   24–72h is a WATCH downgrade and a severe flag.
6. **Liquidity depth <$500K** — sizing constraint for the Portfolio Manager.
   Only total inability to exit is a veto.

## Kill Criteria (Position Exit)
If ANY of these fire, trigger immediate re-evaluation with bias toward exit.

1. **Core thesis invalidated** — the reason you bought no longer holds
2. **Exploit/hack** — protocol loses >10% of TVL to exploit
3. **Regulatory action** — enforcement action in US, EU, or UK
4. **Team rug** — treasury drained, team goes dark
5. **Competitive obsolescence** — dominant competitor captures >70% market share
6. **Token unlock cliff** — >20% of circulating supply unlocking within 30 days with no demand catalyst
7. **Score drops below 40** — on re-evaluation

## Risk Scoring Framework

### Risk Categories (each scored 0-100, lower = riskier)
- **Smart Contract Risk**: Audit quality, code complexity, upgrade mechanism, exploit history
- **Market Risk**: Liquidity depth, volatility, correlation to BTC, FDV/MCap
- **Regulatory Risk**: Token classification, jurisdiction, compliance posture
- **Team Risk**: Track record, doxxed status, key person dependency
- **Concentration Risk**: Token holder distribution, governance centralization
- **Systemic Risk**: Dependencies on other protocols, oracle risk, bridge risk

### Composite Risk Score
Weighted average of above categories. Weights:
- Smart Contract: 25%
- Market: 20%
- Regulatory: 15%
- Team: 15%
- Concentration: 15%
- Systemic: 10%

## Data Confidence
- Agents must flag when data is stale (>7 days for on-chain, >30 days for fundamentals)
- Low-confidence assessments (agent reports "low") reduce the weight of that agent's score by 50%
- If >3 agents report "low" confidence, evaluation is marked "INSUFFICIENT_DATA" regardless of scores
