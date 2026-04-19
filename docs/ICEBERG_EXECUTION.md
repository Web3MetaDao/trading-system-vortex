# 冰山订单执行集成指南

## Phase 4 完成状态 ✅

### 已实现功能

**文件位置**: `src/execution_engine.py` (v2.0.0)

**核心功能**:
- ✅ **TWAP 冰山算法**: 大单拆分成多个小单
- ✅ **随机化切片**: 避免规律性被狙击
- ✅ **Maker 挂单优化**: 减少滑点和手续费
- ✅ **超时撤单追价**: 30 秒未成交自动撤单
- ✅ **幂等性保证**: 防止断网重复下单
- ✅ **实时盘口拉取**: 每次发单前更新价格

---

## 快速开始

### 1. 基本使用（异步）

```python
from src.execution_engine import ExecutionEngine
import asyncio

async def execute_large_order():
    engine = ExecutionEngine(mode='testnet')
    
    # 执行 10000 USDT 的冰山订单
    stats = await engine.submit_twap_iceberg_order(
        symbol='BTCUSDT',
        side='BUY',
        total_quantity=10000,      # 总数量 1 万 USDT
        min_slice=500,             # 最小切片 500 USDT
        max_slices=10,             # 最多 10 个切片
        price_offset_pct=0.001     # 价格偏移 0.1%
    )
    
    print(f"执行完成:")
    print(f"  总数量：{stats.total_quantity} USDT")
    print(f"  已成交：{stats.filled_quantity} USDT")
    print(f"  成交切片：{stats.executed_slices}/{len(stats.slice_details)}")
    print(f"  平均价格：{stats.avg_price:.2f}")
    print(f"  执行时间：{stats.execution_time_seconds:.1f}秒")
    print(f"  总手续费：{stats.total_fees:.4f} USDT")

asyncio.run(execute_large_order())
```

### 2. 同步包装器

```python
from src.execution_engine import ExecutionEngine
import asyncio

def execute_iceberg_sync(**kwargs):
    """同步调用冰山订单"""
    engine = ExecutionEngine(mode='testnet')
    return asyncio.run(engine.submit_twap_iceberg_order(**kwargs))

# 使用
stats = execute_iceberg_sync(
    symbol='BTCUSDT',
    side='BUY',
    total_quantity=50000,
    min_slice=1000
)
```

---

## 核心算法详解

### 1. 切片策略

**目标**: 将大单拆分成不易被察觉的小单

```python
# 计算切片大小（随机化）
avg_slice_size = total_quantity / max_slices
slice_size = max(min_slice, avg_slice_size * random.uniform(0.8, 1.2))

# 例：10000 USDT，分成 10 片
# 平均切片：1000 USDT
# 实际切片：800-1200 USDT 随机
```

**优势**:
- 避免固定模式被高频交易识别
- 减少市场冲击
- 降低滑点

### 2. Maker 挂单优化

**逻辑**:
```python
if side == 'BUY':
    # 买单：买一价上方 0.1%
    limit_price = best_bid * 1.001
else:
    # 卖单：卖一价下方 0.1%
    limit_price = best_ask * 0.999
```

**优势**:
- 确保优先成交（价格优于盘口）
- 仍为 Maker 单（享受低手续费）
- 减少滑点损失

### 3. 超时撤单机制

**流程**:
```
1. 提交限价单
2. 等待最多 30 秒
   - 每 2 秒查询一次状态
   - 如果成交 → 记录并继续下一片
   - 如果超时 → 撤单并返回失败
3. 失败的切片不计入统计
```

**代码**:
```python
while time.time() - start_wait < 30:
    time.sleep(2)
    status = check_order_status()
    
    if status == 'FILLED':
        return {'accepted': True, ...}
    
# 超时撤单
cancel_order()
return {'accepted': False, 'reason': 'timeout_30s'}
```

### 4. 幂等性保证

**问题**: 断网重连可能导致重复下单

