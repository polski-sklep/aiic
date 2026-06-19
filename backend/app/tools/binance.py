"""Binance public API tools for candles, orderbook depth, and derived TA levels."""
from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict, cast

import httpx

from app.llm import JSONValue, SourceReference, ToolDefinition
from app.tools.registry import ToolArguments

if TYPE_CHECKING:
    from app.tools.registry import ToolRegistry

BINANCE_API = "https://api.binance.com/api/v3"
COMMON_QUOTES = ("USDT", "USDC", "FDUSD", "BTC", "ETH", "BNB", "EUR", "TRY", "BUSD")


class ToolError(TypedDict, total=False):
    error: str
    details: str


def _normalize_symbol(raw_symbol: object) -> str:
    return str(raw_symbol or "").upper().replace("-", "").replace("/", "").strip()


def _split_symbol(symbol: str) -> tuple[str, str]:
    for quote in COMMON_QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)], quote
    return symbol, ""


def _market_url(symbol: str) -> str:
    base, quote = _split_symbol(symbol)
    if base and quote:
        return f"https://www.binance.com/en/trade/{base}_{quote}?type=spot"
    return f"https://www.binance.com/en/markets/overview?symbol={symbol}"


def _source(symbol: str, *, supports: str) -> SourceReference:
    return cast(
        SourceReference,
        {
            "label": f"Binance market: {symbol}",
            "url": _market_url(symbol),
            "kind": "market_data",
            "supports": supports,
        },
    )


async def get_klines(args: ToolArguments) -> dict[str, JSONValue] | ToolError:
    """Fetch OHLCV candles for a spot trading pair."""
    symbol = _normalize_symbol(args.get("symbol", ""))
    interval = str(args.get("interval", "4h")).strip()
    requested_limit = args.get("limit", 200)
    limit = min(int(requested_limit) if isinstance(requested_limit, int | float) else 200, 500)

    valid_intervals = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"}
    if not symbol:
        return {"error": "symbol is required"}
    if interval not in valid_intervals:
        return {"error": f"Invalid interval. Use one of: {sorted(valid_intervals)}"}

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{BINANCE_API}/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )

    if response.status_code == 400:
        return {"error": f"Symbol '{symbol}' not found on Binance spot markets."}
    if response.status_code == 451:
        return {"error": "Binance market data is unavailable from this region."}

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return {"error": f"Binance klines request failed with status {exc.response.status_code}."}

    raw = cast(list[list[JSONValue]], response.json())
    candles: list[dict[str, JSONValue]] = []
    for row in raw:
        candles.append(
            {
                "timestamp": int(cast(int | float, row[0])),
                "open": float(cast(int | float | str, row[1])),
                "high": float(cast(int | float | str, row[2])),
                "low": float(cast(int | float | str, row[3])),
                "close": float(cast(int | float | str, row[4])),
                "volume": float(cast(int | float | str, row[5])),
            }
        )

    closes = [float(candle["close"]) for candle in candles]
    highs = [float(candle["high"]) for candle in candles]
    lows = [float(candle["low"]) for candle in candles]
    volumes = [float(candle["volume"]) for candle in candles]

    current_price = closes[-1] if closes else None
    period_high = max(highs) if highs else None
    period_low = min(lows) if lows else None
    avg_volume = (sum(volumes) / len(volumes)) if volumes else None

    pct_from_high = None
    pct_from_low = None
    if current_price is not None and period_high not in (None, 0):
        pct_from_high = round(((current_price - period_high) / period_high) * 100, 2)
    if current_price is not None and period_low not in (None, 0):
        pct_from_low = round(((current_price - period_low) / period_low) * 100, 2)

    return {
        "symbol": symbol,
        "interval": interval,
        "candle_count": len(candles),
        "current_price": current_price,
        "period_high": period_high,
        "period_low": period_low,
        "pct_from_period_high": pct_from_high,
        "pct_from_period_low": pct_from_low,
        "avg_volume": avg_volume,
        "candles": candles,
        "sources": [
            _source(
                symbol,
                supports=f"Binance OHLCV candles for {symbol} on the {interval} timeframe.",
            )
        ],
    }


