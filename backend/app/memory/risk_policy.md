# Risk Policy

## Hard Guardrails (Non-Negotiable)

### Automatic VETO Triggers
These conditions trigger an automatic veto from the Risk Officer. No override possible.

1. **Unaudited contracts with >$10M TVL** — exploit risk too high
2. **Active SEC/DOJ investigation** — regulatory risk materialised
3. **Rug pull pattern detected** — anonymous team + concentrated supply + no timelock + recent launch
4. **Wash trading evidence >50% of volume** — liquidity is fake
5. **Known scammer connections** — team members linked to prior frauds/exploits
6. **Token classified as security** — in any major jurisdiction with active enforcement

### Automatic WATCH Downgrade Triggers
These conditions downgrade a BUY to WATCH until resolved.

1. **FDV/MCap ratio >10x** — massive dilution ahead
2. **Top 5 wallets hold >40% circulating** — concentration risk
3. **No revenue for 12+ months** — product-market fit unproven
4. **Team departure** — key technical or business leads leaving
5. **Smart contract upgrade with <72h timelock** — governance risk
6. **Liquidity depth <$500K** — can't exit position without slippage

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
