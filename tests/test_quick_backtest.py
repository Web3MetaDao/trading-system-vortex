"""
Tests for quick_backtest module
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class QuickBacktestImportTests(unittest.TestCase):
    def test_import_backtest(self):
        try:
            from quick_backtest import BacktestConfig, BacktestMetrics, QuickBacktest

            self.assertTrue(True)
        except ImportError as e:
            self.fail(f"Failed to import QuickBacktest: {e}")


class BacktestConfigTests(unittest.TestCase):
    def test_default_config(self):
        from quick_backtest import BacktestConfig

        config = BacktestConfig()
        self.assertEqual(config.symbols, ["BTCUSDT", "ETHUSDT", "BNBUSDT"])
        self.assertEqual(config.interval, "1h")
        self.assertEqual(config.lookback_bars, 50)
        self.assertEqual(config.initial_capital, 10000.0)
        self.assertEqual(config.position_size_usdt, 100.0)


class BacktestMetricsTests(unittest.TestCase):
    def test_default_metrics(self):
        from quick_backtest import BacktestMetrics

        metrics = BacktestMetrics()
        self.assertEqual(metrics.total_trades, 0)
        self.assertEqual(metrics.winning_trades, 0)
        self.assertEqual(metrics.losing_trades, 0)
        self.assertEqual(metrics.total_pnl, 0.0)
        self.assertEqual(metrics.total_pnl_percent, 0.0)
        self.assertEqual(metrics.max_drawdown, 0.0)
        self.assertEqual(metrics.win_rate, 0.0)


class QuickBacktestTests(unittest.TestCase):
    @patch("quick_backtest.TelegramNotifier")
    @patch("quick_backtest.MarketDataClient")
    @patch("quick_backtest.SignalEngine")
    @patch("quick_backtest.RiskEngine")
    def test_init(self, mock_risk, mock_signal, mock_market, mock_telegram):
        from quick_backtest import BacktestConfig, QuickBacktest

        config = BacktestConfig()
        backtest = QuickBacktest(config)
        self.assertIsNotNone(backtest)
        self.assertEqual(backtest.current_capital, config.initial_capital)
        self.assertEqual(len(backtest.trades), 0)


if __name__ == "__main__":
    unittest.main()
