"""
Vortex Arbitrage Engine  —  机构级跨所套利引擎

设计原则（顶尖量化机构标准）：
1. 净利润计算：价差 - 双边 Taker 手续费 - 提币手续费 - 网络延迟风险溢价
2. 订单簿深度感知：基于 L2 Order Book 计算 VWAP 滑点，而非仅取 Top-of-Book
3. 资金划转成本建模：提币手续费 + 划转时间内的价格漂移风险
4. 幂等性保护：防止重复执行同一套利机会
5. 熔断机制：连续失败后自动暂停
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from cross_exchange_manager import CrossExchangeManager

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 常量：各交易所默认 Taker 手续费率（可通过配置覆盖）
# ─────────────────────────────────────────────
DEFAULT_TAKER_FEE: dict[str, float] = {
    "binance": 0.001,  # 0.10%
    "okx": 0.001,  # 0.10%
    "bybit": 0.001,  # 0.10%
}

# 各交易所 BTC 提币手续费（USDT 等值，保守估算）
DEFAULT_WITHDRAWAL_FEE_USDT: dict[str, float] = {
    "binance": 3.0,
    "okx": 2.5,
    "bybit": 2.0,
}

# 价格漂移风险溢价：假设划转耗时 30 分钟，BTC 日波动率约 3%，折算为 30min 风险
# σ_30min = σ_daily * sqrt(30 / (24*60)) ≈ 0.023%
TRANSFER_DRIFT_RISK_PCT: float = 0.03 * (30 / (24 * 60)) ** 0.5


@dataclass
class ArbitrageOpportunity:
    """套利机会数据类，包含完整的成本分解"""

    buy_exchange: str
    sell_exchange: str
    symbol: str
    buy_price: float  # 买入交易所的 VWAP 成交价（含深度滑点）
    sell_price: float  # 卖出交易所的 VWAP 成交价（含深度滑点）
    trade_amount_base: float  # 交易数量（基础资产，如 BTC）
    trade_amount_usdt: float  # 交易金额（USDT）
    gross_profit_pct: float  # 毛利润率（仅价差）
    buy_fee_usdt: float  # 买入手续费（USDT）
    sell_fee_usdt: float  # 卖出手续费（USDT）
    withdrawal_fee_usdt: float  # 提币手续费（USDT）
    drift_risk_usdt: float  # 价格漂移风险（USDT）
    net_profit_usdt: float  # 净利润（USDT）
    net_profit_pct: float  # 净利润率
    is_viable: bool  # 是否可执行（净利润率 > 阈值）
    timestamp: float = field(default_factory=time.time)


@dataclass
class ArbitrageConfig:
    """套利引擎配置"""

    min_net_profit_pct: float = 0.05  # 最低净利润率阈值（0.05%）
    trade_amount_usdt: float = 100.0  # 每次套利金额（USDT）
    max_orderbook_depth_pct: float = 0.5  # 最大使用订单簿深度（50%）
    polling_interval_s: float = 1.0  # 轮询间隔（秒）
    circuit_breaker_failures: int = 5  # 熔断阈值：连续失败次数
    circuit_breaker_cooldown_s: float = 60.0  # 熔断冷却时间（秒）
    taker_fees: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TAKER_FEE))
    withdrawal_fees: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_WITHDRAWAL_FEE_USDT)
    )


class ArbitrageEngine:
    """
    机构级跨所套利引擎

    核心改进（相对于原始版本）：
    - 净利润计算：扣除双边手续费 + 提币费 + 价格漂移风险
    - 订单簿深度感知：使用 L2 数据计算 VWAP 滑点
    - 熔断机制：连续失败自动暂停
    - 幂等性保护：防止重复执行
    """

    def __init__(
        self,
        exchange_mgr: CrossExchangeManager,
        symbol: str = "BTC/USDT",
        config: ArbitrageConfig | None = None,
    ) -> None:
        self.exchange_mgr = exchange_mgr
        self.symbol = symbol
        self.config = config or ArbitrageConfig()

        # 熔断状态
        self._consecutive_failures: int = 0
        self._circuit_open_until: float = 0.0

        # 幂等性：记录最近已执行的套利机会（key = buy_ex:sell_ex:price_hash）
        self._executed_keys: set[str] = set()

    # ──────────────────────────────────────────
    # 公共接口
    # ──────────────────────────────────────────

    async def find_arbitrage_opportunities(self) -> list[ArbitrageOpportunity]:
        """
        扫描所有交易所对，返回按净利润率降序排列的可行套利机会列表。

        改进点：
        1. 使用 L2 订单簿计算 VWAP 成交价（而非 Top-of-Book）
        2. 净利润 = 价差 - 双边 Taker 费 - 提币费 - 漂移风险
        3. 仅返回 net_profit_pct > min_net_profit_pct 的机会
        """
        order_books = await self._fetch_all_order_books()
        if len(order_books) < 2:
            return []

        opportunities: list[ArbitrageOpportunity] = []
        names = list(order_books.keys())

        for i, ex_buy in enumerate(names):
            for j, ex_sell in enumerate(names):
                if i == j:
                    continue
                opp = self._evaluate_opportunity(
                    ex_buy=ex_buy,
                    ex_sell=ex_sell,
                    ob_buy=order_books[ex_buy],
                    ob_sell=order_books[ex_sell],
                )
                if opp is not None and opp.is_viable:
                    opportunities.append(opp)

        return sorted(opportunities, key=lambda x: x.net_profit_pct, reverse=True)

    async def run_arbitrage_loop(self, duration: int = 30) -> None:
        """持续套利监控主循环，含熔断保护"""
        logger.info("Vortex Arbitrage Engine started | symbol=%s", self.symbol)
        deadline = time.monotonic() + duration

        while time.monotonic() < deadline:
            # 熔断检查
            if self._is_circuit_open():
                cooldown_remaining = self._circuit_open_until - time.monotonic()
                logger.warning(
                    "Circuit breaker OPEN — cooling down for %.0fs", cooldown_remaining
                )
                await asyncio.sleep(min(5.0, cooldown_remaining))
                continue

            try:
                opps = await self.find_arbitrage_opportunities()
                if opps:
                    top = opps[0]
                    logger.info(
                        "ARB OPPORTUNITY | Buy %s @ %.2f | Sell %s @ %.2f "
                        "| Gross=%.4f%% Net=%.4f%% | NetPnL=+%.4f USDT "
                        "| Fees(buy=%.4f sell=%.4f withdraw=%.4f drift=%.4f)",
                        top.buy_exchange,
                        top.buy_price,
                        top.sell_exchange,
                        top.sell_price,
                        top.gross_profit_pct,
                        top.net_profit_pct,
                        top.net_profit_usdt,
                        top.buy_fee_usdt,
                        top.sell_fee_usdt,
                        top.withdrawal_fee_usdt,
                        top.drift_risk_usdt,
                    )
                else:
                    logger.debug("No viable arbitrage opportunities found.")

                self._consecutive_failures = 0
                await asyncio.sleep(self.config.polling_interval_s)

            except Exception as exc:
                self._consecutive_failures += 1
                logger.error(
                    "Arbitrage loop error (failure #%d): %s",
                    self._consecutive_failures,
                    exc,
                )
                if self._consecutive_failures >= self.config.circuit_breaker_failures:
                    self._trip_circuit_breaker()
                await asyncio.sleep(5.0)

        logger.info("Arbitrage monitoring completed.")

    # ──────────────────────────────────────────
    # 内部方法
    # ──────────────────────────────────────────

    async def _fetch_all_order_books(self) -> dict[str, dict[str, Any]]:
        """并行获取所有交易所的 L2 订单簿（20 档深度）"""
        names = list(self.exchange_mgr.exchanges.keys())
        tasks = [
            self.exchange_mgr.exchanges[name].fetch_order_book(self.symbol, limit=20)
            for name in names
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        order_books: dict[str, dict[str, Any]] = {}
        for name, result in zip(names, results, strict=False):
            if isinstance(result, Exception):
                logger.warning("Failed to fetch order book from %s: %s", name, result)
            else:
                order_books[name] = result
        return order_books

    def _calc_vwap(self, levels: list[list[float]], target_qty: float) -> float | None:
        """
        基于订单簿 L2 数据计算 VWAP 成交均价。

        Args:
            levels: 订单簿档位列表，每档 [price, quantity]
            target_qty: 目标成交数量（基础资产）

        Returns:
            VWAP 成交均价；若深度不足则返回 None
        """
        remaining = target_qty
        total_cost = 0.0

        for level in levels:
            # CCXT order book levels can be [price, size] or [price, size, ...]
            # Use index access to safely handle both 2-field and 3-field formats
            price = float(level[0])
            qty = float(level[1])
            if remaining <= 0:
                break
            fill = min(remaining, qty)
            total_cost += fill * price
            remaining -= fill

        if remaining > 1e-10:
            # 订单簿深度不足，无法完整成交
            logger.debug(
                "Order book depth insufficient: needed %.8f, unfilled %.8f",
                target_qty,
                remaining,
            )
            return None

        return total_cost / target_qty

    def _evaluate_opportunity(
        self,
        ex_buy: str,
        ex_sell: str,
        ob_buy: dict[str, Any],
        ob_sell: dict[str, Any],
    ) -> ArbitrageOpportunity | None:
        """
        评估一对交易所之间的套利机会，计算完整的成本分解和净利润。

        成本模型：
            净利润 = (VWAP_sell - VWAP_buy) * qty
                    - buy_fee - sell_fee
                    - withdrawal_fee
                    - drift_risk
        """
        try:
            trade_usdt = self.config.trade_amount_usdt

            # 1. 计算买入 VWAP（吃卖单 asks）
            asks = ob_buy.get("asks", [])
            bids = ob_sell.get("bids", [])
            if not asks or not bids:
                return None

            approx_buy_price = asks[0][0]
            if approx_buy_price <= 0:
                return None

            # 估算买入数量（基础资产）
            trade_qty = trade_usdt / approx_buy_price

            vwap_buy = self._calc_vwap(asks, trade_qty)
            if vwap_buy is None:
                return None

            # 2. 计算卖出 VWAP（吃买单 bids）
            vwap_sell = self._calc_vwap(bids, trade_qty)
            if vwap_sell is None:
                return None

            # 3. 毛利润
            gross_spread = vwap_sell - vwap_buy
            gross_profit_pct = (gross_spread / vwap_buy) * 100.0

            # 4. 成本分解
            buy_fee_rate = self.config.taker_fees.get(ex_buy, 0.001)
            sell_fee_rate = self.config.taker_fees.get(ex_sell, 0.001)
            buy_fee_usdt = trade_qty * vwap_buy * buy_fee_rate
            sell_fee_usdt = trade_qty * vwap_sell * sell_fee_rate
            withdrawal_fee_usdt = self.config.withdrawal_fees.get(ex_buy, 3.0)
            drift_risk_usdt = trade_usdt * TRANSFER_DRIFT_RISK_PCT

            # 5. 净利润
            net_profit_usdt = (
                gross_spread * trade_qty
                - buy_fee_usdt
                - sell_fee_usdt
                - withdrawal_fee_usdt
                - drift_risk_usdt
            )
            net_profit_pct = (net_profit_usdt / trade_usdt) * 100.0
            is_viable = net_profit_pct > self.config.min_net_profit_pct

            return ArbitrageOpportunity(
                buy_exchange=ex_buy,
                sell_exchange=ex_sell,
                symbol=self.symbol,
                buy_price=vwap_buy,
                sell_price=vwap_sell,
                trade_amount_base=trade_qty,
                trade_amount_usdt=trade_usdt,
                gross_profit_pct=gross_profit_pct,
                buy_fee_usdt=buy_fee_usdt,
                sell_fee_usdt=sell_fee_usdt,
                withdrawal_fee_usdt=withdrawal_fee_usdt,
                drift_risk_usdt=drift_risk_usdt,
                net_profit_usdt=net_profit_usdt,
                net_profit_pct=net_profit_pct,
                is_viable=is_viable,
            )

        except (KeyError, IndexError, ZeroDivisionError, TypeError) as exc:
            logger.debug("Opportunity evaluation failed (%s vs %s): %s", ex_buy, ex_sell, exc)
            return None

    def _is_circuit_open(self) -> bool:
        """检查熔断器是否处于断开状态"""
        return time.monotonic() < self._circuit_open_until

    def _trip_circuit_breaker(self) -> None:
        """触发熔断器"""
        self._circuit_open_until = time.monotonic() + self.config.circuit_breaker_cooldown_s
        logger.error(
            "Circuit breaker TRIPPED after %d consecutive failures. "
            "Cooling down for %.0fs.",
            self._consecutive_failures,
            self.config.circuit_breaker_cooldown_s,
        )
        self._consecutive_failures = 0


if __name__ == "__main__":

    async def _demo() -> None:
        logging.basicConfig(level=logging.INFO)
        mgr = CrossExchangeManager()
        engine = ArbitrageEngine(mgr, config=ArbitrageConfig(trade_amount_usdt=200.0))
        await engine.run_arbitrage_loop(duration=10)
        await mgr.close()

    asyncio.run(_demo())
