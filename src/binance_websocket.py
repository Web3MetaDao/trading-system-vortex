from __future__ import annotations

import asyncio
import orjson as json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import websockets
from websockets.client import WebSocketClientProtocol

from execution_engine import ExecutionEngine


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass
class WebSocketMessage:
    event_type: str
    symbol: str | None
    data: dict[str, Any]
    timestamp: datetime
    trace_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "symbol": self.symbol,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
            "trace_id": self.trace_id,
        }


@dataclass
class WebSocketConfig:
    binance_ws_url: str = "wss://stream.binance.com:9443/ws"
    testnet_ws_url: str = "wss://testnet.binance.vision/ws"
    ping_interval: float = 30.0
    ping_timeout: float = 10.0
    reconnect_delay: float = 1.0
    max_reconnect_delay: float = 60.0
    max_reconnect_attempts: int = 10
    message_queue_size: int = 1000
    enable_logging: bool = True


class WebSocketReconnector:
    def __init__(
        self,
        config: WebSocketConfig | None = None,
        on_state_change: Callable[[ConnectionState], None] | None = None,
    ):
        self.config = config or WebSocketConfig()
        self.on_state_change = on_state_change
        self._state = ConnectionState.DISCONNECTED
        self._reconnect_attempts = 0
        self._last_connected: float | None = None
        self._logger = logging.getLogger("ws_reconnector")

    @property
    def state(self) -> ConnectionState:
        return self._state

    @state.setter
    def state(self, value: ConnectionState):
        if self._state != value:
            self._state = value
            if self.on_state_change:
                self.on_state_change(value)
            if self.config.enable_logging:
                self._logger.info(f"WebSocket state changed to: {value.value}")

    def should_reconnect(self) -> bool:
        if self._reconnect_attempts >= self.config.max_reconnect_attempts:
            return False
        if self.state == ConnectionState.FAILED:
            return False
        return True

    def get_reconnect_delay(self) -> float:
        delay = min(
            self.config.reconnect_delay * (2**self._reconnect_attempts),
            self.config.max_reconnect_delay,
        )
        return delay

    def record_attempt(self):
        self._reconnect_attempts += 1

    def record_success(self):
        self._reconnect_attempts = 0
        self._last_connected = time.time()
        self.state = ConnectionState.CONNECTED

    def reset(self):
        self._reconnect_attempts = 0
        self.state = ConnectionState.DISCONNECTED


