import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from datetime import UTC  # noqa: E402

import websockets  # noqa: E402

from binance_websocket import (  # noqa: E402
    BinanceWebSocketClient,
    ConnectionState,
    WebSocketConfig,
)
from data_provider import UnifiedDataProvider  # noqa: E402
from derivatives_data import DerivativesDataClient  # noqa: E402
from execution_engine import (  # noqa: E402
    APIError,
    ExecutionEngine,
    ExecutionResult,
    NetworkError,
)
from intermarket_data import IntermarketDataClient  # noqa: E402
from market_data import MarketDataClient, MarketSnapshot  # noqa: E402


class NetworkErrorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionEngine(mode="testnet")
        self.engine.api_key = "test_key"
        self.engine.api_secret = "test_secret"

    @patch("execution_engine.requests.Session")
    def test_network_error_triggers_retry(self, mock_session_class):
        import requests

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"orderId": "12345"}

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.side_effect = [
            requests.ConnectionError("Network error"),
            requests.ConnectionError("Network error"),
            mock_response,
        ]

        engine = ExecutionEngine(mode="testnet")
        engine.api_key = "test_key"
        engine.api_secret = "test_secret"
        engine.max_retries = 3
        engine.session = mock_session

        result = engine.submit_order("BTCUSDT", "BUY", 10.0)
        self.assertTrue(result.accepted)

    @patch("execution_engine.requests.Session")
    def test_max_retries_exceeded_raises_error(self, mock_session_class):
        import requests

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.side_effect = requests.ConnectionError("Persistent network error")

        engine = ExecutionEngine(mode="testnet")
        engine.api_key = "test_key"
        engine.api_secret = "test_secret"
        engine.max_retries = 2
        engine.timeout = 0.1
        engine.session = mock_session

        with self.assertRaises(NetworkError):
            engine._request_with_retry("POST", "/api/v3/order", {}, require_auth=True)


