from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

# 配置日志
logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    approved: bool
    position_size_usdt: float
    reason: str


class RiskEngine:
    def __init__(self, config: dict):
        self.config = config
        # 按标的追踪连续亏损，用于精细化风控
        self._consecutive_loss_streak_by_symbol: dict[str, int] = defaultdict(int)

    def size_position(self, signal_grade: str) -> RiskDecision:
        capital = float(self.config.get("capital_usdt", 100))
        risk_pct = float(self.config.get("risk_per_trade_pct", 1.0)) / 100.0
        stop_loss_pct = float(self.config.get("stop_loss_pct", 2.5)) / 100.0
        if stop_loss_pct <= 0:
            stop_loss_pct = 0.025

        grade_multipliers = {
            "A": float(self.config.get("grade_a_risk_multiplier", 1.5)),
            "B": float(self.config.get("grade_b_risk_multiplier", 0.8)),
        }
        hard_caps = {
            "A": float(self.config.get("grade_a_max_position_pct", 15.0)) / 100.0,
            "B": float(self.config.get("grade_b_max_position_pct", 8.0)) / 100.0,
        }

        if signal_grade not in grade_multipliers:
            return RiskDecision(False, 0.0, "C-grade signals are blocked")

        risk_budget_usdt = capital * risk_pct * grade_multipliers[signal_grade]
        stop_based_size_usdt = risk_budget_usdt / stop_loss_pct
        capped_size_usdt = min(stop_based_size_usdt, capital * hard_caps[signal_grade])
        final_size_usdt = round(max(0.0, capped_size_usdt), 2)

        if final_size_usdt <= 0:
            return RiskDecision(False, 0.0, f"{signal_grade}-grade sizing resolved to 0")

        return RiskDecision(
            True,
            final_size_usdt,
            (
                f"{signal_grade}-grade size {final_size_usdt:.2f} USDT "
                f"(risk_budget={risk_budget_usdt:.2f}, stop_loss={stop_loss_pct * 100:.2f}%, cap={hard_caps[signal_grade] * 100:.2f}%)"
            ),
        )

    def can_open_position(
        self,
        portfolio,
        symbol: str,
        requested_size_usdt: float,
        signal_grade: str | None = None,
        data_health: dict | None = None,
    ) -> RiskDecision:
        data_health = data_health or {}
        health_status = data_health.get("status", "ok")

        if health_status == "degraded":
            return RiskDecision(
                False,
                0.0,
                f"Data health degraded: {health_status} - new positions blocked",
            )

        trade_gate = self.trade_gate(portfolio, symbol)
        if not trade_gate.approved:
            return trade_gate

        max_open_positions = int(self.config.get("max_open_positions", 2))
        max_total_exposure_pct = float(self.config.get("max_total_exposure_pct", 30))
        capital = float(self.config.get("capital_usdt", 100))
        max_total_exposure_usdt = capital * (max_total_exposure_pct / 100.0)

        if portfolio.has_open_position(symbol):
            return RiskDecision(False, 0.0, f"Position already exists for {symbol}")
        if portfolio.open_position_count() >= max_open_positions:
            return RiskDecision(False, 0.0, f"Max open positions reached ({max_open_positions})")

        if signal_grade:
            grade_limit_key = f"max_{signal_grade.lower()}_positions"
            grade_limit = self.config.get(grade_limit_key)
            if grade_limit is not None:
                grade_limit = int(grade_limit)
                current_grade_count = portfolio.open_position_count_for_grade(signal_grade)
                if current_grade_count >= grade_limit:
                    return RiskDecision(
                        False, 0.0, f"{signal_grade}-grade position cap reached ({grade_limit})"
                    )

        if portfolio.total_exposure_usdt() + requested_size_usdt > max_total_exposure_usdt:
            return RiskDecision(
                False,
                0.0,
                f"Exposure cap exceeded: {portfolio.total_exposure_usdt() + requested_size_usdt:.2f} > {max_total_exposure_usdt:.2f} USDT",
            )
        if portfolio.state.cash_usdt < requested_size_usdt:
            return RiskDecision(
                False, 0.0, f"Insufficient cash: {portfolio.state.cash_usdt:.2f} USDT"
            )
        return RiskDecision(True, requested_size_usdt, "Portfolio filters passed")

    def trade_gate(
        self, portfolio, symbol: str | None = None, now_utc: datetime | None = None
    ) -> RiskDecision:
        """
        全局交易开关，支持按标的的精细化风控。

        Args:
            portfolio: 投资组合对象
            symbol: 交易标的，用于按标的统计连亏
            now_utc: 当前 UTC 时间

        Returns:
            RiskDecision 对象
        """
        now_utc = now_utc or datetime.now(UTC)
        capital = float(self.config.get("capital_usdt", 100))
        daily_stop_loss_pct = float(self.config.get("daily_stop_loss_pct", 0.0))
        consecutive_loss_pause = int(self.config.get("consecutive_loss_pause", 0))
        closed_positions = list(getattr(portfolio.state, "closed_positions", []))

        if daily_stop_loss_pct > 0:
            today_pnl_usdt = self._today_realized_pnl_usdt(closed_positions, now_utc)
            daily_loss_limit_usdt = capital * (daily_stop_loss_pct / 100.0)
            if today_pnl_usdt <= -abs(daily_loss_limit_usdt):
                return RiskDecision(
                    False,
                    0.0,
                    f"Daily stop-loss reached: {today_pnl_usdt:.4f} USDT <= -{abs(daily_loss_limit_usdt):.4f} USDT",
                )

        if consecutive_loss_pause > 0:
            # 优化：支持按标的统计连亏
            if symbol:
                loss_streak = self._consecutive_loss_streak_by_symbol(closed_positions, symbol)
            else:
                loss_streak = self._consecutive_loss_streak(closed_positions)

            if loss_streak >= consecutive_loss_pause:
                streak_type = f"for {symbol}" if symbol else "overall"
                return RiskDecision(
                    False,
                    0.0,
                    f"Consecutive loss pause active {streak_type}: {loss_streak} losses >= limit {consecutive_loss_pause}",
                )

        return RiskDecision(True, 0.0, "Trade gate passed")

    def position_monitor(self, position: dict, snapshot) -> dict:
        entry_price = float(position.get("entry_price") or 0.0)
        current_price = float(snapshot.price or 0.0)
        stop_loss_pct = float(self.config.get("stop_loss_pct", 2.5))
        take_profit_pct = float(self.config.get("take_profit_pct", 4.5))

        pnl_pct = 0.0
        if entry_price and current_price:
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
            if position.get("side") == "SELL":
                pnl_pct *= -1

        return {
            "symbol": position.get("symbol"),
            "side": position.get("side"),
            "signal_grade": position.get("signal_grade"),
            "entry_price": entry_price,
            "current_price": current_price,
            "pnl_pct": round(pnl_pct, 4),
            "stop_loss_pct": -abs(stop_loss_pct),
            "take_profit_pct": abs(take_profit_pct),
            "distance_to_stop_loss_pct": round(pnl_pct + abs(stop_loss_pct), 4),
            "distance_to_take_profit_pct": round(abs(take_profit_pct) - pnl_pct, 4),
        }

    def exit_reason(self, position: dict, snapshot, market_state: str) -> str | None:
        """
        判断是否应该平仓，包含优化的 EMA 退出逻辑。

        优化内容：
        1. EMA 退出增加缓冲阈值，防止 Whipsaw
        2. 支持结合其他指标确认趋势反转
        """
        entry_price = float(position.get("entry_price") or 0.0)
        peak_pnl_pct = float(position.get("peak_pnl_pct", 0.0))
        if not entry_price or snapshot.price is None:
            return None

        stop_loss_pct = float(self.config.get("stop_loss_pct", 2.5))
        take_profit_pct = float(self.config.get("take_profit_pct", 4.5))
        ema_exit_period = int(self.config.get("ema_exit_period", 20))
        ema_exit_buffer_pct = float(self.config.get("ema_exit_buffer_pct", 0.5))  # 新增缓冲参数

        pnl_pct = ((float(snapshot.price) - entry_price) / entry_price) * 100
        if position.get("side") == "SELL":
            pnl_pct *= -1

        if pnl_pct <= -stop_loss_pct:
            return f"stop_loss hit ({pnl_pct:.2f}%)"
        if pnl_pct >= take_profit_pct:
            return f"take_profit hit ({pnl_pct:.2f}%)"

        if pnl_pct > peak_pnl_pct:
            position["peak_pnl_pct"] = pnl_pct

        # [FIX] 从配置读取，消除硬编码
        trailing_stop_pct = float(self.config.get("trailing_stop_pct", 1.5))
        trailing_stop_activation_pct = float(self.config.get("trailing_stop_activation_pct", 2.0))
        if peak_pnl_pct >= trailing_stop_activation_pct and pnl_pct <= peak_pnl_pct - trailing_stop_pct:
            return f"trailing_stop ({pnl_pct:.2f}%)"

        if market_state == "S5":
            if pnl_pct < 0:
                if pnl_pct <= -2.0:
                    return f"s5_stop ({pnl_pct:.2f}%)"
            elif pnl_pct >= 1.5:
                return f"s5_protect ({pnl_pct:.2f}%)"

        klines = getattr(snapshot, "klines", []) or []
        if len(klines) >= ema_exit_period:
            ema_value = self._calc_ema_from_klines(klines, ema_exit_period)
            if ema_value and pnl_pct > 0:
                # 优化的 EMA 退出逻辑：引入缓冲阈值防止 Whipsaw
                price_below_ema_pct = ((ema_value - snapshot.price) / ema_value) * 100
                if price_below_ema_pct > ema_exit_buffer_pct:
                    return f"ema_exit ({pnl_pct:.2f}%, buffer={ema_exit_buffer_pct}%)"

        return None

    def _calc_ema_from_klines(self, klines: list, period: int) -> float | None:
        closes = [c.get("close") for c in klines[-period:] if c.get("close") is not None]
        if len(closes) < period // 2:
            closes = [c.get("close") for c in klines if c.get("close") is not None]
            if len(closes) < 3:
                return None
        multiplier = 2 / (period + 1)
        ema = closes[0]
        for price in closes[1:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _ema(self, values: list[float], period: int) -> float:
        if not values:
            return 0.0
        period = max(1, min(period, len(values)))
        multiplier = 2 / (period + 1)
        ema = values[0]
        for price in values[1:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _today_realized_pnl_usdt(self, closed_positions: list[dict], now_utc: datetime) -> float:
        today = now_utc.date()
        total = 0.0
        for item in closed_positions:
            closed_date = self._extract_closed_date(item)
            if closed_date is None or closed_date != today:
                continue
            pnl_usdt = item.get("realized_pnl_usdt")
            if pnl_usdt is None:
                pnl_usdt = item.get("pnl_usdt")
            if pnl_usdt is not None:
                total += float(pnl_usdt)
            else:
                pnl_pct = self._position_pnl_pct(item)
                size_usdt = item.get("size_usdt")
                if pnl_pct is not None and size_usdt not in (None, 0):
                    total += float(size_usdt) * (pnl_pct / 100.0)
        return round(total, 4)

    def _consecutive_loss_streak(self, closed_positions: list[dict]) -> int:
        """全局连亏计数"""
        streak = 0
        for item in reversed(closed_positions):
            pnl_pct = self._position_pnl_pct(item)
            if pnl_pct is None:
                break
            if pnl_pct < 0:
                streak += 1
                continue
            break
        return streak

    def _consecutive_loss_streak_by_symbol(self, closed_positions: list[dict], symbol: str) -> int:
        """
        按标的统计连亏次数，用于精细化风控。

        Args:
            closed_positions: 已平仓头寸列表
            symbol: 交易标的

        Returns:
            该标的的连续亏损次数
        """
        streak = 0
        symbol_upper = str(symbol).upper()
        for item in reversed(closed_positions):
            if str(item.get("symbol", "")).upper() != symbol_upper:
                continue
            pnl_pct = self._position_pnl_pct(item)
            if pnl_pct is None:
                break
            if pnl_pct < 0:
                streak += 1
                continue
            break
        return streak

    def _position_pnl_pct(self, item: dict) -> float | None:
        pnl_pct = item.get("realized_pnl_pct")
        if pnl_pct is None:
            pnl_pct = item.get("pnl_pct")
        if pnl_pct is not None:
            return float(pnl_pct)
        entry = item.get("entry_price")
        exit_price = item.get("exit_price")
        if entry in (None, 0) or exit_price in (None, 0):
            return None
        pnl = ((float(exit_price) - float(entry)) / float(entry)) * 100.0
        if str(item.get("side", "")).upper() == "SELL":
            pnl *= -1
        return float(pnl)

    def _extract_closed_date(self, item: dict):
        raw = item.get("closed_at")
        if not raw:
            return None
        text = str(raw).strip()
        if not text:
            return None
        try:
            normalized = text.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).astimezone(UTC).date()
        except ValueError:
            return None
