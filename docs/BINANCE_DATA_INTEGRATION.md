# Binance K 线数据接入说明

## ✅ 当前接入状态

您的 Mini-Leviathan 量化系统已经**完全接入** Binance K 线数据！

### 数据源配置

```python
# src/market_data.py
class MarketDataClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        # 主节点
        primary = "https://api.binance.com"
        
        # 备用节点
        fallback = "https://api1.binance.com"
        
        # 第三备用（Vision API）
        vision = "https://data-api.binance.vision"
```

### API 端点

| 数据类型 | API 端点 | 说明 |
|----------|----------|------|
| **K 线数据** | `/api/v3/klines` | OHLCV 蜡烛图数据 |
| **24 小时行情** | `/api/v3/ticker/24hr` | 24 小时涨跌幅、成交量 |
| **最新价格** | `/api/v3/ticker/price` | 简单价格查询 |

### K 线数据格式

```python
{
    "open_time": 1776020400000.0,    # 开盘时间（毫秒）
    "open": 71171.06,                # 开盘价
    "high": 71200.00,                # 最高价
    "low": 71100.00,                 # 最低价
    "close": 71171.06,               # 收盘价
    "volume": 1234.567,              # 成交量
    "close_time": 1776023999999.0,   # 收盘时间（毫秒）
    "quote_volume": 87654321.0       # 成交额
}
```

## 🔧 配置选项

### 环境变量（.env）

```bash
# Binance API 配置
BINANCE_BASE_URL=https://api.binance.com
BINANCE_FALLBACK_BASE_URL=https://api1.binance.com
BINANCE_VISION_BASE_URL=https://data-api.binance.vision

# 超时和重试
BINANCE_TIMEOUT_SECONDS=10.0
BINANCE_MAX_RETRIES=2
BINANCE_RETRY_DELAY_SECONDS=1.0
```

### 支持的 K 线周期

```
1m, 3m, 5m, 15m, 30m,
1h, 2h, 4h, 6h, 8h, 12h,
1d, 3d, 1w, 1M
```

### 数据获取限制

- **单次请求最大返回**: 1000 根 K 线
- **60 天回测需要**: 1440 根 K 线（1 小时周期）
- **解决方案**: 多次请求 + 时间范围分割

## 📊 当前回测状态

### 运行中：60 天完整回测

```
标的：BTCUSDT
周期：1h
K 线数量：1440 根
初始资金：$10,000
单笔仓位：$100
预计耗时：30-60 分钟
```

### 数据获取流程

1. **获取 K 线历史数据**
   ```python
   klines = client.fetch_klines("BTCUSDT", interval="1h", limit=1440)
   ```

2. **构建上下文**
   ```python
   context = data_provider.build_context(
       benchmark_symbol="BTCUSDT",
       watchlist=["BTCUSDT"],
       state_interval="1h",
       state_limit=100,
       signal_interval="1h",
       signal_limit=100,
   )
   ```

3. **逐根 K 线回测**
   - 对于每根 K 线，重新获取完整上下文
   - 调用 State Engine 评估市场状态
   - 调用 Signal Engine 生成交易信号
   - 执行模拟交易

## 🎯 优化建议

### 1. 使用缓存减少 API 调用

```python
# 回测前一次性获取所有 K 线
all_klines = client.fetch_klines("BTCUSDT", interval="1h", limit=1440)

# 回测过程中使用本地数据，不再重复调用 API
for i in range(20, len(all_klines)):
    context_klines = all_klines[:i]
    current_kline = all_klines[i]
```

### 2. 使用 WebSocket 实时数据（实盘）

```python
# 实盘模式可以使用 WebSocket 接收实时 K 线
# wss://stream.binance.com:9443/ws/btcusdt@kline_1h
```

### 3. 使用本地数据库

```python
# 将历史 K 线存储到 SQLite/PostgreSQL
# 回测时直接查询本地数据库
```

## 📱 Telegram 报告

回测完成后，系统会自动发送详细报告到您的 Telegram，包括：

- 💰 资金状况
- 📊 交易统计
- 📊 绩效指标
- 📊 历史对比（100 天回测数据）
- 📝 最近交易记录

## ✅ 验证数据接入

运行以下命令验证数据接入是否正常：

```bash
cd "/Users/micheal/Documents/trading system"
python -c "
import sys
sys.path.insert(0, 'src')
from market_data import MarketDataClient
client = MarketDataClient()
klines = client.fetch_klines('BTCUSDT', interval='1h', limit=10)
print(f'✅ 成功获取 {len(klines)} 根 K 线')
print(f'最新收盘价：{klines[-1][\"close\"]}')
"
```

---

**结论**: 您的系统已经完全接入 Binance K 线数据，回测正在正常运行中！
