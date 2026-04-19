"""
模拟回测系统 - 使用历史数据验证策略表现

功能：
- 使用 MarketDataClient 获取历史 K 线
- 模拟信号生成 + 风控检查 + 订单执行
- 统计盈亏、胜率、最大回撤等指标
- 发送回测报告到 Telegram
"""

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DOTENV_CONFIG", ".env")
load_dotenv()

from data_provider import UnifiedDataProvider  # noqa: E402
from execution_engine import ExecutionEngine  # noqa: E402
from market_data import MarketDataClient  # noqa: E402
from risk_engine import RiskEngine  # noqa: E402
from signal_engine import SignalEngine  # noqa: E402
from telegram_notifier import TelegramAlertLevel, TelegramNotifier  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backtest_runner")


@dataclass
class BacktestConfig:
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "BNBUSDT"])
    interval: str = "1h"
    lookback_bars: int = 100
    initial_capital: float = 10000.0
    position_size_usdt: float = 100.0
    max_leverage: int = 20


@dataclass
class BacktestMetrics:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    total_pnl_percent: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0


class BacktestRunner:
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.market_data = MarketDataClient()
        self.data_provider = UnifiedDataProvider(client=self.market_data)
        self.signal_engine = SignalEngine()
        self.risk_engine = RiskEngine({})
        self.execution_engine = ExecutionEngine(mode="paper")

        self.telegram = TelegramNotifier(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            enabled=True,
        )

        self.metrics = BacktestMetrics()
        self.trades: list[dict[str, Any]] = []
        self.equity_curve: list[float] = []
        self.current_capital = config.initial_capital

    def run_backtest(self) -> BacktestMetrics:
        logger.info(f"Starting backtest for {self.config.symbols}")
        logger.info(f"Interval: {self.config.interval}, Lookback: {self.config.lookback_bars} bars")

        for symbol in self.config.symbols:
            self._backtest_symbol(symbol)

        self._calculate_metrics()
        return self.metrics

    def _backtest_symbol(self, symbol: str):
        logger.info(f"Backtesting {symbol}...")

        try:
            klines = self.market_data.fetch_klines(
                symbol,
                interval=self.config.interval,
                limit=self.config.lookback_bars,
            )

            if not klines or len(klines) < 20:
                logger.warning(f"Insufficient data for {symbol}")
                return

            for i in range(20, len(klines)):
                _context_klines = klines[:i]  # noqa: F841  # reserved for future multi-timeframe context
                current_kline = klines[i]

                context = self.data_provider.build_context(
                    benchmark_symbol="BTCUSDT",
                    watchlist=[symbol],
                    state_interval=self.config.interval,
                    state_limit=20,
                    signal_interval=self.config.interval,
                    signal_limit=20,
                )

                signal = self.signal_engine.evaluate(
                    symbol,
                    "normal",
                    {
                        "snapshot": self._build_snapshot_from_kline(symbol, current_kline),
                        "benchmark_snapshot": context.benchmark_snapshot,
                        "intermarket": context.intermarket,
                        "derivatives": context.derivatives,
                        "data_health": context.data_health,
                    },
                )

                if signal.grade in ("A", "B"):
                    risk_decision = self.risk_engine.can_open_position(
                        portfolio=self.execution_engine,
                        symbol=symbol,
                        requested_size_usdt=self.config.position_size_usdt,
                        signal_grade=signal.grade,
                        data_health=context.data_health,
                    )

                    if risk_decision.approved:
                        self._simulate_trade(symbol, signal, current_kline)

        except Exception as e:
            logger.error(f"Error backtesting {symbol}: {e}")

    def _build_snapshot_from_kline(self, symbol: str, kline: dict) -> Any:
        from market_data import MarketSnapshot

        snapshot = MarketSnapshot(
            symbol=symbol.upper(),
            price=float(kline.get("close", 0)),
            source="kline_derived",
        )
        return snapshot

    def _simulate_trade(self, symbol: str, signal: Any, entry_kline: dict):
        entry_price = float(entry_kline.get("close", 0))
        entry_time = entry_kline.get("close_time")

        direction = signal.direction
        grade = signal.grade
        confidence = signal.confidence

        next_bar_idx = self._find_next_bar_index(entry_kline)
        if next_bar_idx is None:
            return

        next_kline = self._get_kline_at_index(next_bar_idx)
        exit_price = float(next_kline.get("open", entry_price))

        if direction == "LONG":
            pnl = (exit_price - entry_price) / entry_price * self.config.position_size_usdt
        else:
            pnl = (entry_price - exit_price) / entry_price * self.config.position_size_usdt

        self.trades.append(
            {
                "symbol": symbol,
                "entry_time": entry_time,
                "entry_price": entry_price,
                "exit_time": next_kline.get("close_time"),
                "exit_price": exit_price,
                "direction": direction,
                "grade": grade,
                "confidence": confidence,
                "pnl": pnl,
            }
        )

        self.current_capital += pnl
        self.equity_curve.append(self.current_capital)

        logger.info(
            f"Trade {symbol}: {direction} | PnL: ${pnl:.2f} | Capital: ${self.current_capital:.2f}"
        )

    def _find_next_bar_index(self, current_kline: dict) -> int | None:
        try:
            klines = self.market_data.fetch_klines(
                current_kline.get("symbol", "BTCUSDT"),
                interval=self.config.interval,
                limit=self.config.lookback_bars,
            )

            current_close_time = current_kline.get("close_time")
            for i, kline in enumerate(klines):
                if kline.get("close_time") == current_close_time:
                    if i + 1 < len(klines):
                        return i + 1
            return None
        except Exception:
            return None

    def _get_kline_at_index(self, index: int) -> dict | None:
        try:
            klines = self.market_data.fetch_klines(
                "BTCUSDT",
                interval=self.config.interval,
                limit=self.config.lookback_bars,
            )
            if 0 <= index < len(klines):
                return klines[index]
            return None
        except Exception:
            return None

    def _calculate_metrics(self):
        if not self.trades:
            logger.warning("No trades executed")
            return

        self.metrics.total_trades = len(self.trades)
        pnls = [t["pnl"] for t in self.trades]

        winning_pnls = [p for p in pnls if p > 0]
        losing_pnls = [p for p in pnls if p < 0]

        self.metrics.winning_trades = len(winning_pnls)
        self.metrics.losing_trades = len(losing_pnls)
        self.metrics.total_pnl = sum(pnls)
        self.metrics.total_pnl_percent = (
            self.metrics.total_pnl / self.config.initial_capital
        ) * 100

        if self.metrics.winning_trades > 0:
            self.metrics.win_rate = self.metrics.winning_trades / self.metrics.total_trades * 100
            self.metrics.avg_win = sum(winning_pnls) / len(winning_pnls)

        if self.metrics.losing_trades > 0:
            self.metrics.avg_loss = sum(losing_pnls) / len(losing_pnls)

        gross_profit = sum(winning_pnls) if winning_pnls else 0
        gross_loss = abs(sum(losing_pnls)) if losing_pnls else 1
        self.metrics.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        if self.equity_curve:
            peak = self.equity_curve[0]
            max_dd = 0
            for equity in self.equity_curve:
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak * 100
                if dd > max_dd:
                    max_dd = dd
            self.metrics.max_drawdown = max_dd

    def generate_report(self) -> str:
        report = [
            "📈 *回测报告*",
            f"⏰ {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC",
            "",
            f"💰 初始资金：${self.config.initial_capital:.2f}",
            f"📊 最终资金：${self.current_capital:.2f}",
            f"📉 总盈亏：${self.metrics.total_pnl:.2f} ({self.metrics.total_pnl_percent:.2f}%)",
            "",
            "📊 交易统计:",
            f"  总交易数：{self.metrics.total_trades}",
            f"  盈利：{self.metrics.winning_trades} | 亏损：{self.metrics.losing_trades}",
            f"  胜率：{self.metrics.win_rate:.1f}%",
            "",
            "📊 绩效指标:",
            f"  平均盈利：${self.metrics.avg_win:.2f}",
            f"  平均亏损：${self.metrics.avg_loss:.2f}",
            f"  盈亏比：{self.metrics.profit_factor:.2f}",
            f"  最大回撤：{self.metrics.max_drawdown:.1f}%",
        ]

        if self.trades:
            report.append("")
            report.append("最近 5 笔交易:")
            for trade in self.trades[-5:]:
                emoji = "✅" if trade["pnl"] > 0 else "❌"
                report.append(
                    f"  {emoji} {trade['symbol']} {trade['direction']} | PnL: ${trade['pnl']:.2f}"
                )

        return "\n".join(report)

    def send_report(self):
        try:
            report = self.generate_report()
            self.telegram.send(report, level=TelegramAlertLevel.INFO)
            logger.info("Backtest report sent to Telegram")
        except Exception as e:
            logger.error(f"Failed to send backtest report: {e}")


def main():
    config = BacktestConfig(
        symbols=["BTCUSDT", "ETHUSDT", "BNBUSDT"],
        interval="1h",
        lookback_bars=100,
        initial_capital=10000.0,
        position_size_usdt=100.0,
    )

    runner = BacktestRunner(config)
    runner.run_backtest()
    runner.send_report()

    print("\n" + "=" * 60)
    print(runner.generate_report())
    print("=" * 60)


if __name__ == "__main__":
    main()
