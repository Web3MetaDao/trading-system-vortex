"""
Mini-Leviathan 量化缠论引擎

轻量级缠论特征识别器，提取核心买卖点信号
- 顶底分型识别（简化版，忽略包含关系处理）
- 笔（Bi）的生成
- 中枢（ZhongShu）定位
- 背驰（Divergence）检测

作者：TRAE AI Assistant
版本：1.1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# 配置日志
logger = logging.getLogger(__name__)


@dataclass
class Fractal:
    """
    分型数据结构
    
    Attributes:
        index: K 线在 DataFrame 中的索引位置
        price: 分型价格（顶分型为 high，底分型为 low）
        fractal_type: 分型类型 ('top' 或 'bottom')
        kline_index: 原始 K 线索引
    """
    index: int
    price: float
    fractal_type: str  # 'top' 或 'bottom'
    kline_index: int = 0


@dataclass
class Bi:
    """
    笔数据结构
    
    Attributes:
        start_index: 起始分型索引
        end_index: 结束分型索引
        start_price: 起始价格
        end_price: 结束价格
        direction: 笔的方向 ('up' 或 'down')
        high: 笔范围内的最高价
        low: 笔范围内的最低价
    """
    start_index: int
    end_index: int
    start_price: float
    end_price: float
    direction: str  # 'up' 或 'down'
    high: float
    low: float


@dataclass
class ZhongShu:
    """
    中枢数据结构
    
    Attributes:
        start_bi_index: 起始笔索引
        end_bi_index: 结束笔索引
        zg: 中枢上沿（高点）
        zd: 中枢下沿（低点）
        direction: 中枢方向 ('up', 'down', 'neutral')
    """
    start_bi_index: int
    end_bi_index: int
    zg: float  # 中枢高点
    zd: float  # 中枢低点
    direction: str = 'neutral'  # 'up', 'down', 'neutral'


class ChanlunEngine:
    """
    量化缠论引擎（轻量级特征提取版）
    
    核心功能：
    1. 识别顶底分型（简化版，直接比较高低点）
    2. 生成笔结构（顶底交替原则）
    3. 定位中枢（连续三笔重叠区间）
    4. 检测背驰信号（价格新低 + 动量背离）
    
    设计原则：
    - 轻量化：忽略复杂的 K 线包含处理
    - 鲁棒性：防御性编程，处理数据缺失
    - 高性能：使用向量化操作
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化缠论引擎
        
        Args:
            config: 配置字典，可包含：
                - min_bi_length: 最小笔长度（K 线数量，默认 4）
                - divergence_lookback: 背驰检测回看笔数（默认 3）
                - momentum_window: 动量计算窗口（默认 5）
        """
        self.config = config or {}
        self.min_bi_length: int = self.config.get('min_bi_length', 4)
        self.divergence_lookback: int = self.config.get('divergence_lookback', 3)
        self.momentum_window: int = self.config.get('momentum_window', 5)
        
        # 缓存分析结果
        self._fractals_cache: List[Fractal] = []
        self._bi_list_cache: List[Bi] = []
        self._zhongshu_list_cache: List[ZhongShu] = []
        
        logger.info(
            "ChanlunEngine 初始化完成，配置：min_bi_length=%d, divergence_lookback=%d",
            self.min_bi_length,
            self.divergence_lookback
        )
    
    def _find_fractals(self, df: pd.DataFrame) -> List[Fractal]:
        """
        识别顶分型和底分型（简化版）
        
        分型定义：
        - 顶分型：中间 K 线 high 最高，且左右 K 线 high 较低
        - 底分型：中间 K 线 low 最低，且左右 K 线 low 较高
        
        注意：为简化计算，本版本忽略 K 线包含关系处理
        
        Args:
            df: 包含 OHLCV 的 DataFrame
            
        Returns:
            List[Fractal]: 分型列表
        """
        if df.empty or len(df) < 3:
            logger.warning("K 线数据不足，无法识别分型")
            return []
        
        fractals: List[Fractal] = []
        
        try:
            highs = df['high'].values
            lows = df['low'].values
            
            # 向量化识别分型（窗口大小=5，即左右各 2 根 K 线）
            for i in range(2, len(df) - 2):
                # 检查顶分型
                is_top = (
                    highs[i] > highs[i-1] and
                    highs[i] > highs[i+1] and
                    highs[i] > highs[i-2] and
                    highs[i] > highs[i+2]
                )
                
                if is_top:
                    fractal = Fractal(
                        index=i,
                        price=float(highs[i]),
                        fractal_type='top',
                        kline_index=int(df.index[i]) if hasattr(df.index[i], '__int__') else i
                    )
                    fractals.append(fractal)
                    logger.debug("识别到顶分型：index=%d, price=%.2f", i, fractal.price)
                
                # 检查底分型
                is_bottom = (
                    lows[i] < lows[i-1] and
                    lows[i] < lows[i+1] and
                    lows[i] < lows[i-2] and
                    lows[i] < lows[i+2]
                )
                
                if is_bottom:
                    fractal = Fractal(
                        index=i,
                        price=float(lows[i]),
                        fractal_type='bottom',
                        kline_index=int(df.index[i]) if hasattr(df.index[i], '__int__') else i
                    )
                    fractals.append(fractal)
                    logger.debug("识别到底分型：index=%d, price=%.2f", i, fractal.price)
        
        except KeyError as e:
            logger.error("DataFrame 缺少必要列：%s", e)
            return []
        except Exception as e:
            logger.error("分型识别失败：%s", e)
            return []
        
        return fractals
    
    def _draw_bi(self, fractals: List[Fractal]) -> List[Bi]:
        """
        按照顶底分型交替原则生成笔
        
        笔的定义：
        - 从底分型到顶分型 = 上升笔
        - 从顶分型到底分型 = 下降笔
        - 必须满足最小 K 线数量（默认 4 根）
        
        Args:
            fractals: 分型列表
            
        Returns:
            List[Bi]: 笔列表
        """
        if len(fractals) < 2:
            logger.debug("分型数量不足，无法生成笔")
            return []
        
        bis: List[Bi] = []
        
        # 按索引排序分型
        sorted_fractals = sorted(fractals, key=lambda f: f.index)
        
        i = 0
        while i < len(sorted_fractals) - 1:
            start_fractal = sorted_fractals[i]
            
            # 寻找下一个相反类型的分型
            j = i + 1
            while j < len(sorted_fractals):
                end_fractal = sorted_fractals[j]
                
                # 检查是否交替（顶 - 底 - 顶 - 底）
                if end_fractal.fractal_type != start_fractal.fractal_type:
                    # 计算笔的长度
                    bi_length = abs(end_fractal.index - start_fractal.index)
                    
                    if bi_length >= self.min_bi_length:
                        # 确定方向
                        if end_fractal.price > start_fractal.price:
                            direction = 'up'
                        else:
                            direction = 'down'
                        
                        # 估算笔范围内的最高/最低价（简化处理）
                        bi_high = max(start_fractal.price, end_fractal.price)
                        bi_low = min(start_fractal.price, end_fractal.price)
                        
                        bi = Bi(
                            start_index=start_fractal.index,
                            end_index=end_fractal.index,
                            start_price=start_fractal.price,
                            end_price=end_fractal.price,
                            direction=direction,
                            high=bi_high,
                            low=bi_low
                        )
                        bis.append(bi)
                        logger.debug(
                            "生成笔：direction=%s, start=%.2f, end=%.2f, length=%d",
                            bi.direction, bi.start_price, bi.end_price, bi_length
                        )
                    
                    # 移动到下一个分型
                    i = j
                    break
                j += 1
            else:
                # 没有找到相反类型分型，结束
                break
        
        return bis
    
    def _find_zhongshu(self, bis: List[Bi]) -> List[ZhongShu]:
        """
        寻找走势中枢
        
        中枢定义：连续三笔有重叠的价格区间
        
        中枢区间计算：
        - ZG（中枢高点）= min(笔 1 high, 笔 2 high, 笔 3 high)
        - ZD（中枢低点）= max(笔 1 low, 笔 2 low, 笔 3 low)
        - 要求：ZG > ZD（有重叠区域）
        
        Args:
            bis: 笔列表
            
        Returns:
            List[ZhongShu]: 中枢列表
        """
        if len(bis) < 3:
            logger.debug("笔数量不足，无法识别中枢")
            return []
        
        zhongshu_list: List[ZhongShu] = []
        
        for i in range(len(bis) - 2):
            bi1 = bis[i]
            bi2 = bis[i + 1]
            bi3 = bis[i + 2]
            
            # 检查是否形成中枢（方向交替）
            is_alternating = (
                bi1.direction != bi2.direction and
                bi2.direction != bi3.direction
            )
            
            if not is_alternating:
                continue
            
            # 计算中枢区间
            zg = min(bi1.high, bi2.high, bi3.high)
            zd = max(bi1.low, bi2.low, bi3.low)
            
            # 检查是否有重叠区域
            if zg > zd:
                # 确定中枢方向
                if bi1.direction == 'up':
                    direction = 'up'  # 下上下
                else:
                    direction = 'down'  # 上下上
                
                zhongshu = ZhongShu(
                    start_bi_index=i,
                    end_bi_index=i + 2,
                    zg=zg,
                    zd=zd,
                    direction=direction
                )
                zhongshu_list.append(zhongshu)
                logger.debug(
                    "识别到中枢：direction=%s, ZG=%.2f, ZD=%.2f",
                    zhongshu.direction, zhongshu.zg, zhongshu.zd
                )
        
        return zhongshu_list
    
    def _calc_momentum(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        """
        计算价格动量（简化版 MACD/RSI）
        
        使用价格变化率作为动量指标：
        momentum = (close[-1] - close[-window]) / close[-window]
        
        Args:
            df: 包含 OHLCV 的 DataFrame
            window: 回看窗口（默认使用配置的 momentum_window）
            
        Returns:
            pd.Series: 动量序列
        """
        if window is None:
            window = self.momentum_window
        
        try:
            closes = df['close']
            
            # 计算价格变化率
            momentum = closes.pct_change(periods=window)
            
            return momentum
        
        except KeyError as e:
            logger.error("DataFrame 缺少 close 列：%s", e)
            return pd.Series(dtype=float)
        except Exception as e:
            logger.error("动量计算失败：%s", e)
            return pd.Series(dtype=float)
    
    def evaluate_divergence(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        评估背驰信号（核心公共方法）
        
        底背驰检测逻辑：
        1. 价格跌破前低（创新低）
        2. 动量未创新低（背离）
        3. 当前处于下降笔末端
        
        Args:
            df: 包含 OHLCV 的 DataFrame
            
        Returns:
            Optional[Dict[str, Any]]: 
                如果检测到背驰，返回：
                {
                    "signal": "BUY",
                    "pattern": "bottom_divergence",
                    "strength": float (0.0-1.0)
                }
                否则返回 None
        """
        if df.empty or len(df) < 10:
            logger.warning("K 线数据不足，无法评估背驰")
            return None
        
        try:
            # 1. 构建缠论结构
            self._fractals_cache = self._find_fractals(df)
            if len(self._fractals_cache) < 2:
                logger.debug("分型数量不足，无法构建笔")
                return None
            
            self._bi_list_cache = self._draw_bi(self._fractals_cache)
            if len(self._bi_list_cache) < self.divergence_lookback:
                logger.debug("笔数量不足，无法检测背驰")
                return None
            
            self._zhongshu_list_cache = self._find_zhongshu(self._bi_list_cache)
            
            # 2. 获取最近几笔
            recent_bis = self._bi_list_cache[-self.divergence_lookback:]
            
            # 3. 检查是否有下降笔
            last_bi = recent_bis[-1]
            if last_bi.direction != 'down':
                logger.debug("最后一笔不是下降笔，跳过背驰检测")
                return None
            
            # 4. 查找前一个下降笔
            prev_down_bi: Optional[Bi] = None
            for bi in reversed(recent_bis[:-1]):
                if bi.direction == 'down':
                    prev_down_bi = bi
                    break
            
            if not prev_down_bi:
                logger.debug("未找到前一个下降笔")
                return None
            
            # 5. 检查价格是否创新低
            price_innovation = last_bi.low < prev_down_bi.low
            if not price_innovation:
                logger.debug("价格未创新低")
                return None
            
            # 6. 计算动量背离
            momentum = self._calc_momentum(df)
            
            if momentum.empty or len(momentum) < last_bi.end_index + 1:
                logger.warning("动量数据不足")
                return None
            
            # 获取两个低点对应的动量值
            current_momentum = momentum.iloc[last_bi.end_index]
            prev_momentum = momentum.iloc[prev_down_bi.end_index]
            
            # 处理 NaN 值
            if pd.isna(current_momentum) or pd.isna(prev_momentum):
                logger.warning("动量值为 NaN")
                return None
            
            # 7. 检测底背驰：价格新低但动量未新低
            # 动量改善超过 20% 视为背离
            momentum_improvement = (current_momentum - prev_momentum) / abs(prev_momentum) if prev_momentum != 0 else 0
            
            is_divergence = momentum_improvement > 0.2
            
            if not is_divergence:
                logger.debug(
                    "动量未出现背离：improvement=%.2f%%",
                    momentum_improvement * 100
                )
                return None
            
            # 8. 计算信号强度
            # 强度 = 动量改善程度 + 基础分
            strength = min(1.0, 0.5 + momentum_improvement)
            
            # 9. 构建返回结果
            result = {
                "signal": "BUY",
                "pattern": "bottom_divergence",
                "strength": round(strength, 2),
                "details": {
                    "price_low": round(last_bi.low, 2),
                    "prev_low": round(prev_down_bi.low, 2),
                    "current_momentum": round(float(current_momentum), 4),
                    "prev_momentum": round(float(prev_momentum), 4),
                    "momentum_improvement": round(momentum_improvement * 100, 2),
                    "zhongshu_count": len(self._zhongshu_list_cache)
                }
            }
            
            logger.info(
                "检测到底背驰信号：strength=%.2f, price_low=%.2f, prev_low=%.2f",
                strength, last_bi.low, prev_down_bi.low
            )
            
            return result
        
        except Exception as e:
            logger.error("背驰评估失败：%s", e)
            return None
    
    def evaluate_third_buy(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        评估第三买点信号
        
        第三买点定义：
        1. 存在一个上升中枢
        2. 价格突破中枢后回踩
        3. 回踩不跌破中枢上沿（ZG）
        
        Args:
            df: 包含 OHLCV 的 DataFrame
            
        Returns:
            Optional[Dict[str, Any]]:
                如果检测到第三买点，返回：
                {
                    "signal": "BUY",
                    "pattern": "third_buy",
                    "strength": float (0.0-1.0)
                }
                否则返回 None
        """
        if df.empty or len(df) < 10:
            return None
        
        try:
            # 确保已构建缠论结构
            if not self._bi_list_cache:
                self._fractals_cache = self._find_fractals(df)
                self._bi_list_cache = self._draw_bi(self._fractals_cache)
                self._zhongshu_list_cache = self._find_zhongshu(self._bi_list_cache)
            
            if not self._zhongshu_list_cache:
                logger.debug("未识别到中枢")
                return None
            
            # 获取最后一个中枢
            last_zhongshu = self._zhongshu_list_cache[-1]
            
            # 检查中枢后是否有突破笔
            if last_zhongshu.end_bi_index >= len(self._bi_list_cache) - 1:
                logger.debug("中枢后无突破笔")
                return None
            
            # 获取突破笔
            breakout_bi = self._bi_list_cache[last_zhongshu.end_bi_index + 1]
            
            # 检查是否向上突破
            if breakout_bi.direction != 'up' or breakout_bi.high <= last_zhongshu.zg:
                logger.debug("未向上突破中枢")
                return None
            
            # 获取回踩笔
            if last_zhongshu.end_bi_index + 2 >= len(self._bi_list_cache):
                logger.debug("无回踩笔")
                return None
            
            pullback_bi = self._bi_list_cache[last_zhongshu.end_bi_index + 2]
            
            # 检查回踩是否不破中枢上沿
            if pullback_bi.direction != 'down' or pullback_bi.low < last_zhongshu.zg:
                logger.debug("回踩跌破中枢上沿")
                return None
            
            # 计算信号强度
            # 距离 ZG 越远，强度越高
            distance_to_zg = (pullback_bi.low - last_zhongshu.zg) / last_zhongshu.zg
            strength = min(1.0, 0.7 + distance_to_zg * 10)
            
            result = {
                "signal": "BUY",
                "pattern": "third_buy",
                "strength": round(strength, 2),
                "details": {
                    "zhongshu_zg": round(last_zhongshu.zg, 2),
                    "zhongshu_zd": round(last_zhongshu.zd, 2),
                    "breakout_high": round(breakout_bi.high, 2),
                    "pullback_low": round(pullback_bi.low, 2),
                    "distance_to_zg_pct": round(distance_to_zg * 100, 2)
                }
            }
            
            logger.info(
                "检测到第三买点：strength=%.2f, zg=%.2f, pullback_low=%.2f",
                strength, last_zhongshu.zg, pullback_bi.low
            )
            
            return result
        
        except Exception as e:
            logger.error("第三买点评估失败：%s", e)
            return None
    
    def analyze(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        综合分析，返回最强的缠论信号
        
        优先级：
        1. 第三买点（最强信号）
        2. 底背驰（次强信号）
        
        Args:
            df: 包含 OHLCV 的 DataFrame
            
        Returns:
            Optional[Dict[str, Any]]: 最强的缠论信号
        """
        # 检测第三买点（优先级更高）
        third_buy = self.evaluate_third_buy(df)
        if third_buy:
            return third_buy
        
        # 检测底背驰
        divergence = self.evaluate_divergence(df)
        if divergence:
            return divergence
        
        # 无明确信号
        logger.debug("未检测到明确缠论信号")
        return None
    
    def get_fractals(self) -> List[Fractal]:
        """获取分型列表"""
        return self._fractals_cache
    
    def get_bi_list(self) -> List[Bi]:
        """获取笔列表"""
        return self._bi_list_cache
    
    def get_zhongshu_list(self) -> List[ZhongShu]:
        """获取中枢列表"""
        return self._zhongshu_list_cache


# ========== 与 SignalEngine 的集成接口 ==========

def integrate_with_signal_engine(
    chanlun_signal: Optional[Dict[str, Any]],
    current_score: int,
    strategy_config: Dict[str, Any]
) -> tuple[int, str, Dict[str, Any]]:
    """
    将缠论信号集成到 SignalEngine 的评分系统
    
    Args:
        chanlun_signal: 缠论信号（来自 evaluate_divergence 或 evaluate_third_buy）
        current_score: 当前 SignalEngine 评分
        strategy_config: 策略配置
        
    Returns:
        Tuple[int, str, Dict]: (新评分，信号类型，解释字典)
    """
    if not chanlun_signal:
        return current_score, 'none', {}
    
    # 获取缠论配置
    chanlun_config = strategy_config.get('chanlun', {})
    enabled = chanlun_config.get('enabled', True)
    weight = chanlun_config.get('weight', 2.0)
    
    if not enabled:
        logger.debug("缠论功能已禁用")
        return current_score, 'none', {}
    
    # 根据信号类型和强度调整评分
    signal_type = chanlun_signal.get('signal', '')
    pattern = chanlun_signal.get('pattern', '')
    strength = chanlun_signal.get('strength', 0.0)
    
    if signal_type == 'BUY':
        if pattern == 'bottom_divergence':
            bonus = int(strength * weight)
            return (
                current_score + bonus,
                'chanlun_divergence',
                chanlun_signal.get('details', {})
            )
        
        elif pattern == 'third_buy':
            bonus = int(strength * weight)
            return (
                current_score + bonus,
                'chanlun_3rd_buy',
                chanlun_signal.get('details', {})
            )
    
    elif signal_type == 'SELL':
        # 顶背驰信号，扣分
        penalty = int(strength * weight)
        return (
            current_score - penalty,
            'chanlun_top_divergence',
            chanlun_signal.get('details', {})
        )
    
    return current_score, 'none', {}


if __name__ == '__main__':
    # 测试示例
    print("=== 缠论引擎测试 ===\n")
    
    # 生成模拟数据（包含明显的底背驰形态）
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=100, freq='1h')
    
    # 构造一个先下跌后反弹的形态
    prices = 50000 + np.cumsum(np.random.randn(100) * 50)
    # 人为制造底背驰：价格创新低但动量改善
    prices[80:] = prices[80:] - 500  # 最后阶段价格下跌
    
    df = pd.DataFrame({
        'open': prices + np.random.randn(100) * 20,
        'high': prices + np.abs(np.random.randn(100) * 20),
        'low': prices - np.abs(np.random.randn(100) * 20),
        'close': prices,
        'volume': np.random.randint(1000, 10000, 100)
    }, index=dates)
    
    # 运行引擎
    engine = ChanlunEngine(config={
        'min_bi_length': 4,
        'divergence_lookback': 3
    })
    
    signal = engine.analyze(df)
    
    if signal:
        print(f"信号：{signal['signal']}")
        print(f"模式：{signal['pattern']}")
        print(f"强度：{signal['strength']:.2f}")
        print(f"详情：{signal.get('details', {})}")
    else:
        print("未检测到明确信号")
    
    print(f"\n分型数量：{len(engine.get_fractals())}")
    print(f"笔数量：{len(engine.get_bi_list())}")
    print(f"中枢数量：{len(engine.get_zhongshu_list())}")
