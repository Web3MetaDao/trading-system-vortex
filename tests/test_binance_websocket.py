import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from binance_websocket import (
    BinanceWebSocketClient,
    WebSocketConfig,
    WebSocketReconnector,
    ConnectionState,
    PositionSyncManager,
    WebSocketMessage,
)


class WebSocketConfigTests(unittest.TestCase):
    def test_default_config(self):
        config = WebSocketConfig()
        self.assertEqual(config.ping_interval, 30.0)
        self.assertEqual(config.ping_timeout, 10.0)
        self.assertEqual(config.reconnect_delay, 1.0)
        self.assertEqual(config.max_reconnect_delay, 60.0)
        self.assertEqual(config.max_reconnect_attempts, 10)

    def test_custom_config(self):
        config = WebSocketConfig(
            ping_interval=60.0,
            max_reconnect_attempts=5,
        )
        self.assertEqual(config.ping_interval, 60.0)
        self.assertEqual(config.max_reconnect_attempts, 5)


class WebSocketReconnectorTests(unittest.TestCase):
    def test_initial_state(self):
        reconnector = WebSocketReconnector()
        self.assertEqual(reconnector.state, ConnectionState.DISCONNECTED)

    def test_should_reconnect_initially(self):
        reconnector = WebSocketReconnector()
        self.assertTrue(reconnector.should_reconnect())

    def test_should_not_reconnect_after_max_attempts(self):
        reconnector = WebSocketReconnector()
        reconnector._reconnect_attempts = 10
        self.assertFalse(reconnector.should_reconnect())

    def test_get_reconnect_delay_exponential(self):
        reconnector = WebSocketReconnector()
        reconnector._reconnect_attempts = 3
        delay = reconnector.get_reconnect_delay()
        self.assertEqual(delay, 8.0)

    def test_record_success_resets_attempts(self):
        reconnector = WebSocketReconnector()
        reconnector._reconnect_attempts = 5
        reconnector.record_success()
        self.assertEqual(reconnector._reconnect_attempts, 0)
        self.assertEqual(reconnector.state, ConnectionState.CONNECTED)

    def test_reset_clears_state(self):
        reconnector = WebSocketReconnector()
        reconnector._reconnect_attempts = 5
        reconnector.state = ConnectionState.CONNECTED
        reconnector.reset()
        self.assertEqual(reconnector._reconnect_attempts, 0)
        self.assertEqual(reconnector.state, ConnectionState.DISCONNECTED)


class BinanceWebSocketClientTests(unittest.TestCase):
    def test_init_paper_mode(self):
        client = BinanceWebSocketClient(mode="paper")
        self.assertEqual(client.mode, "paper")

    def test_init_testnet_mode(self):
        client = BinanceWebSocketClient(mode="testnet")
        self.assertEqual(client.mode, "testnet")

    def test_init_live_mode(self):
        client = BinanceWebSocketClient(mode="live")
        self.assertEqual(client.mode, "live")

    def test_symbol_normalization(self):
        client = BinanceWebSocketClient(mode="paper", symbols=["btcusdt", "ethusdt"])
        self.assertEqual(client.symbols, ["BTCUSDT", "ETHUSDT"])

    def test_build_stream_url_single_symbol(self):
        client = BinanceWebSocketClient(mode="testnet", symbols=["btcusdt"], streams=["trade"])
        url = client._build_stream_url()
        self.assertIn("btcusdt@trade", url)

    def test_build_stream_url_multiple_streams(self):
        client = BinanceWebSocketClient(
            mode="testnet",
            symbols=["btcusdt"],
            streams=["trade", "kline_1m"],
        )
        url = client._build_stream_url()
        self.assertIn("btcusdt@trade", url)
        self.assertIn("btcusdt@kline_1m", url)

    def test_subscribe_handler(self):
        client = BinanceWebSocketClient(mode="paper")
        handler = MagicMock()
        client.subscribe("trade", handler)
        self.assertIn(handler, client._handlers["trade"])

    def test_unsubscribe_handler(self):
        client = BinanceWebSocketClient(mode="paper")
        handler = MagicMock()
        client.subscribe("trade", handler)
        client.unsubscribe("trade", handler)
        self.assertNotIn(handler, client._handlers["trade"])

    def test_get_latest_price_no_data(self):
        client = BinanceWebSocketClient(mode="paper")
        price = client.get_latest_price("BTCUSDT")
        self.assertIsNone(price)

    def test_is_connected_paper_mode(self):
        client = BinanceWebSocketClient(mode="paper")
        self.assertTrue(client.is_connected)

    def test_connection_state_paper_mode(self):
        client = BinanceWebSocketClient(mode="paper")
        self.assertEqual(client.connection_state, ConnectionState.CONNECTED)


