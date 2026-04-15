from __future__ import annotations

from dataclasses import dataclass

from backtest import build_snapshot
from derivatives_data import DerivativesDataClient
from intermarket_data import IntermarketDataClient
from market_data import MarketDataClient, MarketSnapshot


@dataclass
class MarketContext:
    timestamp: float | None
    benchmark_snapshot: MarketSnapshot
    signal_snapshots: dict[str, MarketSnapshot]
    signal_interval: str
    signal_limit: int
    state_interval: str
    state_limit: int
    intermarket: dict
    derivatives: dict[str, dict]
    data_health: dict
    source: str = "kline_derived"


class UnifiedDataProvider:
    """Single-source market context builder.

    Realtime/paper must consume the same kline-derived snapshot shape used by
    backtest/audit so strategy/state/risk share one data path.
    """

    def __init__(
        self,
        client: MarketDataClient,
        derivatives_client: DerivativesDataClient | None = None,
        intermarket_client: IntermarketDataClient | None = None,
    ):
        self.client = client
        self.derivatives_client = derivatives_client or DerivativesDataClient()
        self.intermarket_client = intermarket_client or IntermarketDataClient(client)

    def _fetch_kline_snapshot(self, symbol: str, interval: str, limit: int) -> MarketSnapshot:
        klines = self.client.fetch_klines(symbol, interval=interval, limit=limit)
        if not klines:
            return MarketSnapshot(
                symbol=symbol.upper(),
                source="kline_derived_degraded",
                degraded=True,
                error="empty_kline_response",
                klines=[],
            )
        snapshot = build_snapshot(symbol.upper(), klines)
        snapshot.source = "kline_derived"
        return snapshot

    def build_context(
        self,
        benchmark_symbol: str,
        watchlist: list[str],
        state_interval: str,
        state_limit: int,
        signal_interval: str,
        signal_limit: int,
    ) -> MarketContext:
        benchmark_snapshot = self._fetch_kline_snapshot(
            benchmark_symbol, state_interval, state_limit
        )
        signal_snapshots: dict[str, MarketSnapshot] = {}
        for symbol in watchlist:
            signal_snapshots[symbol.upper()] = self._fetch_kline_snapshot(
                symbol, signal_interval, signal_limit
            )
        intermarket = self.intermarket_client.fetch_context(benchmark_snapshot=benchmark_snapshot)
        derivatives: dict[str, dict] = {}
        target_symbols = {benchmark_symbol.upper()} | {s.upper() for s in watchlist}
        for symbol in target_symbols:
            derivatives[symbol] = self.derivatives_client.fetch_symbol_metrics(symbol)
        derivatives_degraded = [s for s, m in derivatives.items() if m.get("status") == "degraded"]
        derivatives_partial = [s for s, m in derivatives.items() if m.get("status") == "partial"]
        intermarket_status = str(intermarket.get("status", "unknown"))
        overall = "ok"
        if benchmark_snapshot.degraded or intermarket_status == "degraded" or derivatives_degraded:
            overall = "degraded"
        elif intermarket_status == "partial" or derivatives_partial:
            overall = "partial"

        timestamp = None
        if benchmark_snapshot.klines:
            timestamp = benchmark_snapshot.klines[-1].get("close_time")

        return MarketContext(
            timestamp=timestamp,
            benchmark_snapshot=benchmark_snapshot,
            signal_snapshots=signal_snapshots,
            signal_interval=signal_interval,
            signal_limit=signal_limit,
            state_interval=state_interval,
            state_limit=state_limit,
            intermarket=intermarket,
            derivatives=derivatives,
            data_health={
                "status": overall,
                "benchmark_status": "degraded" if benchmark_snapshot.degraded else "ok",
                "intermarket_status": intermarket_status,
                "derivatives_degraded_symbols": derivatives_degraded,
                "derivatives_partial_symbols": derivatives_partial,
            },
            source="kline_derived",
        )
