import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from automated_trading import (  # noqa: E402
    AutomatedTradingLoop,
    AutomatedTradingLoopConfig,
    AutoSymbolRotator,
    LoopMetrics,
    LoopState,
    PositionMonitor,
    PositionMonitorConfig,
    SchedulerConfig,
    SymbolConfig,
    TradingScheduler,
)


class SchedulerConfigTests(unittest.TestCase):
    def test_default_config(self):
        config = SchedulerConfig()
        self.assertEqual(config.check_interval, 5.0)
        self.assertTrue(config.trading_enabled)

    def test_custom_config(self):
        config = SchedulerConfig(check_interval=10.0, max_consecutive_errors=3)
        self.assertEqual(config.check_interval, 10.0)
        self.assertEqual(config.max_consecutive_errors, 3)


class TradingSchedulerTests(unittest.TestCase):
    def test_initial_state(self):
        scheduler = TradingScheduler()
        self.assertFalse(scheduler.is_running)

    def test_register_callback(self):
        scheduler = TradingScheduler()
        callback = MagicMock()
        scheduler.register_callback(callback)
        self.assertIn(callback, scheduler._callbacks)

    def test_unregister_callback(self):
        scheduler = TradingScheduler()
        callback = MagicMock()
        scheduler.register_callback(callback)
        scheduler.unregister_callback(callback)
        self.assertNotIn(callback, scheduler._callbacks)


class SymbolConfigTests(unittest.TestCase):
    def test_default_config(self):
        config = SymbolConfig(symbol="BTCUSDT")
        self.assertEqual(config.symbol, "BTCUSDT")
        self.assertTrue(config.enabled)
        self.assertEqual(config.priority, 0)

    def test_custom_config(self):
        config = SymbolConfig(symbol="ETHUSDT", priority=5, min_signal_score=7.0)
        self.assertEqual(config.priority, 5)
        self.assertEqual(config.min_signal_score, 7.0)


class AutoSymbolRotatorTests(unittest.TestCase):
    def test_add_symbol(self):
        rotator = AutoSymbolRotator()
        rotator.add_symbol("BTCUSDT", priority=1)
        self.assertIn("BTCUSDT", rotator.get_all_symbols())

    def test_remove_symbol(self):
        rotator = AutoSymbolRotator()
        rotator.add_symbol("BTCUSDT")
        result = rotator.remove_symbol("BTCUSDT")
        self.assertTrue(result)
        self.assertNotIn("BTCUSDT", rotator.get_all_symbols())

    def test_enable_disable_symbol(self):
        rotator = AutoSymbolRotator()
        rotator.add_symbol("BTCUSDT")
        rotator.disable_symbol("BTCUSDT")
        self.assertNotIn("BTCUSDT", rotator.get_enabled_symbols())

        rotator.enable_symbol("BTCUSDT")
        self.assertIn("BTCUSDT", rotator.get_enabled_symbols())

    def test_get_next_symbol_round_robin(self):
        rotator = AutoSymbolRotator(["BTCUSDT", "ETHUSDT"])
        rotator.enable_symbol("BTCUSDT")
        rotator.enable_symbol("ETHUSDT")

        symbols = []
        for _ in range(4):
            sym = rotator.get_next_symbol()
            if sym:
                symbols.append(sym)

        self.assertEqual(symbols, ["BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT"])

    def test_priority_ordering(self):
        rotator = AutoSymbolRotator()
        rotator.add_symbol("ETHUSDT", priority=1)
        rotator.add_symbol("BTCUSDT", priority=5)
        rotator.add_symbol("SOLUSDT", priority=3)

        rotator.enable_symbol("BTCUSDT")
        rotator.enable_symbol("ETHUSDT")
        rotator.enable_symbol("SOLUSDT")

        rotator.reset_index()
        first = rotator.get_next_symbol()
        self.assertEqual(first, "BTCUSDT")

    def test_get_symbol_config(self):
        rotator = AutoSymbolRotator()
        rotator.add_symbol("BTCUSDT", priority=5)
        config = rotator.get_symbol_config("BTCUSDT")
        self.assertIsNotNone(config)
        self.assertEqual(config.priority, 5)


class PositionMonitorConfigTests(unittest.TestCase):
    def test_default_config(self):
        config = PositionMonitorConfig()
        self.assertTrue(config.stop_loss_enabled)
        self.assertTrue(config.take_profit_enabled)
        self.assertFalse(config.trailing_stop_enabled)

    def test_trailing_stop_config(self):
        config = PositionMonitorConfig(trailing_stop_enabled=True, trailing_stop_pct=2.0)
        self.assertTrue(config.trailing_stop_enabled)
        self.assertEqual(config.trailing_stop_pct, 2.0)


