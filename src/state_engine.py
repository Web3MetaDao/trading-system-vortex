from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StateResult:
    state: str
    reason: str


class StateEngine:
    """Classifies the market into S1-S5 using higher-timeframe benchmark structure."""

    def classify(self, context: dict) -> StateResult:
        snapshot = context.get("benchmark_snapshot")
        strategy = context.get("strategy", {})
        params = strategy.get("state_params", {})

        if snapshot is None or not snapshot.klines:
            return StateResult(state="S3", reason="Missing benchmark klines, fallback to neutral")

        closes = [c["close"] for c in snapshot.klines if c.get("close")]
        highs = [c["high"] for c in snapshot.klines if c.get("high")]
        lows = [c["low"] for c in snapshot.klines if c.get("low")]
        if len(closes) < 5 or not highs or not lows:
            return StateResult(state="S3", reason="Insufficient benchmark kline history")

        ema_fast_period = int(params.get("ema_fast_period", 20))
        ema_slow_period = int(params.get("ema_slow_period", 50))
        trend_up_min_pct = float(params.get("trend_up_min_pct", 2.0))
        trend_strong_min_pct = float(params.get("trend_strong_min_pct", 5.0))
        trend_down_min_pct = float(params.get("trend_down_min_pct", -2.0))
        danger_down_min_pct = float(params.get("danger_down_min_pct", -6.0))
        close_near_high_min = float(params.get("close_near_high_min", 0.68))
        close_near_low_max = float(params.get("close_near_low_max", 0.32))
        high_volatility_min_pct = float(params.get("high_volatility_min_pct", 8.0))
        s5_requires_bearish = bool(params.get("s5_requires_bearish_ema_alignment", True))

        ema_fast = self._ema(closes, ema_fast_period)
        ema_slow = self._ema(closes, ema_slow_period)
        last_close = closes[-1]
        first_close = closes[0]
        trend_pct = ((last_close - first_close) / first_close) * 100 if first_close else 0.0
        recent_high = max(highs[-ema_fast_period:]) if len(highs) >= ema_fast_period else max(highs)
        recent_low = min(lows[-ema_fast_period:]) if len(lows) >= ema_fast_period else min(lows)
        close_position = (
            ((last_close - recent_low) / (recent_high - recent_low))
            if recent_high > recent_low
            else 0.5
        )
        volatility_pct = ((recent_high - recent_low) / last_close) * 100 if last_close else 0.0
        bearish_alignment = last_close < ema_fast < ema_slow
        bullish_alignment = last_close > ema_fast > ema_slow

        if (
            trend_pct <= danger_down_min_pct
            and (bearish_alignment or not s5_requires_bearish)
            and volatility_pct >= high_volatility_min_pct * 0.7
        ):
            return StateResult(
                state="S5",
                reason=(
                    f"Risk-off benchmark: trend {trend_pct:.2f}%, ema_fast {ema_fast:.2f}, "
                    f"ema_slow {ema_slow:.2f}, volatility {volatility_pct:.2f}%"
                ),
            )

        if (
            bearish_alignment
            and trend_pct <= trend_down_min_pct
            and close_position <= close_near_low_max
        ):
            return StateResult(
                state="S4",
                reason=(
                    f"Weak downtrend: trend {trend_pct:.2f}%, ema_fast {ema_fast:.2f}, "
                    f"ema_slow {ema_slow:.2f}, close_pos {close_position:.2f}"
                ),
            )

        if (
            bullish_alignment
            and trend_pct >= trend_strong_min_pct
            and close_position >= close_near_high_min
        ):
            return StateResult(
                state="S1",
                reason=(
                    f"Strong uptrend: trend {trend_pct:.2f}%, ema_fast {ema_fast:.2f}, "
                    f"ema_slow {ema_slow:.2f}, close_pos {close_position:.2f}"
                ),
            )

        if last_close >= ema_fast >= ema_slow and trend_pct >= trend_up_min_pct:
            return StateResult(
                state="S2",
                reason=(
                    f"Constructive trend: trend {trend_pct:.2f}%, ema_fast {ema_fast:.2f}, "
                    f"ema_slow {ema_slow:.2f}"
                ),
            )

        return StateResult(
            state="S3",
            reason=(
                f"Neutral/range: trend {trend_pct:.2f}%, ema_fast {ema_fast:.2f}, "
                f"ema_slow {ema_slow:.2f}, close_pos {close_position:.2f}"
            ),
        )

    def _ema(self, values: list[float], period: int) -> float:
        if not values:
            return 0.0
        period = max(1, min(period, len(values)))
        multiplier = 2 / (period + 1)
        ema = values[0]
        for price in values[1:]:
            ema = (price - ema) * multiplier + ema
        return ema