class WebSocketMessageTests(unittest.TestCase):
    def test_message_creation(self):
        from datetime import datetime, timezone

        message = WebSocketMessage(
            event_type="trade",
            symbol="BTCUSDT",
            data={"p": "50000", "q": "1.5"},
            timestamp=datetime.now(timezone.utc),
            trace_id="trace-123",
        )
        self.assertEqual(message.event_type, "trade")
        self.assertEqual(message.symbol, "BTCUSDT")
        self.assertEqual(message.trace_id, "trace-123")

    def test_message_to_dict(self):
        from datetime import datetime, timezone

        message = WebSocketMessage(
            event_type="kline",
            symbol="ETHUSDT",
            data={"o": "3000", "h": "3100"},
            timestamp=datetime.now(timezone.utc),
        )
        d = message.to_dict()
        self.assertEqual(d["event_type"], "kline")
        self.assertEqual(d["symbol"], "ETHUSDT")
        self.assertIn("timestamp", d)


class PositionSyncManagerTests(unittest.TestCase):
    def setUp(self):
        self.mock_engine = MagicMock()
        self.mock_engine.mode = "paper"
        self.sync_manager = PositionSyncManager(
            execution_engine=self.mock_engine,
            sync_interval=1.0,
        )

    def test_update_local_position(self):
        self.sync_manager.update_local_position(
            "BTCUSDT",
            {"quantity": 1.5, "side": "LONG"},
        )
        pos = self.sync_manager.get_local_position("BTCUSDT")
        self.assertIsNotNone(pos)
        self.assertEqual(pos["quantity"], 1.5)

    def test_get_local_position_not_exists(self):
        pos = self.sync_manager.get_local_position("ETHUSDT")
        self.assertIsNone(pos)

    def test_detect_orphan_no_positions(self):
        orphans = self.sync_manager.detect_orphan_positions()
        self.assertEqual(len(orphans), 0)

    def test_detect_orphan_local_only(self):
        self.sync_manager.update_local_position(
            "BTCUSDT",
            {"quantity": 1.5, "side": "LONG"},
        )
        orphans = self.sync_manager.detect_orphan_positions()
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]["symbol"], "BTCUSDT")

    def test_get_sync_status(self):
        status = self.sync_manager.get_sync_status()
        self.assertIn("is_connected", status)
        self.assertIn("last_sync", status)
        self.assertIn("sync_errors", status)
        self.assertIn("is_in_sync", status)

    def test_sync_status_no_errors(self):
        from datetime import datetime, timezone

        self.sync_manager._sync_errors = 0
        self.sync_manager._last_sync = datetime.now(timezone.utc)
        self.assertTrue(self.sync_manager.is_in_sync())

    def test_sync_status_with_errors(self):
        self.sync_manager._sync_errors = 1
        self.assertFalse(self.sync_manager.is_in_sync())


class BinanceWebSocketClientReconnectTests(unittest.TestCase):
    @patch("binance_websocket.websockets.connect", new_callable=AsyncMock)
    async def test_connect_success(self, mock_connect):
        mock_ws = AsyncMock()
        mock_connect.return_value = mock_ws

        client = BinanceWebSocketClient(mode="testnet", symbols=["BTCUSDT"])
        result = await client.connect()

        self.assertTrue(result)
        self.assertEqual(client.connection_state, ConnectionState.CONNECTED)

    @patch("binance_websocket.websockets.connect", new_callable=AsyncMock)
    async def test_connect_failure(self, mock_connect):
        mock_connect.side_effect = Exception("Connection failed")

        client = BinanceWebSocketClient(mode="testnet", symbols=["BTCUSDT"])
        result = await client.connect()

        self.assertFalse(result)
        self.assertEqual(client.connection_state, ConnectionState.FAILED)


if __name__ == "__main__":
    unittest.main()
