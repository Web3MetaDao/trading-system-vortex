from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import requests


class DerivativesDataClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        primary = base_url or os.getenv("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com")
        fallback = os.getenv("BINANCE_FUTURES_FALLBACK_BASE_URL", "https://fstream.binance.com")
        self.base_urls = []
        for url in [primary, fallback]:
            if url and url not in self.base_urls:
                self.base_urls.append(url)
        self.timeout = float(os.getenv("BINANCE_TIMEOUT_SECONDS", timeout))
        self.max_retries = int(os.getenv("BINANCE_MAX_RETRIES", "2"))
        self.retry_delay = float(os.getenv("BINANCE_RETRY_DELAY_SECONDS", "1.0"))
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "trading-system-derivatives/1.0", "Accept": "application/json"}
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
        raise RuntimeError(str(last_error) if last_error else f"derivatives request failed: {path}")

    def fetch_symbol_metrics(self, symbol: str) -> dict:
        symbol = symbol.upper()
        out = {
            "symbol": symbol,
            "oi_change_pct": None,
            "funding_rate": None,
            "source": "binance_futures_rest",
            "timestamp_utc": datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "status": "ok",
            "errors": [],
        }

        try:
            rows = self._get(
                "/fapi/v1/openInterestHist", {"symbol": symbol, "period": "1h", "limit": 2}
            )
            if isinstance(rows, list) and len(rows) >= 2:
                prev_oi = self._to_float(rows[-2].get("sumOpenInterest"))
                curr_oi = self._to_float(rows[-1].get("sumOpenInterest"))
                if prev_oi not in (None, 0) and curr_oi is not None:
                    out["oi_change_pct"] = round(((curr_oi - prev_oi) / prev_oi) * 100.0, 4)
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            out["errors"].append(f"oi_fetch_failed:{exc}")

        try:
            funding_rows = self._get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": 1})
            if isinstance(funding_rows, list) and funding_rows:
                out["funding_rate"] = self._to_float(funding_rows[-1].get("fundingRate"))
        except Exception as exc:
            out["errors"].append(f"funding_fetch_failed:{exc}")

        if out["oi_change_pct"] is None and out["funding_rate"] is None:
            out["status"] = "degraded"
        elif out["oi_change_pct"] is None or out["funding_rate"] is None:
            out["status"] = "partial"

        return out

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
