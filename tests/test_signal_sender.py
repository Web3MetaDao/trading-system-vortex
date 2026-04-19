"""
Tests for signal_sender module
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class SignalSenderImportTests(unittest.TestCase):
    def test_import_signal_sender(self):
        try:
            from signal_sender import SignalSender

            self.assertTrue(True)
        except ImportError as e:
            self.fail(f"Failed to import SignalSender: {e}")


class SignalSenderTests(unittest.TestCase):
    @patch("signal_sender.TelegramNotifier")
    @patch("signal_sender.ExecutionEngine")
    @patch("signal_sender.MarketDataClient")
    @patch("signal_sender.UnifiedDataProvider")
    @patch("signal_sender.SignalEngine")
    @patch("signal_sender.RiskEngine")
    def test_init(self, mock_risk, mock_signal, mock_data, mock_market, mock_exec, mock_telegram):
        from signal_sender import SignalSender

        sender = SignalSender()
        self.assertIsNotNone(sender)
        self.assertFalse(sender.running)
        self.assertEqual(sender.interval, 300)

    def test_generate_report_structure(self):
        from signal_sender import SignalSender

        sender = SignalSender()
        report = sender.generate_signal_report()
        self.assertIsInstance(report, str)
        self.assertIn("交易信号报告", report)


if __name__ == "__main__":
    unittest.main()
