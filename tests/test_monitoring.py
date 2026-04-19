import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from monitoring import (  # noqa: E402
    Alert,
    AlertEngine,
    AlertRule,
    AlertSeverity,
    AlertType,
    LogEntry,
    LogLevel,
    MetricPoint,
    MetricsCollector,
    MonitoringDashboard,
    StructuredLogger,
)


class StructuredLoggerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.logger = StructuredLogger(
            component="test_logger",
            log_dir=self.temp_dir,
            log_level="DEBUG",
            enable_console=False,
        )

    def tearDown(self):
        StructuredLogger.reset_instance()
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_log_creates_entry(self):
        entry = self.logger.info("Test message", {"key": "value"})
        self.assertIsInstance(entry, LogEntry)
        self.assertEqual(entry.level, "INFO")
        self.assertEqual(entry.message, "Test message")
        self.assertEqual(entry.context, {"key": "value"})
        self.assertIsNotNone(entry.trace_id)

    def test_log_levels(self):
        debug_entry = self.logger.debug("Debug message")
        self.assertEqual(debug_entry.level, "DEBUG")

        info_entry = self.logger.info("Info message")
        self.assertEqual(info_entry.level, "INFO")

        warning_entry = self.logger.warning("Warning message")
        self.assertEqual(warning_entry.level, "WARNING")

        error_entry = self.logger.error("Error message")
        self.assertEqual(error_entry.level, "ERROR")

        critical_entry = self.logger.critical("Critical message")
        self.assertEqual(critical_entry.level, "CRITICAL")

    def test_trace_id_uniqueness(self):
        entry1 = self.logger.info("Message 1")
        entry2 = self.logger.info("Message 2")
        self.assertNotEqual(entry1.trace_id, entry2.trace_id)

    def test_log_entry_to_dict(self):
        entry = self.logger.info("Test", {"foo": "bar"})
        d = entry.to_dict()
        self.assertIn("timestamp", d)
        self.assertIn("level", d)
        self.assertIn("component", d)
        self.assertIn("message", d)
        self.assertIn("context", d)
        self.assertIn("trace_id", d)

    def test_singleton_pattern(self):
        logger1 = StructuredLogger.get_instance(component="singleton_test")
        logger2 = StructuredLogger.get_instance()
        self.assertEqual(logger1, logger2)


class MetricsCollectorTests(unittest.TestCase):
    def setUp(self):
        self.metrics = MetricsCollector()
        MetricsCollector.reset_instance()

    def tearDown(self):
        MetricsCollector.reset_instance()

    def test_record_counter(self):
        point = self.metrics.record_counter("test_counter", 5.0, {"tag1": "val1"})
        self.assertIsInstance(point, MetricPoint)
        self.assertEqual(point.name, "test_counter")
        self.assertEqual(point.value, 5.0)
        self.assertEqual(point.metric_type, "counter")
        self.assertEqual(point.tags, {"tag1": "val1"})

    def test_record_gauge(self):
        point = self.metrics.record_gauge("test_gauge", 42.5)
        self.assertIsInstance(point, MetricPoint)
        self.assertEqual(point.name, "test_gauge")
        self.assertEqual(point.value, 42.5)
        self.assertEqual(point.metric_type, "gauge")

    def test_record_timing(self):
        point = self.metrics.record_timing("test_timing", 150.5)
        self.assertIsInstance(point, MetricPoint)
        self.assertEqual(point.name, "test_timing")
        self.assertEqual(point.value, 150.5)
        self.assertEqual(point.metric_type, "timing")

    def test_counter_accumulates(self):
        self.metrics.record_counter("counter1", 1.0)
        self.metrics.record_counter("counter1", 2.0)
        self.assertEqual(self.metrics.get_counter("counter1"), 3.0)

    def test_get_gauge(self):
        self.metrics.record_gauge("gauge1", 100.0)
        self.assertEqual(self.metrics.get_gauge("gauge1"), 100.0)
        self.assertIsNone(self.metrics.get_gauge("nonexistent"))

    def test_get_all_metrics(self):
        self.metrics.record_counter("c1", 1.0)
        self.metrics.record_gauge("g1", 10.0)
        metrics = self.metrics.get_all_metrics()
        self.assertEqual(len(metrics), 2)

    def test_flush_clears_metrics(self):
        self.metrics.record_counter("c1", 1.0)
        flushed = self.metrics.flush()
        self.assertEqual(len(flushed), 1)
        self.assertEqual(len(self.metrics.get_all_metrics()), 0)

    def test_get_metrics_summary(self):
        self.metrics.record_counter("c1", 1.0)
        self.metrics.record_gauge("g1", 10.0)
        summary = self.metrics.get_metrics_summary()
        self.assertEqual(summary["total_metrics"], 2)
        self.assertIn("c1", summary["counters"])
        self.assertIn("g1", summary["gauges"])


