import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from execution_engine import (
    AccountInfo,
    APIError,
    BinanceSigner,
    ExecutionEngine,
    ExecutionError,
    ExecutionResult,
    NetworkError,
    OrderStatus,
    PositionInfo,
    TradeValidation,
)


class BinanceSignerTests(unittest.TestCase):
    def test_sign_produces_consistent_signature(self):
        signer = BinanceSigner("test_secret_key")
        params = {"symbol": "BTCUSDT", "side": "BUY", "quantity": 10.0}
        sig1 = signer.sign(params)
        sig2 = signer.sign(params)
        self.assertEqual(sig1, sig2)
        self.assertIsInstance(sig1, str)
        self.assertTrue(len(sig1) > 0)

    def test_different_params_produce_different_signatures(self):
        signer = BinanceSigner("test_secret_key")
        params1 = {"symbol": "BTCUSDT", "side": "BUY", "quantity": 10.0}
        params2 = {"symbol": "ETHUSDT", "side": "BUY", "quantity": 10.0}
        sig1 = signer.sign(params1)
        sig2 = signer.sign(params2)
        self.assertNotEqual(sig1, sig2)


class ExecutionEnginePaperModeTests(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionEngine(mode="paper")

    def test_paper_submit_returns_accepted(self):
        result = self.engine.submit_order("BTCUSDT", "BUY", 10.0)
        self.assertIsInstance(result, ExecutionResult)
        self.assertTrue(result.accepted)
        self.assertEqual(result.mode, "paper")
        self.assertIn("PAPER", result.detail)

    def test_paper_close_returns_accepted(self):
        result = self.engine.close_order("BTCUSDT", "SELL", 10.0, "stop_loss")
        self.assertIsInstance(result, ExecutionResult)
        self.assertTrue(result.accepted)
        self.assertEqual(result.mode, "paper")
        self.assertIn("PAPER", result.detail)
        self.assertIn("stop_loss", result.detail)


class ExecutionEngineTestnetModeTests(unittest.TestCase):
    def test_testnet_without_credentials_returns_not_accepted(self):
        engine = ExecutionEngine(mode="testnet")
        engine.api_key = ""
        engine.api_secret = ""

        result = engine.submit_order("BTCUSDT", "BUY", 10.0)
        self.assertIsInstance(result, ExecutionResult)
        self.assertFalse(result.accepted)
        self.assertIn("not configured", result.detail)

    def test_testnet_close_without_credentials(self):
        engine = ExecutionEngine(mode="testnet")
        engine.api_key = ""
        engine.api_secret = ""

        result = engine.close_order("BTCUSDT", "SELL", 10.0, "take_profit")
        self.assertIsInstance(result, ExecutionResult)
        self.assertFalse(result.accepted)
        self.assertIn("not configured", result.detail)


class ExecutionEngineIdempotencyTests(unittest.TestCase):
    def test_client_order_id_format(self):
        engine = ExecutionEngine(mode="paper")
        order_id = engine._generate_client_order_id("BTCUSDT", "BUY")
        self.assertTrue(order_id.startswith("TDS_BTCUSDT_BUY_"))
        self.assertTrue(len(order_id.split("_")) >= 4)

    def test_idempotency_cache_initialization(self):
        engine = ExecutionEngine(mode="testnet")
        self.assertIsInstance(engine._idempotency_cache, dict)
        self.assertEqual(len(engine._idempotency_cache), 0)


class ExecutionEngineRetryTests(unittest.TestCase):
    @patch("execution_engine.requests.Session")
    def test_request_retry_on_network_error(self, mock_session_class):
        import requests

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "success"}

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.side_effect = [
            requests.ConnectionError("Network error"),
            requests.ConnectionError("Network error"),
            mock_response,
        ]

        engine = ExecutionEngine(mode="testnet")
        engine.api_key = "test_key"
        engine.api_secret = "test_secret"
        engine.max_retries = 3
        engine.session = mock_session

        with patch.object(engine, "_sign_request", return_value={"sig": "test"}):
            result = engine._request_with_retry("GET", "/api/v3/order", {}, require_auth=True)
            self.assertEqual(result, {"data": "success"})

    @patch("execution_engine.requests.Session")
    def test_request_fails_after_max_retries(self, mock_session_class):
        import requests

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.side_effect = requests.ConnectionError("Persistent network error")

        engine = ExecutionEngine(mode="testnet")
        engine.api_key = "test_key"
        engine.api_secret = "test_secret"
        engine.max_retries = 2
        engine.timeout = 0.1
        engine.session = mock_session

        with patch.object(engine, "_sign_request", return_value={"sig": "test"}):
            with self.assertRaises(NetworkError):
                engine._request_with_retry("GET", "/api/v3/order", {}, require_auth=True)


