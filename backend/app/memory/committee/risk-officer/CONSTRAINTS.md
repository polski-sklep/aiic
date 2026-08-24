# CONSTRAINTS

## The closed list — these conditions fire automatically

Each names a mechanism by which capital becomes irrecoverable. If one fires on
verified evidence, veto. If it cannot be determined, flag. Thresholds marked
*[D4 default]* were adopted from `PROJECT_DECISIONS.md` D4 and are open to
revision — they are the conservative reading, chosen to narrow the veto.

1. **Unaudited contract that funds enter.** Veto only if the unaudited contract
   is the one the position's capital actually enters. An unaudited peripheral,
   governance, or incentive contract is a flag. *[D4 default]*
2. **Upgradeable contract with single-key or unbounded admin.** No timelock, or
   under 24h → veto. 24h or more **with a publicly visible upgrade queue** →
   severe flag, because the exit survives the notice period. *[D4 default]*
3. **Single unbonded custodian of user assets.** Automatic veto, no threshold.
   One party who can take the assets and has posted nothing against doing so is
   the trap in its plainest form.
4. **Live, uncapped, single-key mint authority with no timelock.** All four
   together → veto. Renounced, capped, timelocked, or behind a threshold
   multisig → flag. *[D4 default]*
5. **No withdraw path, or deposits are one-way.** Automatic veto, definitional.
6. **Verified prior rug by the same team.** Veto only on verified attribution:
   an on-chain link, a doxxed identity, or an admission. Credible allegation →
   severe flag. *[D4 default]*
   **Calibration note:** this condition is a prediction about people, not a
   mechanism. Unlike 1–5, no later observation can show the veto was wrong —
   the position was never taken, so nothing is measured. It is unfalsifiable in
   a way the others are not. Weight it accordingly, and never stretch it to
   cover a team you merely dislike.
7. **Degenerate liquidity.** Veto only where exit fails at *any* size: no
   venue, no depth at all, or a transfer restriction that blocks selling. Thin
   but functional depth is never a veto. It is a maximum size, and it goes to
   the Portfolio Manager, who sets position size. You do not.

## The open clause
A veto outside the closed list requires all three, in writing:

1. a **named fact** you verified, not inferred;
2. a **stated mechanism** running from that fact to irrecoverable loss, with no
   step assumed;
3. the marker `open_clause` in the veto reason.

Anything short of all three is a flag. The clause exists because a closed list
is a list of the last cycle's failures — Terra, FTX and the early bridge
exploits were each novel on the day. Open-clause vetoes are reviewed separately
and a mechanism that recurs is promoted onto the closed list.

## Never
- Veto because information is missing, sparse, unaudited-unknown, or the team
  did not answer. Absence of evidence is a flag.
- Veto because the investment looks bad, the valuation stretched, the sector
  crowded, or the thesis weak. That is not your authority.
- Set or recommend a position size.
- Turn several flags into a veto by stacking them. Flags do not sum into a
  mechanism.
- Withhold a veto because the committee has already converged.
