import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TelegramNotifierImportTests(unittest.TestCase):
    def test_import_telegram_notifier(self):
        try:
            from telegram_notifier import TelegramNotifier, TelegramAlertLevel
            self.assertIsNotNone(TelegramNotifier)
            self.assertIsNotNone(TelegramAlertLevel)
        except ImportError:
            self.skipTest("telegram_notifier not available")

    def test_telegram_alert_level_values(self):
        try:
            from telegram_notifier import TelegramAlertLevel
            self.assertEqual(TelegramAlertLevel.INFO.value, "INFO")
            self.assertEqual(TelegramAlertLevel.WARNING.value, "WARNING")
            self.assertEqual(TelegramAlertLevel.ERROR.value, "ERROR")
            self.assertEqual(TelegramAlertLevel.CRITICAL.value, "CRITICAL")
        except ImportError:
            self.skipTest("telegram_notifier not available")


class TelegramNotifierInitTests(unittest.TestCase):
    @patch("telegram_notifier.telegram")
    def test_init_without_token_disabled(self, mock_telegram):
        try:
            from telegram_notifier import TelegramNotifier
            TelegramNotifier.reset_instance()

            notifier = TelegramNotifier(
                bot_token="",
                chat_id="",
                enabled=False,
            )
            self.assertFalse(notifier.is_enabled)
        except ImportError:
            self.skipTest("python-telegram-bot not installed")

    @patch("telegram_notifier.telegram")
    def test_init_with_credentials_enabled(self, mock_telegram):
        try:
            from telegram_notifier import TELEGRAM_AVAILABLE
            if not TELEGRAM_AVAILABLE:
                self.skipTest("python-telegram-bot not installed")

            from telegram_notifier import TelegramNotifier
            TelegramNotifier.reset_instance()

            mock_bot = MagicMock()
            mock_telegram.Bot.return_value = mock_bot

            notifier = TelegramNotifier(
                bot_token="test_token",
                chat_id="test_chat_id",
                enabled=True,
            )
            self.assertTrue(notifier.is_enabled)
            self.assertEqual(notifier.bot_token, "test_token")
            self.assertEqual(notifier.chat_id, "test_chat_id")
        except ImportError:
            self.skipTest("python-telegram-bot not installed")


class TelegramNotifierFormatTests(unittest.TestCase):
    def setUp(self):
        try:
            from telegram_notifier import TelegramNotifier
            TelegramNotifier.reset_instance()
            self.notifier = TelegramNotifier(enabled=False)
        except ImportError:
            self.skipTest("python-telegram-bot not installed")

    def test_format_trade_alert(self):
        text = self.notifier.format_trade_alert(
            symbol="BTCUSDT",
            side="BUY",
            quantity=0.1,
            price=50000.0,
            result="SUCCESS",
        )
        self.assertIn("BTCUSDT", text)
        self.assertIn("BUY", text)
        self.assertIn("0.1", text)
        self.assertIn("50000", text)

    def test_format_signal_alert(self):
        text = self.notifier.format_signal_alert(
            symbol="ETHUSDT",
            grade="A",
            score=8.5,
            market_state="S1",
        )
        self.assertIn("ETHUSDT", text)
        self.assertIn("A", text)
        self.assertIn("8.5", text)

    def test_format_risk_alert_approved(self):
        text = self.notifier.format_risk_alert(
            symbol="BTCUSDT",
            approved=True,
            reason=None,
            size_usdt=100.0,
        )
        self.assertIn("BTCUSDT", text)
        self.assertIn("APPROVED", text)

    def test_format_risk_alert_rejected(self):
        text = self.notifier.format_risk_alert(
            symbol="BTCUSDT",
            approved=False,
            reason="Insufficient balance",
            size_usdt=100.0,
        )
        self.assertIn("BTCUSDT", text)
        self.assertIn("REJECTED", text)
        self.assertIn("Insufficient balance", text)

    def test_format_data_health_alert_degraded(self):
        text = self.notifier.format_data_health_alert(
            status="degraded",
            details={"binance": False},
        )
        self.assertIn("DEGRADED", text)
        self.assertIn("binance", text)

    def test_format_error_alert(self):
        text = self.notifier.format_error_alert(
            error_type="NETWORK_ERROR",
            message="Connection timeout",
            context={"url": "https://api.binance.com"},
        )
        self.assertIn("NETWORK_ERROR", text)
        self.assertIn("Connection timeout", text)

    def test_format_position_alert(self):
        text = self.notifier.format_position_alert(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.1,
            entry_price=50000.0,
            current_price=51000.0,
            pnl_pct=2.0,
            action="HOLD",
        )
        self.assertIn("BTCUSDT", text)
        self.assertIn("LONG", text)
        self.assertIn("2.0", text)

    def test_format_daily_summary(self):
        text = self.notifier.format_daily_summary(
            total_trades=10,
            winning_trades=7,
            losing_trades=3,
            total_pnl=25.5,
            open_positions=2,
            portfolio_value=1025.5,
        )
        self.assertIn("10", text)
        self.assertIn("7", text)
        self.assertIn("3", text)
        self.assertIn("25.5", text)


class TelegramNotifierSendTests(unittest.TestCase):
    def setUp(self):
        try:
            from telegram_notifier import TelegramNotifier
            TelegramNotifier.reset_instance()
            self.notifier = TelegramNotifier(enabled=False)
        except ImportError:
            self.skipTest("python-telegram-bot not installed")

    def test_send_when_disabled(self):
        result = self.notifier.send("Test message")
        self.assertFalse(result)

    def test_send_trade_alert_when_disabled(self):
        result = self.notifier.send_trade_alert(
            symbol="BTCUSDT",
            side="BUY",
            quantity=0.1,
            price=50000.0,
        )
        self.assertFalse(result)

    def test_send_signal_alert_when_disabled(self):
        result = self.notifier.send_signal_alert(
            symbol="ETHUSDT",
            grade="A",
            score=8.5,
            market_state="S1",
        )
        self.assertFalse(result)

    def test_send_risk_alert_when_disabled(self):
        result = self.notifier.send_risk_alert(
            symbol="BTCUSDT",
            approved=False,
            reason="Insufficient balance",
        )
        self.assertFalse(result)

    def test_send_data_health_alert_when_disabled(self):
        result = self.notifier.send_data_health_alert(
            status="degraded",
            details={"binance": False},
        )
        self.assertFalse(result)

    def test_send_error_alert_when_disabled(self):
        result = self.notifier.send_error_alert(
            error_type="NETWORK_ERROR",
            message="Connection timeout",
        )
        self.assertFalse(result)

    def test_is_available_property(self):
        try:
            from telegram_notifier import TELEGRAM_AVAILABLE
            self.assertIsInstance(TELEGRAM_AVAILABLE, bool)
        except ImportError:
            pass


if __name__ == "__main__":
    unittest.main()
