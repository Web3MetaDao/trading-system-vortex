"""
VORTEX Trading System - 统一数据提供者 (v2.0 - 机构级重构)

修复了以下体检发现的关键问题：
1. [CRITICAL] OracleSnapshot 未接入主数据流 -> 已将 MultiModalOracle 集成进 MarketContext
2. oracle 采用异步懒加载 + 5 分钟缓存，避免阻塞主循环
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from backtest import build_snapshot
from derivatives_data import DerivativesDataClient
from intermarket_data import IntermarketDataClient
from market_data import MarketDataClient, MarketSnapshot
from multimodal_oracle import MultiModalOracle, OracleSnapshot

logger = logging.getLogger(__name__)


@dataclass
class MarketContext:
    """统一市场上下文，包含所有下游引擎所需的数据。

    新增字段：
        oracle_snapshot: 多模态神谕快照（宏观情绪 + 盘口失衡），
            若获取失败则为 None，下游引擎应做 None 检查。
    """

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
    oracle_snapshot: OracleSnapshot | None = None
    source: str = "kline_derived"


class UnifiedDataProvider:
    """Single-source market context builder.

    Realtime/paper must consume the same kline-derived snapshot shape used by
    backtest/audit so strategy/state/risk share one data path.

    v2.0 新增：
    - 集成 MultiModalOracle，将 OracleSnapshot 注入 MarketContext
    - Oracle 采用带 TTL 的内存缓存，避免每个 tick 都发起外部 HTTP 请求
    - oracle_enabled 开关，允许在测试/回测环境中禁用
    """

    # Oracle 缓存 TTL（秒），与 MultiModalOracle 内部缓存保持一致
    _ORACLE_CACHE_TTL: float = 300.0

    def __init__(
        self,
        client: MarketDataClient,
        derivatives_client: DerivativesDataClient | None = None,
        intermarket_client: IntermarketDataClient | None = None,
        oracle: MultiModalOracle | None = None,
        oracle_enabled: bool = True,
    ):
        self.client = client
        self.derivatives_client = derivatives_client or DerivativesDataClient()
        self.intermarket_client = intermarket_client or IntermarketDataClient(client)
        self.oracle: MultiModalOracle | None = oracle if oracle_enabled else None
        if oracle_enabled and self.oracle is None:
            self.oracle = MultiModalOracle()

        # Provider 层的 Oracle 缓存（防止在同一 tick 内重复调用）
        self._oracle_cache: OracleSnapshot | None = None
        self._oracle_cache_ts: float = 0.0

        logger.info(
            "UnifiedDataProvider 初始化完成，oracle_enabled=%s",
            oracle_enabled,
        )

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

    def _get_oracle_snapshot(self, orderbook: dict[str, Any] | None = None) -> OracleSnapshot | None:
        """获取神谕快照，带 Provider 层缓存。

        Args:
            orderbook: 标准 ccxt 盘口字典，用于计算盘口失衡。
                若为 None，则传入空字典（OBI 将返回 0.0）。

        Returns:
            OracleSnapshot 或 None（oracle 被禁用或获取失败时）
        """
        if self.oracle is None:
            return None

        now = time.monotonic()
        if self._oracle_cache is not None and (now - self._oracle_cache_ts) < self._ORACLE_CACHE_TTL:
            logger.debug("Oracle 缓存命中，跳过外部请求")
            return self._oracle_cache

        try:
            ob = orderbook or {}
            snapshot = self.oracle.get_oracle_snapshot(ob)
            self._oracle_cache = snapshot
            self._oracle_cache_ts = now
            logger.info(
                "Oracle 快照已刷新：sentiment=%.2f, obi=%.2f, permitted=%s",
                snapshot.sentiment_score,
                snapshot.orderbook_imbalance,
                snapshot.is_trade_permitted,
            )
            return snapshot
        except Exception as exc:
            logger.warning("Oracle 快照获取失败（降级为 None）：%s", exc)
            return None

    async def _get_oracle_snapshot_async(
        self, orderbook: dict[str, Any] | None = None
    ) -> OracleSnapshot | None:
        """异步版本的神谕快照获取，适用于 asyncio 主循环。"""
        if self.oracle is None:
            return None

        now = time.monotonic()
        if self._oracle_cache is not None and (now - self._oracle_cache_ts) < self._ORACLE_CACHE_TTL:
            return self._oracle_cache

        try:
            ob = orderbook or {}
            snapshot = await self.oracle.get_oracle_snapshot_async(ob)
            self._oracle_cache = snapshot
            self._oracle_cache_ts = now
            logger.info(
                "Oracle 快照已异步刷新：sentiment=%.2f, obi=%.2f, permitted=%s",
                snapshot.sentiment_score,
                snapshot.orderbook_imbalance,
                snapshot.is_trade_permitted,
            )
            return snapshot
        except Exception as exc:
            logger.warning("Oracle 异步快照获取失败（降级为 None）：%s", exc)
            return None

    def build_context(
        self,
        benchmark_symbol: str,
        watchlist: list[str],
        state_interval: str,
        state_limit: int,
        signal_interval: str,
        signal_limit: int,
        orderbook: dict[str, Any] | None = None,
    ) -> MarketContext:
        """构建统一市场上下文（同步版本）。

        Args:
            benchmark_symbol: 基准标的（如 BTCUSDT）
            watchlist: 监控标的列表
            state_interval: 状态 K 线周期
            state_limit: 状态 K 线数量
            signal_interval: 信号 K 线周期
            signal_limit: 信号 K 线数量
            orderbook: 可选的盘口数据，用于 Oracle 盘口失衡计算
        """
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

        # 获取神谕快照（带缓存，失败时降级为 None）
        oracle_snapshot = self._get_oracle_snapshot(orderbook)
        oracle_status = "ok" if oracle_snapshot is not None else "unavailable"

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
            oracle_snapshot=oracle_snapshot,
            data_health={
                "status": overall,
                "benchmark_status": "degraded" if benchmark_snapshot.degraded else "ok",
                "intermarket_status": intermarket_status,
                "derivatives_degraded_symbols": derivatives_degraded,
                "derivatives_partial_symbols": derivatives_partial,
                "oracle_status": oracle_status,
            },
            source="kline_derived",
        )

    async def build_context_async(
        self,
        benchmark_symbol: str,
        watchlist: list[str],
        state_interval: str,
        state_limit: int,
        signal_interval: str,
        signal_limit: int,
        orderbook: dict[str, Any] | None = None,
    ) -> MarketContext:
        """构建统一市场上下文（异步版本，适用于 asyncio 主循环）。

        与同步版本相比，Oracle 快照采用 await 方式获取，避免阻塞事件循环。
        """
        # 同步部分：K 线、衍生品、跨市场数据
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

        # 异步获取神谕快照
        oracle_snapshot = await self._get_oracle_snapshot_async(orderbook)
        oracle_status = "ok" if oracle_snapshot is not None else "unavailable"

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
            oracle_snapshot=oracle_snapshot,
            data_health={
                "status": overall,
                "benchmark_status": "degraded" if benchmark_snapshot.degraded else "ok",
                "intermarket_status": intermarket_status,
                "derivatives_degraded_symbols": derivatives_degraded,
                "derivatives_partial_symbols": derivatives_partial,
                "oracle_status": oracle_status,
            },
            source="kline_derived",
        )
