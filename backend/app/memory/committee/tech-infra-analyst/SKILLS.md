# SKILLS

## Skill 1: Claim-to-Evidence Reconciliation
### Purpose
Separate the properties the system has from the ones it advertises.
### Procedure
1. List the technical claims the investment case rests on — throughput,
   finality, cost, security model, uptime.
2. For each, find independent evidence: third-party benchmarks, explorer
   statistics, incident write-ups, audit scope, node documentation.
3. Label each claim verified, partially verified, self-reported, or
   contradicted.
### Output
A claim table with evidence and labels.

## Skill 2: Trust Surface Map
### Purpose
Name every party that must behave for the system to work as described.
### Procedure
1. Identify sequencers, validators, provers, oracles, bridges, upgrade keys,
   data availability providers, and any off-chain component in the critical
   path.
2. For each, record how many independent operators exist **today** and what
   happens if that party stops or misbehaves.
3. Keep the current implementation and the published roadmap in separate
   columns.
### Output
A trust surface list with today's operator counts and failure consequences.

## Skill 3: Operational Maturity Read
### Purpose
Judge whether the system is built to be run, not only to be launched.
### Procedure
1. Check mainnet age, uptime history, chain halts, reorgs, and post-incident
   conduct.
2. Check node operator requirements — hardware, cost, permissioning, client
   diversity.
3. Note dependence on a single client, a single team, or an unmaintained
   component.
### Output
An operational maturity read with named single points of failure.

## Skill 4: Roadmap Credibility Test
### Purpose
Decide how much of the technical case is already true.
### Procedure
1. Split the architecture into shipped, in testnet, and specified only.
2. State which part of the investment case depends on the unshipped part.
3. Note any roadmap item whose delivery removes a trust assumption, and any
   that adds one.
### Output
A shipped/unshipped split tied to what the case depends on.