class BinanceWebSocketClient:
    TESTNET_MODE = "testnet"
    LIVE_MODE = "live"

    def __init__(
        self,
        mode: str | None = None,
        symbols: list[str] | None = None,
        streams: list[str] | None = None,
        config: WebSocketConfig | None = None,
    ):
        if mode:
            resolved_mode = mode.lower()
        else:
            resolved_mode = os.getenv("TRADING_MODE", "paper").lower()

        if resolved_mode in (self.TESTNET_MODE, self.LIVE_MODE):
            self.mode = resolved_mode
        elif resolved_mode == "paper":
            self.mode = "paper"
        else:
            self.mode = self.TESTNET_MODE

        ws_base = (
            config.binance_ws_url if self.mode == self.LIVE_MODE else config.testnet_ws_url
        ) if config else (
            WebSocketConfig().binance_ws_url
            if self.mode == self.LIVE_MODE
            else WebSocketConfig().testnet_ws_url
        )

        self.ws_base = ws_base
        self.symbols = [s.upper() for s in (symbols or [])]
        self.streams = streams or ["trade", "kline_1m", "mini_ticker"]
        self.config = config or WebSocketConfig()

        self._logger = logging.getLogger("binance_ws")

        self.reconnector = WebSocketReconnector(
            config=self.config,
            on_state_change=self._on_connection_state_change,
        )

        if self.mode == "paper":
            self.reconnector.state = ConnectionState.CONNECTED

        self._ws: WebSocketClientProtocol | None = None
        self._receive_task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._running = False
        self._message_queue: asyncio.Queue[WebSocketMessage] = asyncio.Queue(
            maxsize=self.config.message_queue_size
        )

        self._handlers: dict[str, list[Callable[[WebSocketMessage], None]]] = {
            "trade": [],
            "kline": [],
            "mini_ticker": [],
            "order_book": [],
            "account_update": [],
        }

        self._latest_data: dict[str, dict[str, Any]] = {
            "prices": {},
            "klines": {},
            "tickers": {},
        }

    def _on_connection_state_change(self, state: ConnectionState):
        self._logger.info(f"Connection state: {state.value}")

    def subscribe(self, event_type: str, handler: Callable[[WebSocketMessage], None]):
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable[[WebSocketMessage], None]):
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                pass

    def _build_stream_url(self) -> str:
        if self.symbols:
            symbol_streams = []
            for symbol in self.symbols:
                for stream in self.streams:
                    if stream == "trade":
                        symbol_streams.append(f"{symbol.lower()}@trade")
                    elif stream == "kline_1m":
                        symbol_streams.append(f"{symbol.lower()}@kline_1m")
                    elif stream == "mini_ticker":
                        symbol_streams.append(f"{symbol.lower()}@miniTicker")
            if symbol_streams:
                return f"{self.ws_base}/{'/'.join(symbol_streams)}"
        return self.ws_base

    async def connect(self) -> bool:
        if self.mode == "paper":
            self.reconnector.state = ConnectionState.CONNECTED
            self._running = True
            return True

        try:
            self.reconnector.state = ConnectionState.CONNECTING
            stream_url = self._build_stream_url()
            self._logger.info(f"Connecting to WebSocket: {stream_url}")

            self._ws = await websockets.connect(
                stream_url,
                ping_interval=self.config.ping_interval,
                ping_timeout=self.config.ping_timeout,
            )

            self.reconnector.record_success()
            self._running = True
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._ping_task = asyncio.create_task(self._ping_loop())

            return True

        except Exception as e:
            self._logger.error(f"Failed to connect: {e}")
            self.reconnector.state = ConnectionState.FAILED
            return False

    async def disconnect(self):
        self._running = False

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        self.reconnector.reset()

    async def reconnect(self) -> bool:
        if not self.reconnector.should_reconnect():
            self._logger.error("Max reconnect attempts reached")
            return False

        self.reconnector.record_attempt()
        delay = self.reconnector.get_reconnect_delay()
        self._logger.info(f"Reconnecting in {delay:.1f}s (attempt {self.reconnector._reconnect_attempts})")

        self.reconnector.state = ConnectionState.RECONNECTING
        await asyncio.sleep(delay)

        return await self.connect()

    async def _receive_loop(self):
        while self._running and self._ws:
            try:
                if self._ws:
                    message = await self._ws.recv()
                    await self._process_message(message)
            except asyncio.CancelledError:
                break
            except websockets.exceptions.ConnectionClosed:
                self._logger.warning("WebSocket connection closed")
                if self._running:
                    await self.reconnect()
            except Exception as e:
                self._logger.error(f"Error receiving message: {e}")
                if self._running:
                    await self.reconnect()

    async def _ping_loop(self):
        while self._running and self._ws:
            try:
                await asyncio.sleep(self.config.ping_interval)
                if self._ws and self._ws.open:
                    await self._ws.ping()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Ping error: {e}")

    async def _process_message(self, raw_message: str):
        try:
            data = json.loads(raw_message)  # Optimized with orjson
            event_type = self._extract_event_type(data)
            symbol = data.get("s") or data.get("symbol")

            if "e" in data:
                event_type = data["e"].lower()

            if not event_type:
                return

            message = WebSocketMessage(
                event_type=event_type,
                symbol=symbol,
                data=data,
                timestamp=datetime.now(UTC),
            )

            if event_type == "trade":
                self._latest_data["prices"][symbol] = {
                    "price": float(data.get("p", 0)),
                    "quantity": float(data.get("q", 0)),
                    "time": data.get("T"),
                }
            elif event_type == "kline":
                kline = data.get("k", {})
                self._latest_data["klines"][symbol] = {
                    "open": float(kline.get("o", 0)),
                    "high": float(kline.get("h", 0)),
                    "low": float(kline.get("l", 0)),
                    "close": float(kline.get("c", 0)),
                    "volume": float(kline.get("v", 0)),
                    "time": kline.get("t"),
                }
            elif event_type == "24hr minimini ticker" or event_type == "mini_ticker":
                self._latest_data["tickers"][symbol] = {
                    "close": float(data.get("c", 0)),
                    "open": float(data.get("o", 0)),
                    "high": float(data.get("h", 0)),
                    "low": float(data.get("l", 0)),
                    "volume": float(data.get("v", 0)),
                }

            if event_type in self._handlers:
                for handler in self._handlers[event_type]:
                    try:
                        handler(message)
                    except Exception as e:
                        self._logger.error(f"Handler error for {event_type}: {e}")

            try:
                self._message_queue.put_nowait(message)
            except asyncio.QueueFull:
                self._logger.warning("Message queue full, dropping message")

        except json.JSONDecodeError as e:
            self._logger.error(f"Failed to decode message: {e}")
        except Exception as e:
            self._logger.error(f"Error processing message: {e}")

    def _extract_event_type(self, data: dict[str, Any]) -> str | None:
        if "e" in data:
            return data["e"].lower()
        if "stream" in data:
            stream = data["stream"]
            if "@trade" in stream:
                return "trade"
            if "@kline" in stream:
                return "kline"
            if "@miniTicker" in stream:
                return "mini_ticker"
        return None

    def get_latest_price(self, symbol: str) -> float | None:
        symbol = symbol.upper()
        if symbol in self._latest_data["prices"]:
            return self._latest_data["prices"][symbol].get("price")
        return None

    def get_latest_kline(self, symbol: str) -> dict[str, Any] | None:
        symbol = symbol.upper()
        return self._latest_data["klines"].get(symbol)

    def get_latest_ticker(self, symbol: str) -> dict[str, Any] | None:
        symbol = symbol.upper()
        return self._latest_data["tickers"].get(symbol)

    async def get_message(self, timeout: float = 1.0) -> WebSocketMessage | None:
        try:
            return await asyncio.wait_for(self._message_queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    @property
    def is_connected(self) -> bool:
        return self.reconnector.state == ConnectionState.CONNECTED

    @property
    def connection_state(self) -> ConnectionState:
        return self.reconnector.state


class PositionSyncManager:
    def __init__(
        self,
        execution_engine: ExecutionEngine,
        ws_client: BinanceWebSocketClient | None = None,
        sync_interval: float = 5.0,
    ):
        self.execution_engine = execution_engine
        self.ws_client = ws_client
        self.sync_interval = sync_interval

        self._local_positions: dict[str, dict[str, Any]] = {}
        self._remote_positions: dict[str, dict[str, Any]] = {}
        self._sync_task: asyncio.Task | None = None
        self._running = False

        self._logger = logging.getLogger("position_sync")
        self._last_sync: datetime | None = None
        self._sync_errors: int = 0

    def update_local_position(self, symbol: str, position: dict[str, Any]):
        symbol = symbol.upper()
        self._local_positions[symbol] = position

    def get_local_position(self, symbol: str) -> dict[str, Any] | None:
        return self._local_positions.get(symbol.upper())

    def get_remote_position(self, symbol: str) -> dict[str, Any] | None:
        return self._remote_positions.get(symbol.upper())

    async def sync_from_exchange(self, symbol: str | None = None) -> dict[str, dict[str, Any]]:
        if self.execution_engine.mode == "paper":
            return {}

        try:
            positions = self.execution_engine.fetch_positions(symbol)
            self._remote_positions.clear()

            for pos in positions:
                self._remote_positions[pos.symbol] = {
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "leverage": pos.leverage,
                }

            self._last_sync = datetime.now(UTC)
            self._sync_errors = 0
            return self._remote_positions

        except Exception as e:
            self._sync_errors += 1
            self._logger.error(f"Failed to sync positions: {e}")
            return {}

    def detect_orphan_positions(self) -> list[dict[str, Any]]:
        orphans = []

        for symbol, local in self._local_positions.items():
            if symbol not in self._remote_positions:
                orphans.append({
                    "symbol": symbol,
                    "local": local,
                    "reason": "exists locally but not on exchange",
                })
            else:
                local_qty = local.get("quantity", 0)
                remote_qty = self._remote_positions[symbol].get("quantity", 0)
                if abs(local_qty - remote_qty) > 0.0001:
                    orphans.append({
                        "symbol": symbol,
                        "local": local,
                        "remote": self._remote_positions[symbol],
                        "reason": "quantity mismatch",
                    })

        return orphans

    async def start_auto_sync(self):
        self._running = True
        self._sync_task = asyncio.create_task(self._sync_loop())

    async def stop_auto_sync(self):
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass

    async def _sync_loop(self):
        while self._running:
            try:
                await self.sync_from_exchange()
                orphans = self.detect_orphan_positions()
                if orphans:
                    self._logger.warning(f"Detected {len(orphans)} orphan positions")
                    for orphan in orphans:
                        self._logger.warning(f"Orphan: {orphan}")

                await asyncio.sleep(self.sync_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Sync loop error: {e}")
                await asyncio.sleep(self.sync_interval)

    @property
    def last_sync_time(self) -> datetime | None:
        return self._last_sync

    @property
    def sync_error_count(self) -> int:
        return self._sync_errors

    def is_in_sync(self) -> bool:
        return self._sync_errors == 0 and self._last_sync is not None

    def get_sync_status(self) -> dict[str, Any]:
        return {
            "is_connected": self.ws_client.is_connected if self.ws_client else False,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "sync_errors": self._sync_errors,
            "is_in_sync": self.is_in_sync(),
            "local_positions": len(self._local_positions),
            "remote_positions": len(self._remote_positions),
            "orphans": len(self.detect_orphan_positions()),
        }
