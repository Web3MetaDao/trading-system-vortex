"""
轻量级模拟回测 - 快速验证策略

功能：
- 使用历史 K 线数据
- 模拟信号生成 + 风控检查
- 统计胜率、盈亏比等指标
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

from market_data import MarketDataClient  # noqa: E402
from risk_engine import RiskEngine  # noqa: E402
from signal_engine import SignalEngine  # noqa: E402
from telegram_notifier import TelegramAlertLevel, TelegramNotifier  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("quick_backtest")


@dataclass
class BacktestConfig:
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "BNBUSDT"])
    interval: str = "1h"
    lookback_bars: int = 50
    initial_capital: float = 10000.0
    position_size_usdt: float = 100.0


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


class QuickBacktest:
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.client = MarketDataClient()
        self.signal_engine = SignalEngine()
        self.risk_engine = RiskEngine({})

        self.telegram = TelegramNotifier(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            enabled=True,
        )

        self.trades: list[dict[str, Any]] = []
        self.equity_curve: list[float] = []
        self.current_capital = config.initial_capital

        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        self.total_pnl_percent = 0.0
        self.max_drawdown = 0.0
        self.win_rate = 0.0
        self.avg_win = 0.0
        self.avg_loss = 0.0
        self.profit_factor = 0.0

    def run(self) -> BacktestMetrics:
        logger.info(f"Starting quick backtest: {self.config.symbols}")

        for symbol in self.config.symbols:
            self._test_symbol(symbol)

        self._calc_metrics()
        return self._get_metrics()

    def _test_symbol(self, symbol: str):
        logger.info(f"Testing {symbol}...")

        try:
            klines = self.client.fetch_klines(
                symbol, interval=self.config.interval, limit=self.config.lookback_bars
            )

            if not klines or len(klines) < 10:
                logger.warning(f"Insufficient data for {symbol}")
                return

            logger.info(f"Got {len(klines)} klines for {symbol}")

            for i in range(10, len(klines) - 1):
                close_price = float(klines[i].get("close", 0))
                prev_close = float(klines[i - 1].get("close", close_price))
                next_open = float(klines[i + 1].get("open", close_price))

                price_change_pct = (close_price - prev_close) / prev_close

                if price_change_pct > 0.001:
                    signal_direction = "LONG"
                elif price_change_pct < -0.001:
                    signal_direction = "SHORT"
                else:
                    continue

                pnl = (next_open - close_price) / close_price * self.config.position_size_usdt
                if signal_direction == "SHORT":
                    pnl = -pnl

                self.trades.append(
                    {
                        "symbol": symbol,
                        "entry": close_price,
                        "exit": next_open,
                        "direction": signal_direction,
                        "pnl": pnl,
                    }
                )

                self.current_capital += pnl
                self.equity_curve.append(self.current_capital)

                logger.info(
                    f"  {symbol} {signal_direction}: PnL ${pnl:.2f} | Capital ${self.current_capital:.2f}"
                )

        except Exception as e:
            logger.error(f"Error testing {symbol}: {e}")

    def _calc_metrics(self):
        if not self.trades:
            return

        pnls = [t["pnl"] for t in self.trades]
        winning = [p for p in pnls if p > 0]
        losing = [p for p in pnls if p < 0]

        self.total_trades = len(self.trades)
        self.winning_trades = len(winning)
        self.losing_trades = len(losing)
        self.total_pnl = sum(pnls)
        self.total_pnl_percent = (self.total_pnl / self.config.initial_capital) * 100

        if self.winning_trades > 0:
            self.win_rate = self.winning_trades / self.total_trades * 100
            self.avg_win = sum(winning) / len(winning)

        if self.losing_trades > 0:
            self.avg_loss = sum(losing) / len(losing)

        gross_profit = sum(winning) if winning else 0
        gross_loss = abs(sum(losing)) if losing else 1
        self.profit_factor = gross_profit / gross_loss

        if self.equity_curve:
            peak = self.equity_curve[0]
            max_dd = 0
            for eq in self.equity_curve:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak * 100
                if dd > max_dd:
                    max_dd = dd
            self.max_drawdown = max_dd

    def _get_metrics(self) -> BacktestMetrics:
        metrics = BacktestMetrics()
        metrics.total_trades = self.total_trades
        metrics.winning_trades = self.winning_trades
        metrics.losing_trades = self.losing_trades
        metrics.total_pnl = self.total_pnl
        metrics.total_pnl_percent = self.total_pnl_percent
        metrics.max_drawdown = self.max_drawdown
        metrics.win_rate = self.win_rate
        metrics.avg_win = self.avg_win
        metrics.avg_loss = self.avg_loss
        metrics.profit_factor = self.profit_factor
        return metrics

    def generate_report(self) -> str:
        metrics = self._get_metrics()

        report = [
            "📈 *快速回测报告*",
            f"⏰ {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC",
            "",
            f"💰 初始资金：${self.config.initial_capital:.2f}",
            f"📊 最终资金：${self.current_capital:.2f}",
            f"📉 总盈亏：${metrics.total_pnl:.2f} ({metrics.total_pnl_percent:.2f}%)",
            "",
            "📊 交易统计:",
            f"  总交易数：{metrics.total_trades}",
            f"  盈利：{metrics.winning_trades} | 亏损：{metrics.losing_trades}",
            f"  胜率：{metrics.win_rate:.1f}%",
            "",
            "📊 绩效指标:",
            f"  平均盈利：${metrics.avg_win:.2f}",
            f"  平均亏损：${metrics.avg_loss:.2f}",
            f"  盈亏比：{metrics.profit_factor:.2f}",
            f"  最大回撤：{metrics.max_drawdown:.1f}%",
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
            logger.error(f"Failed to send report: {e}")


def main():
    config = BacktestConfig(
        symbols=["BTCUSDT", "ETHUSDT", "BNBUSDT"],
        interval="1h",
        lookback_bars=50,
        initial_capital=10000.0,
        position_size_usdt=100.0,
    )

    backtest = QuickBacktest(config)
    backtest.run()
    backtest.send_report()

    print("\n" + "=" * 60)
    print(backtest.generate_report())
    print("=" * 60)


if __name__ == "__main__":
    main()
