"""
Mini-Leviathan 多模态神谕模块 (v2.0)

赋予系统"视觉"与"语感"的宏观感知能力
- 文本情绪模态：Alternative.me 恐惧贪婪指数
- 订单流模态：盘口深度失衡分析 (OBI)
- 融合评估：宏观环境过滤

作者：TRAE AI Assistant
版本：2.0.0
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

# 配置日志
logger = logging.getLogger(__name__)


@dataclass
class OracleSnapshot:
    """
    神谕快照数据类

    多模态分析的标准化输出，供 SignalEngine 使用

    Attributes:
        sentiment_score: 情绪得分 [-1.0, 1.0]，-1 为极度恐慌，1 为极度贪婪
        orderbook_imbalance: 盘口失衡 [-1.0, 1.0]，-1 为极端卖压，1 为极端买盘
        is_trade_permitted: 是否允许交易（风控过滤结果）
    """

    sentiment_score: float  # [-1.0, 1.0]
    orderbook_imbalance: float  # [-1.0, 1.0]
    is_trade_permitted: bool  # 风控结果


class MultiModalOracle:
    """
    多模态神谕引擎

    核心功能：
    1. 获取宏观情绪（恐惧贪婪指数）
    2. 分析微观订单流（盘口失衡）
    3. 综合评估交易许可

    使用场景：
    - 在 SignalEngine 中作为过滤器
    - 防止在极端行情下接飞刀
    - 提供逆向交易的宏观信号
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """
        初始化多模态神谕

        Args:
            config: 配置字典，可包含：
                - fear_greed_api_url: 恐惧贪婪指数 API 地址
                - request_timeout: HTTP 请求超时（秒）
                - extreme_fear_threshold: 极度恐慌阈值（默认 -0.6）
                - strong_pressure_threshold: 强抛压阈值（默认 -0.4）
        """
        self.config = config or {}

        # API 配置
        self.fear_greed_api_url = self.config.get(
            "fear_greed_api_url", "https://api.alternative.me/fng/"
        )
        self.request_timeout = self.config.get("request_timeout", 10)

        # 风控阈值配置
        self.extreme_fear_threshold = self.config.get("extreme_fear_threshold", -0.6)
        self.strong_pressure_threshold = self.config.get("strong_pressure_threshold", -0.4)

        # 缓存
        self._sentiment_cache: float | None = None
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 300.0  # 5 分钟缓存

        logger.info(
            "MultiModalOracle 初始化完成，风控阈值：extreme_fear=%.2f, strong_pressure=%.2f",
            self.extreme_fear_threshold,
            self.strong_pressure_threshold,
        )

    async def _fetch_crypto_sentiment(self) -> float:
        """
        获取加密货币情绪指数（异步方法）

        调用 Alternative.me API 获取恐惧贪婪指数，将 0-100 的数值
        标准化映射到 [-1.0, 1.0] 区间：
        - 0 (极度恐惧) -> -1.0
        - 50 (中性) -> 0.0
        - 100 (极度贪婪) -> 1.0

        Returns:
            float: 标准化情绪得分 [-1.0, 1.0]
        """
        import time

        current_time = time.time()

        # 检查缓存
        if (
            self._sentiment_cache is not None
            and (current_time - self._cache_timestamp) < self._cache_ttl
        ):
            logger.debug("使用缓存的情绪数据：%.2f", self._sentiment_cache)
            return self._sentiment_cache

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.fear_greed_api_url,
                    timeout=aiohttp.ClientTimeout(total=self.request_timeout),
                ) as response:
                    if response.status != 200:
                        logger.warning("恐惧贪婪指数 API 返回异常状态码：%d", response.status)
                        raise Exception(f"HTTP {response.status}")

                    data = await response.json()

                    if not data or "data" not in data or not data["data"]:
                        logger.warning("恐惧贪婪指数 API 返回空数据")
                        raise Exception("Empty data")

                    # 获取最新数据
                    latest = data["data"][0]
                    raw_value = float(latest["value"])  # 0-100

                    # 标准化映射：(value - 50) / 50
                    normalized_score = (raw_value - 50.0) / 50.0

                    # 限制在 [-1.0, 1.0] 范围内
                    normalized_score = max(-1.0, min(1.0, normalized_score))

                    logger.info(
                        "恐惧贪婪指数：raw=%.1f, normalized=%.2f", raw_value, normalized_score
                    )

                    # 更新缓存
                    self._sentiment_cache = normalized_score
                    self._cache_timestamp = current_time

                    return normalized_score

        except TimeoutError:
            logger.error("恐惧贪婪指数 API 请求超时")
            return 0.0  # 返回中性值

        except aiohttp.ClientError as e:
            logger.error("恐惧贪婪指数 HTTP 请求失败：%s", e)
            return 0.0

        except Exception as e:
            logger.error("获取恐惧贪婪指数失败：%s", e)
            return 0.0

    def _analyze_orderbook_imbalance(self, orderbook: dict[str, Any]) -> float:
        """
        分析订单簿失衡 (Order Book Imbalance, OBI)

        接收标准的 ccxt orderbook 字典，计算买卖盘口失衡度。
        公式：OBI = (Bid_Vol - Ask_Vol) / (Bid_Vol + Ask_Vol)

        取盘口前 10 档的挂单量进行计算：
        - OBI > 0：买盘强劲
        - OBI < 0：卖压沉重
        - OBI = 0：买卖均衡

        Args:
            orderbook: 标准 ccxt 盘口字典
                {
                    'bids': [[price, volume], ...],  # 买单列表
                    'asks': [[price, volume], ...],  # 卖单列表
                }

        Returns:
            float: 盘口失衡得分 [-1.0, 1.0]
        """
        if not orderbook:
            logger.warning("盘口数据为空")
            return 0.0

        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        if not bids or not asks:
            logger.warning("盘口数据不完整")
            return 0.0

        # 取前 10 档
        lookback_levels = self.config.get("obi_lookback_levels", 10)
        bids = bids[:lookback_levels]
        asks = asks[:lookback_levels]

        if len(bids) == 0 or len(asks) == 0:
            logger.warning("盘口深度不足")
            return 0.0

        try:
            # 计算买单总量
            bid_volume = sum(float(vol) for _, vol in bids)

            # 计算卖单总量
            ask_volume = sum(float(vol) for _, vol in asks)

            # 计算总成交量
            total_volume = bid_volume + ask_volume

            if total_volume == 0:
                logger.warning("盘口总成交量为零")
                return 0.0

            # 计算失衡度：(Bid_Vol - Ask_Vol) / (Bid_Vol + Ask_Vol)
            obi = (bid_volume - ask_volume) / total_volume

            # 限制在 [-1.0, 1.0] 范围内
            obi = max(-1.0, min(1.0, obi))

            logger.info(
                "盘口失衡分析：bid_vol=%.2f, ask_vol=%.2f, obi=%.2f", bid_volume, ask_volume, obi
            )

            return obi

        except (TypeError, ValueError) as e:
            logger.error("盘口失衡计算失败：%s", e)
            return 0.0

    def evaluate_macro_environment(self, orderbook: dict[str, Any]) -> OracleSnapshot:
        """
        综合评估宏观交易环境

        核心风控逻辑：
        1. 如果情绪极度恐慌（score < -0.6）且盘口呈现极强抛压（OBI < -0.4），
           则 is_trade_permitted = False（禁止做多接飞刀）
        2. 其他情况允许交易

        这是系统的"刹车系统"，防止在极端行情下盲目开仓

        Args:
            orderbook: 标准 ccxt 盘口字典

        Returns:
            OracleSnapshot: 标准化神谕快照
        """
        # 异步获取情绪得分（在同步方法中使用 asyncio.run）
        try:
            # 尝试获取当前运行循环
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果在运行中的循环里，创建新任务但不等待
                # 这里使用缓存值或默认值
                sentiment_score = (
                    self._sentiment_cache if self._sentiment_cache is not None else 0.0
                )
            else:
                sentiment_score = loop.run_until_complete(self._fetch_crypto_sentiment())
        except RuntimeError:
            # 没有运行循环，直接运行
            sentiment_score = asyncio.run(self._fetch_crypto_sentiment())

        # 分析盘口失衡
        obi = self._analyze_orderbook_imbalance(orderbook)

        # 核心风控逻辑
        is_extreme_fear = sentiment_score < self.extreme_fear_threshold
        is_strong_pressure = obi < self.strong_pressure_threshold

        # 禁止做多接飞刀：极度恐慌 + 极强抛压
        if is_extreme_fear and is_strong_pressure:
            is_trade_permitted = False
            logger.warning(
                "宏观风控触发：sentiment=%.2f (极度恐慌), obi=%.2f (强抛压) -> 禁止交易",
                sentiment_score,
                obi,
            )
        else:
            is_trade_permitted = True
            logger.debug("宏观环境许可：sentiment=%.2f, obi=%.2f -> 允许交易", sentiment_score, obi)

        # 生成快照
        snapshot = OracleSnapshot(
            sentiment_score=sentiment_score,
            orderbook_imbalance=obi,
            is_trade_permitted=is_trade_permitted,
        )

        return snapshot

    async def get_oracle_snapshot_async(self, orderbook: dict[str, Any]) -> OracleSnapshot:
        """
        异步版本的神谕快照获取

        Args:
            orderbook: 标准 ccxt 盘口字典

        Returns:
            OracleSnapshot: 标准化神谕快照
        """
        # 获取情绪得分
        sentiment_score = await self._fetch_crypto_sentiment()

        # 分析盘口失衡
        obi = self._analyze_orderbook_imbalance(orderbook)

        # 核心风控逻辑
        is_extreme_fear = sentiment_score < self.extreme_fear_threshold
        is_strong_pressure = obi < self.strong_pressure_threshold

        if is_extreme_fear and is_strong_pressure:
            is_trade_permitted = False
            logger.warning(
                "宏观风控触发：sentiment=%.2f, obi=%.2f -> 禁止交易", sentiment_score, obi
            )
        else:
            is_trade_permitted = True

        return OracleSnapshot(
            sentiment_score=sentiment_score,
            orderbook_imbalance=obi,
            is_trade_permitted=is_trade_permitted,
        )

    def get_sentiment_description(self, score: float) -> str:
        """
        获取情绪描述文本

        Args:
            score: 情绪得分 [-1.0, 1.0]

        Returns:
            str: 描述文本
        """
        if score >= 0.8:
            return "极度贪婪"
        elif score >= 0.4:
            return "乐观"
        elif score >= 0.1:
            return "轻微乐观"
        elif score >= -0.1:
            return "中性"
        elif score >= -0.4:
            return "轻微恐慌"
        elif score >= -0.8:
            return "恐慌"
        else:
            return "极度恐慌"

    def get_obi_description(self, obi: float) -> str:
        """
        获取盘口失衡描述文本

        Args:
            obi: 盘口失衡得分 [-1.0, 1.0]

        Returns:
            str: 描述文本
        """
        if obi >= 0.8:
            return "极端买盘"
        elif obi >= 0.4:
            return "买盘强劲"
        elif obi >= 0.1:
            return "轻微买盘"
        elif obi >= -0.1:
            return "买卖均衡"
        elif obi >= -0.4:
            return "轻微卖压"
        elif obi >= -0.8:
            return "卖压沉重"
        else:
            return "极端卖盘"


