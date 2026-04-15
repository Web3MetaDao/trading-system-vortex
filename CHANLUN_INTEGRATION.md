# 缠论引擎集成指南

## Phase 1 完成状态 ✅

### 已实现功能

**文件位置**: `src/chanlun_engine.py`

**核心类**:
- `ChanlunEngine`: 主引擎类
- `Fractal`: 顶底分型数据结构
- `Bi`: 笔数据结构
- `Zhongshu`: 中枢数据结构
- `ChanlunSignal`: 信号输出（与 SignalEngine 对接）

**识别能力**:
1. ✅ 顶底分型识别（处理包含关系）
2. ✅ 笔生成（顶底交替原则）
3. ✅ 中枢定位（连续三笔重叠）
4. ✅ 底背驰检测（价格新低 + 动量背离）
5. ✅ 第三买点（突破中枢回踩不破）

---

## 快速开始

### 1. 基本使用

```python
from src.chanlun_engine import ChanlunEngine
import pandas as pd

# 初始化引擎
engine = ChanlunEngine(config={
    'min_bi_length': 4,           # 最小笔长度
    'zhongshu_overlap_threshold': 0.8,
    'divergence_lookback': 3
})

# 分析 K 线数据
df = get_kline_data()  # DataFrame with OHLCV columns
signal = engine.analyze(df)

# 输出结果
print(f"信号：{signal.signal}")      # 'BUY', 'SELL', 'WAIT'
print(f"模式：{signal.pattern}")     # 'bottom_divergence', 'third_buy'
print(f"强度：{signal.strength:.2f}") # 0.0-1.0
```

### 2. 与 SignalEngine 集成

```python
from src.signal_engine import SignalEngine
from src.chanlun_engine import ChanlunEngine, integrate_with_signal_engine

# 现有 SignalEngine
signal_engine = SignalEngine()

# 新增 ChanlunEngine
chanlun_engine = ChanlunEngine()

# 1. 获取原始评分
decision = signal_engine.evaluate(symbol, market_state, context)
original_score = decision.score

# 2. 缠论分析
df = context['snapshot'].to_dataframe()  # 转换为 DataFrame
chanlun_signal = chanlun_engine.analyze(df)

# 3. 融合评分
new_score, setup_type, explain = integrate_with_signal_engine(
    chanlun_signal,
    original_score,
    strategy_config
)

# 4. 更新决策
if chanlun_signal.signal == 'BUY' and new_score > original_score:
    decision.score = new_score
    decision.setup = setup_type
    decision.explain.update(explain)
```

---

## 配置文件更新

### config/strategy.yaml

```yaml
# 添加缠论配置块
chanlun:
  enabled: true              # 是否启用
  weight: 2.0                # 信号权重（影响评分加分）
  min_bi_length: 4           # 最小笔长度（K 线数）
  divergence_lookback: 3     # 背驰检测回看笔数
  
  # 信号模式开关
  patterns:
    bottom_divergence: true  # 底背驰
    third_buy: true          # 第三买点
    top_divergence: false    # 顶背驰（暂时禁用）
```

---

## 信号说明

### 1. 底背驰 (Bottom Divergence)

**检测条件**:
- 价格创新低（当前笔 low < 前一笔 low）
- MACD 动量未创新低（动量缩小 > 20%）
- 当前处于下降笔末端

**信号强度计算**:
```python
strength = min(1.0, (current_momentum - prev_momentum) / abs(prev_momentum) + 0.5)
```

**示例输出**:
```json
{
  "signal": "BUY",
  "pattern": "bottom_divergence",
  "strength": 0.85,
  "explain": {
    "price_low": 48500.0,
    "prev_low": 49000.0,
    "current_momentum": -0.015,
    "prev_momentum": -0.025,
    "reason": "价格创新低但动量未创新低（底背驰）"
  }
}
```

### 2. 第三买点 (Third Buy Point)

**检测条件**:
- 存在一个上升中枢
- 价格突破中枢（突破笔 high > 中枢 ZG）
- 回踩不破（回踩笔 low >= 中枢 ZG）

**信号强度计算**:
```python
distance_to_zg = (pullback_low - zhongshu_zg) / zhongshu_zg
strength = min(1.0, 0.7 + distance_to_zg * 10)
```

**示例输出**:
```json
{
  "signal": "BUY",
  "pattern": "third_buy",
  "strength": 0.92,
  "explain": {
    "zhongshu_zg": 50000.0,
    "zhongshu_zd": 49500.0,
    "breakout_high": 51000.0,
    "pullback_low": 50100.0,
    "distance_to_zg": "0.20%",
    "reason": "突破中枢后回踩不破 ZG（第三买点）"
  }
}
```

---

## 日志示例

```
INFO:chanlun_engine:ChanlunEngine 初始化完成，配置：{'min_bi_length': 4}
DEBUG:chanlun_engine:识别到顶分型：index=45, price=51200.00
DEBUG:chanlun_engine:识别到底分型：index=52, price=49800.00
DEBUG:chanlun_engine:生成笔：direction=down, start=51200.00, end=49800.00
DEBUG:chanlun_engine:识别到中枢：direction=up, ZG=50500.00, ZD=50000.00
INFO:chanlun_engine:检测到底背驰信号，强度=0.85
```

