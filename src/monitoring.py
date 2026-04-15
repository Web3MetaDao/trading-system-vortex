from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

try:
    from telegram_notifier import TelegramAlertLevel, TelegramNotifier
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    TelegramNotifier = None
    TelegramAlertLevel = None


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AlertSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AlertType(Enum):
    DATA_HEALTH_DEGRADED = "DATA_HEALTH_DEGRADED"
    DATA_HEALTH_PARTIAL = "DATA_HEALTH_PARTIAL"
    TRADING_BLOCKED = "TRADING_BLOCKED"
    POSITION_LOSS = "POSITION_LOSS"
    DRAWDOWN_EXCEEDED = "DRAWDOWN_EXCEEDED"
    CONSECUTIVE_LOSSES = "CONSECUTIVE_LOSSES"
    API_ERROR = "API_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"


@dataclass
class LogEntry:
    timestamp: str
    level: str
    component: str
    message: str
    context: dict[str, Any]
    trace_id: str | None

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StructuredLogger:
    _instance: StructuredLogger | None = None

    def __init__(
        self,
        component: str = "trading-system",
        log_dir: str | None = None,
        log_level: str = "INFO",
        enable_console: bool = True,
    ):
        self.component = component
        self.log_dir = log_dir or os.getenv("LOG_DIR", "logs")
        self.log_level = getattr(LogLevel, log_level.upper(), LogLevel.INFO)
        self.enable_console = enable_console
        self._trace_counter = 0
        self._trace_prefix = f"{int(time.time())}"

        self._setup_file_handler()

    def _setup_file_handler(self):
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir, exist_ok=True)

        log_file = os.path.join(
            self.log_dir,
            f"{self.component}_{datetime.now(UTC).strftime('%Y%m%d')}.log",
        )

        self._file_handler = logging.FileHandler(log_file, encoding="utf-8")
        self._file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )

    def _generate_trace_id(self) -> str:
        self._trace_counter += 1
        return f"{self._trace_prefix}-{self._trace_counter:06d}"

    def _should_log(self, level: LogLevel) -> bool:
        return level.value >= self.log_level.value

    def log(
        self,
        level: LogLevel,
        message: str,
        context: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> LogEntry:
        if not self._should_log(level):
            return LogEntry(
                timestamp=datetime.now(UTC).isoformat(),
                level=level.value,
                component=self.component,
                message=message,
                context=context or {},
                trace_id=trace_id,
            )

        context = context or {}
        trace_id = trace_id or self._generate_trace_id()

        entry = LogEntry(
            timestamp=datetime.now(UTC).isoformat(),
            level=level.value,
            component=self.component,
            message=message,
            context=context,
            trace_id=trace_id,
        )

        if self.enable_console:
            print(f"[{entry.timestamp}] {entry.level} [{self.component}] {message}")
            if context:
                print(f"  Context: {json.dumps(context, ensure_ascii=False, default=str)}")

        logger = logging.getLogger(self.component)
        logger.addHandler(self._file_handler)
        logger.setLevel(self.log_level.value)

        log_func = getattr(logger, level.value.lower())
        log_func(f"{message} | Context: {json.dumps(context, ensure_ascii=False, default=str)}")

        return entry

    def debug(
        self, message: str, context: dict[str, Any] | None = None, trace_id: str | None = None
    ) -> LogEntry:
        return self.log(LogLevel.DEBUG, message, context, trace_id)

    def info(
        self, message: str, context: dict[str, Any] | None = None, trace_id: str | None = None
    ) -> LogEntry:
        return self.log(LogLevel.INFO, message, context, trace_id)

    def warning(
        self, message: str, context: dict[str, Any] | None = None, trace_id: str | None = None
    ) -> LogEntry:
        return self.log(LogLevel.WARNING, message, context, trace_id)

    def error(
        self, message: str, context: dict[str, Any] | None = None, trace_id: str | None = None
    ) -> LogEntry:
        return self.log(LogLevel.ERROR, message, context, trace_id)

    def critical(
        self, message: str, context: dict[str, Any] | None = None, trace_id: str | None = None
    ) -> LogEntry:
        return self.log(LogLevel.CRITICAL, message, context, trace_id)

    @classmethod
    def get_instance(cls, **kwargs) -> StructuredLogger:
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None


@dataclass
class MetricPoint:
    name: str
    value: float
    timestamp: str
    tags: dict[str, str]
    metric_type: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MetricsCollector:
    _instance: MetricsCollector | None = None

    def __init__(self, flush_interval_seconds: int = 60):
        self.flush_interval = flush_interval_seconds
        self._metrics: list[MetricPoint] = []
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._last_flush = time.time()

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat()

    def record_counter(
        self, name: str, value: float = 1.0, tags: dict[str, str] | None = None
    ) -> MetricPoint:
        tags = tags or {}
        self._counters[name] = self._counters.get(name, 0.0) + value

        point = MetricPoint(
            name=name,
            value=value,
            timestamp=self._now_iso(),
            tags=tags,
            metric_type="counter",
        )
        self._metrics.append(point)
        return point

    def record_gauge(
        self, name: str, value: float, tags: dict[str, str] | None = None
    ) -> MetricPoint:
        tags = tags or {}
        self._gauges[name] = value

        point = MetricPoint(
            name=name,
            value=value,
            timestamp=self._now_iso(),
            tags=tags,
            metric_type="gauge",
        )
        self._metrics.append(point)
        return point

    def record_timing(
        self, name: str, duration_ms: float, tags: dict[str, str] | None = None
    ) -> MetricPoint:
        tags = tags or {}

        point = MetricPoint(
            name=name,
            value=duration_ms,
            timestamp=self._now_iso(),
            tags=tags,
            metric_type="timing",
        )
        self._metrics.append(point)
        return point

    def get_counter(self, name: str) -> float:
        return self._counters.get(name, 0.0)

    def get_gauge(self, name: str) -> float | None:
        return self._gauges.get(name)

    def get_all_metrics(self) -> list[MetricPoint]:
        return self._metrics.copy()

    def get_metrics_summary(self) -> dict[str, Any]:
        return {
            "total_metrics": len(self._metrics),
            "counters": self._counters.copy(),
            "gauges": self._gauges.copy(),
            "last_flush_age_seconds": time.time() - self._last_flush,
        }

    def flush(self) -> list[MetricPoint]:
        self._last_flush = time.time()
        flushed = self._metrics.copy()
        self._metrics.clear()
        return flushed

    @classmethod
    def get_instance(cls) -> MetricsCollector:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None


@dataclass
class Alert:
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    timestamp: str
    context: dict[str, Any]
    resolved: bool = False
    resolved_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def resolve(self):
        self.resolved = True
        self.resolved_at = datetime.now(UTC).isoformat()


class AlertRule:
    def __init__(
        self,
        name: str,
        alert_type: AlertType,
        condition: callable,
        severity: AlertSeverity = AlertSeverity.WARNING,
        cooldown_seconds: int = 300,
    ):
        self.name = name
        self.alert_type = alert_type
        self.condition = condition
        self.severity = severity
        self.cooldown_seconds = cooldown_seconds
        self._last_triggered: float | None = None

    def should_fire(self, context: dict[str, Any]) -> bool:
        if self._last_triggered and (time.time() - self._last_triggered) < self.cooldown_seconds:
            return False

        try:
            should_fire = self.condition(context)
            if should_fire:
                self._last_triggered = time.time()
            return should_fire
        except Exception:
            return False

    def reset_cooldown(self):
        self._last_triggered = None


class AlertEngine:
    def __init__(self):
        self._rules: list[AlertRule] = []
        self._alerts: list[Alert] = []
        self._alert_history: list[Alert] = []
        self._max_history = 1000

    def register_rule(self, rule: AlertRule):
        self._rules.append(rule)

    def unregister_rule(self, rule_name: str) -> bool:
        for i, rule in enumerate(self._rules):
            if rule.name == rule_name:
                self._rules.pop(i)
                return True
        return False

    def check_and_fire(self, context: dict[str, Any]) -> list[Alert]:
        fired_alerts = []

        for rule in self._rules:
            if rule.should_fire(context):
                alert = Alert(
                    alert_type=rule.alert_type,
                    severity=rule.severity,
                    message=f"{rule.name}: Alert triggered",
                    timestamp=datetime.now(UTC).isoformat(),
                    context=context,
                )
                self._alerts.append(alert)
                self._alert_history.append(alert)

                if len(self._alert_history) > self._max_history:
                    self._alert_history = self._alert_history[-self._max_history :]

                fired_alerts.append(alert)

        return fired_alerts

    def get_active_alerts(self, unresolved_only: bool = True) -> list[Alert]:
        if unresolved_only:
            return [a for a in self._alerts if not a.resolved]
        return self._alerts.copy()

    def resolve_alert(self, alert_type: AlertType | None = None, index: int | None = None) -> bool:
        if index is not None and 0 <= index < len(self._alerts):
            self._alerts[index].resolve()
            return True

        if alert_type:
            for alert in self._alerts:
                if alert.alert_type == alert_type and not alert.resolved:
                    alert.resolve()
                    return True

        return False

    def get_alert_summary(self) -> dict[str, Any]:
        active = self.get_active_alerts()
        by_severity = {}
        by_type = {}

        for alert in active:
            severity_key = alert.severity.value
            by_severity[severity_key] = by_severity.get(severity_key, 0) + 1

            type_key = alert.alert_type.value
            by_type[type_key] = by_type.get(type_key, 0) + 1

        return {
            "total_active": len(active),
            "total_history": len(self._alert_history),
            "by_severity": by_severity,
            "by_type": by_type,
        }

    def reset(self):
        self._alerts.clear()
        for rule in self._rules:
            rule.reset_cooldown()


class MonitoringDashboard:
    def __init__(
        self,
        logger: StructuredLogger | None = None,
        metrics: MetricsCollector | None = None,
        alerts: AlertEngine | None = None,
        telegram_enabled: bool = False,
        telegram_bot_token: str | None = None,
        telegram_chat_id: str | None = None,
    ):
        self.logger = logger or StructuredLogger.get_instance()
        self.metrics = metrics or MetricsCollector.get_instance()
        self.alerts = alerts or AlertEngine()
        self.telegram_enabled = telegram_enabled and TELEGRAM_AVAILABLE

        if self.telegram_enabled:
            self.telegram = TelegramNotifier.get_instance(
                bot_token=telegram_bot_token,
                chat_id=telegram_chat_id,
                enabled=True,
            )
        else:
            self.telegram = None

        self._setup_default_rules()

    def _setup_default_rules(self):
        self.alerts.register_rule(
            AlertRule(
                name="data_health_degraded",
                alert_type=AlertType.DATA_HEALTH_DEGRADED,
                condition=lambda ctx: ctx.get("data_health_status") == "degraded",
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=600,
            )
        )

        self.alerts.register_rule(
            AlertRule(
                name="data_health_partial",
                alert_type=AlertType.DATA_HEALTH_PARTIAL,
                condition=lambda ctx: ctx.get("data_health_status") == "partial",
                severity=AlertSeverity.WARNING,
                cooldown_seconds=300,
            )
        )

        self.alerts.register_rule(
            AlertRule(
                name="trading_blocked",
                alert_type=AlertType.TRADING_BLOCKED,
                condition=lambda ctx: ctx.get("trading_blocked", False),
                severity=AlertSeverity.WARNING,
                cooldown_seconds=60,
            )
        )

        self.alerts.register_rule(
            AlertRule(
                name="consecutive_losses",
                alert_type=AlertType.CONSECUTIVE_LOSSES,
                condition=lambda ctx: ctx.get("consecutive_losses", 0) >= 3,
                severity=AlertSeverity.ERROR,
                cooldown_seconds=600,
            )
        )

        self.alerts.register_rule(
            AlertRule(
                name="drawdown_exceeded",
                alert_type=AlertType.DRAWDOWN_EXCEEDED,
                condition=lambda ctx: ctx.get("drawdown_pct", 0) > 5.0,
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=300,
            )
        )

        self.alerts.register_rule(
            AlertRule(
                name="api_error",
                alert_type=AlertType.API_ERROR,
                condition=lambda ctx: ctx.get("api_error_count", 0) > 5,
                severity=AlertSeverity.ERROR,
                cooldown_seconds=300,
            )
        )

    def log_trade_event(
        self,
        event_type: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None = None,
        result: str | None = None,
    ):
        self.logger.info(
            f"Trade event: {event_type}",
            {
                "event_type": event_type,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
                "result": result,
            },
        )

        if event_type == "order_submitted":
            self.metrics.record_counter(
                "trade.order.submitted", tags={"symbol": symbol, "side": side}
            )
        elif event_type == "order_filled":
            self.metrics.record_counter("trade.order.filled", tags={"symbol": symbol, "side": side})
        elif event_type == "order_rejected":
            self.metrics.record_counter(
                "trade.order.rejected", tags={"symbol": symbol, "side": side}
            )

        if self.telegram_enabled and self.telegram:
            self.telegram.send_trade_alert(symbol, side, quantity, price, result)

    def log_signal_generated(self, symbol: str, grade: str, score: float):
        self.logger.info(
            f"Signal generated: {grade}",
            {"symbol": symbol, "grade": grade, "score": score},
        )
        self.metrics.record_counter("signal.generated", tags={"symbol": symbol, "grade": grade})

        if self.telegram_enabled and self.telegram:
            self.telegram.send_signal_alert(symbol, grade, score, "S1")

    def log_risk_decision(self, symbol: str, approved: bool, reason: str | None = None, size_usdt: float = 0.0):
        self.logger.info(
            f"Risk decision: {'APPROVED' if approved else 'REJECTED'}",
            {"symbol": symbol, "approved": approved, "reason": reason},
        )
        self.metrics.record_counter(
            "risk.decision",
            tags={"symbol": symbol, "approved": str(approved)},
        )

        if self.telegram_enabled and self.telegram:
            self.telegram.send_risk_alert(symbol, approved, reason, size_usdt)

    def log_data_health(self, status: str, details: dict[str, Any]):
        self.logger.info(
            f"Data health: {status}",
            {"status": status, **details},
        )
        self.metrics.record_gauge("data_health.status", 1.0 if status == "ok" else 0.0)

        self.alerts.check_and_fire({"data_health_status": status, **details})

        if self.telegram_enabled and self.telegram and status != "ok":
            self.telegram.send_data_health_alert(status, details)

    def log_execution(self, mode: str, accepted: bool, detail: str):
        self.logger.info(
            f"Execution [{mode}]: {'ACCEPTED' if accepted else 'REJECTED'}",
            {"mode": mode, "accepted": accepted, "detail": detail},
        )
        self.metrics.record_counter(
            "execution.result",
            tags={"mode": mode, "accepted": str(accepted)},
        )

        if self.telegram_enabled and self.telegram and not accepted:
            self.telegram.send_error_alert(
                error_type="EXECUTION_REJECTED",
                message=detail,
                context={"mode": mode},
            )

    def log_system_metrics(self, context: dict[str, Any]):
        self.metrics.record_gauge("system.uptime_seconds", context.get("uptime_seconds", 0))
        self.metrics.record_gauge("system.memory_mb", context.get("memory_mb", 0))
        self.metrics.record_gauge("system.cpu_percent", context.get("cpu_percent", 0))

        if "portfolio_value" in context:
            self.metrics.record_gauge(
                "portfolio.value",
                context["portfolio_value"],
            )

        if "open_positions" in context:
            self.metrics.record_gauge(
                "portfolio.open_positions",
                context["open_positions"],
            )

        if "daily_pnl" in context:
            self.metrics.record_counter("portfolio.daily_pnl", context["daily_pnl"])

        if "drawdown_pct" in context:
            self.metrics.record_gauge("portfolio.drawdown_pct", context["drawdown_pct"])

        self.alerts.check_and_fire(context)

    def generate_dashboard_report(self) -> dict[str, Any]:
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "metrics_summary": self.metrics.get_metrics_summary(),
            "alerts_summary": self.alerts.get_alert_summary(),
            "active_alerts": [a.to_dict() for a in self.alerts.get_active_alerts()],
        }

    def print_dashboard(self):
        report = self.generate_dashboard_report()

        print("\n" + "=" * 60)
        print("MONITORING DASHBOARD")
        print("=" * 60)
        print(f"Generated at: {report['generated_at']}")

        print("\n--- Metrics Summary ---")
        metrics_sum = report["metrics_summary"]
        print(f"Total metrics recorded: {metrics_sum['total_metrics']}")
        print(f"Counters: {metrics_sum['counters']}")
        print(f"Gauges: {metrics_sum['gauges']}")

        print("\n--- Alerts Summary ---")
        alerts_sum = report["alerts_summary"]
        print(f"Active alerts: {alerts_sum['total_active']}")
        print(f"By severity: {alerts_sum['by_severity']}")
        print(f"By type: {alerts_sum['by_type']}")

        if report["active_alerts"]:
            print("\n--- Active Alerts ---")
            for alert in report["active_alerts"]:
                print(f"  [{alert['severity']}] {alert['alert_type']}: {alert['message']}")
                print(f"    Context: {alert['context']}")

        print("=" * 60 + "\n")
