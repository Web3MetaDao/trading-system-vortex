from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from binance_websocket import BinanceWebSocketClient, PositionSyncManager
from data_provider import UnifiedDataProvider
from execution_engine import ExecutionEngine
from market_data import MarketDataClient
from risk_engine import RiskEngine
from signal_engine import SignalEngine


class LoopState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class SchedulerConfig:
    check_interval: float = 5.0
    market_open_check: bool = True
    market_close_check: bool = True
    trading_enabled: bool = True
    max_consecutive_errors: int = 5


@dataclass
class LoopMetrics:
    loops_completed: int = 0
    loops_failed: int = 0
    last_loop_time: datetime | None = None
    last_error: str | None = None
    consecutive_errors: int = 0
    total_trades_executed: int = 0
    uptime_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "loops_completed": self.loops_completed,
            "loops_failed": self.loops_failed,
            "last_loop_time": self.last_loop_time.isoformat() if self.last_loop_time else None,
            "last_error": self.last_error,
            "consecutive_errors": self.consecutive_errors,
            "total_trades_executed": self.total_trades_executed,
            "uptime_seconds": self.uptime_seconds,
        }


class TradingScheduler:
    def __init__(self, config: SchedulerConfig | None = None):
        self.config = config or SchedulerConfig()
        self._running = False
        self._task: asyncio.Task | None = None
        self._callbacks: list[Callable[[], None]] = []
        self._logger = logging.getLogger("trading_scheduler")

    def register_callback(self, callback: Callable[[], None]):
        self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable[[], None]):
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def start(self):
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        self._logger.info("Trading scheduler started")

    async def stop(self):
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._logger.info("Trading scheduler stopped")

    async def _run_loop(self):
        while self._running:
            try:
                if self.config.trading_enabled:
                    for callback in self._callbacks:
                        callback()

                await asyncio.sleep(self.config.check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(self.config.check_interval)

    @property
    def is_running(self) -> bool:
        return self._running


@dataclass
class SymbolConfig:
    symbol: str
    enabled: bool = True
    priority: int = 0
    min_signal_score: float = 5.0
    max_positions: int = 1


class AutoSymbolRotator:
    def __init__(self, symbols: list[str] | None = None):
        self._symbols: dict[str, SymbolConfig] = {}
        self._current_index = 0
        self._logger = logging.getLogger("symbol_rotator")

        if symbols:
            for symbol in symbols:
                self.add_symbol(symbol)

    def add_symbol(self, symbol: str, priority: int = 0, **kwargs):
        symbol = symbol.upper()
        self._symbols[symbol] = SymbolConfig(
            symbol=symbol,
            priority=priority,
            **{k: v for k, v in kwargs.items() if k in SymbolConfig.__dataclass_fields__},
        )

    def remove_symbol(self, symbol: str) -> bool:
        symbol = symbol.upper()
        if symbol in self._symbols:
            del self._symbols[symbol]
            return True
        return False

    def enable_symbol(self, symbol: str):
        symbol = symbol.upper()
        if symbol in self._symbols:
            self._symbols[symbol].enabled = True

    def disable_symbol(self, symbol: str):
        symbol = symbol.upper()
        if symbol in self._symbols:
            self._symbols[symbol].enabled = False

    def get_next_symbol(self) -> str | None:
        enabled_symbols = self.get_enabled_symbols()
        if not enabled_symbols:
            return None

        if self._current_index >= len(enabled_symbols):
            self._current_index = 0

        symbol = enabled_symbols[self._current_index]
        self._current_index += 1
        return symbol

    def get_enabled_symbols(self) -> list[str]:
        return [
            s.symbol for s in sorted(self._symbols.values(), key=lambda x: -x.priority) if s.enabled
        ]

    def get_all_symbols(self) -> list[str]:
        return list(self._symbols.keys())

    def get_symbol_config(self, symbol: str) -> SymbolConfig | None:
        return self._symbols.get(symbol.upper())

    def update_priority(self, symbol: str, priority: int):
        symbol = symbol.upper()
        if symbol in self._symbols:
            self._symbols[symbol].priority = priority

    def reset_index(self):
        self._current_index = 0


@dataclass
class PositionMonitorConfig:
    check_interval: float = 1.0
    stop_loss_enabled: bool = True
    take_profit_enabled: bool = True
    trailing_stop_enabled: bool = False
    trailing_stop_pct: float = 1.0
    max_holding_hours: float = 24.0


class PositionMonitor:
    def __init__(
        self,
        execution_engine: ExecutionEngine,
        config: PositionMonitorConfig | None = None,
    ):
        self.execution_engine = execution_engine
        self.config = config or PositionMonitorConfig()
        self._running = False
        self._task: asyncio.Task | None = None
        self._position_callbacks: list[Callable[[str, dict], None]] = []
        self._logger = logging.getLogger("position_monitor")

    def register_exit_callback(self, callback: Callable[[str, dict], None]):
        self._position_callbacks.append(callback)

    async def start(self):
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        self._logger.info("Position monitor started")

    async def stop(self):
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._logger.info("Position monitor stopped")

    async def _monitor_loop(self):
        while self._running:
            try:
                await self._check_positions()
                await asyncio.sleep(self.config.check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Monitor error: {e}")
                await asyncio.sleep(self.config.check_interval)

    async def _check_positions(self):
        if self.execution_engine.mode == "paper":
            return

        try:
            positions = self.execution_engine.fetch_positions()
            for position in positions:
                should_exit, exit_reason = await self._evaluate_exit(position)

                if should_exit:
                    for callback in self._position_callbacks:
                        try:
                            callback(position.symbol, {"reason": exit_reason})
                        except Exception as e:
                            self._logger.error(f"Callback error: {e}")

        except Exception as e:
            self._logger.error(f"Failed to check positions: {e}")

    async def _evaluate_exit(self, position) -> tuple[bool, str | None]:
        return False, None

    @property
    def is_running(self) -> bool:
        return self._running


@dataclass
class AutomatedTradingLoopConfig:
    check_interval: float = 5.0
    symbols: list[str] = field(default_factory=list)
    max_positions_per_symbol: int = 1
    max_total_positions: int = 3
    trading_enabled: bool = True
    pause_on_error: bool = True
    max_consecutive_errors: int = 5
    enable_position_monitor: bool = True
    enable_ws_market_data: bool = True


class AutomatedTradingLoop:
    def __init__(
        self,
        execution_engine: ExecutionEngine,
        market_data_client: MarketDataClient,
        data_provider: UnifiedDataProvider,
        signal_engine: SignalEngine,
        risk_engine: RiskEngine,
        config: AutomatedTradingLoopConfig | None = None,
        ws_client: BinanceWebSocketClient | None = None,
        position_sync: PositionSyncManager | None = None,
        strategy_config: dict | None = None,
    ):
        self.execution_engine = execution_engine
        self.market_data_client = market_data_client
        self.data_provider = data_provider
        self.signal_engine = signal_engine
        self.risk_engine = risk_engine
        self.ws_client = ws_client
        self.position_sync = position_sync
        self.config = config or AutomatedTradingLoopConfig()
        # [FIX] 接收并持久化 strategy_config，确保每次 evaluate 都能透传
        self._strategy_config: dict = strategy_config or {}

        self.state = LoopState.STOPPED
        self.metrics = LoopMetrics()
        self._running = False
        self._main_task: asyncio.Task | None = None
        self._start_time: datetime | None = None

        self.scheduler = TradingScheduler()
        self.symbol_rotator = AutoSymbolRotator(self.config.symbols)
        self.position_monitor = PositionMonitor(execution_engine)

        self._logger = logging.getLogger("automated_loop")

    async def start(self):
        if self._running:
            return

        self._logger.info("Starting automated trading loop...")
        self.state = LoopState.STARTING
        self._running = True
        self._start_time = datetime.now(UTC)

        self.scheduler.register_callback(self._trading_cycle)
        await self.scheduler.start()

        if self.config.enable_position_monitor:
            await self.position_monitor.start()

        self.state = LoopState.RUNNING
        self._logger.info("Automated trading loop started")

    async def stop(self):
        if not self._running:
            return

        self._logger.info("Stopping automated trading loop...")
        self.state = LoopState.STOPPING

        await self.scheduler.stop()
        await self.position_monitor.stop()

        self._running = False
        self.state = LoopState.STOPPED
        self._logger.info("Automated trading loop stopped")

    async def pause(self):
        if not self._running:
            return

        self._logger.info("Pausing automated trading loop...")
        self.scheduler.config.trading_enabled = False
        self.state = LoopState.PAUSED

    async def resume(self):
        if not self._running:
            return

        self._logger.info("Resuming automated trading loop...")
        self.scheduler.config.trading_enabled = True
        self.state = LoopState.RUNNING

    async def _trading_cycle(self):
        try:
            self._logger.debug("Starting trading cycle...")

            symbol = self.symbol_rotator.get_next_symbol()
            if not symbol:
                self._logger.warning("No symbols to trade")
                return

            self._logger.debug(f"Processing symbol: {symbol}")

            await self._process_symbol(symbol)

            self.metrics.loops_completed += 1
            self.metrics.last_loop_time = datetime.now(UTC)
            self.metrics.consecutive_errors = 0

            if self._start_time:
                self.metrics.uptime_seconds = (datetime.now(UTC) - self._start_time).total_seconds()

        except Exception as e:
            self._logger.error(f"Trading cycle error: {e}")
            self.metrics.loops_failed += 1
            self.metrics.consecutive_errors += 1
            self.metrics.last_error = str(e)

            if self.metrics.consecutive_errors >= self.config.max_consecutive_errors:
                if self.config.pause_on_error:
                    await self.pause()
                    self._logger.warning(
                        f"Too many consecutive errors ({self.metrics.consecutive_errors}), pausing loop"
                    )

    async def _process_symbol(self, symbol: str):
        try:
            snapshot = self.market_data_client.fetch_snapshot(symbol)
            if not snapshot or snapshot.price is None:
                self._logger.warning(f"Failed to fetch snapshot for {symbol}")
                return

            benchmark_symbol = self._strategy_config.get("benchmark_symbol", "BTCUSDT")
            market_data_cfg = self._strategy_config.get("market_data", {})
            state_interval = market_data_cfg.get("state_interval", "1h")
            state_limit = market_data_cfg.get("state_limit", 20)
            signal_interval = market_data_cfg.get("signal_interval", "1h")
            signal_limit = market_data_cfg.get("signal_limit", 120)

            # [FIX] 使用 build_context 替代已废弃的 gather_context
            context = self.data_provider.build_context(
                benchmark_symbol=benchmark_symbol,
                watchlist=[symbol],
                state_interval=state_interval,
                state_limit=state_limit,
                signal_interval=signal_interval,
                signal_limit=signal_limit,
            )

            # 从 MarketContext 中提取 market_state（通过 signal_snapshots 推断）
            sym_upper = symbol.upper()
            ctx_snapshot = context.signal_snapshots.get(sym_upper, snapshot)

            signal = self.signal_engine.evaluate(
                symbol,
                "unknown",  # market_state 由 main.py 的 MarketStateEngine 负责，此处降级
                {
                    "snapshot": ctx_snapshot if not ctx_snapshot.degraded else snapshot,
                    # [FIX] 透传 strategy_config，确保信号引擎使用正确参数
                    "strategy": self._strategy_config,
                    "benchmark_snapshot": context.benchmark_snapshot,
                    "intermarket": context.intermarket,
                    "derivatives": context.derivatives.get(sym_upper, {}),
                    "data_health": context.data_health,
                    # [FIX] 注入 oracle_snapshot，启用宏观情绪过滤
                    "oracle_snapshot": context.oracle_snapshot,
                },
            )

            if signal.grade not in ("A", "B"):
                self._logger.debug(f"{symbol}: Signal grade {signal.grade} not actionable")
                return

            risk_decision = self.risk_engine.can_open_position(
                portfolio=self._get_portfolio(),
                symbol=symbol,
                requested_size_usdt=10.0,
                signal_grade=signal.grade,
                data_health=context.get("data_health"),
            )

            if not risk_decision.approved:
                self._logger.debug(f"{symbol}: Risk decision rejected - {risk_decision.reason}")
                return

            result = self.execution_engine.submit_order(
                symbol=symbol,
                side="BUY",
                quantity_usdt=risk_decision.size_usdt,
            )

            if result.accepted:
                self.metrics.total_trades_executed += 1
                self._logger.info(f"Order executed: {result.detail}")

        except Exception as e:
            self._logger.error(f"Error processing {symbol}: {e}")
            raise

    def _get_portfolio(self):
        return self.execution_engine

    def get_status(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "is_running": self._running,
            "metrics": self.metrics.to_dict(),
            "symbols": self.symbol_rotator.get_all_symbols(),
            "enabled_symbols": self.symbol_rotator.get_enabled_symbols(),
            "scheduler_running": self.scheduler.is_running,
            "position_monitor_running": self.position_monitor.is_running,
            "ws_connected": self.ws_client.is_connected if self.ws_client else None,
        }

    def print_status(self):
        status = self.get_status()
        print("\n" + "=" * 60)
        print("AUTOMATED TRADING LOOP STATUS")
        print("=" * 60)
        print(f"State: {status['state']}")
        print(f"Running: {status['is_running']}")
        print(f"Total loops: {status['metrics']['loops_completed']}")
        print(f"Failed loops: {status['metrics']['loops_failed']}")
        print(f"Consecutive errors: {status['metrics']['consecutive_errors']}")
        print(f"Total trades: {status['metrics']['total_trades_executed']}")
        print(f"Uptime: {status['metrics']['uptime_seconds']:.1f}s")
        print(f"Symbols: {', '.join(status['symbols'])}")
        print(f"Enabled: {', '.join(status['enabled_symbols'])}")
        print(f"Scheduler: {'Running' if status['scheduler_running'] else 'Stopped'}")
        print(f"Position Monitor: {'Running' if status['position_monitor_running'] else 'Stopped'}")
        print(f"WebSocket: {'Connected' if status['ws_connected'] else 'Disconnected'}")
        print("=" * 60 + "\n")

    @property
    def is_running(self) -> bool:
        return self._running
