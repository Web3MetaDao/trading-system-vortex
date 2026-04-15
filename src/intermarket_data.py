from __future__ import annotations

import os
from datetime import UTC, datetime

from market_data import MarketDataClient, MarketSnapshot


class IntermarketDataClient:
    def __init__(self, market_client: MarketDataClient):
        self.market_client = market_client

    def fetch_context(self, benchmark_snapshot: MarketSnapshot | None = None) -> dict:
        out = {
            "btc_change_24h_pct": None,
            "eth_change_24h_pct": None,
            "nq_change_24h_pct": None,
            "dxy_change_24h_pct": None,
            "source": "spot_plus_env",
            "timestamp_utc": datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "status": "ok",
            "errors": [],
        }
        btc_change = benchmark_snapshot.change_24h_pct if benchmark_snapshot is not None else None
        if btc_change is None:
            btc_snapshot = self.market_client.fetch_snapshot_safe(
                "BTCUSDT", interval="1h", kline_limit=5
            )
            btc_change = btc_snapshot.change_24h_pct
            if btc_snapshot.degraded:
                out["errors"].append(f"btc_fallback_degraded:{btc_snapshot.error}")

        eth_snapshot = self.market_client.fetch_snapshot_safe(
            "ETHUSDT", interval="1h", kline_limit=5
        )
        if eth_snapshot.degraded:
            out["errors"].append(f"eth_snapshot_degraded:{eth_snapshot.error}")
        nq_change = self._env_float("INTERMARKET_NQ_CHANGE_24H_PCT")
        dxy_change = self._env_float("INTERMARKET_DXY_CHANGE_24H_PCT")

        out["btc_change_24h_pct"] = self._round_or_none(btc_change)
        out["eth_change_24h_pct"] = self._round_or_none(eth_snapshot.change_24h_pct)
        out["nq_change_24h_pct"] = self._round_or_none(nq_change)
        out["dxy_change_24h_pct"] = self._round_or_none(dxy_change)

        missing_count = sum(
            1
            for key in [
                "btc_change_24h_pct",
                "eth_change_24h_pct",
                "nq_change_24h_pct",
                "dxy_change_24h_pct",
            ]
            if out[key] is None
        )
        if missing_count >= 3:
            out["status"] = "degraded"
        elif missing_count > 0:
            out["status"] = "partial"
        return out

    @staticmethod
    def _env_float(key: str) -> float | None:
        raw = os.getenv(key)
        if raw in (None, ""):
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    @staticmethod
    def _round_or_none(value: float | None) -> float | None:
        if value is None:
            return None
        return round(float(value), 4)