async def get_orderbook_depth(args: ToolArguments) -> dict[str, JSONValue] | ToolError:
    """Fetch top-of-book depth and highlight large nearby liquidity walls."""
    symbol = _normalize_symbol(args.get("symbol", ""))
    requested_limit = args.get("limit", 100)
    limit = min(int(requested_limit) if isinstance(requested_limit, int | float) else 100, 500)

    if not symbol:
        return {"error": "symbol is required"}

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{BINANCE_API}/depth",
            params={"symbol": symbol, "limit": limit},
        )

    if response.status_code == 400:
        return {"error": f"Symbol '{symbol}' not found on Binance spot markets."}
    if response.status_code == 451:
        return {"error": "Binance market data is unavailable from this region."}

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return {"error": f"Binance depth request failed with status {exc.response.status_code}."}

    data = cast(dict[str, list[list[str]]], response.json())
    bids = [(float(price), float(quantity)) for price, quantity in data.get("bids", [])]
    asks = [(float(price), float(quantity)) for price, quantity in data.get("asks", [])]
    if not bids or not asks:
        return {"error": "Empty orderbook received from Binance."}

    best_bid = bids[0][0]
    best_ask = asks[0][0]
    spread = best_ask - best_bid
    spread_pct = (spread / best_bid) * 100 if best_bid else None
    mid_price = (best_bid + best_ask) / 2

    def cumulative_depth(orders: list[tuple[float, float]], pct: float, side: str) -> float:
        threshold = mid_price * (1 - pct / 100) if side == "bid" else mid_price * (1 + pct / 100)
        if side == "bid":
            return sum(quantity for price, quantity in orders if price >= threshold)
        return sum(quantity for price, quantity in orders if price <= threshold)

    bid_depth_1pct = cumulative_depth(bids, 1, "bid")
    bid_depth_2pct = cumulative_depth(bids, 2, "bid")
    bid_depth_5pct = cumulative_depth(bids, 5, "bid")
    ask_depth_1pct = cumulative_depth(asks, 1, "ask")
    ask_depth_2pct = cumulative_depth(asks, 2, "ask")
    ask_depth_5pct = cumulative_depth(asks, 5, "ask")

    avg_bid_size = sum(quantity for _, quantity in bids) / len(bids)
    avg_ask_size = sum(quantity for _, quantity in asks) / len(asks)

    bid_walls: list[dict[str, JSONValue]] = []
    for price, quantity in bids[:50]:
        if quantity > avg_bid_size * 3:
            bid_walls.append({"price": price, "quantity": quantity, "value_usd": price * quantity})
    ask_walls: list[dict[str, JSONValue]] = []
    for price, quantity in asks[:50]:
        if quantity > avg_ask_size * 3:
            ask_walls.append({"price": price, "quantity": quantity, "value_usd": price * quantity})

    bid_ask_ratio_5pct = round(bid_depth_5pct / ask_depth_5pct, 2) if ask_depth_5pct else None

    return {
        "symbol": symbol,
        "mid_price": round(mid_price, 8),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": round(spread, 8),
        "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
        "depth": {
            "bid_qty_within_1pct": round(bid_depth_1pct, 2),
            "bid_qty_within_2pct": round(bid_depth_2pct, 2),
            "bid_qty_within_5pct": round(bid_depth_5pct, 2),
            "ask_qty_within_1pct": round(ask_depth_1pct, 2),
            "ask_qty_within_2pct": round(ask_depth_2pct, 2),
            "ask_qty_within_5pct": round(ask_depth_5pct, 2),
        },
        "bid_walls": bid_walls[:5],
        "ask_walls": ask_walls[:5],
        "bid_ask_ratio_5pct": bid_ask_ratio_5pct,
        "sources": [
            _source(
                symbol,
                supports=f"Binance orderbook depth and nearby liquidity walls for {symbol}.",
            )
        ],
    }


