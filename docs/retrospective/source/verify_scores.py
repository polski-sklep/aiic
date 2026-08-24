#!/usr/bin/env python3
"""Reproduce every ledger score from the Notion text, and profile agent bias.

Two purposes:
  1. Prove the Notion page bodies are the *same runs* as the calibration_records
     rows. If _calc_score over the Notion scores reproduces overall_score to
     0.1, the corpus is the right corpus.
  2. Measure each agent's systematic deviation from the composite it feeds.

Reads only docs/retrospective/source/notion-*.txt. No network.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# backend/app/agents/orchestrator.py::_calc_score
WEIGHTS = {
    "tokenomics_analyst": 0.15, "onchain_analyst": 0.12, "tech_infra_analyst": 0.15,
    "governance_analyst": 0.08, "competitive_intel": 0.10, "field_intel": 0.05,
    "risk_officer": 0.15, "maturation_scorer": 0.10, "legal_regulatory": 0.05,
    "portfolio_manager": 0.05,
}
EXCLUDED = {"report_writer", "ray_dalio", "committee_chair", "technical_analyst"}

# file, block index (blocks split on '\n---\n'), ledger overall_score
CASES = [
    ("notion-aave.txt",    2, 77.20),
    ("notion-plasma.txt",  2, 34.30),
    ("notion-geodnet.txt", 0, 62.60),
    ("notion-ethena.txt",  0, 53.20),
    ("notion-morpho.txt",  0, 65.60),
    ("notion-pendle.txt",  0, 62.30),
]


def scores(fname, block):
    txt = open(os.path.join(HERE, fname)).read().split("\n---\n")[block]
    out = {}
    for name, val in re.findall(r"\*\*(\w+)\*\* \(score: ([^)]*)\)", txt):
        out[name] = None if val == "None" else float(val)
    return out


def calc(sc):
    num = den = 0.0
    missing = []
    for agent, w in WEIGHTS.items():
        v = sc.get(agent)
        if v is None:
            missing.append(agent)
            continue
        num += v * w
        den += w
    return (num / den if den else None), missing


def main():
    dev = {}
    print(f"{'project':10} {'ledger':>7} {'recomputed':>11} {'delta':>7}  dropped agents")
    for fname, block, ledger in CASES:
        sc = scores(fname, block)
        got, missing = calc(sc)
        proj = fname.split("-")[1].split(".")[0]
        print(f"{proj:10} {ledger:7.2f} {got:11.2f} {got - ledger:7.2f}  "
              f"{', '.join(missing) or '-'}")
        for agent, v in sc.items():
            if v is None or agent in EXCLUDED:
                continue
            dev.setdefault(agent, []).append(v - got)

    print()
    print(f"{'agent':20} {'n':>2} {'mean dev vs composite':>22}")
    for agent, ds in sorted(dev.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        w = WEIGHTS.get(agent)
        tag = f"w={w:.2f}" if w else "unweighted"
        print(f"{agent:20} {len(ds):2d} {sum(ds) / len(ds):+22.1f}   {tag}")


if __name__ == "__main__":
    main()
