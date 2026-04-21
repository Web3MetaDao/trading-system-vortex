from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests


class MarketDataError(Exception):
    pass


@dataclass
class MarketSnapshot:
    symbol: str
    price: float | None = None
    volume: float | None = None
    change_24h_pct: float | None = None
    quote_volume: float | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    open_price: float | None = None
    klines: list[dict[str, float]] = field(default_factory=list)
    source: str = "binance_spot_rest"
    degraded: bool = False
    error: str | None = None


class MarketDataClient:
    """Binance public market data client with retry/fallback handling."""

    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        primary = base_url or os.getenv("BINANCE_BASE_URL", "https://api.binance.com")
        fallback = os.getenv("BINANCE_FALLBACK_BASE_URL", "https://api1.binance.com")
        vision = os.getenv("BINANCE_VISION_BASE_URL", "https://data-api.binance.vision")
        self.base_urls = []
        for url in [primary, fallback, vision]:
            if url and url not in self.base_urls:
                self.base_urls.append(url)
        self.timeout = float(os.getenv("BINANCE_TIMEOUT_SECONDS", timeout))
        self.max_retries = int(os.getenv("BINANCE_MAX_RETRIES", "2"))
        self.retry_delay = float(os.getenv("BINANCE_RETRY_DELAY_SECONDS", "1.0"))
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "trading-system-paper/1.0",
                "Accept": "application/json",
                "Connection": "close",
            }
        )

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for base_url in self.base_urls:
            url = f"{base_url}{path}"
            for attempt in range(1, self.max_retries + 1):
                try:
                    response = self.session.get(url, params=params, timeout=self.timeout)
                    response.raise_for_status()
                    return response.json()
                except requests.RequestException as exc:
                    last_error = exc
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay)
        raise MarketDataError(
            str(last_error) if last_error else f"Unknown market data error for {path}"
        )

    def fetch_snapshot(
        self, symbol: str, interval: str = "1h", kline_limit: int = 120
    ) -> MarketSnapshot:
        symbol = symbol.upper()
        klines = self.fetch_klines(symbol, interval=interval, limit=kline_limit)

        ticker_24h_error = None
        ticker = None
        try:
            ticker = self._get("/api/v3/ticker/24hr", {"symbol": symbol})
        except Exception as exc:
            ticker_24h_error = str(exc)

        if ticker is None:
            try:
                price_only = self._get("/api/v3/ticker/price", {"symbol": symbol})
                return MarketSnapshot(
                    symbol=symbol,
                    price=self._to_float(price_only.get("price")),
                    klines=klines,
                    source="binance_spot_rest_partial",
                    error=f"ticker_24hr_failed: {ticker_24h_error}" if ticker_24h_error else None,
                )
            except Exception as exc:
                raise MarketDataError(
                    f"ticker_24hr_failed: {ticker_24h_error}; ticker_price_failed: {exc}"
                ) from exc

        return MarketSnapshot(
            symbol=symbol,
            price=self._to_float(ticker.get("lastPrice")),
            volume=self._to_float(ticker.get("volume")),
            change_24h_pct=self._to_float(ticker.get("priceChangePercent")),
            quote_volume=self._to_float(ticker.get("quoteVolume")),
            high_24h=self._to_float(ticker.get("highPrice")),
            low_24h=self._to_float(ticker.get("lowPrice")),
            open_price=self._to_float(ticker.get("openPrice")),
            klines=klines,
        )

    def fetch_snapshot_safe(
        self, symbol: str, interval: str = "1h", kline_limit: int = 120
    ) -> MarketSnapshot:
        try:
            return self.fetch_snapshot(symbol, interval=interval, kline_limit=kline_limit)
        except Exception as exc:
            return MarketSnapshot(
                symbol=symbol.upper(),
                source="binance_spot_rest_degraded",
                degraded=True,
                error=str(exc),
                klines=[],
            )

    def fetch_klines(
        self, symbol: str, interval: str = "1h", limit: int = 120, end_time: float | None = None
    ) -> list[dict[str, float]]:
        params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        if end_time is not None:
            params["endTime"] = int(end_time)
        rows = self._get(
            "/api/v3/klines",
            params,
        )
        candles: list[dict[str, float]] = []
        for row in rows:
            candles.append(
                {
                    "open_time": float(row[0]),
                    "open": self._to_float(row[1]) or 0.0,
                    "high": self._to_float(row[2]) or 0.0,
                    "low": self._to_float(row[3]) or 0.0,
                    "close": self._to_float(row[4]) or 0.0,
                    "volume": self._to_float(row[5]) or 0.0,
                    "close_time": float(row[6]),
                    "quote_volume": self._to_float(row[7]) or 0.0,
                }
            )
        return candles

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
