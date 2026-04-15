import logging
import os
import sys
import time
from datetime import UTC, datetime
from threading import Thread

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = ROOT
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("DOTENV_CONFIG", ".env")
load_dotenv()

from data_provider import UnifiedDataProvider
from execution_engine import ExecutionEngine
from market_data import MarketDataClient
from risk_engine import RiskEngine
from signal_engine import SignalEngine
from telegram_notifier import TelegramAlertLevel, TelegramNotifier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("signal_sender")


class SignalSender:
    def __init__(self, interval_seconds: int = 300):
        self.interval = interval_seconds
        self.running = False
        self.thread: Thread | None = None

        self.execution_engine = ExecutionEngine(mode="paper")
        self.market_data = MarketDataClient()
        self.data_provider = UnifiedDataProvider(client=self.market_data)
        self.signal_engine = SignalEngine()
        self.risk_engine = RiskEngine({})

        self.telegram = TelegramNotifier(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            enabled=True,
        )

    def generate_signal_report(self) -> str:
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        report_lines = [
            "📊 *交易信号报告*",
            f"⏰ {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC",
            "",
        ]

        for symbol in symbols:
            try:
                snapshot = self.market_data.fetch_snapshot(symbol)
                if not snapshot or snapshot.price is None:
                    continue

                context = self.data_provider.build_context(
                    benchmark_symbol="BTCUSDT",
                    watchlist=[symbol],
                    state_interval="1h",
                    state_limit=20,
                    signal_interval="1h",
                    signal_limit=20,
                )

                signal = self.signal_engine.evaluate(
                    symbol,
                    "normal",
                    {
                        "snapshot": snapshot,
                        "benchmark_snapshot": context.benchmark_snapshot,
                        "intermarket": context.intermarket,
                        "derivatives": context.derivatives,
                        "data_health": context.data_health,
                    },
                )

                status = "✅" if signal.grade in ("A", "B") else "❌"
                report_lines.append(
                    f"{status} `{symbol}` | Grade: {signal.grade} | Score: {signal.score}"
                )
                report_lines.append(f"   方向：{signal.side} | 理由：{signal.reason[:30]}")

                if signal.grade in ("A", "B"):
                    risk_decision = self.risk_engine.can_open_position(
                        portfolio=self.execution_engine,
                        symbol=symbol,
                        requested_size_usdt=10.0,
                        signal_grade=signal.grade,
                        data_health=context.data_health,
                    )
                    if risk_decision.approved:
                        report_lines.append(
                            f"   ✅ 风控通过 | 建议仓位: {risk_decision.position_size_usdt:.2f} USDT"
                        )
                    else:
                        report_lines.append(f"   🚫 风控拒绝: {risk_decision.reason}")

            except Exception as e:
                report_lines.append(f"❌ `{symbol}` | 错误: {str(e)[:80]}")

            report_lines.append("")

        return "\n".join(report_lines)

    def send_report(self):
        try:
            report = self.generate_signal_report()
            self.telegram.send(
                report,
                level=TelegramAlertLevel.INFO if "✅" in report else TelegramAlertLevel.WARNING,
            )
            logger.info(f"Signal report sent at {datetime.now(UTC).isoformat()}")
        except Exception as e:
            logger.error(f"Failed to send signal report: {e}")

    def _worker(self):
        while self.running:
            self.send_report()
            for _ in range(self.interval):
                if self.running:
                    time.sleep(1)

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = Thread(target=self._worker, daemon=True)
        self.thread.start()
        logger.info(f"Signal sender started (interval: {self.interval}s)")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Signal sender stopped")


if __name__ == "__main__":
    sender = SignalSender(interval_seconds=300)
    sender.start()
    logger.info("Press Ctrl+C to stop")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sender.stop()