class AlertRuleTests(unittest.TestCase):
    def test_rule_fires_when_condition_true(self):
        rule = AlertRule(
            name="test_rule",
            alert_type=AlertType.API_ERROR,
            condition=lambda ctx: ctx.get("error", False),
        )
        self.assertTrue(rule.should_fire({"error": True}))

    def test_rule_does_not_fire_when_condition_false(self):
        rule = AlertRule(
            name="test_rule",
            alert_type=AlertType.API_ERROR,
            condition=lambda ctx: ctx.get("error", False),
        )
        self.assertFalse(rule.should_fire({"error": False}))

    def test_rule_respects_cooldown(self):
        rule = AlertRule(
            name="test_rule",
            alert_type=AlertType.API_ERROR,
            condition=lambda ctx: True,
            cooldown_seconds=1,
        )
        self.assertTrue(rule.should_fire({}))
        self.assertFalse(rule.should_fire({}))
        import time

        time.sleep(1.1)
        self.assertTrue(rule.should_fire({}))

    def test_reset_cooldown(self):
        rule = AlertRule(
            name="test_rule",
            alert_type=AlertType.API_ERROR,
            condition=lambda ctx: True,
            cooldown_seconds=60,
        )
        rule.should_fire({})
        rule.reset_cooldown()
        self.assertTrue(rule.should_fire({}))


class AlertEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = AlertEngine()

    def test_register_rule(self):
        rule = AlertRule(
            name="test_rule",
            alert_type=AlertType.API_ERROR,
            condition=lambda ctx: False,
        )
        self.engine.register_rule(rule)
        self.assertEqual(len(self.engine._rules), 1)

    def test_unregister_rule(self):
        rule = AlertRule(
            name="test_rule",
            alert_type=AlertType.API_ERROR,
            condition=lambda ctx: False,
        )
        self.engine.register_rule(rule)
        result = self.engine.unregister_rule("test_rule")
        self.assertTrue(result)
        self.assertEqual(len(self.engine._rules), 0)

    def test_check_and_fire(self):
        rule = AlertRule(
            name="test_rule",
            alert_type=AlertType.API_ERROR,
            condition=lambda ctx: ctx.get("error", False),
        )
        self.engine.register_rule(rule)
        alerts = self.engine.check_and_fire({"error": True})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, AlertType.API_ERROR)

    def test_no_fire_when_condition_false(self):
        rule = AlertRule(
            name="test_rule",
            alert_type=AlertType.API_ERROR,
            condition=lambda ctx: ctx.get("error", False),
        )
        self.engine.register_rule(rule)
        alerts = self.engine.check_and_fire({"error": False})
        self.assertEqual(len(alerts), 0)

    def test_resolve_alert(self):
        rule = AlertRule(
            name="test_rule",
            alert_type=AlertType.API_ERROR,
            condition=lambda ctx: True,
        )
        self.engine.register_rule(rule)
        self.engine.check_and_fire({})
        self.assertEqual(len(self.engine.get_active_alerts()), 1)

        self.engine.resolve_alert(AlertType.API_ERROR)
        self.assertEqual(len(self.engine.get_active_alerts()), 0)

    def test_alert_summary(self):
        rule = AlertRule(
            name="test_rule",
            alert_type=AlertType.API_ERROR,
            condition=lambda ctx: True,
        )
        self.engine.register_rule(rule)
        self.engine.check_and_fire({})

        summary = self.engine.get_alert_summary()
        self.assertEqual(summary["total_active"], 1)
        self.assertIn("WARNING", summary["by_severity"])