**解决**:
```python
# 生成唯一订单 ID
idempotency_key = f"ICE_{sha256(symbol_side_index_time)[:16]}"

# 检查是否已执行
if idempotency_key in _idempotency_cache:
    logger.info("切片已执行，跳过")
    continue

# 执行后记录
_idempotency_cache[idempotency_key] = order_id
```

**效果**: 即使网络中断重连，也不会重复执行同一切片

---

## 数据结构

### IcebergStats

```python
@dataclass
class IcebergStats:
    total_quantity: float          # 总数量
    executed_slices: int           # 已执行切片数
    filled_quantity: float         # 已成交数量
    avg_price: float               # 平均成交价
    total_fees: float              # 总手续费
    execution_time_seconds: float  # 执行时间
    slice_details: list[dict]      # 每个切片详情
```

### Slice Detail

```python
{
    'slice_index': 0,              # 切片索引
    'quantity': 1000.0,            # 切片数量
    'limit_price': 50050.0,        # 限价
    'filled_qty': 1000.0,          # 成交量
    'avg_price': 50048.5,          # 均价
    'fee': 1.0,                    # 手续费
    'status': 'FILLED',            # 状态
    'reason': 'filled_after_wait'  # 成交原因
}
```

---

## 配置参数说明

### submit_twap_iceberg_order 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `symbol` | str | - | 交易对 (BTCUSDT) |
| `side` | str | - | 'BUY' 或 'SELL' |
| `total_quantity` | float | - | 总数量 (USDT) |
| `min_slice` | float | - | 最小切片数量 |
| `max_slices` | int | 10 | 最大切片数 |
| `price_offset_pct` | float | 0.001 | 价格偏移 (0.1%) |

### 推荐配置

**小资金 (< 1 万 USDT)**:
```python
{
    'min_slice': 500,
    'max_slices': 5,
    'price_offset_pct': 0.001
}
```

**中等资金 (1-10 万 USDT)**:
```python
{
    'min_slice': 1000,
    'max_slices': 10,
    'price_offset_pct': 0.001
}
```

**大资金 (> 10 万 USDT)**:
```python
{
    'min_slice': 2000,
    'max_slices': 20,
    'price_offset_pct': 0.002,  # 加大偏移确保成交
    'wait_time_range': (5, 10)  # 延长等待时间
}
```

---

## 日志示例

```
INFO:execution_engine:开始 TWAP 冰山订单：symbol=BTCUSDT, side=BUY, total_qty=10000.00, min_slice=500.00
INFO:execution_engine:切片 0/10 执行完成：qty=950.00, filled=950.00, price=50048.50
DEBUG:execution_engine:等待 5.3 秒后执行下一切片
INFO:execution_engine:切片 1/10 执行完成：qty=1100.00, filled=1100.00, price=50052.30
INFO:execution_engine:切片 2/10 执行失败：timeout_30s
WARNING:execution_engine:切片 2 执行失败：timeout_30s
INFO:execution_engine:TWAP 冰山订单完成：executed=9/10, filled=9200.00/10000.00, avg_price=50051.25, time=67.5s
```

---

## 性能优化建议

### 1. 减少 API 调用

```python
# 缓存盘口数据（500ms 内有效）
if ticker_cache and time.time() - cache_time < 0.5:
    ticker = ticker_cache
else:
    ticker = fetch_latest_ticker()
```

### 2. 并发执行（高级）

```python
# 同时提交多个切片（需控制风险）
tasks = [
    submit_slice(symbol, side, qty, price, i)
    for i, qty in enumerate(slices)
]
results = await asyncio.gather(*tasks)
```

### 3. 动态调整切片

```python
# 根据市场流动性调整
if market_volume_24h < 1000000:
    # 低流动性：减小切片
    slice_size *= 0.5
elif market_volume_24h > 100000000:
    # 高流动性：增大切片
    slice_size *= 1.5
```

