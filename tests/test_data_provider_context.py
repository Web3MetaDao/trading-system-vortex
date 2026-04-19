import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backtest import build_snapshot  # noqa: E402
from data_provider import UnifiedDataProvider  # noqa: E402
from market_data import MarketSnapshot  # noqa: E402
from risk_engine import RiskEngine  # noqa: E402
from signal_engine import SignalEngine  # noqa: E402


class _FakeMarketClient:
    def fetch_klines(self, symbol: str, interval: str = "1h", limit: int = 120):
        base = 100.0 if symbol.upper() == "BTCUSDT" else 50.0
        candles = []
        for i in range(6):
            price = base + i
            candles.append(
                {
                    "open_time": float(1000 + i),
                    "open": price - 0.5,
                    "high": price + 0.5,
                    "low": price - 1.0,
                    "close": price,
                    "volume": 1000.0 + i,
                    "close_time": float(2000 + i),
                    "quote_volume": 50000.0 + i,
                }
            )
        return candles


class _FakeDerivativesClient:
    def fetch_symbol_metrics(self, symbol: str):
        if symbol.upper() == "BTCUSDT":
            return {"symbol": "BTCUSDT", "oi_change_pct": 2.5, "funding_rate": 0.01, "status": "ok"}
        return {
            "symbol": symbol.upper(),
            "oi_change_pct": 1.0,
            "funding_rate": -0.01,
            "status": "ok",
        }


class _FakeIntermarketClient:
    def fetch_context(self, benchmark_snapshot=None):
        return {
            "btc_change_24h_pct": 1.2,
            "eth_change_24h_pct": 1.0,
            "nq_change_24h_pct": 0.4,
            "dxy_change_24h_pct": -0.5,
            "status": "ok",
        }


class DataProviderContextTests(unittest.TestCase):
    def test_build_context_includes_derivatives_and_intermarket(self):
        provider = UnifiedDataProvider(
            _FakeMarketClient(),
            derivatives_client=_FakeDerivativesClient(),
            intermarket_client=_FakeIntermarketClient(),
        )
        context = provider.build_context(
            benchmark_symbol="BTCUSDT",
            watchlist=["BTCUSDT", "ETHUSDT"],
            state_interval="4h",
            state_limit=120,
            signal_interval="1h",
            signal_limit=120,
        )
        self.assertIn("BTCUSDT", context.signal_snapshots)
        self.assertIn("ETHUSDT", context.signal_snapshots)
        self.assertIn("BTCUSDT", context.derivatives)
        self.assertIn("ETHUSDT", context.derivatives)
        self.assertEqual(context.intermarket.get("nq_change_24h_pct"), 0.4)
        self.assertEqual(context.derivatives["BTCUSDT"].get("oi_change_pct"), 2.5)
        self.assertEqual(context.data_health.get("status"), "ok")

    def test_build_context_degraded_when_upstream_degraded(self):
        class _DegradedDerivativesClient:
            def fetch_symbol_metrics(self, symbol: str):
                return {
                    "symbol": symbol.upper(),
                    "oi_change_pct": None,
                    "funding_rate": None,
                    "status": "degraded",
                }

        class _DegradedIntermarketClient:
            def fetch_context(self, benchmark_snapshot=None):
                return {
                    "btc_change_24h_pct": None,
                    "eth_change_24h_pct": None,
                    "nq_change_24h_pct": None,
                    "dxy_change_24h_pct": None,
                    "status": "degraded",
                }

        provider = UnifiedDataProvider(
            _FakeMarketClient(),
            derivatives_client=_DegradedDerivativesClient(),
            intermarket_client=_DegradedIntermarketClient(),
        )
        context = provider.build_context(
            benchmark_symbol="BTCUSDT",
            watchlist=["BTCUSDT", "ETHUSDT"],
            state_interval="4h",
            state_limit=120,
            signal_interval="1h",
            signal_limit=120,
        )
        self.assertEqual(context.data_health.get("status"), "degraded")
        self.assertEqual(context.data_health.get("intermarket_status"), "degraded")
        self.assertTrue(context.data_health.get("derivatives_degraded_symbols"))


class DataHealthRiskIntegrationTests(unittest.TestCase):
    def test_risk_gate_blocks_new_positions_when_data_health_degraded(self):
        risk_engine = RiskEngine(
            {"capital_usdt": 100, "risk_per_trade_pct": 1.0, "stop_loss_pct": 2.5}
        )

        class _FakePortfolio:
            def has_open_position(self, symbol):
                return False

            def open_position_count(self):
                return 0

            def total_exposure_usdt(self):
                return 0.0

            @property
            def state(self):
                class FakeState:
                    cash_usdt = 100.0

                return FakeState()

        portfolio = _FakePortfolio()
        data_health_degraded = {
            "status": "degraded",
            "benchmark_status": "ok",
            "intermarket_status": "ok",
            "derivatives_degraded_symbols": ["BTCUSDT"],
        }

        decision = risk_engine.can_open_position(
            portfolio, "BTCUSDT", 10.0, "A", data_health_degraded
        )
        self.assertFalse(decision.approved)
        self.assertIn("Data health degraded", decision.reason)

    def test_risk_gate_allows_when_data_health_ok(self):
        risk_engine = RiskEngine(
            {
                "capital_usdt": 100,
                "risk_per_trade_pct": 1.0,
                "stop_loss_pct": 2.5,
                "max_open_positions": 2,
                "max_total_exposure_pct": 30,
            }
        )

        class _FakePortfolio:
            def has_open_position(self, symbol):
                return False

            def open_position_count(self):
                return 0

            def open_position_count_for_grade(self, grade):
                return 0

            def total_exposure_usdt(self):
                return 0.0

            @property
            def state(self):
                class FakeState:
                    cash_usdt = 100.0

                return FakeState()

        portfolio = _FakePortfolio()
        data_health_ok = {
            "status": "ok",
            "benchmark_status": "ok",
            "intermarket_status": "ok",
            "derivatives_degraded_symbols": [],
        }

        decision = risk_engine.can_open_position(portfolio, "BTCUSDT", 10.0, "A", data_health_ok)
        self.assertTrue(decision.approved)


if __name__ == "__main__":
    unittest.main()
