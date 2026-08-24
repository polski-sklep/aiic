#!/bin/bash
# Fetch 67-day price paths from CoinGecko free tier. Rate limited: 20s between calls.
# from = 2026-06-10T00:00:00Z, to = 2026-08-24T23:59:59Z
DIR="$(cd "$(dirname "$0")" && pwd)"
FROM=1781049600   # 2026-06-10T00:00:00Z
TO=1787615999     # 2026-08-24T23:59:59Z
for id in bitcoin aave plasma geodnet ethena morpho pendle; do
  out="$DIR/${id}-range.json"
  if [ -s "$out" ]; then echo "skip $id (cached)"; continue; fi
  url="https://api.coingecko.com/api/v3/coins/${id}/market_chart/range?vs_currency=usd&from=${FROM}&to=${TO}"
  code=$(curl -s -w '%{http_code}' -o "$out.tmp" "$url")
  echo "$id http=$code bytes=$(wc -c < "$out.tmp")"
  if [ "$code" = "200" ]; then mv "$out.tmp" "$out"; else cp "$out.tmp" "$DIR/${id}-range.ERROR.json"; rm -f "$out.tmp"; fi
  sleep 20
done
