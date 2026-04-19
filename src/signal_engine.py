from __future__ import annotations

import logging
from dataclasses import dataclass, field

# 配置日志
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


class SignalEngine:
    """Transforms inputs into A/B/C trade signals using lower-timeframe entry rules."""

    def evaluate(self, symbol: str, market_state: str, context: dict) -> SignalDecision:
        snapshot = context.get("snapshot")
        strategy = context.get("strategy", {})
        signal_levels = strategy.get("signal_levels", {})
        params = strategy.get("signal_params", {})
        setup_filters = strategy.get("setup_filters", {})
        feature_flags = strategy.get("feature_flags", {})
        signal_features = strategy.get("signal_features", {})
        macro_filters = strategy.get("macro_filters", {})
        derivatives_filters = strategy.get("derivatives_filters", {})

        if snapshot is None or snapshot.price is None or not snapshot.klines:
            return SignalDecision(
                symbol=symbol, grade="C", side="WAIT", reason="Missing snapshot/klines"
            )

        closes = [c["close"] for c in snapshot.klines if c.get("close")]
        highs = [c["high"] for c in snapshot.klines if c.get("high")]
        lows = [c["low"] for c in snapshot.klines if c.get("low")]
        opens = [c["open"] for c in snapshot.klines if c.get("open") is not None]
        if len(closes) < 5 or not highs or not lows or not opens:
            return SignalDecision(
                symbol=symbol, grade="C", side="WAIT", reason="Insufficient kline history"
            )

        ema_fast_period = int(params.get("ema_fast_period", 20))
        ema_slow_period = int(params.get("ema_slow_period", 50))
        recent_window = int(params.get("recent_window", 20))
        momentum_strong_min_pct = float(params.get("momentum_strong_min_pct", 2.0))
        momentum_positive_min_pct = float(params.get("momentum_positive_min_pct", 0.8))
        momentum_negative_max_pct = float(params.get("momentum_negative_max_pct", -0.8))
        momentum_weak_max_pct = float(params.get("momentum_weak_max_pct", -2.0))
        close_near_high_min = float(params.get("close_near_high_min", 0.7))
        close_near_low_max = float(params.get("close_near_low_max", 0.3))
        high_quote_volume_min = float(params.get("high_quote_volume_min", 500_000_000))
        excess_intraday_volatility_min_pct = float(
            params.get("excess_intraday_volatility_min_pct", 8.0)
        )
        ema_alignment_bonus = int(params.get("ema_alignment_bonus", 2))
        breakout_bonus = int(params.get("breakout_bonus", 1))
        pullback_bonus = int(params.get("pullback_bonus", 1))
        breakdown_penalty = int(params.get("breakdown_penalty", 2))

        price = float(snapshot.price)
        open_price = float(snapshot.open_price or price)
        quote_volume = float(snapshot.quote_volume or 0.0)
        change_pct = float(snapshot.change_24h_pct or 0.0)
        intraday_range_pct = (
            (((snapshot.high_24h or price) - (snapshot.low_24h or price)) / open_price) * 100
            if open_price
            else 0.0
        )

        ema_fast = self._ema(closes, ema_fast_period)
        ema_slow = self._ema(closes, ema_slow_period)
        window_high = max(highs[-recent_window:]) if len(highs) >= recent_window else max(highs)
        window_low = min(lows[-recent_window:]) if len(lows) >= recent_window else min(lows)
        close_position = (
            ((price - window_low) / (window_high - window_low)) if window_high > window_low else 0.5
        )

        score = 0
        reasons: list[str] = []
        score_components: list[dict] = []

        def add_component(key: str, delta: int, detail: str) -> None:
            nonlocal score
            score += delta
            reasons.append(detail)
            score_components.append({"key": key, "delta": delta, "detail": detail})

        if market_state == "S1":
            add_component("market_state", 2, "market S1 trend support")
        elif market_state == "S2":
            add_component("market_state", 1, "market S2 constructive support")
        elif market_state == "S4":
            add_component("market_state", -2, "market S4 headwind")
        elif market_state == "S5":
            add_component("market_state", -4, "market S5 risk-off")

        if change_pct >= momentum_strong_min_pct:
            add_component("momentum_24h", 2, f"strong 24h momentum {change_pct:.2f}%")
        elif change_pct >= momentum_positive_min_pct:
            add_component("momentum_24h", 1, f"positive 24h momentum {change_pct:.2f}%")
        elif change_pct <= momentum_weak_max_pct:
            add_component("momentum_24h", -2, f"weak 24h momentum {change_pct:.2f}%")
        elif change_pct <= momentum_negative_max_pct:
            add_component("momentum_24h", -1, f"negative 24h momentum {change_pct:.2f}%")

        if close_position >= close_near_high_min:
            add_component(
                "close_position", breakout_bonus, f"trading near recent high ({close_position:.2f})"
            )
        elif close_position <= close_near_low_max:
            add_component(
                "close_position",
                -breakdown_penalty,
                f"trading near recent low ({close_position:.2f})",
            )

        if quote_volume >= high_quote_volume_min:
            add_component("quote_volume", 1, "high quote volume")

        if intraday_range_pct >= excess_intraday_volatility_min_pct:
            add_component(
                "intraday_volatility", -1, f"excess intraday volatility {intraday_range_pct:.2f}%"
            )

        intermarket_delta, intermarket_detail, intermarket_metrics = self._intermarket_component(
            context=context,
            feature_flags=feature_flags,
            macro_filters=macro_filters,
            symbol=symbol,
            symbol_change_pct=change_pct,
        )
        if intermarket_delta != 0:
            add_component("intermarket", intermarket_delta, intermarket_detail)

        oi_delta, oi_detail, oi_metrics = self._oi_change_component(
            context=context,
            feature_flags=feature_flags,
            derivatives_filters=derivatives_filters,
            symbol_change_pct=change_pct,
        )
        if oi_delta != 0:
            add_component("oi_change", oi_delta, oi_detail)

        funding_delta, funding_detail, funding_metrics = self._funding_shift_component(
            context=context,
            feature_flags=feature_flags,
            derivatives_filters=derivatives_filters,
        )
        if funding_delta != 0:
            add_component("funding_shift", funding_delta, funding_detail)

        setup = self._detect_setup(snapshot, market_state, ema_fast, setup_filters)
        if setup != "none":
            reasons.append(f"setup={setup}")

        # ── EMA 对齐加分（ema_fast > ema_slow 时趋势顺势加分）──
        # 原始代码读取了 ema_alignment_bonus / pullback_bonus 但从未使用，此处补全逻辑
        if ema_fast > ema_slow:
            add_component(
                "ema_alignment",
                ema_alignment_bonus,
                f"ema_fast({ema_fast:.2f}) > ema_slow({ema_slow:.2f}): bullish alignment",
            )
        elif ema_fast < ema_slow:
            add_component(
                "ema_alignment",
                -ema_alignment_bonus,
                f"ema_fast({ema_fast:.2f}) < ema_slow({ema_slow:.2f}): bearish alignment",
            )

        # ── Pullback 加分（setup=pullback 时额外奖励）──
        if setup == "pullback":
            add_component(
                "pullback_setup",
                pullback_bonus,
                f"pullback setup confirmed (bonus={pullback_bonus})",
            )

        vwap_cfg = signal_features.get("vwap_dev", {})
        vwap_enabled = bool(feature_flags.get("use_vwap_dev", False)) and bool(
            vwap_cfg.get("enabled", False)
        )
        vwap_metrics = {"enabled": vwap_enabled}
        if vwap_enabled:
            lookback_bars = int(vwap_cfg.get("lookback_bars", 24))
            extreme_zscore = float(vwap_cfg.get("extreme_zscore", 2.0))
            mean_revert_zscore = float(vwap_cfg.get("mean_revert_zscore", 1.5))
            breakout_zscore = float(vwap_cfg.get("breakout_zscore", 0.5))
            score_bonus_reclaim = int(vwap_cfg.get("score_bonus_reclaim", 1))
            score_bonus_breakout = int(vwap_cfg.get("score_bonus_breakout", 1))
            score_penalty_exhaustion = int(vwap_cfg.get("score_penalty_exhaustion", 1))
            vwap_price, zscore = self._vwap_price_and_zscore(snapshot.klines, lookback_bars)
            vwap_metrics.update(
                {
                    "lookback_bars": lookback_bars,
                    "vwap_price": round(vwap_price, 8) if vwap_price is not None else None,
                    "zscore": round(zscore, 6) if zscore is not None else None,
                }
            )
            if zscore is not None:
                abs_z = abs(zscore)
                if abs_z > extreme_zscore:
                    add_component(
                        "vwap_dev",
                        -abs(score_penalty_exhaustion),
                        f"vwap exhaustion z={zscore:.2f}",
                    )
                elif setup == "reclaim" and mean_revert_zscore <= abs_z <= extreme_zscore:
                    add_component(
                        "vwap_dev", score_bonus_reclaim, f"vwap reclaim zone z={zscore:.2f}"
                    )
                elif setup == "breakout" and breakout_zscore <= zscore <= extreme_zscore:
                    add_component(
                        "vwap_dev", score_bonus_breakout, f"vwap breakout support z={zscore:.2f}"
                    )

        min_score_a = int(signal_levels.get("A", {}).get("min_score", 5))
        min_score_b = int(signal_levels.get("B", {}).get("min_score", 3))

        data_health = context.get("data_health") if isinstance(context, dict) else None
        health_status = data_health.get("status", "ok") if isinstance(data_health, dict) else "ok"
        if health_status == "partial":
            score_adjustment_pct = 0.2
            min_score_a = int(min_score_a * (1 + score_adjustment_pct))
            min_score_b = int(min_score_b * (1 + score_adjustment_pct))
            reasons.append(
                f"data_health_partial: thresholds raised by {score_adjustment_pct * 100:.0f}%"
            )

        require_setup = bool(setup_filters.get("require_setup_for_buy", True))
        setup_ok = (setup != "none") or not require_setup

        # ========== 关键风控层：Oracle 宏观过滤 ==========
        # 在 S5 过滤之后，评分评级之前，检查宏观环境
        # [FIX] 增加 feature_flags 开关检查，允许在回测/测试环境中禁用
        oracle_macro_enabled = bool(feature_flags.get("use_oracle_macro_filter", True))
        oracle_snapshot = context.get("oracle_snapshot")
        macro_blocked = False

        if oracle_macro_enabled and oracle_snapshot is not None and not oracle_snapshot.is_trade_permitted:
            # 宏观环境禁止交易（极度恐慌 + 强抛压）
            macro_blocked = True
            reasons.append(
                f"macro_blocked: sentiment={oracle_snapshot.sentiment_score:.2f}, obi={oracle_snapshot.orderbook_imbalance:.2f}"
            )
            logger.warning(
                "[%s] 宏观风控拦截：sentiment=%.2f, obi=%.2f",
                symbol,
                oracle_snapshot.sentiment_score,
                oracle_snapshot.orderbook_imbalance,
            )
        elif oracle_macro_enabled and oracle_snapshot is None:
            # Oracle 不可用时记录警告但不阻断交易（降级运行）
            logger.debug("[%s] oracle_snapshot 不可用，宏观过滤降级跳过", symbol)

        blocked_reason = None
        grade = "C"
        side = "WAIT"

        # 第一优先级：S5 状态过滤
        if market_state == "S5":
            blocked_reason = "market_state_s5"
        # 第二优先级：Oracle 宏观过滤
        elif macro_blocked:
            blocked_reason = "oracle_macro_blocked"
        # 第三优先级：正常评分评级
        elif score >= min_score_a and signal_levels.get("A", {}).get("enabled", True) and setup_ok:
            grade = "A"
            side = "BUY"
        elif score >= min_score_b and signal_levels.get("B", {}).get("enabled", True) and setup_ok:
            grade = "B"
            side = "BUY"
        else:
            if not setup_ok:
                blocked_reason = "missing_required_setup"
                reasons.append("missing_required_setup")
            elif score < min_score_b:
                blocked_reason = "score_below_B_threshold"
            else:
                blocked_reason = "grade_disabled_or_unqualified"

        explain = {
            "market_state": market_state,
            "score": score,
            "score_components": score_components,
            "setup": setup,
            "setup_required": require_setup,
            "setup_ok": setup_ok,
            "grade_thresholds": {"A": min_score_a, "B": min_score_b},
            "blocked_reason": blocked_reason,
            "decision": {"grade": grade, "side": side},
            "snapshot_window": {
                "signal_kline_count": len(snapshot.klines),
                "recent_window": recent_window,
                "ema_fast_period": ema_fast_period,
                "ema_slow_period": ema_slow_period,
            },
            "metrics": {
                "price": round(price, 8),
                "open_price": round(open_price, 8),
                "change_24h_pct": round(change_pct, 4),
                "quote_volume": round(quote_volume, 4),
                "intraday_range_pct": round(intraday_range_pct, 4),
                "ema_fast": round(ema_fast, 8),
                "ema_slow": round(ema_slow, 8),
                "window_high": round(window_high, 8),
                "window_low": round(window_low, 8),
                "close_position": round(close_position, 6),
                "vwap_dev": vwap_metrics,
                "intermarket": intermarket_metrics,
                "oi_change": oi_metrics,
                "funding_shift": funding_metrics,
            },
        }

        return SignalDecision(
            symbol=symbol,
            grade=grade,
            side=side,
            score=score,
            reason="; ".join(reasons) or "No edge",
            setup=setup,
            explain=explain,
        )

    def _detect_setup(
        self, snapshot, market_state: str, ema_fast: float, setup_filters: dict
    ) -> str:
        klines = snapshot.klines
        if len(klines) < 3:
            return "none"

        breakout_cfg = setup_filters.get("breakout", {})
        pullback_cfg = setup_filters.get("pullback", {})
        reclaim_cfg = setup_filters.get("reclaim", {})

        closes = [c["close"] for c in klines if c.get("close") is not None]
        highs = [c["high"] for c in klines if c.get("high") is not None]
        opens = [c["open"] for c in klines if c.get("open") is not None]
        if len(closes) < 3 or len(highs) < 3 or len(opens) < 3:
            return "none"

        last_close = closes[-1]
        prev_close = closes[-2]
        prev_open = opens[-2]
        prev_highs = highs[:-1]

        if breakout_cfg.get("enabled", True):
            lookback_bars = int(breakout_cfg.get("lookback_bars", 20))
            allowed_states = set(breakout_cfg.get("require_market_states", ["S1", "S2"]))
            if market_state in allowed_states and prev_highs:
                lookback = (
                    prev_highs[-lookback_bars:] if len(prev_highs) >= lookback_bars else prev_highs
                )
                breakout_level = max(lookback) if lookback else None
                if (
                    breakout_level is not None
                    and last_close > breakout_level
                    and prev_close <= breakout_level
                ):
                    return "breakout"

        if pullback_cfg.get("enabled", True):
            allowed_states = set(pullback_cfg.get("require_market_states", ["S1", "S2"]))
            if market_state in allowed_states and prev_close <= ema_fast and last_close > ema_fast:
                return "pullback"

        if reclaim_cfg.get("enabled", True):
            if prev_close <= ema_fast and last_close > ema_fast and last_close > prev_open:
                return "reclaim"

        return "none"

    def _ema(self, values: list[float], period: int) -> float:
        if not values:
            return 0.0
        period = max(1, min(period, len(values)))
        multiplier = 2 / (period + 1)
        ema = values[0]
        for price in values[1:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _vwap_price_and_zscore(
        self, klines: list[dict], lookback_bars: int
    ) -> tuple[float | None, float | None]:
        if not klines:
            return None, None
        lookback_bars = max(3, lookback_bars)
        window = klines[-lookback_bars:] if len(klines) >= lookback_bars else klines
        prices: list[float] = []
        weighted_total = 0.0
        volume_total = 0.0
        for item in window:
            close = item.get("close")
            volume = item.get("volume", 0.0)
            if close is None:
                continue
            price = float(close)
            prices.append(price)
            weight = float(volume or 0.0)
            weighted_total += price * weight
            volume_total += weight
        if len(prices) < 3:
            return None, None
        if volume_total > 0:
            vwap_price = weighted_total / volume_total
        else:
            vwap_price = sum(prices) / len(prices)
        variance = sum((p - vwap_price) ** 2 for p in prices) / len(prices)
        stddev = variance**0.5
        if stddev <= 0:
            return vwap_price, 0.0
        last_price = float(prices[-1])
        zscore = (last_price - vwap_price) / stddev
        return vwap_price, zscore

    def _intermarket_component(
        self,
        context: dict,
        feature_flags: dict,
        macro_filters: dict,
        symbol: str,
        symbol_change_pct: float,
    ) -> tuple[int, str, dict]:
        cfg = macro_filters.get("intermarket", {})
        enabled = bool(feature_flags.get("use_intermarket_filter", False)) and bool(
            cfg.get("enabled", False)
        )
        metrics = {
            "enabled": enabled,
            "symbol": symbol.upper(),
            "symbol_change_24h_pct": round(symbol_change_pct, 4),
        }
        if not enabled:
            return 0, "", metrics

        bonus = int(cfg.get("score_bonus_risk_on", 1))
        penalty = int(cfg.get("score_penalty_risk_off", 1))
        btc_vs_nq_positive_min = float(cfg.get("btc_vs_nq_positive_min", 0.2))
        btc_vs_dxy_negative_max = float(cfg.get("btc_vs_dxy_negative_max", -0.2))

        benchmark_snapshot = context.get("benchmark_snapshot")
        intermarket_ctx = context.get("intermarket", {}) or {}
        btc_change = intermarket_ctx.get("btc_change_24h_pct")
        if btc_change is None and benchmark_snapshot is not None:
            btc_change = getattr(benchmark_snapshot, "change_24h_pct", None)
        nq_change = intermarket_ctx.get("nq_change_24h_pct")
        dxy_change = intermarket_ctx.get("dxy_change_24h_pct")

        btc_change = float(btc_change) if btc_change is not None else None
        nq_change = float(nq_change) if nq_change is not None else None
        dxy_change = float(dxy_change) if dxy_change is not None else None

        metrics.update(
            {
                "btc_change_24h_pct": round(btc_change, 4) if btc_change is not None else None,
                "nq_change_24h_pct": round(nq_change, 4) if nq_change is not None else None,
                "dxy_change_24h_pct": round(dxy_change, 4) if dxy_change is not None else None,
            }
        )

        if btc_change is None:
            metrics["status"] = "missing_btc_proxy"
            return 0, "", metrics

        risk_on_votes = 0
        risk_off_votes = 0

        if nq_change is not None:
            spread = btc_change - nq_change
            metrics["btc_minus_nq"] = round(spread, 4)
            if spread >= btc_vs_nq_positive_min:
                risk_on_votes += 1
            elif spread <= -abs(btc_vs_nq_positive_min):
                risk_off_votes += 1

        if dxy_change is not None:
            inverse_spread = btc_change - dxy_change
            metrics["btc_minus_dxy"] = round(inverse_spread, 4)
            if inverse_spread >= abs(btc_vs_dxy_negative_max):
                risk_on_votes += 1
            elif inverse_spread <= btc_vs_dxy_negative_max:
                risk_off_votes += 1

        if risk_on_votes > risk_off_votes and risk_on_votes > 0:
            metrics["status"] = "risk_on"
            return bonus, f"intermarket risk-on votes={risk_on_votes}", metrics
        if risk_off_votes > risk_on_votes and risk_off_votes > 0:
            metrics["status"] = "risk_off"
            return -abs(penalty), f"intermarket risk-off votes={risk_off_votes}", metrics

        metrics["status"] = "neutral"
        return 0, "", metrics

    def _oi_change_component(
        self,
        context: dict,
        feature_flags: dict,
        derivatives_filters: dict,
        symbol_change_pct: float,
    ) -> tuple[int, str, dict]:
        cfg = derivatives_filters.get("oi_change", {})
        enabled = bool(feature_flags.get("use_oi_change", False)) and bool(
            cfg.get("enabled", False)
        )
        metrics = {
            "enabled": enabled,
            "symbol_change_24h_pct": round(symbol_change_pct, 4),
        }
        if not enabled:
            return 0, "", metrics

        oi_ctx = context.get("derivatives", {}) or {}
        oi_change_pct = oi_ctx.get("oi_change_pct")
        oi_change_pct = float(oi_change_pct) if oi_change_pct is not None else None
        metrics["oi_change_pct"] = round(oi_change_pct, 4) if oi_change_pct is not None else None
        if oi_change_pct is None:
            metrics["status"] = "missing_oi"
            return 0, "", metrics

        strong_build_up_pct = float(cfg.get("strong_build_up_pct", 5.0))
        weak_build_up_pct = float(cfg.get("weak_build_up_pct", 2.0))
        score_bonus_trend_confirm = int(cfg.get("score_bonus_trend_confirm", 1))
        score_penalty_squeeze_risk = int(cfg.get("score_penalty_squeeze_risk", 1))

        if symbol_change_pct > 0 and oi_change_pct >= weak_build_up_pct:
            metrics["status"] = "trend_confirm"
            return score_bonus_trend_confirm, f"oi trend confirm ({oi_change_pct:.2f}%)", metrics

        if symbol_change_pct < 0 and oi_change_pct >= strong_build_up_pct:
            metrics["status"] = "squeeze_risk"
            return (
                -abs(score_penalty_squeeze_risk),
                f"oi squeeze risk ({oi_change_pct:.2f}%)",
                metrics,
            )

        metrics["status"] = "neutral"
        return 0, "", metrics

    def _funding_shift_component(
        self, context: dict, feature_flags: dict, derivatives_filters: dict
    ) -> tuple[int, str, dict]:
        cfg = derivatives_filters.get("funding_shift", {})
        enabled = bool(feature_flags.get("use_funding_shift", False)) and bool(
            cfg.get("enabled", False)
        )
        metrics = {"enabled": enabled}
        if not enabled:
            return 0, "", metrics

        derivatives_ctx = context.get("derivatives", {}) or {}
        funding_rate = derivatives_ctx.get("funding_rate")
        funding_rate = float(funding_rate) if funding_rate is not None else None
        metrics["funding_rate"] = round(funding_rate, 6) if funding_rate is not None else None
        if funding_rate is None:
            metrics["status"] = "missing_funding"
            return 0, "", metrics

        extreme_positive_threshold = float(cfg.get("extreme_positive_threshold", 0.03))
        extreme_negative_threshold = float(cfg.get("extreme_negative_threshold", -0.03))
        score_bonus_contrarian = int(cfg.get("score_bonus_contrarian", 1))
        score_penalty_crowded = int(cfg.get("score_penalty_crowded", 1))

        if funding_rate >= extreme_positive_threshold:
            metrics["status"] = "crowded_long"
            return (
                -abs(score_penalty_crowded),
                f"funding crowded long ({funding_rate:.4f})",
                metrics,
            )
        if funding_rate <= extreme_negative_threshold:
            metrics["status"] = "crowded_short"
            return score_bonus_contrarian, f"funding contrarian long ({funding_rate:.4f})", metrics

        metrics["status"] = "neutral"
        return 0, "", metrics
