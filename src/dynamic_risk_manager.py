import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DynamicRiskDecision:
    approved: bool
    position_size_usdt: float
    stop_loss_price: float
    take_profit_price: float
    reason: str


class DynamicRiskManager:
    """
    Vortex Dynamic Risk Manager
    Implements ATR-based stop-loss and trailing take-profit logic.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.atr_multiplier = float(config.get("atr_multiplier", 2.0))
        self.tp_multiplier = float(config.get("tp_multiplier", 3.0))
        self.max_risk_pct = float(config.get("max_risk_pct", 1.5)) / 100.0

    def calculate_dynamic_levels(self, price: float, atr: float, side: str) -> DynamicRiskDecision:
        """Calculate ATR-based stop-loss and take-profit levels"""
        if not atr or atr <= 0:
            return DynamicRiskDecision(False, 0.0, 0.0, 0.0, "Invalid ATR value")

        stop_loss_dist = atr * self.atr_multiplier
        tp_dist = atr * self.tp_multiplier

        if side == "LONG":
            sl_price = price - stop_loss_dist
            tp_price = price + tp_dist
        else:
            sl_price = price + stop_loss_dist
            tp_price = price - tp_dist

        # Sizing based on ATR risk
        capital = float(self.config.get("capital_usdt", 1000.0))
        risk_budget = capital * self.max_risk_pct
        pos_size = risk_budget / (stop_loss_dist / price)

        # Hard cap on size
        max_size = capital * float(self.config.get("max_position_pct", 10.0)) / 100.0
        final_size = min(pos_size, max_size)

        return DynamicRiskDecision(
            True,
            round(final_size, 2),
            round(sl_price, 8),
            round(tp_price, 8),
            f"ATR Risk-based sizing: SL at {sl_price:.2f}, TP at {tp_price:.2f}",
        )

    def update_trailing_stop(
        self, current_price: float, entry_price: float, peak_price: float, atr: float, side: str
    ) -> float | None:
        """Calculate trailing stop based on peak price and ATR"""
        if side == "LONG":
            if current_price < entry_price:
                return None
            # Trail SL behind peak price by 1.5x ATR
            new_sl = peak_price - (atr * 1.5)
            return round(new_sl, 8)
        else:
            if current_price > entry_price:
                return None
            new_sl = peak_price + (atr * 1.5)
            return round(new_sl, 8)


if __name__ == "__main__":
    risk = DynamicRiskManager({"capital_usdt": 1000.0, "max_risk_pct": 1.0})
    decision = risk.calculate_dynamic_levels(76000.0, 500.0, "LONG")
    print(f"Dynamic Risk Test: {decision}")
