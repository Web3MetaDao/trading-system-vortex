from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class SignalDecision:
    symbol: str
    grade: str
    side: str
    reason: str
    score: int = 0
    setup: str = "none"
    explain: dict = field(default_factory=dict)


class SignalEngineV2:
    """
    Optimized Signal Engine (V2)
    - Vectorized indicator calculations using pandas
    - Added ATR-based volatility filtering
    - Refined Trend following logic
    """

    def evaluate(self, symbol: str, market_state: str, context: dict) -> SignalDecision:
        snapshot = context.get("snapshot")
        strategy = context.get("strategy", {})
        params = strategy.get("signal_params", {})

        if not snapshot or not snapshot.klines or len(snapshot.klines) < 30:
            return SignalDecision(symbol, "C", "WAIT", "Insufficient history")

        # Convert klines to DataFrame for vectorized calculations
        df = pd.DataFrame(snapshot.klines)
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)

        # 1. ATR Calculation (Volatility Filter)
        atr_period = 14
        df["tr"] = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                abs(df["high"] - df["close"].shift(1)), abs(df["low"] - df["close"].shift(1))
            ),
        )
        atr = df["tr"].rolling(window=atr_period).mean().iloc[-1]
        price = float(snapshot.price)

        # 2. Vectorized EMAs
        ema_fast_period = int(params.get("ema_fast_period", 20))
        ema_slow_period = int(params.get("ema_slow_period", 50))
        df["ema_fast"] = df["close"].ewm(span=ema_fast_period, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=ema_slow_period, adjust=False).mean()

        ema_fast = df["ema_fast"].iloc[-1]
        ema_slow = df["ema_slow"].iloc[-1]

        # 3. MACD Calculation
        df["ema_12"] = df["close"].ewm(span=12, adjust=False).mean()
        df["ema_26"] = df["close"].ewm(span=26, adjust=False).mean()
        df["macd"] = df["ema_12"] - df["ema_26"]
        df["signal_line"] = df["macd"].ewm(span=9, adjust=False).mean()
        macd = df["macd"].iloc[-1]
        signal_line = df["signal_line"].iloc[-1]

        # 4. RSI Calculation
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df["rsi"] = 100 - (100 / (1 + rs))
        rsi = df["rsi"].iloc[-1]

        # 5. Scoring Logic
        score = 0
        reasons = []

        # Trend Filter: EMA Alignment
        if ema_fast > ema_slow:
            score += 2
            reasons.append("EMA Bullish Alignment")
        elif ema_fast < ema_slow:
            score -= 2
            reasons.append("EMA Bearish Alignment")

        # MACD Filter
        if macd > signal_line and macd > 0:
            score += 2
            reasons.append("MACD Bullish Cross")
        elif macd < signal_line and macd < 0:
            score -= 2
            reasons.append("MACD Bearish Cross")

        # Volatility Filter: Don't trade if ATR is too low (choppy)
        # [FIX] 使用当前实时价格（price）而非历史均价（avg_price）计算 ATR%，更准确反映当前波动性
        atr_pct = (atr / price) * 100 if price > 0 else 0.0
        if atr_pct < 0.5:
            score -= 1
            reasons.append(f"Low Volatility (ATR {atr_pct:.2f}%)")

        # Momentum Filter
        change_24h = float(snapshot.change_24h_pct or 0.0)
        if change_24h > 3.0:
            score += 2
            reasons.append(f"Strong Momentum ({change_24h:.1f}%)")

        # RSI Overbought/Oversold Filter
        if rsi > 70:
            score -= 2
            reasons.append(f"RSI Overbought ({rsi:.1f})")
        elif rsi < 30:
            score += 2
            reasons.append(f"RSI Oversold ({rsi:.1f})")

        # Final Grading
        grade = "C"
        if score >= 6:
            grade = "A"
        elif score >= 4:
            grade = "B"

        side = "LONG" if score > 0 else "SHORT" if score < 0 else "WAIT"

        return SignalDecision(
            symbol=symbol,
            grade=grade,
            side=side,
            reason=", ".join(reasons),
            score=score,
            explain={"atr_pct": atr_pct, "ema_diff": ema_fast - ema_slow},
        )
