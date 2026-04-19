import sys
import unittest
from datetime import UTC, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from market_data import MarketSnapshot  # noqa: E402
from risk_engine import RiskEngine  # noqa: E402
from signal_engine import SignalEngine  # noqa: E402


class _PortfolioStub:
    def __init__(self, closed_positions):
        self.state = type(
            "State", (), {"closed_positions": closed_positions, "cash_usdt": 1000.0}
        )()

    def has_open_position(self, symbol: str) -> bool:
        return False

    def open_position_count(self) -> int:
        return 0

    def open_position_count_for_grade(self, signal_grade: str) -> int:
        return 0

    def total_exposure_usdt(self) -> float:
        return 0.0


class SignalRiskRulesTests(unittest.TestCase):
    def test_breakout_setup_detected(self):
        klines = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5},
            {"open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0},
            {"open": 101.0, "high": 103.0, "low": 100.5, "close": 102.0},
            {"open": 102.0, "high": 103.5, "low": 101.5, "close": 103.0},
            {"open": 103.0, "high": 104.0, "low": 102.5, "close": 104.5},
        ]
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=104.5,
            volume=1_000_000,
            change_24h_pct=2.5,
            quote_volume=800_000_000,
            high_24h=104.5,
            low_24h=99.0,
            open_price=100.0,
            klines=klines,
            source="test",
        )
        strategy = {
            "signal_levels": {
                "A": {"enabled": True, "min_score": 5},
                "B": {"enabled": True, "min_score": 3},
            },
            "signal_params": {"recent_window": 3, "ema_fast_period": 3, "ema_slow_period": 4},
            "setup_filters": {
                "require_setup_for_buy": True,
                "breakout": {
                    "enabled": True,
                    "lookback_bars": 3,
                    "require_market_states": ["S1", "S2"],
                },
                "pullback": {"enabled": False},
                "reclaim": {"enabled": False},
            },
        }
        engine = SignalEngine()
        decision = engine.evaluate("BTCUSDT", "S1", {"snapshot": snapshot, "strategy": strategy})
        self.assertEqual(decision.setup, "breakout")

    def test_vwap_dev_adds_breakout_component_when_enabled(self):
        klines = [
            {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.0, "volume": 1000},
            {"open": 100.0, "high": 101.5, "low": 99.8, "close": 100.5, "volume": 1000},
            {"open": 100.5, "high": 102.0, "low": 100.2, "close": 101.0, "volume": 1000},
            {"open": 101.0, "high": 103.0, "low": 100.8, "close": 102.0, "volume": 1000},
            {"open": 102.0, "high": 104.0, "low": 101.8, "close": 103.2, "volume": 1000},
            {"open": 103.2, "high": 105.5, "low": 103.0, "close": 105.0, "volume": 1000},
        ]
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=105.0,
            volume=1_200_000,
            change_24h_pct=3.0,
            quote_volume=900_000_000,
            high_24h=105.5,
            low_24h=99.5,
            open_price=100.0,
            klines=klines,
            source="test",
        )
        strategy = {
            "signal_levels": {
                "A": {"enabled": True, "min_score": 5},
                "B": {"enabled": True, "min_score": 3},
            },
            "signal_params": {"recent_window": 4, "ema_fast_period": 3, "ema_slow_period": 5},
            "setup_filters": {
                "require_setup_for_buy": True,
                "breakout": {
                    "enabled": True,
                    "lookback_bars": 4,
                    "require_market_states": ["S1", "S2"],
                },
                "pullback": {"enabled": False},
                "reclaim": {"enabled": False},
            },
            "feature_flags": {"use_vwap_dev": True},
            "signal_features": {
                "vwap_dev": {
                    "enabled": True,
                    "lookback_bars": 6,
                    "extreme_zscore": 4.0,
                    "mean_revert_zscore": 1.5,
                    "breakout_zscore": 0.2,
                    "score_bonus_reclaim": 1,
                    "score_bonus_breakout": 1,
                    "score_penalty_exhaustion": 1,
                }
            },
        }
        decision = SignalEngine().evaluate(
            "BTCUSDT", "S1", {"snapshot": snapshot, "strategy": strategy}
        )
        self.assertEqual(decision.setup, "breakout")
        self.assertTrue(
            any(
                item.get("key") == "vwap_dev"
                for item in decision.explain.get("score_components", [])
            )
        )
        self.assertTrue(decision.explain.get("metrics", {}).get("vwap_dev", {}).get("enabled"))

    def test_trade_gate_blocks_daily_stop_loss(self):
        engine = RiskEngine(
            {"capital_usdt": 100.0, "daily_stop_loss_pct": 3.0, "consecutive_loss_pause": 3}
        )
        today_iso = (
            datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        portfolio = _PortfolioStub(
            [
                {"realized_pnl_usdt": -2.0, "closed_at": today_iso},
                {"realized_pnl_usdt": -1.5, "closed_at": today_iso},
            ]
        )
        decision = engine.trade_gate(portfolio)
        self.assertFalse(decision.approved)
        self.assertIn("Daily stop-loss reached", decision.reason)

    def test_intermarket_risk_on_adds_score_component(self):
        klines = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.2},
            {"open": 100.2, "high": 101.2, "low": 99.8, "close": 100.6},
            {"open": 100.6, "high": 101.8, "low": 100.2, "close": 101.2},
            {"open": 101.2, "high": 102.5, "low": 100.8, "close": 102.0},
            {"open": 102.0, "high": 103.8, "low": 101.8, "close": 103.4},
        ]
        snapshot = MarketSnapshot(
            symbol="ETHUSDT",
            price=103.4,
            volume=1_000_000,
            change_24h_pct=2.2,
            quote_volume=850_000_000,
            high_24h=103.8,
            low_24h=99.0,
            open_price=100.0,
            klines=klines,
            source="test",
        )
        benchmark_snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=62000.0,
            volume=1_000_000,
            change_24h_pct=1.2,
            quote_volume=850_000_000,
            high_24h=62100.0,
            low_24h=60000.0,
            open_price=60500.0,
            klines=klines,
            source="test",
        )
        strategy = {
            "signal_levels": {
                "A": {"enabled": True, "min_score": 5},
                "B": {"enabled": True, "min_score": 3},
            },
            "signal_params": {"recent_window": 3, "ema_fast_period": 3, "ema_slow_period": 4},
            "setup_filters": {
                "require_setup_for_buy": True,
                "breakout": {"enabled": True, "lookback_bars": 3},
                "pullback": {"enabled": False},
                "reclaim": {"enabled": False},
            },
            "feature_flags": {"use_intermarket_filter": True},
            "macro_filters": {
                "intermarket": {
                    "enabled": True,
                    "btc_vs_nq_positive_min": 0.2,
                    "btc_vs_dxy_negative_max": -0.2,
                    "score_bonus_risk_on": 1,
                    "score_penalty_risk_off": 1,
                }
            },
        }
        decision = SignalEngine().evaluate(
            "ETHUSDT",
            "S1",
            {
                "snapshot": snapshot,
                "strategy": strategy,
                "benchmark_snapshot": benchmark_snapshot,
                "intermarket": {"dxy_change_24h_pct": -0.8, "nq_change_24h_pct": 0.4},
            },
        )
        self.assertTrue(
            any(
                item.get("key") == "intermarket" and item.get("delta", 0) > 0
                for item in decision.explain.get("score_components", [])
            )
        )

    def test_intermarket_risk_off_adds_penalty_component(self):
        klines = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.2},
            {"open": 100.2, "high": 101.2, "low": 99.8, "close": 100.6},
            {"open": 100.6, "high": 101.8, "low": 100.2, "close": 101.2},
            {"open": 101.2, "high": 102.5, "low": 100.8, "close": 102.0},
            {"open": 102.0, "high": 103.8, "low": 101.8, "close": 103.4},
        ]
        snapshot = MarketSnapshot(
            symbol="ETHUSDT",
            price=103.4,
            volume=1_000_000,
            change_24h_pct=0.5,
            quote_volume=850_000_000,
            high_24h=103.8,
            low_24h=99.0,
            open_price=100.0,
            klines=klines,
            source="test",
        )
        benchmark_snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            price=62000.0,
            volume=1_000_000,
            change_24h_pct=-1.0,
            quote_volume=850_000_000,
            high_24h=62100.0,
            low_24h=60000.0,
            open_price=60500.0,
            klines=klines,
            source="test",
        )
        strategy = {
            "signal_levels": {
                "A": {"enabled": True, "min_score": 5},
                "B": {"enabled": True, "min_score": 3},
            },
            "signal_params": {"recent_window": 3, "ema_fast_period": 3, "ema_slow_period": 4},
            "setup_filters": {
                "require_setup_for_buy": True,
                "breakout": {"enabled": True, "lookback_bars": 3},
                "pullback": {"enabled": False},
                "reclaim": {"enabled": False},
            },
            "feature_flags": {"use_intermarket_filter": True},
            "macro_filters": {
                "intermarket": {
                    "enabled": True,
                    "btc_vs_nq_positive_min": 0.2,
                    "btc_vs_dxy_negative_max": -0.2,
                    "score_bonus_risk_on": 1,
                    "score_penalty_risk_off": 2,
                }
            },
        }
        decision = SignalEngine().evaluate(
            "ETHUSDT",
            "S1",
            {
                "snapshot": snapshot,
                "strategy": strategy,
                "benchmark_snapshot": benchmark_snapshot,
                "intermarket": {"dxy_change_24h_pct": 0.8, "nq_change_24h_pct": 0.6},
            },
        )
        self.assertTrue(
            any(
                item.get("key") == "intermarket" and item.get("delta", 0) < 0
                for item in decision.explain.get("score_components", [])
            )
        )

    def test_oi_change_trend_confirm_adds_component(self):
        klines = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.2},
            {"open": 100.2, "high": 101.2, "low": 99.8, "close": 100.6},
            {"open": 100.6, "high": 101.8, "low": 100.2, "close": 101.2},
            {"open": 101.2, "high": 102.5, "low": 100.8, "close": 102.0},
            {"open": 102.0, "high": 103.8, "low": 101.8, "close": 103.4},
        ]
        snapshot = MarketSnapshot(
            symbol="ETHUSDT",
            price=103.4,
            volume=1_000_000,
            change_24h_pct=1.6,
            quote_volume=850_000_000,
            high_24h=103.8,
            low_24h=99.0,
            open_price=100.0,
            klines=klines,
            source="test",
        )
        strategy = {
            "signal_levels": {
                "A": {"enabled": True, "min_score": 5},
                "B": {"enabled": True, "min_score": 3},
            },
            "signal_params": {"recent_window": 3, "ema_fast_period": 3, "ema_slow_period": 4},
            "setup_filters": {
                "require_setup_for_buy": True,
                "breakout": {"enabled": True, "lookback_bars": 3},
                "pullback": {"enabled": False},
                "reclaim": {"enabled": False},
            },
            "feature_flags": {"use_oi_change": True},
            "derivatives_filters": {
                "oi_change": {
                    "enabled": True,
                    "strong_build_up_pct": 5.0,
                    "weak_build_up_pct": 2.0,
                    "score_bonus_trend_confirm": 1,
                    "score_penalty_squeeze_risk": 1,
                }
            },
        }
        decision = SignalEngine().evaluate(
            "ETHUSDT",
            "S1",
            {"snapshot": snapshot, "strategy": strategy, "derivatives": {"oi_change_pct": 3.2}},
        )
        self.assertTrue(
            any(
                item.get("key") == "oi_change" and item.get("delta", 0) > 0
                for item in decision.explain.get("score_components", [])
            )
        )

    def test_oi_change_downtrend_build_up_adds_penalty(self):
        klines = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.2},
            {"open": 100.2, "high": 101.2, "low": 99.8, "close": 100.6},
            {"open": 100.6, "high": 101.8, "low": 100.2, "close": 101.2},
            {"open": 101.2, "high": 102.5, "low": 100.8, "close": 102.0},
            {"open": 102.0, "high": 103.8, "low": 101.8, "close": 103.4},
        ]
        snapshot = MarketSnapshot(
            symbol="ETHUSDT",
            price=103.4,
            volume=1_000_000,
            change_24h_pct=-1.2,
            quote_volume=850_000_000,
            high_24h=103.8,
            low_24h=99.0,
            open_price=100.0,
            klines=klines,
            source="test",
        )
        strategy = {
            "signal_levels": {
                "A": {"enabled": True, "min_score": 5},
                "B": {"enabled": True, "min_score": 3},
            },
            "signal_params": {"recent_window": 3, "ema_fast_period": 3, "ema_slow_period": 4},
            "setup_filters": {
                "require_setup_for_buy": True,
                "breakout": {"enabled": True, "lookback_bars": 3},
                "pullback": {"enabled": False},
                "reclaim": {"enabled": False},
            },
            "feature_flags": {"use_oi_change": True},
            "derivatives_filters": {
                "oi_change": {
                    "enabled": True,
                    "strong_build_up_pct": 5.0,
                    "weak_build_up_pct": 2.0,
                    "score_bonus_trend_confirm": 1,
                    "score_penalty_squeeze_risk": 2,
                }
            },
        }
        decision = SignalEngine().evaluate(
            "ETHUSDT",
            "S1",
            {"snapshot": snapshot, "strategy": strategy, "derivatives": {"oi_change_pct": 6.0}},
        )
        self.assertTrue(
            any(
                item.get("key") == "oi_change" and item.get("delta", 0) < 0
                for item in decision.explain.get("score_components", [])
            )
        )

    def test_funding_shift_extreme_positive_adds_penalty(self):
        klines = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.2},
            {"open": 100.2, "high": 101.2, "low": 99.8, "close": 100.6},
            {"open": 100.6, "high": 101.8, "low": 100.2, "close": 101.2},
            {"open": 101.2, "high": 102.5, "low": 100.8, "close": 102.0},
            {"open": 102.0, "high": 103.8, "low": 101.8, "close": 103.4},
        ]
        snapshot = MarketSnapshot(
            symbol="ETHUSDT",
            price=103.4,
            volume=1_000_000,
            change_24h_pct=1.0,
            quote_volume=850_000_000,
            high_24h=103.8,
            low_24h=99.0,
            open_price=100.0,
            klines=klines,
            source="test",
        )
        strategy = {
            "signal_levels": {
                "A": {"enabled": True, "min_score": 5},
                "B": {"enabled": True, "min_score": 3},
            },
            "signal_params": {"recent_window": 3, "ema_fast_period": 3, "ema_slow_period": 4},
            "setup_filters": {
                "require_setup_for_buy": True,
                "breakout": {"enabled": True, "lookback_bars": 3},
                "pullback": {"enabled": False},
                "reclaim": {"enabled": False},
            },
            "feature_flags": {"use_funding_shift": True},
            "derivatives_filters": {
                "funding_shift": {
                    "enabled": True,
                    "extreme_positive_threshold": 0.03,
                    "extreme_negative_threshold": -0.03,
                    "score_bonus_contrarian": 1,
                    "score_penalty_crowded": 2,
                }
            },
        }
        decision = SignalEngine().evaluate(
            "ETHUSDT",
            "S1",
            {"snapshot": snapshot, "strategy": strategy, "derivatives": {"funding_rate": 0.05}},
        )
        self.assertTrue(
            any(
                item.get("key") == "funding_shift" and item.get("delta", 0) < 0
                for item in decision.explain.get("score_components", [])
            )
        )

    def test_funding_shift_extreme_negative_adds_bonus(self):
        klines = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.2},
            {"open": 100.2, "high": 101.2, "low": 99.8, "close": 100.6},
            {"open": 100.6, "high": 101.8, "low": 100.2, "close": 101.2},
            {"open": 101.2, "high": 102.5, "low": 100.8, "close": 102.0},
            {"open": 102.0, "high": 103.8, "low": 101.8, "close": 103.4},
        ]
        snapshot = MarketSnapshot(
            symbol="ETHUSDT",
            price=103.4,
            volume=1_000_000,
            change_24h_pct=1.0,
            quote_volume=850_000_000,
            high_24h=103.8,
            low_24h=99.0,
            open_price=100.0,
            klines=klines,
            source="test",
        )
        strategy = {
            "signal_levels": {
                "A": {"enabled": True, "min_score": 5},
                "B": {"enabled": True, "min_score": 3},
            },
            "signal_params": {"recent_window": 3, "ema_fast_period": 3, "ema_slow_period": 4},
            "setup_filters": {
                "require_setup_for_buy": True,
                "breakout": {"enabled": True, "lookback_bars": 3},
                "pullback": {"enabled": False},
                "reclaim": {"enabled": False},
            },
            "feature_flags": {"use_funding_shift": True},
            "derivatives_filters": {
                "funding_shift": {
                    "enabled": True,
                    "extreme_positive_threshold": 0.03,
                    "extreme_negative_threshold": -0.03,
                    "score_bonus_contrarian": 2,
                    "score_penalty_crowded": 1,
                }
            },
        }
        decision = SignalEngine().evaluate(
            "ETHUSDT",
            "S1",
            {"snapshot": snapshot, "strategy": strategy, "derivatives": {"funding_rate": -0.05}},
        )
        self.assertTrue(
            any(
                item.get("key") == "funding_shift" and item.get("delta", 0) > 0
                for item in decision.explain.get("score_components", [])
            )
        )

    def test_trade_gate_blocks_consecutive_losses(self):
        engine = RiskEngine(
            {"capital_usdt": 100.0, "daily_stop_loss_pct": 0.0, "consecutive_loss_pause": 3}
        )
        portfolio = _PortfolioStub(
            [
                {"realized_pnl_pct": -1.0},
                {"realized_pnl_pct": -0.5},
                {"realized_pnl_pct": -2.0},
            ]
        )
        decision = engine.trade_gate(portfolio)
        self.assertFalse(decision.approved)
        self.assertIn("Consecutive loss pause active", decision.reason)


if __name__ == "__main__":
    unittest.main()
