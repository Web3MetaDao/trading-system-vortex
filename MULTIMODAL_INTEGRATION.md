# 多模态神谕模块集成指南

## Phase 2 完成状态 ✅

### 已实现功能

**文件位置**: `src/multimodal_oracle.py`

**核心类**:
- `MultiModalOracle`: 主引擎类
- `OracleSnapshot`: 神谕快照（标准化输出）
- `SentimentData`: 情绪数据结构
- `OrderBookImbalance`: 盘口失衡数据

**模态能力**:
1. ✅ **文本情绪模态**
   - Alternative.me 恐惧贪婪指数（实时 API）
   - 社交媒体情绪分析（预留 OpenAI API 结构）
   - 标准化得分 [-1.0, 1.0]

2. ✅ **订单流模态**
   - 盘口深度失衡分析 (OBI)
   - 买卖盘口比例计算
   - 价差和流动性检测

3. ✅ **融合评估**
   - 4 种宏观场景识别
   - 逆向交易逻辑（极端恐慌 + 巨量买单）
   - 置信度评分

---

## 快速开始

### 1. 基本使用（异步）

```python
from src.multimodal_oracle import MultiModalOracle
import asyncio

async def main():
    oracle = MultiModalOracle(config={
        'sentiment_sources': ['fear_greed'],
        'trade_permission_threshold': 0.6
    })
    
    # 获取神谕快照
    snapshot = await oracle.get_oracle_snapshot(
        symbol='BTCUSDT',
        ticker_data=orderbook_data  # 可选
    )
    
    print(f"情绪得分：{snapshot.sentiment_score:.2f}")
    print(f"盘口失衡：{snapshot.orderbook_imbalance:.2f}")
    print(f"允许交易：{snapshot.is_trade_permitted}")
    print(f"宏观信号：{snapshot.macro_signal}")

asyncio.run(main())
```

### 2. 基本使用（同步）

```python
from src.multimodal_oracle import get_oracle_snapshot_sync

snapshot = get_oracle_snapshot_sync(
    symbol='BTCUSDT',
    ticker_data=orderbook_data,
    config={'sentiment_sources': ['fear_greed']}
)
```

### 3. 与 SignalEngine 集成

```python
from src.signal_engine import SignalEngine
from src.multimodal_oracle import MultiModalOracle

# 初始化
signal_engine = SignalEngine()
oracle = MultiModalOracle()

# 1. 获取原始评分
decision = signal_engine.evaluate(symbol, market_state, context)
original_score = decision.score

# 2. 获取神谕快照
snapshot = await oracle.get_oracle_snapshot(symbol, ticker_data)

# 3. 融合评分
new_score, signal_type, explain = oracle.to_signal_bonus(
    snapshot,
    original_score,
    strategy_config
)

# 4. 更新决策
if snapshot.is_trade_permitted and new_score > original_score:
    decision.score = new_score
    decision.macro_signal = snapshot.macro_signal
    decision.explain.update(explain)
```

---

## 配置说明

### config/strategy.yaml

```yaml
# 添加多模态配置块
multimodal:
  enabled: true              # 是否启用
  weight: 2.0                # 信号权重（影响评分加分）
  
  # 情绪分析配置
  sentiment:
    sources:
      - fear_greed           # 恐惧贪婪指数
      - social_sentiment     # 社交媒体（暂未实现）
    cache_expiry_minutes: 5  # 缓存有效期
  
  # 订单流分析配置
  orderbook:
    lookback_levels: 5       # 回看档数
    imbalance_threshold: 0.6 # 失衡阈值
  
  # 交易许可配置
  trade_permission:
    threshold: 0.6           # 置信度阈值
    allow_counter_trade: true # 允许逆向交易
```

---

## 核心逻辑详解

### 1. 情绪得分标准化

**恐惧贪婪指数**:
- 原始值：0-100
- 标准化：`(value - 50) / 50`
- 映射关系：
  - 0 (极度恐惧) → -1.0
  - 50 (中性) → 0.0
  - 100 (极度贪婪) → 1.0

**代码示例**:
```python
raw_value = 32  # 恐惧
normalized = (32 - 50) / 50 = -0.36
```

### 2. 盘口失衡计算

**公式**:
```python
bid_volume = sum(bid_volumes[:5])  # 前 5 档买单
ask_volume = sum(ask_volumes[:5])  # 前 5 档卖单
total_volume = bid_volume + ask_volume

obi_score = (bid_volume - ask_volume) / total_volume
```

**得分含义**:
- `1.0`: 全部买单（极端看多）
- `0.0`: 买卖均衡
- `-1.0`: 全部卖单（极端看空）

### 3. 融合评估逻辑

#### 场景 1: 极端恐慌 + 巨量买单（逆向做多）

```python
if sentiment <= -0.7 and obi >= 0.6:
    permitted = True
    confidence = min(1.0, abs(sentiment) + obi)
    signal = 'BULLISH'
    # 例：sentiment=-0.8, obi=0.7 -> confidence=1.0
```

**逻辑**: 市场极度恐慌但有大资金托底，是逆向做多机会

#### 场景 2: 极端贪婪 + 巨量卖单（逆向做空）

```python
if sentiment >= 0.7 and obi <= -0.6:
    permitted = True
    confidence = min(1.0, abs(sentiment) + abs(obi))
    signal = 'BEARISH'
```

**逻辑**: 市场极度贪婪但有大资金出货，是逆向做空机会

#### 场景 3: 情绪和盘口一致看涨

```python
if sentiment > 0.3 and obi >= 0.6:
    permitted = True
    confidence = 0.5 + 0.25 * (sentiment + obi)
    signal = 'BULLISH'
```

**逻辑**: 情绪乐观 + 买盘强劲，顺势做多