---

## API 参考

### ChanlunEngine 类

#### `__init__(config: Optional[Dict] = None)`
初始化缠论引擎

**参数**:
- `config`: 配置字典
  - `min_bi_length`: 最小笔长度（默认 4）
  - `zhongshu_overlap_threshold`: 中枢重叠阈值（默认 0.8）
  - `divergence_lookback`: 背驰检测回看笔数（默认 3）

#### `analyze(df: pd.DataFrame) -> ChanlunSignal`
分析 K 线数据

**参数**:
- `df`: pandas DataFrame，包含 OHLCV 列

**返回**:
- `ChanlunSignal`: 信号对象

#### `get_fractals() -> List[Fractal]`
获取分型列表

#### `get_bi_list() -> List[Bi]`
获取笔列表

#### `get_zhongshu_list() -> List[Zhongshu]`
获取中枢列表

---

### 数据结构

#### Fractal
```python
@dataclass
class Fractal:
    index: int          # K 线索引
    price: float        # 分型价格
    fractal_type: str   # 'top' 或 'bottom'
    confirmed: bool     # 是否已确认
```

#### Bi
```python
@dataclass
class Bi:
    start_index: int    # 起始 K 线索引
    end_index: int      # 结束 K 线索引
    start_price: float  # 起始价格
    end_price: float    # 结束价格
    direction: str      # 'up' 或 'down'
    high: float         # 笔的最高价
    low: float          # 笔的最低价
```

#### Zhongshu
```python
@dataclass
class Zhongshu:
    start_bi_index: int   # 起始笔索引
    end_bi_index: int     # 结束笔索引
    high: float           # 中枢上沿（ZG）
    low: float            # 中枢下沿（ZD）
    direction: str        # 'up', 'down', 'neutral'
```

---

## 测试用例

### 单元测试示例

```python
import unittest
import pandas as pd
import numpy as np
from src.chanlun_engine import ChanlunEngine

class TestChanlunEngine(unittest.TestCase):
    def setUp(self):
        # 生成测试数据
        np.random.seed(42)
        dates = pd.date_range('2024-01-01', periods=100, freq='1h')
        prices = 50000 + np.cumsum(np.random.randn(100) * 100)
        
        self.df = pd.DataFrame({
            'open': prices + np.random.randn(100) * 50,
            'high': prices + np.abs(np.random.randn(100) * 50),
            'low': prices - np.abs(np.random.randn(100) * 50),
            'close': prices,
            'volume': np.random.randint(1000, 10000, 100)
        }, index=dates)
        
        self.engine = ChanlunEngine()
    
    def test_analyze(self):
        signal = self.engine.analyze(self.df)
        self.assertIn(signal.signal, ['BUY', 'SELL', 'WAIT'])
        self.assertIsInstance(signal.strength, float)
    
    def test_get_bi_list(self):
        self.engine.analyze(self.df)
        bi_list = self.engine.get_bi_list()
        self.assertIsInstance(bi_list, list)
    
    def test_get_zhongshu_list(self):
        self.engine.analyze(self.df)
        zhongshu_list = self.engine.get_zhongshu_list()
        self.assertIsInstance(zhongshu_list, list)

if __name__ == '__main__':
    unittest.main()
```

---

## 性能优化建议

1. **缓存重用**: 避免重复分析相同 K 线数据
   ```python
   # 使用缓存
   if not cache_expired:
       signal = engine.get_cached_signal()
   ```

2. **增量更新**: 只分析最新 K 线变化
   ```python
   # 增量分析最后 N 根 K 线
   signal = engine.analyze_incremental(df.tail(20))
   ```

3. **并行检测**: 多交易对并行分析
   ```python
   from concurrent.futures import ThreadPoolExecutor
   
   with ThreadPoolExecutor(max_workers=5) as executor:
       signals = executor.map(engine.analyze, df_list)
   ```

---

## 下一步计划 (Phase 2)

### 多模态 AI 分析

**目标**: 融合市场情绪、新闻舆情、链上数据

**模块**:
- `src/multi_modal_analyzer.py`: 多模态分析引擎
- `src/sentiment_analysis.py`: 情绪分析
- `src/on_chain_metrics.py`: 链上数据分析

**集成方式**:
```python
# 综合评分
final_score = (
    signal_engine_score * 0.5 +
    chanlun_score * 0.3 +
    multi_modal_score * 0.2
)
```

---

## 故障排查

### 常见问题

**Q1: 信号始终为 WAIT**
- 检查 K 线数据是否充足（至少 10 根）
- 检查是否形成完整笔和中枢
- 调整 `min_bi_length` 参数

**Q2: 背驰检测不准确**
- 增加 `divergence_lookback` 回看笔数
- 优化 `_calc_momentum` 方法（可替换为真实 MACD）

**Q3: 性能问题**
- 启用缓存机制
- 减少分析频率（每 N 根 K 线分析一次）

---

**版本**: 1.0.0  
**最后更新**: 2024-04-12  
**维护者**: Mini-Leviathan 开发团队