async def compute_technical_levels(args: ToolArguments) -> dict[str, JSONValue] | ToolError:
    """Compute EMAs, RSI, ATR, support, and resistance from Binance candles."""
    symbol = _normalize_symbol(args.get("symbol", ""))
    interval = str(args.get("interval", "4h")).strip() or "4h"
    if not symbol:
        return {"error": "symbol is required"}

    klines_result = await get_klines({"symbol": symbol, "interval": interval, "limit": 200})
    if "error" in klines_result:
        return klines_result

    candles = cast(list[dict[str, JSONValue]], klines_result["candles"])
    if len(candles) < 50:
        return {"error": "Insufficient data for technical analysis (need at least 50 candles)."}

    closes = [float(candle["close"]) for candle in candles]
    highs = [float(candle["high"]) for candle in candles]
    lows = [float(candle["low"]) for candle in candles]
    volumes = [float(candle["volume"]) for candle in candles]

    def ema(values: list[float], period: int) -> float | None:
        if len(values) < period:
            return None
        multiplier = 2 / (period + 1)
        current = sum(values[:period]) / period
        for price in values[period:]:
            current = (price * multiplier) + (current * (1 - multiplier))
        return round(current, 8)

    def rsi(values: list[float], period: int = 14) -> float | None:
        if len(values) < period + 1:
            return None
        gains: list[float] = []
        losses: list[float] = []
        for index in range(1, len(values)):
            change = values[index] - values[index - 1]
            gains.append(max(change, 0.0))
            losses.append(abs(min(change, 0.0)))
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for index in range(period, len(gains)):
            avg_gain = ((avg_gain * (period - 1)) + gains[index]) / period
            avg_loss = ((avg_loss * (period - 1)) + losses[index]) / period
        if avg_loss == 0:
            return 100.0
        relative_strength = avg_gain / avg_loss
        return round(100 - (100 / (1 + relative_strength)), 2)

    def atr(high_values: list[float], low_values: list[float], close_values: list[float], period: int = 14) -> float | None:
        if len(high_values) < period + 1:
            return None
        true_ranges: list[float] = []
        for index in range(1, len(high_values)):
            true_range = max(
                high_values[index] - low_values[index],
                abs(high_values[index] - close_values[index - 1]),
                abs(low_values[index] - close_values[index - 1]),
            )
            true_ranges.append(true_range)
        current = sum(true_ranges[:period]) / period
        for value in true_ranges[period:]:
            current = ((current * (period - 1)) + value) / period
        return round(current, 8)

    def cluster_levels(levels: list[float], tolerance: float = 0.02) -> list[dict[str, JSONValue]]:
        if not levels:
            return []
        sorted_levels = sorted(levels)
        clusters: list[list[float]] = [[sorted_levels[0]]]
        for level in sorted_levels[1:]:
            if clusters[-1][-1] and (level / clusters[-1][-1]) - 1 < tolerance:
                clusters[-1].append(level)
            else:
                clusters.append([level])
        return [
            {"price": round(sum(cluster) / len(cluster), 8), "touches": len(cluster)}
            for cluster in clusters
            if len(cluster) >= 2
        ]

    ema_20 = ema(closes, 20)
    ema_50 = ema(closes, 50)
    ema_200 = ema(closes, 200) if len(closes) >= 200 else None
    rsi_14 = rsi(closes, 14)
    atr_14 = atr(highs, lows, closes, 14)

    pivots_high: list[float] = []
    pivots_low: list[float] = []
    for index in range(2, len(candles) - 2):
        high = highs[index]
        low = lows[index]
        if high > highs[index - 1] and high > highs[index - 2] and high > highs[index + 1] and high > highs[index + 2]:
            pivots_high.append(high)
        if low < lows[index - 1] and low < lows[index - 2] and low < lows[index + 1] and low < lows[index + 2]:
            pivots_low.append(low)

    current_price = closes[-1]
    resistance_levels = sorted(
        [level for level in cluster_levels(pivots_high) if float(level["price"]) > current_price],
        key=lambda item: float(item["price"]),
    )[:5]
    support_levels = sorted(
        [level for level in cluster_levels(pivots_low) if float(level["price"]) < current_price],
        key=lambda item: -float(item["price"]),
    )[:5]

    price_min = min(lows)
    price_max = max(highs)
    bucket_total = 20
    bucket_size = (price_max - price_min) / bucket_total if price_max > price_min else 0.0
    volume_at_price = [0.0] * bucket_total
    if bucket_size > 0:
        for candle, volume in zip(candles, volumes):
            start_bucket = int((float(candle["low"]) - price_min) / bucket_size)
            end_bucket = int((float(candle["high"]) - price_min) / bucket_size)
            start_bucket = max(0, min(start_bucket, bucket_total - 1))
            end_bucket = max(0, min(end_bucket, bucket_total - 1))
            bucket_span = max(1, end_bucket - start_bucket + 1)
            volume_per_bucket = volume / bucket_span
            for bucket in range(start_bucket, end_bucket + 1):
                volume_at_price[bucket] += volume_per_bucket
    poc_bucket = volume_at_price.index(max(volume_at_price)) if volume_at_price else 0
    poc_price = price_min + ((poc_bucket + 0.5) * bucket_size) if bucket_size > 0 else current_price

    trend = "neutral"
    if ema_20 is not None and ema_50 is not None:
        if current_price > ema_20 > ema_50:
            trend = "uptrend"
        elif current_price < ema_20 < ema_50:
            trend = "downtrend"
        elif current_price > ema_50 and ema_20 < ema_50:
            trend = "recovery"
        elif current_price < ema_50 and ema_20 > ema_50:
            trend = "weakening"

    atr_pct = round((atr_14 / current_price) * 100, 2) if atr_14 and current_price else None

    return {
        "symbol": symbol,
        "interval": interval,
        "current_price": current_price,
        "period_high": klines_result["period_high"],
        "period_low": klines_result["period_low"],
        "indicators": {
            "ema_20": ema_20,
            "ema_50": ema_50,
            "ema_200": ema_200,
            "rsi_14": rsi_14,
            "atr_14": atr_14,
            "atr_pct": atr_pct,
        },
        "trend": trend,
        "support_levels": support_levels,
        "resistance_levels": resistance_levels,
        "point_of_control": round(poc_price, 8),
        "volume_profile": {
            "avg_volume": round(sum(volumes) / len(volumes), 2) if volumes else None,
            "bucket_count": bucket_total,
        },
        "sources": [
            _source(
                symbol,
                supports=f"Binance candles and derived technical levels for {symbol} on the {interval} timeframe.",
            )
        ],
    }


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            name="get_klines",
            description="Get spot OHLCV candles for a Binance trading pair. Use for multi-timeframe chart context and recent price structure.",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Binance spot pair such as BTCUSDT or LINKUSDT."},
                    "interval": {
                        "type": "string",
                        "description": "Candle interval.",
                        "enum": ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"],
                        "default": "4h",
                    },
                    "limit": {"type": "integer", "description": "Number of candles to fetch (max 500).", "default": 200},
                },
                "required": ["symbol"],
            },
        ),
        get_klines,
    )

    registry.register(
        ToolDefinition(
            name="get_orderbook_depth",
            description="Get current Binance spot orderbook depth, spread, and nearby liquidity walls for a trading pair.",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Binance spot pair such as BTCUSDT or LINKUSDT."},
                    "limit": {"type": "integer", "description": "Depth levels to fetch (max 500).", "default": 100},
                },
                "required": ["symbol"],
            },
        ),
        get_orderbook_depth,
    )

    registry.register(
        ToolDefinition(
            name="compute_technical_levels",
            description="Compute EMAs, RSI, ATR, support, resistance, and point of control from Binance candles for a trading pair.",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Binance spot pair such as BTCUSDT or LINKUSDT."},
                    "interval": {
                        "type": "string",
                        "description": "Candle interval used for the derived technical levels.",
                        "enum": ["1h", "4h", "1d", "3d", "1w"],
                        "default": "4h",
                    },
                },
                "required": ["symbol"],
            },
        ),
        compute_technical_levels,
    )