class ExecutionEngineCancelTests(unittest.TestCase):
    def test_paper_cancel_returns_accepted(self):
        engine = ExecutionEngine(mode="paper")
        result = engine.cancel_order("BTCUSDT", "12345")
        self.assertTrue(result.accepted)
        self.assertIn("PAPER", result.detail)

    def test_testnet_cancel_without_credentials(self):
        engine = ExecutionEngine(mode="testnet")
        engine.api_key = ""
        engine.api_secret = ""

        result = engine.cancel_order("BTCUSDT", "12345")
        self.assertFalse(result.accepted)
        self.assertIn("not configured", result.detail)


class ExecutionEngineGetOrderStatusTests(unittest.TestCase):
    def test_paper_mode_returns_none(self):
        engine = ExecutionEngine(mode="paper")
        result = engine.get_order_status("BTCUSDT", "12345")
        self.assertIsNone(result)

    def test_missing_credentials_returns_none(self):
        engine = ExecutionEngine(mode="testnet")
        engine.api_key = ""
        engine.api_secret = ""

        result = engine.get_order_status("BTCUSDT", "12345")
        self.assertIsNone(result)


class ExecutionEngineSignatureTests(unittest.TestCase):
    def test_sign_request_adds_timestamp_and_recv_window(self):
        signer = BinanceSigner("test_secret")
        engine = ExecutionEngine(mode="testnet")
        engine.signer = signer
        engine.api_key = "test_key"

        params = {"symbol": "BTCUSDT", "quantity": 10.0}
        signed = engine._sign_request(params.copy())

        self.assertIn("timestamp", signed)
        self.assertIn("recvWindow", signed)
        self.assertIn("signature", signed)

    def test_sign_request_without_signer_returns_unchanged(self):
        engine = ExecutionEngine(mode="testnet")
        engine.signer = None

        params = {"symbol": "BTCUSDT", "quantity": 10.0}
        signed = engine._sign_request(params.copy())

        self.assertEqual(signed, params)


if __name__ == "__main__":
    unittest.main()


class ExecutionEngineLiveModeTests(unittest.TestCase):
    def test_fetch_positions_paper_mode_returns_empty(self):
        engine = ExecutionEngine(mode="paper")
        result = engine.fetch_positions("BTCUSDT")
        self.assertEqual(result, [])

    def test_fetch_positions_missing_credentials_returns_empty(self):
        engine = ExecutionEngine(mode="live")
        engine.api_key = ""
        engine.api_secret = ""
        result = engine.fetch_positions("BTCUSDT")
        self.assertEqual(result, [])

    def test_fetch_account_info_paper_mode_returns_none(self):
        engine = ExecutionEngine(mode="paper")
        result = engine.fetch_account_info()
        self.assertIsNone(result)

    def test_fetch_account_info_missing_credentials_returns_none(self):
        engine = ExecutionEngine(mode="live")
        engine.api_key = ""
        engine.api_secret = ""
        result = engine.fetch_account_info()
        self.assertIsNone(result)


class TradeValidationTests(unittest.TestCase):
    def test_paper_mode_validation_always_approved(self):
        engine = ExecutionEngine(mode="paper")
        result = engine.validate_trade_pre_conditions("BTCUSDT", "BUY", 10.0)
        self.assertIsInstance(result, TradeValidation)
        self.assertTrue(result.approved)
        self.assertTrue(result.account_ready)
        self.assertTrue(result.positions_synced)

    def test_missing_credentials_validation_rejected(self):
        engine = ExecutionEngine(mode="live")
        engine.api_key = ""
        engine.api_secret = ""
        result = engine.validate_trade_pre_conditions("BTCUSDT", "BUY", 10.0)
        self.assertIsInstance(result, TradeValidation)
        self.assertFalse(result.approved)
        self.assertFalse(result.account_ready)
        self.assertFalse(result.positions_synced)


class SetLeverageTests(unittest.TestCase):
    def test_paper_mode_set_leverage_returns_accepted(self):
        engine = ExecutionEngine(mode="paper")
        result = engine.set_leverage("BTCUSDT", 10)
        self.assertTrue(result.accepted)
        self.assertIn("PAPER", result.detail)
        self.assertIn("10x", result.detail)

    def test_testnet_missing_credentials_returns_not_accepted(self):
        engine = ExecutionEngine(mode="testnet")
        engine.api_key = ""
        engine.api_secret = ""
        result = engine.set_leverage("BTCUSDT", 5)
        self.assertFalse(result.accepted)
        self.assertIn("not configured", result.detail)
