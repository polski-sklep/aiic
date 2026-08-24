#!/usr/bin/env python3
"""Compute 67-day return and alpha vs BTC for the six usable calibration records.

Reads only the cached CoinGecko market_chart/range JSON in this directory.
No network access. Entry prices and BTC-at-entry come from the live
calibration_records table (docs/retrospective/source/pg-calibration-records.txt).
"""
import json
import os
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

# (project, coingecko_id, recommendation, score, chair_conf, entry_usd,
#  btc_at_entry, entry_captured_at ISO)
RECORDS = [
    ("Aave",    "aave",    "PASS",  77.20, "high",   63.09,     62964, "2026-06-11T13:47:05Z"),
    ("Plasma",  "plasma",  "PASS",  34.30, "high",   0.106058,  63983, "2026-06-18T10:45:08Z"),
    ("GEODNET", "geodnet", "WATCH", 62.60, "medium", 0.216691,  64090, "2026-06-18T11:02:38Z"),
    ("Ethena",  "ethena",  "WATCH", 53.20, "medium", 0.094421,  63960, "2026-06-18T11:20:28Z"),
    ("Morpho",  "morpho",  "WATCH", 65.60, "medium", 1.99,      63964, "2026-06-18T11:38:28Z"),
    ("Pendle",  "pendle",  "WATCH", 62.30, "medium", 1.43,      63889, "2026-06-18T11:56:39Z"),
]


def load(cg_id):
    with open(os.path.join(HERE, f"{cg_id}-range.json")) as fh:
        return json.load(fh)["prices"]  # [[ms, usd], ...]


def at(series, ts_ms):
    """Nearest observation to ts_ms."""
    return min(series, key=lambda p: abs(p[0] - ts_ms))


def last(series):
    return series[-1]


def daily_closes(series):
    """One observation per UTC day: the last of that day."""
    out = {}
    for ms, px in series:
        d = datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()
        out[d] = px
    return out


def main():
    btc = load("bitcoin")
    btc_now_ms, btc_now = last(btc)
    now_dt = datetime.fromtimestamp(btc_now_ms / 1000, timezone.utc)

    rows = []
    for name, cg, rec, score, conf, entry, btc_entry, iso in RECORDS:
        ser = load(cg)
        entry_ms = int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
        cg_entry_ms, cg_entry = at(ser, entry_ms)
        _, px_now = last(ser)
        cg_btc_entry_ms, cg_btc_entry = at(btc, entry_ms)

        days = (now_dt - datetime.fromisoformat(iso.replace("Z", "+00:00"))).days
        ret = (px_now - entry) / entry * 100.0
        btc_ret = (btc_now - btc_entry) / btc_entry * 100.0
        alpha = ret - btc_ret

        # drawdown / max-gain path since entry
        path = [p for p in ser if p[0] >= entry_ms]
        lo = min(path, key=lambda p: p[1])
        hi = max(path, key=lambda p: p[1])

        rows.append(dict(
            project=name, cg_id=cg, rec=rec, score=score, conf=conf,
            entry_usd=entry, price_now=px_now, days=days,
            return_pct=ret, btc_return_pct=btc_ret, alpha_pct=alpha,
            btc_entry_ledger=btc_entry, btc_entry_coingecko=round(cg_btc_entry, 2),
            entry_coingecko=cg_entry,
            entry_vs_coingecko_pct=(entry - cg_entry) / cg_entry * 100.0,
            min_usd=lo[1],
            min_date=datetime.fromtimestamp(lo[0] / 1000, timezone.utc).date().isoformat(),
            max_usd=hi[1],
            max_date=datetime.fromtimestamp(hi[0] / 1000, timezone.utc).date().isoformat(),
            max_drawdown_pct=(lo[1] - entry) / entry * 100.0,
            max_runup_pct=(hi[1] - entry) / entry * 100.0,
        ))

    meta = dict(
        as_of=now_dt.isoformat(),
        btc_now=btc_now,
        source="CoinGecko /coins/{id}/market_chart/range, cached in this directory",
        note="alpha = simple difference (asset return - BTC return), matching "
             "knowledge/calibration.py convention",
    )
    print(json.dumps(dict(meta=meta, rows=rows), indent=2))

    # Also dump per-day closes for the driver narrative.
    closes = {"bitcoin": daily_closes(btc)}
    for _, cg, *_ in RECORDS:
        closes[cg] = daily_closes(load(cg))
    with open(os.path.join(HERE, "daily-closes.json"), "w") as fh:
        json.dump(closes, fh, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