# ========== 与 SignalEngine 的集成接口 ==========


def integrate_with_signal_engine(
    snapshot: OracleSnapshot, current_score: int, strategy_config: dict[str, Any]
) -> tuple[int, str, dict[str, Any]]:
    """
    将神谕信号集成到 SignalEngine 的评分系统

    Args:
        snapshot: 神谕快照
        current_score: 当前 SignalEngine 评分
        strategy_config: 策略配置

    Returns:
        Tuple[int, str, Dict]: (新评分，信号类型，解释字典)
    """
    if not snapshot.is_trade_permitted:
        # 禁止交易，扣 2 分
        logger.info("神谕禁止交易，扣分：%d -> %d", current_score, current_score - 2)
        return (
            current_score - 2,
            "macro_blocked",
            {
                "reason": "极端宏观环境",
                "sentiment": snapshot.sentiment_score,
                "obi": snapshot.orderbook_imbalance,
            },
        )

    # 获取神谕配置
    multimodal_config = strategy_config.get("multimodal", {})
    enabled = multimodal_config.get("enabled", True)
    weight = multimodal_config.get("weight", 2.0)

    if not enabled:
        logger.debug("多模态功能已禁用")
        return current_score, "none", {}

    # 根据情绪和盘口情况加分
    bonus = 0

    # 情绪乐观且买盘强劲：加分
    if snapshot.sentiment_score > 0.4 and snapshot.orderbook_imbalance > 0.4:
        bonus = int((snapshot.sentiment_score + snapshot.orderbook_imbalance) * weight / 2)
        logger.info(
            "神谕加分：sentiment=%.2f, obi=%.2f -> +%d",
            snapshot.sentiment_score,
            snapshot.orderbook_imbalance,
            bonus,
        )
        return (
            current_score + bonus,
            "macro_bullish",
            {
                "sentiment": snapshot.sentiment_score,
                "obi": snapshot.orderbook_imbalance,
                "reason": "情绪乐观 + 买盘强劲",
            },
        )

    # 中性情况：不加分也不扣分
    return current_score, "none", {}