class PositionMonitorTests(unittest.TestCase):
    def setUp(self):
        self.mock_engine = MagicMock()
        self.mock_engine.mode = "paper"
        self.monitor = PositionMonitor(self.mock_engine)

    def test_initial_state(self):
        self.assertFalse(self.monitor.is_running)

    def test_register_exit_callback(self):
        callback = MagicMock()
        self.monitor.register_exit_callback(callback)
        self.assertIn(callback, self.monitor._position_callbacks)


class LoopMetricsTests(unittest.TestCase):
    def test_default_metrics(self):
        metrics = LoopMetrics()
        self.assertEqual(metrics.loops_completed, 0)
        self.assertEqual(metrics.loops_failed, 0)
        self.assertIsNone(metrics.last_error)

    def test_to_dict(self):
        metrics = LoopMetrics(loops_completed=10, loops_failed=1)
        d = metrics.to_dict()
        self.assertEqual(d["loops_completed"], 10)
        self.assertEqual(d["loops_failed"], 1)


class AutomatedTradingLoopConfigTests(unittest.TestCase):
    def test_default_config(self):
        config = AutomatedTradingLoopConfig()
        self.assertEqual(config.check_interval, 5.0)
        self.assertTrue(config.trading_enabled)
        self.assertTrue(config.pause_on_error)
        self.assertEqual(config.max_consecutive_errors, 5)


class AutomatedTradingLoopTests(unittest.TestCase):
    def setUp(self):
        self.mock_execution = MagicMock()
        self.mock_market = MagicMock()
        self.mock_data_provider = MagicMock()
        self.mock_signal = MagicMock()
        self.mock_risk = MagicMock()

        self.mock_execution.mode = "paper"

        config = AutomatedTradingLoopConfig(
            symbols=["BTCUSDT"],
            check_interval=1.0,
        )

        self.loop = AutomatedTradingLoop(
            execution_engine=self.mock_execution,
            market_data_client=self.mock_market,
            data_provider=self.mock_data_provider,
            signal_engine=self.mock_signal,
            risk_engine=self.mock_risk,
            config=config,
        )

    def test_initial_state(self):
        self.assertEqual(self.loop.state, LoopState.STOPPED)
        self.assertFalse(self.loop.is_running)

    def test_get_status(self):
        status = self.loop.get_status()
        self.assertIn("state", status)
        self.assertIn("metrics", status)
        self.assertIn("symbols", status)
        self.assertEqual(status["state"], "stopped")

    def test_symbol_rotator_integration(self):
        self.loop.symbol_rotator.add_symbol("ETHUSDT")
        self.assertIn("ETHUSDT", self.loop.symbol_rotator.get_all_symbols())

    def test_scheduler_integration(self):
        self.assertFalse(self.loop.scheduler.is_running)

    def test_position_monitor_integration(self):
        self.assertFalse(self.loop.position_monitor.is_running)

    def test_metrics_initialization(self):
        self.assertEqual(self.loop.metrics.loops_completed, 0)
        self.assertEqual(self.loop.metrics.consecutive_errors, 0)


class AutomatedTradingLoopIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.mock_execution = MagicMock()
        self.mock_market = MagicMock()
        self.mock_data_provider = MagicMock()
        self.mock_signal = MagicMock()
        self.mock_risk = MagicMock()

        self.mock_execution.mode = "paper"
        self.mock_market.fetch_snapshot.return_value = None

        config = AutomatedTradingLoopConfig(
            symbols=["BTCUSDT", "ETHUSDT"],
            check_interval=0.1,
        )

        self.loop = AutomatedTradingLoop(
            execution_engine=self.mock_execution,
            market_data_client=self.mock_market,
            data_provider=self.mock_data_provider,
            signal_engine=self.mock_signal,
            risk_engine=self.mock_risk,
            config=config,
        )

    def test_print_status(self):
        self.loop.print_status()
        status = self.loop.get_status()
        self.assertEqual(status["state"], "stopped")

    def test_get_status_includes_all_fields(self):
        status = self.loop.get_status()
        required_fields = [
            "state",
            "is_running",
            "metrics",
            "symbols",
            "enabled_symbols",
            "scheduler_running",
            "position_monitor_running",
            "ws_connected",
        ]
        for field_name in required_fields:
            self.assertIn(field_name, status)


if __name__ == "__main__":
    unittest.main()