class AlertTests(unittest.TestCase):
    def test_alert_to_dict(self):
        alert = Alert(
            alert_type=AlertType.TRADING_BLOCKED,
            severity=AlertSeverity.WARNING,
            message="Test alert",
            timestamp="2024-01-01T00:00:00",
            context={"symbol": "BTCUSDT"},
        )
        d = alert.to_dict()
        self.assertEqual(d["alert_type"].value, "TRADING_BLOCKED")
        self.assertEqual(d["severity"].value, "WARNING")
        self.assertEqual(d["message"], "Test alert")
        self.assertEqual(d["resolved"], False)

    def test_alert_resolve(self):
        alert = Alert(
            alert_type=AlertType.API_ERROR,
            severity=AlertSeverity.ERROR,
            message="Test",
            timestamp="2024-01-01T00:00:00",
            context={},
        )
        self.assertFalse(alert.resolved)
        alert.resolve()
        self.assertTrue(alert.resolved)
        self.assertIsNotNone(alert.resolved_at)


class MonitoringDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.logger = StructuredLogger(
            component="test",
            log_dir=self.temp_dir,
            enable_console=False,
        )
        self.metrics = MetricsCollector()
        self.alerts = AlertEngine()
        self.dashboard = MonitoringDashboard(
            logger=self.logger,
            metrics=self.metrics,
            alerts=self.alerts,
        )

    def tearDown(self):
        StructuredLogger.reset_instance()
        MetricsCollector.reset_instance()
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_log_trade_event(self):
        self.dashboard.log_trade_event(
            "order_submitted", "BTCUSDT", "BUY", 10.0, 50000.0, "accepted"
        )
        summary = self.metrics.get_metrics_summary()
        self.assertIn("trade.order.submitted", summary["counters"])

    def test_log_signal_generated(self):
        self.dashboard.log_signal_generated("BTCUSDT", "A", 7.5)
        summary = self.metrics.get_metrics_summary()
        self.assertIn("signal.generated", summary["counters"])

    def test_log_risk_decision(self):
        self.dashboard.log_risk_decision("BTCUSDT", True, "Approved")
        summary = self.metrics.get_metrics_summary()
        self.assertIn("risk.decision", summary["counters"])

    def test_log_data_health(self):
        self.dashboard.log_data_health("ok", {"benchmark_status": "ok"})
        summary = self.metrics.get_metrics_summary()
        self.assertIn("data_health.status", summary["gauges"])

    def test_log_data_health_triggers_alert(self):
        self.dashboard.log_data_health("degraded", {"degraded_sources": 2})
        alerts = self.alerts.get_active_alerts()
        self.assertGreater(len(alerts), 0)

    def test_log_execution(self):
        self.dashboard.log_execution("paper", True, "Order accepted")
        summary = self.metrics.get_metrics_summary()
        self.assertIn("execution.result", summary["counters"])

    def test_log_system_metrics(self):
        self.dashboard.log_system_metrics(
            {
                "uptime_seconds": 3600,
                "memory_mb": 512,
                "cpu_percent": 25.5,
                "portfolio_value": 10000.0,
                "open_positions": 3,
                "daily_pnl": 150.0,
                "drawdown_pct": 1.5,
            }
        )
        summary = self.metrics.get_metrics_summary()
        self.assertIn("system.uptime_seconds", summary["gauges"])
        self.assertIn("portfolio.value", summary["gauges"])

    def test_generate_dashboard_report(self):
        report = self.dashboard.generate_dashboard_report()
        self.assertIn("generated_at", report)
        self.assertIn("metrics_summary", report)
        self.assertIn("alerts_summary", report)
        self.assertIn("active_alerts", report)

    def test_print_dashboard(self):
        self.dashboard.print_dashboard()
        self.assertTrue(True)


class MetricPointTests(unittest.TestCase):
    def test_metric_point_to_dict(self):
        point = MetricPoint(
            name="test_metric",
            value=42.0,
            timestamp="2024-01-01T00:00:00",
            tags={"env": "test"},
            metric_type="gauge",
        )
        d = point.to_dict()
        self.assertEqual(d["name"], "test_metric")
        self.assertEqual(d["value"], 42.0)
        self.assertEqual(d["tags"], {"env": "test"})
        self.assertEqual(d["metric_type"], "gauge")


class LogEntryTests(unittest.TestCase):
    def test_log_entry_to_json(self):
        entry = LogEntry(
            timestamp="2024-01-01T00:00:00",
            level="INFO",
            component="test",
            message="Test message",
            context={"key": "value"},
            trace_id="trace-123",
        )
        json_str = entry.to_json()
        self.assertIsInstance(json_str, str)
        parsed = eval(json_str)
        self.assertEqual(parsed["message"], "Test message")


if __name__ == "__main__":
    unittest.main()