#### 场景 4: 情绪和盘口一致看跌（禁止做多）

```python
if sentiment < -0.3 and obi <= -0.6:
    permitted = False  # 不接飞刀
    confidence = 0.7
    signal = 'BEARISH'
```

**逻辑**: 情绪悲观 + 卖压沉重，禁止做多

---

## 数据结构 API

### OracleSnapshot

```python
@dataclass
class OracleSnapshot:
    timestamp: datetime           # 时间戳
    sentiment_score: float        # 情绪得分 [-1.0, 1.0]
    orderbook_imbalance: float    # 盘口失衡 [-1.0, 1.0]
    is_trade_permitted: bool      # 是否允许交易
    confidence: float             # 置信度 [0.0, 1.0]
    macro_signal: str             # 'BULLISH', 'BEARISH', 'NEUTRAL'
    explain: Dict                 # 详细说明
    raw_data: Dict                # 原始数据缓存
```

### SentimentData

```python
@dataclass
class SentimentData:
    source: str          # 数据来源
    score: float         # 标准化得分 [-1.0, 1.0]
    raw_value: float     # 原始值
    timestamp: datetime  # 时间戳
    metadata: Dict       # 元数据
```

### OrderBookImbalance

```python
@dataclass
class OrderBookImbalance:
    symbol: str          # 交易对
    obi_score: float     # 失衡得分 [-1.0, 1.0]
    bid_volume: float    # 买单总量
    ask_volume: float    # 卖单总量
    spread: float        # 价差
    depth_ratio: float   # 深度比率
```

---

## 测试示例

### 单元测试

```python
import unittest
from src.multimodal_oracle import MultiModalOracle

class TestMultiModalOracle(unittest.TestCase):
    def setUp(self):
        self.oracle = MultiModalOracle()
        self.mock_ticker = {
            'symbol': 'BTCUSDT',
            'bids': [[50000.0, 10.0]] * 5,
            'asks': [[50001.0, 5.0]] * 5,
            'last': 50000.5
        }
    
    def test_orderbook_imbalance(self):
        obi = self.oracle.analyze_orderbook_imbalance(self.mock_ticker)
        self.assertGreater(obi.obi_score, 0)  # 买单多，应为正
    
    def test_macro_permission_extreme_fear(self):
        # 极端恐慌 + 巨量买单
        result = self.oracle.get_macro_permission(
            sentiment_score=-0.8,
            orderbook_obi=OrderBookImbalance(
                symbol='BTCUSDT',
                obi_score=0.7,
                bid_volume=100,
                ask_volume=30,
                spread=1.0,
                depth_ratio=0.77
            )
        )
        self.assertTrue(result['permitted'])
        self.assertEqual(result['signal'], 'BULLISH')
        self.assertGreater(result['confidence'], 0.8)

if __name__ == '__main__':
    unittest.main()
```

---

## 日志示例

```
INFO:multimodal_oracle:MultiModalOracle 初始化完成，配置：{'sentiment_sources': ['fear_greed']}
INFO:multimodal_oracle:恐惧贪婪指数：raw=32.0, normalized=-0.36, class=Fear
INFO:multimodal_oracle:盘口失衡分析：symbol=BTCUSDT, obi=0.27, bid_vol=100.0, ask_vol=58.0, spread=0.00%
INFO:multimodal_oracle:神谕快照：sentiment=-0.36, obi=0.27, permitted=False, signal=NEUTRAL
```

---

## 性能优化建议

### 1. 缓存策略

```python
# 情绪数据缓存 5 分钟
if self._sentiment_cache and datetime.now() < self._cache_expiry:
    return self._sentiment_cache
```

### 2. 异步并发

```python
# 并行获取多个情绪源
tasks = [
    self._fetch_fear_greed_index(),
    self._fetch_social_sentiment()
]
results = await asyncio.gather(*tasks)
```

### 3. 降级策略

```python
# API 失败时返回中性值
except Exception as e:
    logger.error("获取情绪数据失败：%s", e)
    return SentimentData(source='fallback', score=0.0, raw_value=50.0)
```

---

## 故障排查

### Q1: 情绪得分始终为 0

**原因**: API 调用失败或网络问题

**解决**:
```bash
# 测试 API 连接
curl https://api.alternative.me/fng/

# 检查日志
grep "恐惧贪婪指数" logs/*.log
```

### Q2: 盘口失衡计算不准确

**检查**:
- 盘口数据格式是否正确
- `lookback_levels` 设置是否合理
- 数据是否实时更新

### Q3: 交易许可置信度过低

**调整**:
```yaml
multimodal:
  trade_permission:
    threshold: 0.5  # 降低阈值（默认 0.6）
```

---

## 下一步计划 (Phase 3)

### 缠论 + 多模态融合

**目标**: 结合微观缠论买卖点和宏观情绪过滤

**集成方式**:
```python
# 综合评分
final_score = (
    signal_engine_score * 0.4 +      # 技术面
    chanlun_score * 0.3 +            # 缠论
    multimodal_score * 0.3           # 宏观情绪
)

# 双重过滤
if (chanlun_signal == 'BUY' and 
    multimodal_snapshot.is_trade_permitted):
    execute_trade()
```

### 新增模态

1. **链上数据模态**: 大额转账、交易所流入流出
2. **新闻舆情模态**: NLP 分析新闻标题
3. **社交媒体模态**: Twitter/Reddit 热度分析

---

## 参考资源

- [Alternative.me API 文档](https://alternative.me/crypto/fear-and-greed-index/#api)
- [aiohttp 异步编程指南](https://docs.aiohttp.org/)
- [订单簿失衡研究论文](https://arxiv.org/abs/1803.00043)

---

**版本**: 1.0.0  
**最后更新**: 2024-04-12  
**维护者**: Mini-Leviathan 开发团队