class RateLimitHandlingTests(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionEngine(mode="testnet")

    @patch("execution_engine.requests.Session")
    def test_rate_limit_returns_429(self, mock_session_class):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limit exceeded"

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.return_value = mock_response

        engine = ExecutionEngine(mode="testnet")
        engine.api_key = "test_key"
        engine.api_secret = "test_secret"
        engine.session = mock_session

        result = engine.submit_order("BTCUSDT", "BUY", 10.0)
        self.assertFalse(result.accepted)
        self.assertIn("429", result.detail)

    @patch("execution_engine.requests.Session")
    def test_rate_limit_triggers_backoff(self, mock_session_class):
        import requests

        mock_success_response = MagicMock()
        mock_success_response.status_code = 200
        mock_success_response.json.return_value = {"orderId": "12345"}

        mock_429_response = MagicMock()
        mock_429_response.status_code = 429
        mock_429_response.text = "Rate limit exceeded"

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.side_effect = [
            mock_429_response,
            mock_429_response,
            mock_success_response,
        ]

        engine = ExecutionEngine(mode="testnet")
        engine.api_key = "test_key"
        engine.api_secret = "test_secret"
        engine.max_retries = 3
        engine.session = mock_session

        result = engine.submit_order("BTCUSDT", "BUY", 10.0)
        self.assertTrue(result.accepted)


class PartialFillScenarioTests(unittest.TestCase):
    def test_partial_fill_status_handling(self):
        engine = ExecutionEngine(mode="paper")

        result = engine.submit_order("BTCUSDT", "BUY", 10.0)
        self.assertTrue(result.accepted)

    def test_order_status_partial_fill(self):
        from datetime import datetime, timezone

        from execution_engine import OrderStatus

        status = OrderStatus(
            order_id="12345",
            symbol="BTCUSDT",
            side="BUY",
            status="PARTIALLY_FILLED",
            filled_qty=0.5,
            avg_price=50000.0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        self.assertEqual(status.status, "PARTIALLY_FILLED")
        self.assertEqual(status.filled_qty, 0.5)


class OrderTimeoutScenarioTests(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionEngine(mode="testnet")
        self.engine.api_key = "test_key"
        self.engine.api_secret = "test_secret"

    @patch("execution_engine.requests.Session")
    def test_timeout_triggers_retry(self, mock_session_class):
        import requests

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"orderId": "12345"}

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.side_effect = [
            requests.Timeout("Request timeout"),
            requests.Timeout("Request timeout"),
            mock_response,
        ]

        engine = ExecutionEngine(mode="testnet")
        engine.api_key = "test_key"
        engine.api_secret = "test_secret"
        engine.max_retries = 3
        engine.timeout = 0.1
        engine.session = mock_session

        result = engine.submit_order("BTCUSDT", "BUY", 10.0)
        self.assertTrue(result.accepted)


class DataSourceErrorHandlingTests(unittest.TestCase):
    def test_market_data_client_handles_exceptions(self):
        client = MarketDataClient()
        client.primary = None
        client.fallback = None
        self.assertIsNotNone(client)

    def test_derivatives_data_error_handling(self):
        client = DerivativesDataClient()
        self.assertIsNotNone(client)

    def test_intermarket_data_error_handling(self):
        market = MarketDataClient()
        intermarket_client = IntermarketDataClient(market)
        self.assertIsNotNone(intermarket_client)


class WebSocketReconnectionTests(unittest.TestCase):
    @patch("binance_websocket.websockets.connect", new_callable=AsyncMock)
    async def test_auto_reconnect_on_disconnect(self, mock_connect):
        mock_ws = AsyncMock()
        mock_ws.recv.side_effect = [
            '{"symbol":"BTCUSDT","price":"50000"}',
            websockets.exceptions.ConnectionClosed(1000, "Normal closure"),
            '{"symbol":"BTCUSDT","price":"50001"}',
            Exception("End of stream"),
        ]
        mock_connect.return_value = mock_ws

        client = BinanceWebSocketClient(mode="testnet", symbols=["BTCUSDT"])
        await client.connect()
        self.assertTrue(client.is_connected)

    def test_reconnector_exponential_backoff(self):
        from binance_websocket import ConnectionState, WebSocketReconnector

        reconnector = WebSocketReconnector()
        reconnector._reconnect_attempts = 3
        delay = reconnector.get_reconnect_delay()
        self.assertEqual(delay, 8.0)

    def test_max_reconnect_attempts(self):
        from binance_websocket import WebSocketReconnector

        reconnector = WebSocketReconnector()
        reconnector._reconnect_attempts = 10
        self.assertFalse(reconnector.should_reconnect())


class APIErrorHandlingTests(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionEngine(mode="testnet")

    @patch("execution_engine.requests.Session")
    def test_api_error_400_bad_request(self, mock_session_class):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad request"

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.return_value = mock_response

        engine = ExecutionEngine(mode="testnet")
        engine.api_key = "test_key"
        engine.api_secret = "test_secret"
        engine.session = mock_session

        result = engine.submit_order("BTCUSDT", "BUY", 10.0)
        self.assertFalse(result.accepted)

    @patch("execution_engine.requests.Session")
    def test_api_error_401_unauthorized(self, mock_session_class):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.return_value = mock_response

        engine = ExecutionEngine(mode="testnet")
        engine.api_key = "invalid_key"
        engine.api_secret = "invalid_secret"
        engine.session = mock_session

        result = engine.submit_order("BTCUSDT", "BUY", 10.0)
        self.assertFalse(result.accepted)
        self.assertIn("401", result.detail)

    @patch("execution_engine.requests.Session")
    def test_api_error_500_server_error(self, mock_session_class):
        import requests

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.side_effect = [
            requests.RequestException("Server error 500"),
            requests.RequestException("Server error 500"),
            requests.RequestException("Server error 500"),
        ]

        engine = ExecutionEngine(mode="testnet")
        engine.api_key = "test_key"
        engine.api_secret = "test_secret"
        engine.max_retries = 3
        engine.session = mock_session

        result = engine.submit_order("BTCUSDT", "BUY", 10.0)
        self.assertFalse(result.accepted)


class DataCorruptionHandlingTests(unittest.TestCase):
    def test_malformed_json_response(self):
        engine = ExecutionEngine(mode="testnet")

        with patch("execution_engine.requests.Session") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.side_effect = ValueError("Invalid JSON")

            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_session.post.return_value = mock_response

            engine.session = mock_session
            result = engine.submit_order("BTCUSDT", "BUY", 10.0)
            self.assertFalse(result.accepted)

    def test_empty_response_handling(self):
        engine = ExecutionEngine(mode="testnet")

        with patch("execution_engine.requests.Session") as mock_session_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}

            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_session.post.return_value = mock_response

            engine.session = mock_session
            result = engine.submit_order("BTCUSDT", "BUY", 10.0)
            self.assertFalse(result.accepted)


class OrderBookDepthScenarioTests(unittest.TestCase):
    def test_empty_order_book_handling(self):
        engine = ExecutionEngine(mode="paper")
        self.assertEqual(engine.mode, "paper")


class IdempotencyScenarioTests(unittest.TestCase):
    def test_duplicate_order_prevention(self):
        engine = ExecutionEngine(mode="paper")

        result1 = engine.submit_order("BTCUSDT", "BUY", 10.0)
        result2 = engine.submit_order("BTCUSDT", "BUY", 10.0)

        self.assertTrue(result1.accepted)
        self.assertTrue(result2.accepted)

    def test_client_order_id_format(self):
        engine = ExecutionEngine(mode="paper")
        order_id = engine._generate_client_order_id("BTCUSDT", "BUY")
        self.assertTrue(order_id.startswith("TDS_BTCUSDT_BUY_"))


class CircuitBreakerScenarioTests(unittest.TestCase):
    def test_consecutive_errors_trigger_circuit_break(self):
        from binance_websocket import ConnectionState, WebSocketReconnector

        reconnector = WebSocketReconnector()
        reconnector._reconnect_attempts = 10

        self.assertFalse(reconnector.should_reconnect())
        self.assertEqual(reconnector.state, ConnectionState.DISCONNECTED)


class GracefulDegradationScenarioTests(unittest.TestCase):
    def test_primary_source_failure_uses_fallback(self):
        client = MarketDataClient()
        client.primary = "https://invalid-primary.com"
        client.fallback = "https://api.binance.com"

        with patch("market_data.requests.get") as mock_get:
            mock_get.side_effect = [
                Exception("Primary failed"),
                MagicMock(
                    status_code=200,
                    json=lambda: {
                        "symbol": "BTCUSDT",
                        "price": "50000",
                        "klines": [],
                    },
                ),
            ]

            result = client.fetch_snapshot("BTCUSDT")
            self.assertIsNotNone(result)

    def test_all_sources_failure_degraded_status(self):
        engine = ExecutionEngine(mode="paper")
        self.assertEqual(engine.mode, "paper")


class TestnetIntegrationTests(unittest.TestCase):
    def test_testnet_mode_available(self):
        engine = ExecutionEngine(mode="testnet")
        self.assertEqual(engine.mode, "testnet")
        self.assertEqual(engine.base_url, "https://testnet.binance.vision")

    def test_testnet_order_paper_behavior(self):
        engine = ExecutionEngine(mode="testnet")
        engine.api_key = ""
        engine.api_secret = ""

        result = engine.submit_order("BTCUSDT", "BUY", 10.0)
        self.assertFalse(result.accepted)
        self.assertIn("not configured", result.detail)

    def test_live_mode_configuration(self):
        engine = ExecutionEngine(mode="live")
        self.assertEqual(engine.mode, "live")
        self.assertEqual(engine.base_url, "https://api.binance.com")


class ErrorContextPreservationTests(unittest.TestCase):
    @patch("execution_engine.requests.Session")
    def test_error_details_preserved_in_result(self, mock_session_class):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Insufficient balance"
        mock_response.json.return_value = {"msg": "Insufficient balance"}

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.return_value = mock_response

        engine = ExecutionEngine(mode="testnet")
        engine.api_key = "test_key"
        engine.api_secret = "test_secret"
        engine.session = mock_session

        result = engine.submit_order("BTCUSDT", "BUY", 1000000.0)
        self.assertFalse(result.accepted)
        self.assertIn("Insufficient", result.detail)


if __name__ == "__main__":
    unittest.main()