---

## 测试用例

### 单元测试

```python
import unittest
from src.execution_engine import ExecutionEngine

class TestIcebergOrder(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionEngine(mode='paper')
    
    def test_iceberg_order_paper(self):
        """测试 Paper 模式冰山订单"""
        import asyncio
        
        async def run_test():
            stats = await self.engine.submit_twap_iceberg_order(
                symbol='BTCUSDT',
                side='BUY',
                total_quantity=1000,
                min_slice=100,
                max_slices=3
            )
            
            self.assertEqual(stats.total_quantity, 1000)
            self.assertGreater(stats.executed_slices, 0)
        
        asyncio.run(run_test())
    
    def test_idempotency_check(self):
        """测试幂等性"""
        key1 = self.engine._generate_iceberg_order_id('BTCUSDT', 'BUY', 0, 1000000)
        key2 = self.engine._generate_iceberg_order_id('BTCUSDT', 'BUY', 0, 1000000)
        
        self.assertEqual(key1, key2)  # 相同参数应生成相同 ID
        
        # 模拟已执行
        self.engine._idempotency_cache[key1] = 'order_123'
        
        self.assertTrue(self.engine._check_idempotency(key1))

if __name__ == '__main__':
    unittest.main()
```

---

## 故障排查

### Q1: 订单长时间未成交

**原因**: 价格偏离市场太远

**解决**:
```python
# 增大价格偏移
price_offset_pct=0.002  # 0.2%
```

### Q2: 执行时间过长

**原因**: 切片太多或等待时间长

**优化**:
```python
# 减少切片数，增大每片大小
max_slices=5,
min_slice=2000
```

### Q3: 断网后重复下单

**检查**: 幂等性是否生效

```python
# 查看日志
grep "切片已执行" logs/*.log

# 检查缓存
print(engine._idempotency_cache)
```

---

## 与现有系统集成

### 在 SignalEngine 中调用

```python
from src.signal_engine import SignalEngine
from src.execution_engine import ExecutionEngine

class IntegratedTradingSystem:
    def __init__(self):
        self.signal_engine = SignalEngine()
        self.execution_engine = ExecutionEngine(mode='live')
    
    async def execute_signal(self, decision, context):
        """执行交易信号"""
        if decision.score < 5:
            return  # 信号太弱
        
        # 计算订单大小
        quantity = self.risk_engine.calculate_position_size(...)
        
        # 大单使用冰山算法
        if quantity > 5000:  # > 5000 USDT
            stats = await self.execution_engine.submit_twap_iceberg_order(
                symbol=decision.symbol,
                side=decision.side,
                total_quantity=quantity,
                min_slice=500
            )
            
            logger.info("冰山订单完成：%s", stats)
        else:
            # 小单直接市价单
            result = self.execution_engine.submit_order(
                symbol=decision.symbol,
                side=decision.side,
                quantity_usdt=quantity,
                order_type='MARKET'
            )
```

---

## 最佳实践

### 1. 执行时机选择

```python
# 避免高波动时段
if market_volatility > 0.05:  # 5% 波动
    # 暂停或减小切片
    min_slice *= 0.5
```

### 2. 监控执行质量

```python
# 计算滑点
slippage = (stats.avg_price - market_price) / market_price

if slippage > 0.005:  # > 0.5%
    # 调整策略
    price_offset_pct *= 1.5
```

### 3. 手续费优化

```python
# 使用 BNB 支付手续费（25% 折扣）
# Binance: Maker 0.1% -> 0.075%
```

---

## 参考资源

- [Binance API 文档](https://binance-docs.github.io/apidocs/)
- [TWAP 算法详解](https://www.investopedia.com/terms/t/twap.asp)
- [冰山订单研究](https://www.jstor.org/stable/2696863)

---

**版本**: 2.0.0  
**最后更新**: 2024-04-12  
**维护者**: Mini-Leviathan 开发团队