if __name__ == "__main__":
    # 测试示例
    print("=== 多模态神谕测试 ===\n")

    # 模拟盘口数据（买盘强劲）
    mock_orderbook_bullish = {
        "bids": [
            [50000.0, 10.0],
            [49999.0, 15.0],
            [49998.0, 20.0],
            [49997.0, 25.0],
            [49996.0, 30.0],
            [49995.0, 35.0],
            [49994.0, 40.0],
            [49993.0, 45.0],
            [49992.0, 50.0],
            [49991.0, 55.0],
        ],
        "asks": [
            [50001.0, 5.0],
            [50002.0, 8.0],
            [50003.0, 12.0],
            [50004.0, 15.0],
            [50005.0, 18.0],
            [50006.0, 20.0],
            [50007.0, 22.0],
            [50008.0, 25.0],
            [50009.0, 28.0],
            [50010.0, 30.0],
        ],
    }

    # 模拟盘口数据（卖压沉重）
    mock_orderbook_bearish = {
        "bids": [
            [50000.0, 5.0],
            [49999.0, 8.0],
            [49998.0, 12.0],
            [49997.0, 15.0],
            [49996.0, 18.0],
            [49995.0, 20.0],
            [49994.0, 22.0],
            [49993.0, 25.0],
            [49992.0, 28.0],
            [49991.0, 30.0],
        ],
        "asks": [
            [50001.0, 10.0],
            [50002.0, 15.0],
            [50003.0, 20.0],
            [50004.0, 25.0],
            [50005.0, 30.0],
            [50006.0, 35.0],
            [50007.0, 40.0],
            [50008.0, 45.0],
            [50009.0, 50.0],
            [50010.0, 55.0],
        ],
    }

    # 运行测试
    oracle = MultiModalOracle(
        config={
            "sentiment_sources": ["fear_greed"],
            "extreme_fear_threshold": -0.6,
            "strong_pressure_threshold": -0.4,
        }
    )

    print("测试场景 1：买盘强劲的盘口")
    snapshot = oracle.evaluate_macro_environment(mock_orderbook_bullish)
    print(
        f"情绪得分：{snapshot.sentiment_score:.2f} ({oracle.get_sentiment_description(snapshot.sentiment_score)})"
    )
    print(
        f"盘口失衡：{snapshot.orderbook_imbalance:.2f} ({oracle.get_obi_description(snapshot.orderbook_imbalance)})"
    )
    print(f"允许交易：{snapshot.is_trade_permitted}")

    print("\n测试场景 2：卖压沉重的盘口")
    snapshot = oracle.evaluate_macro_environment(mock_orderbook_bearish)
    print(
        f"情绪得分：{snapshot.sentiment_score:.2f} ({oracle.get_sentiment_description(snapshot.sentiment_score)})"
    )
    print(
        f"盘口失衡：{snapshot.orderbook_imbalance:.2f} ({oracle.get_obi_description(snapshot.orderbook_imbalance)})"
    )
    print(f"允许交易：{snapshot.is_trade_permitted}")

    print("\n=== 测试完成 ===")
