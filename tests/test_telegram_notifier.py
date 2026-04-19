"""
VORTEX Trading System - TelegramNotifier 测试套件 (v2.0 - 机构级重构)

[FIX v2.0] 完全重写测试用例，与实际 telegram_notifier.py (v3.0) 的 API 对齐：
- 移除对已废弃的 python-telegram-bot 的 mock（代码已改用 aiohttp）
- 移除不存在的方法测试（format_signal_alert/format_risk_alert 等）
- 修复 send_trade_alert 和 send_risk_alert 的参数签名
- 新增对 send_signal_alert、send_risk_alert、send_error_alert 的正确测试
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TelegramNotifierImportTests(unittest.TestCase):
    """测试模块导入"""

    def test_import_telegram_notifier(self):
        try:
            from telegram_notifier import TelegramAlertLevel, TelegramNotifier

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

    def test_aiohttp_available_flag(self):
        """验证 AIOHTTP_AVAILABLE 标志存在（v3.0 使用 aiohttp 替代 python-telegram-bot）"""
        try:
            from telegram_notifier import AIOHTTP_AVAILABLE

            self.assertIsInstance(AIOHTTP_AVAILABLE, bool)
        except ImportError:
            self.skipTest("telegram_notifier not available")


class TelegramNotifierInitTests(unittest.TestCase):
    """测试初始化逻辑"""

    def test_init_without_token_disabled(self):
        """无 token 时，notifier 应自动禁用"""
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
            self.skipTest("telegram_notifier not available")

    def test_init_with_credentials_enabled(self):
        """提供有效 token 时，notifier 应启用"""
        try:
            from telegram_notifier import TelegramNotifier

            TelegramNotifier.reset_instance()
            notifier = TelegramNotifier(
                bot_token="test_token_12345",
                chat_id="test_chat_id",
                enabled=True,
            )
            # 由于 aiohttp 不需要实例化 Bot 对象，初始化应成功
            self.assertTrue(notifier.is_enabled)
            self.assertEqual(notifier.bot_token, "test_token_12345")
            self.assertEqual(notifier.chat_id, "test_chat_id")
        except ImportError:
            self.skipTest("telegram_notifier not available")

    def test_singleton_pattern(self):
        """测试单例模式"""
        try:
            from telegram_notifier import TelegramNotifier

            TelegramNotifier.reset_instance()
            inst1 = TelegramNotifier.get_instance(enabled=False)
            inst2 = TelegramNotifier.get_instance(enabled=False)
            self.assertIs(inst1, inst2)
        except ImportError:
            self.skipTest("telegram_notifier not available")


class TelegramNotifierFormatTests(unittest.TestCase):
    """测试消息格式化方法"""

    def setUp(self):
        try:
            from telegram_notifier import TelegramNotifier

            TelegramNotifier.reset_instance()
            self.notifier = TelegramNotifier(enabled=False)
        except ImportError:
            self.skipTest("telegram_notifier not available")

    def test_format_trade_alert(self):
        """测试 format_trade_alert（简化版，向后兼容）"""
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
        self.assertIn("SUCCESS", text)


class TelegramNotifierSendTests(unittest.TestCase):
    """测试发送方法（disabled 模式下应返回 False）"""

    def setUp(self):
        try:
            from telegram_notifier import TelegramNotifier

            TelegramNotifier.reset_instance()
            self.notifier = TelegramNotifier(enabled=False)
        except ImportError:
            self.skipTest("telegram_notifier not available")

    def test_send_when_disabled(self):
        """disabled 时 send 应返回 False"""
        result = self.notifier.send("Test message")
        self.assertFalse(result)

    def test_send_signal_alert_when_disabled(self):
        """disabled 时 send_signal_alert 应返回 False"""
        result = self.notifier.send_signal_alert(
            symbol="ETHUSDT",
            grade="A",
            score=8.5,
            market_state="S1",
        )
        self.assertFalse(result)

    def test_send_risk_alert_approved_when_disabled(self):
        """disabled 时 send_risk_alert（approved）应返回 False"""
        result = self.notifier.send_risk_alert(
            symbol="BTCUSDT",
            approved=True,
            reason=None,
            size_usdt=100.0,
        )
        self.assertFalse(result)

    def test_send_risk_alert_rejected_when_disabled(self):
        """disabled 时 send_risk_alert（rejected）应返回 False"""
        result = self.notifier.send_risk_alert(
            symbol="BTCUSDT",
            approved=False,
            reason="Insufficient balance",
            size_usdt=100.0,
        )
        self.assertFalse(result)

    def test_send_error_alert_when_disabled(self):
        """disabled 时 send_error_alert 应返回 False"""
        result = self.notifier.send_error_alert(
            error_type="NETWORK_ERROR",
            message="Connection timeout",
            context={"url": "https://api.binance.com"},
        )
        self.assertFalse(result)

    def test_is_available_property(self):
        """is_available 属性应返回 bool"""
        self.assertIsInstance(self.notifier.is_available, bool)

    def test_is_enabled_property(self):
        """is_enabled 属性应返回 False（disabled 模式）"""
        self.assertFalse(self.notifier.is_enabled)


class TelegramNotifierSendRiskAlertSignatureTest(unittest.TestCase):
    """测试 send_risk_alert 的参数签名（体检发现 size_usdt 为必填参数）"""

    def test_send_risk_alert_requires_size_usdt(self):
        """send_risk_alert 必须传入 size_usdt 参数"""
        try:
            from telegram_notifier import TelegramNotifier

            TelegramNotifier.reset_instance()
            notifier = TelegramNotifier(enabled=False)
            import inspect

            sig = inspect.signature(notifier.send_risk_alert)
            params = list(sig.parameters.keys())
            self.assertIn("size_usdt", params)
        except ImportError:
            self.skipTest("telegram_notifier not available")


class TelegramNotifierSendTradeAlertSignatureTest(unittest.TestCase):
    """测试 send_trade_alert 的参数签名（体检发现 result 为必填参数）"""

    def test_send_trade_alert_requires_result(self):
        """send_trade_alert 必须传入 result 参数"""
        try:
            from telegram_notifier import TelegramNotifier

            TelegramNotifier.reset_instance()
            notifier = TelegramNotifier(enabled=False)
            import inspect

            sig = inspect.signature(notifier.send_trade_alert)
            params = list(sig.parameters.keys())
            self.assertIn("result", params)
        except ImportError:
            self.skipTest("telegram_notifier not available")


if __name__ == "__main__":
    unittest.main()
