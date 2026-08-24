# SKILLS

## Skill 1: Exit-Path Trace
### Purpose
Establish whether capital can leave, by what route, and controlled by whom.
### Procedure
1. Name the contract or account the position's funds enter.
2. Name every party that can move, freeze, pause, seize, or dilute them, and
   what constrains that party — timelock, bond, multisig threshold, or nothing.
3. Confirm the withdraw path is permissionless and live.
### Output
Exit path, controllers, constraints. An unnamed controller is a gap, and gaps
are flagged, not vetoed.

## Skill 2: Closed-List Adjudication
### Purpose
Rule on each condition in CONSTRAINTS, one at a time.
### Procedure
1. For each, record fires / does not fire / cannot determine.
2. Cite the fact behind each verdict.
3. Cannot-determine is a flag. It is never a veto.
### Output
One verdict per condition, each with evidence.

## Skill 3: Open-Clause Construction
### Purpose
Catch a trap the closed list does not name.
### Procedure
1. Name one fact you verified.
2. State the chain from that fact to irrecoverable loss, step by step, with no
   step assumed.
3. If any step is inference rather than observation, downgrade to a flag.
### Output
An `open_clause` veto, or a flag.

## Skill 4: Depth Handoff
### Purpose
Separate the degenerate liquidity case from the sizing case.
### Procedure
1. Test whether exit is possible at any size at all.
2. If it is, the constraint is a size, not a stop.
### Output
Either a liquidity veto, or a maximum exitable size handed to the Portfolio
Manager.
