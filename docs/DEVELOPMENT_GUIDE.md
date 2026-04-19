# 量化交易系统开发文档

## 目录

1. [系统概述](#系统概述)
2. [架构设计](#架构设计)
3. [核心模块](#核心模块)
4. [数据流](#数据流)
5. [配置系统](#配置系统)
6. [开发环境](#开发环境)
7. [测试与回测](#测试与回测)
8. [部署指南](#部署指南)
9. [Phase-2 功能](#phase-2-功能)
10. [故障排查](#故障排查)

---

## 系统概述

### 系统定位

专业级自动化量化交易系统，支持：
- **现货交易** (Binance)
- **实时信号生成**
- **多层次风控**
- **回测验证**
- **模拟/实盘双模式**

### 技术栈

- **语言**: Python 3.11+
- **依赖管理**: pip / pyproject.toml
- **配置格式**: YAML
- **数据存储**: JSONL (日志) + JSON (状态)
- **代码质量**: Ruff + Black + Mypy

### 核心特性

| 特性 | 状态 | 说明 |
|------|------|------|
| 市场状态分类 (S1-S5) | ✅ 完成 | 基于趋势、波动率、动能 |
| 信号分级 (A-B-C) | ✅ 完成 | 基于评分系统 |
|  setups (Breakout/Pullback/Reclaim) | ✅ 完成 | 三种入场模式 |
| 风险管理 | ✅ 完成 | 仓位、止损、熔断 |
| 一致性审计 | ✅ 完成 | 回测/实盘一致性验证 |
| Telegram 通知 | ✅ 完成 | 交易报告、告警 |
| Phase-2 增强 | ✅ 已启用 | VWAP、跨市场、OI、Funding |

---

## 架构设计

### 系统架构图

```
┌─────────────────────────────────────────────────────────┐
│                    Main Entry Point                      │
│                      (main.py)                           │
└───────────────────┬─────────────────────────────────────┘
                    │
        ┌───────────┼───────────┐
        │           │           │
        ▼           ▼           ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Market Data  │ │ State Engine │ │Signal Engine │
│  (Binance)   │ │  (S1-S5)     │ │  (A-B-C)     │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                 │
       └────────────────┼─────────────────┘
                        │
                        ▼
              ┌──────────────────┐
              │  Risk Engine     │
              │  (Position Sizing)│
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │ Execution Engine │
              │  (Order Mgmt)    │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │Portfolio Manager │
              │  (PnL Tracking)  │
              └──────────────────┘
```

### 模块职责

| 模块 | 职责 | 关键文件 |
|------|------|----------|
| **数据层** | 获取K线、实时推送 | `market_data.py`, `data_provider.py` |
| **分析层** | 市场状态、信号生成 | `state_engine.py`, `signal_engine.py` |
| **决策层** | 仓位管理、风险控制 | `risk_engine.py`, `portfolio_manager.py` |
| **执行层** | 订单提交、状态跟踪 | `execution_engine.py`, `automated_trading.py` |
| **监控层** | 日志、通知、审计 | `monitoring.py`, `consistency_audit.py` |

---

## 核心模块

### 1. 市场数据模块

#### MarketDataClient (`market_data.py`)

**职责**: 从 Binance 获取现货 K 线数据

```python
client = MarketDataClient()
klines = client.fetch_klines(
    symbol="BTCUSDT",
    interval="1h",
    limit=100,
    end_time=1700000000000
)
```

**方法**:
- `fetch_klines()`: 获取历史 K 线
- `get_ticker()`: 获取实时价格
- `get_24h_ticker()`: 获取 24 小时行情

#### DataProvider (`data_provider.py`)

**职责**: 统一数据接口，整合多源数据

```python
provider = DataProvider()
snapshot = provider.get_market_snapshot(
    symbol="BTCUSDT",
    benchmark_symbol="BTCUSDT",
    signal_limit=100,
    state_limit=100
)
```

**返回**:
```python
{
    "snapshot": MarketSnapshot(...),
    "benchmark_snapshot": MarketSnapshot(...),
    "intermarket": {...},
    "derivatives": {...},
    "data_health": {...}
}
```

### 2. 状态引擎 (`state_engine.py`)

**职责**: 根据市场数据分类为 S1-S5 状态

**状态定义**:

| 状态 | 趋势 | 波动率 | 操作建议 |
|------|------|--------|----------|
| **S1** | 强上涨 | 低 | 积极做多 |
| **S2** | 上涨 | 中 | 谨慎做多 |
| **S3** | 震荡 | - | 选择性交易 |
| **S4** | 下跌 | 中 | 谨慎观望 |
| **S5** | 强下跌 | 高 | 禁止开仓 |

**使用示例**:
```python
state_engine = StateEngine()
market_state = state_engine.classify({
    "strategy": strategy_config,
    "benchmark_snapshot": snapshot
})
# 返回: {"state": "S3", "confidence": 0.85}
```

### 3. 信号引擎 (`signal_engine.py`)

**职责**: 生成交易信号 (A-B-C 级)

**信号评分组成**:
```python
score = (
    ema_alignment_bonus +      # EMA 多头排列
    breakout_bonus +           # 突破信号
    pullback_bonus +           # 回调确认
    vwap_dev_bonus +           # VWAP 偏离 (Phase-2)
    intermarket_bonus -        # 跨市场脉冲 (Phase-2)
    breakdown_penalty          # 跌破支撑
)
```

**信号等级**:

| 等级 | 最低分数 | 最大持仓 | 风险系数 |
|------|----------|----------|----------|
| **A** | 6 | 2 | 1.0 |
| **B** | 3 | 3 | 0.8 |
| **C** | 0 | 0 | 0 (禁止) |

**使用示例**:
```python
signal_engine = SignalEngine()
decision = signal_engine.evaluate(
    symbol="BTCUSDT",
    market_state="S3",
    context={
        "snapshot": snapshot,
        "strategy": strategy_config,
        "benchmark_snapshot": benchmark,
        "intermarket": intermarket_data,
        "derivatives": derivatives_data,
        "data_health": health_status
    }
)
# 返回: SignalDecision(grade="B", side="BUY", score=4)
```

### 4. 风险引擎 (`risk_engine.py`)

**职责**: 仓位计算、止损止盈、退出管理

**核心方法**:

```python
# 计算仓位大小
position_size = risk_engine.calculate_position_size(
    symbol="BTCUSDT",
    price=50000,
    stop_loss_pct=2.0
)
# 返回: 0.001 BTC (基于 2% 风险)

# 检查是否应该退出
exit_reason = risk_engine.exit_reason(
    position={"entry_price": 50000, "side": "BUY"},
    snapshot=current_snapshot,
    market_state="S5"
)
# 返回: "s5_protect (1.52%)" 或 None
```

**退出规则优先级**:
1. 止损触发 (`stop_loss_pct`)
2. 止盈触发 (`take_profit_pct`)
3. S5 保护 (盈利>1.5% 时保护利润)
4. EMA 退出 (价格跌破 EMA)
5. 追踪止损 (从高点回撤 1.5%)

### 5. 执行引擎 (`execution_engine.py`)

**职责**: 订单提交、状态跟踪、幂等性保证

**订单类型**:
- `MARKET`: 市价单
- `LIMIT`: 限价单

**使用示例**:
```python
executor = ExecutionEngine()
order = executor.submit_order(
    symbol="BTCUSDT",
    side="BUY",
    type="MARKET",
    quantity=0.001,
    idempotency_key="btc_buy_20240101_120000"
)
```

**幂等性保证**:
- 每个订单有唯一 `idempotency_key`
- 防止重复提交
- 订单状态缓存 5 分钟

### 6. 组合管理 (`portfolio_manager.py`)

**职责**: 持仓跟踪、PnL 计算、业绩统计

**持久化**:
- 文件：`logs/portfolio_state.json`
- 格式：JSON
- 更新频率：每笔交易后

**统计指标**:
- 总盈亏 (USDT / %)
- 胜率
- 盈利因子
- 最大回撤
- 平均持仓时间

---

## 数据流

### 实时交易流程

```
1. 定时触发 (每 15 分钟)
   ↓
2. 获取市场数据
   ├─ 主交易对 K 线 (100 条)
   ├─ 基准 K 线 (100 条)
   ├─ 跨市场数据 (BTC/ETH/DXY/NQ)
   └─ 衍生品数据 (OI/Funding)
   ↓
3. 状态分类
   └─ 输出: S1/S2/S3/S4/S5
   ↓
4. 信号生成
   ├─ Setup 检测 (Breakout/Pullback/Reclaim)
   ├─ 评分计算
   └─ 输出: A/B/C 级 + BUY/SELL/HOLD
   ↓
5. 风险检查
   ├─ 仓位限制
   ├─ 日止损检查
   ├─ 连续亏损检查
   └─ 市场状态过滤 (S5 禁止)
   ↓
6. 执行下单
   ├─ 生成订单 ID
   ├─ 提交到 Binance
   └─ 记录到日志
   ↓
7. 更新状态
   ├─ Portfolio 状态
   ├─ Journal 日志
   └─ Telegram 通知
```

### 回测流程

```python
# backtest.py
for each historical bar:
    1. 获取截至当前的 K 线
    2. 调用 state_engine.classify()
    3. 调用 signal_engine.evaluate()
    4. 检查 risk_engine.exit_reason()
    5. 更新持仓和 PnL
    6. 记录交易历史

# 输出分析面板
=== BACKTEST ANALYSIS PANEL ===
Mode: single | Symbols: BTCUSDT
Performance: closed=11 wins=8 losses=3 winRate=72.73%
By market state:
- S3: count=10 winRate=80.0%
By setup:
- reclaim: count=11 winRate=72.73%
```

---

## 配置系统

### 配置文件结构

```
config/
├── strategy.yaml      # 策略参数 (信号、状态)
├── risk.yaml          # 风控参数 (仓位、止损)
├── symbols.yaml       # 交易对列表
└── signal_taxonomy.yaml  # 信号分类定义
```

### strategy.yaml 详解

```yaml
# 交易模式
mode: spot              # spot / futures

# 特征开关 (Phase-2)
feature_flags:
  use_vwap_dev: true
  use_intermarket_filter: true
  use_oi_change: true
  use_funding_shift: true

# 信号参数
signal_params:
  ema_fast_period: 20
  ema_slow_period: 50
  momentum_strong_min_pct: 2.0
  close_near_high_min: 0.70

# 信号等级
signal_levels:
  A:
    enabled: false      # 禁用 A 级 (当前策略)
    min_score: 10
  B:
    enabled: true
    min_score: 3
    max_positions: 3

# Setup 过滤器
setup_filters:
  pullback:
    enabled: true
    require_market_states: [S1, S2]  # 只在强趋势允许
  reclaim:
    enabled: true
    require_market_states: [S1, S2]

# Phase-2 功能配置
signal_features:
  vwap_dev:
    enabled: true
    stddev_period: 20
    extreme_zscore: 2.0

macro_filters:
  intermarket:
    enabled: true
    reference_assets: [BTCUSDT, ETHUSDT, NQ100, DXY]
    state_override_risk_off: true

derivatives_filters:
  oi_change:
    enabled: true
    strong_build_up_pct: 5.0
  funding_shift:
    enabled: true
    extreme_positive_threshold: 0.03
```

### risk.yaml 详解

```yaml
capital_usdt: 100           # 初始资金
risk_per_trade_pct: 2.5     # 单笔风险 (2.5%)
stop_loss_pct: 2.0          # 止损 2%
take_profit_pct: 3.5        # 止盈 3.5% (1.75:1 盈亏比)
ema_exit_period: 12         # EMA 退出周期

daily_stop_loss_pct: 4.0    # 日止损 4%
max_total_exposure_pct: 50  # 最大总仓位 50%
max_open_positions: 3       # 最多 3 个持仓
consecutive_loss_pause: 3   # 连续 3 笔亏损暂停

hard_stop_required: true    # 强制止损
allow_withdrawal: false     # 禁止提币
```

---

## 开发环境

### 环境搭建

```bash
# 1. 克隆项目
cd /Users/micheal/Documents/trading\ system

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt
# 或
pip install -e ".[dev]"

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 Binance API 密钥

# 5. 验证安装
python src/main.py --help
```

### 代码规范

**格式化**:
```bash
# Black 格式化
black src/ tests/

# isort 导入排序
isort src/ tests/

# Ruff 检查
ruff check src/
```

**类型检查**:
```bash
mypy src/ --ignore-missing-imports
```

**提交前检查**:
```bash
ruff check src/ && black --check src/ && mypy src/
```

### 目录结构

```
trading-system/
├── src/                    # 源代码
│   ├── main.py            # 主入口
│   ├── backtest.py        # 回测引擎
│   ├── signal_engine.py   # 信号引擎
│   ├── risk_engine.py     # 风险引擎
│   └── ...
├── config/                 # 配置文件
├── tests/                  # 测试用例
├── logs/                   # 运行日志
│   ├── journal.jsonl      # 交易日志
│   ├── portfolio_state.json  # 持仓状态
│   └── backtest_*.json    # 回测结果
├── requirements.txt        # 依赖列表
├── pyproject.toml         # 项目配置
└── DEVELOPMENT_GUIDE.md   # 本文档
```

---

## 测试与回测

### 单元测试

```bash
# 运行所有测试
pytest tests/ -v

# 运行特定测试
pytest tests/test_signal_engine.py -v

# 覆盖率报告
pytest --cov=src tests/
```

### 回测

#### 基础回测

```bash
# 100 天回测 (约 950 条 1h K 线)
PYTHONPATH=src python src/backtest.py \
  --symbol BTCUSDT \
  --benchmark-symbol BTCUSDT \
  --signal-limit 2400 \
  --state-limit 2400 \
  --out logs/backtest_100d.json
```

#### 参数扫描

```bash
# 扫描 EMA 周期和 B 级门槛
PYTHONPATH=src python src/backtest.py \
  --scan \
  --symbol BTCUSDT \
  --ema-periods 10,20,30 \
  --b-scores 3,4,5 \
  --allow-s3-values true,false
```

#### 策略实验室

```bash
# 对比不同 setup 组合
PYTHONPATH=src python src/strategy_lab.py \
  --symbol BTCUSDT \
  --out logs/strategy_lab.json
```

### 解读回测结果

```json
{
  "performance": {
    "closed_trades": 11,
    "wins": 8,
    "losses": 3,
    "win_rate_pct": 72.73,
    "total_pnl_usdt": 0.23,
    "profit_factor": 1.41,
    "max_drawdown_pct": 0.48
  },
  "analysis": {
    "by_market_state": {
      "S3": {"count": 10, "win_rate": 80.0}
    },
    "by_setup": {
      "reclaim": {"count": 11, "win_rate": 72.73}
    }
  }
}
```

**关键指标**:
- **胜率** > 60%: 良好
- **盈利因子** > 1.5: 优秀
- **最大回撤** < 1%: 安全

---

## 部署指南

### 模拟交易部署

```bash
# 1. 设置环境变量
export TRADING_MODE=paper
export BINANCE_API_KEY=your_testnet_key
export BINANCE_API_SECRET=your_testnet_secret

# 2. 运行定时任务
PYTHONPATH=src python src/runner.py \
  --interval-seconds 900 \
  --symbols BTCUSDT,ETHUSDT
```

### 实盘部署

**⚠️ 风险提示**: 实盘前必须完成以下步骤:

1. **回测验证**: 胜率>60%, 盈利因子>1.3
2. **一致性审计**: 回测/实盘信号一致率>95%
3. **小额测试**: 先用 0.1% 仓位测试 1 周
4. **监控告警**: Telegram 通知必须启用

```bash
# 1. 设置实盘环境
export TRADING_MODE=live
export BINANCE_API_KEY=your_live_key
export BINANCE_API_SECRET=your_live_secret
export TELEGRAM_BOT_TOKEN=your_bot_token
export TELEGRAM_CHAT_ID=your_chat_id

# 2. 启动监控
PYTHONPATH=src python src/monitoring.py &

# 3. 启动交易
PYTHONPATH=src python src/runner.py \
  --interval-seconds 900 \
  --symbols BTCUSDT
```

### 定时任务 (cron)

```bash
# 编辑 crontab
crontab -e

# 每 15 分钟运行一次
*/15 * * * * cd /path/to/trading-system && \
  /path/to/.venv/bin/python src/runner.py --interval-seconds 900 >> logs/cron.log 2>&1
```

---

## Phase-2 功能

### 已实现功能

#### 1. VWAP 偏离 (`vwap_dev`)

**位置**: `signal_engine.py:L159-196`

**逻辑**:
- 计算价格相对 VWAP 的 Z-score
- Z-score > 2.0: 极端偏离，扣分
- Z-score 在 1.5-2.0: 回归机会，加分
- Z-score < 0.5: 突破确认，加分

**配置**:
```yaml
signal_features:
  vwap_dev:
    enabled: true
    stddev_period: 20
    extreme_zscore: 2.0
    score_bonus_reclaim: 2
```

#### 2. 跨市场脉冲 (`intermarket`)

**位置**: `signal_engine.py:L370-446`

**逻辑**:
- 获取 BTC/ETH/NQ100/DXY 价格
- 计算相关性
- BTC 强 + DXY 弱 -> Risk-on (+1 分)
- BTC 弱 + DXY 强 -> Risk-off (-1 分)

**配置**:
```yaml
macro_filters:
  intermarket:
    enabled: true
    reference_assets: [BTCUSDT, ETHUSDT, NQ100, DXY]
    state_override_risk_off: true  # S5 时降级处理
```

#### 3. OI 变化 (`oi_change`)

**位置**: `signal_engine.py:L448-492`

**逻辑**:
- 获取 Binance 期货持仓量
- 价格上涨 + OI 上涨 -> 趋势确认 (+1 分)
- 价格下跌 + OI 上涨 -> 挤压风险 (-1 分)

**配置**:
```yaml
derivatives_filters:
  oi_change:
    enabled: true
    strong_build_up_pct: 5.0
```

#### 4. 资金费率偏移 (`funding_shift`)

**位置**: `signal_engine.py:L494-530`

**逻辑**:
- 获取资金费率历史
- 费率 > 0.03%: 多头拥挤，反向交易加分
- 费率 < -0.03%: 空头拥挤，反向交易加分

**配置**:
```yaml
derivatives_filters:
  funding_shift:
    enabled: true
    extreme_positive_threshold: 0.03
```

### 启用/禁用功能

```yaml
# config/strategy.yaml
feature_flags:
  use_vwap_dev: true           # 启用 VWAP
  use_intermarket_filter: true # 启用跨市场
  use_oi_change: true          # 启用 OI
  use_funding_shift: true      # 启用 Funding
```

**建议**: 一次只启用一个功能，回测验证后再启用下一个。

---

## 故障排查

### 常见问题

#### 1. API 连接失败

**症状**:
```
Error: Binance API request failed: Connection timeout
```

**解决**:
```bash
# 检查网络
ping api.binance.com

# 检查 API 密钥
cat .env | grep BINANCE

# 测试 API 连接
PYTHONPATH=src python -c "from src.market_data import MarketDataClient; print(MarketDataClient().get_ticker('BTCUSDT'))"
```

#### 2. 回测结果为空

**症状**:
```
Performance: closed=0 wins=0 losses=0
```

**原因**: 信号门槛过高或数据不足

**解决**:
```yaml
# 降低 B 级门槛
signal_levels:
  B:
    min_score: 3  # 改为 2 试试

# 放宽 Setup 限制
setup_filters:
  pullback:
    require_market_states: [S1, S2, S3]  # 加入 S3
```

#### 3. 实盘不交易

**症状**: 回测有信号，实盘无交易

**检查清单**:
- [ ] `TRADING_MODE` 设置正确？
- [ ] API 密钥有现货交易权限？
- [ ] 仓位限制是否太紧？
- [ ] 日止损是否触发？
- [ ] 市场状态是否 S5？

```bash
# 检查日志
tail -f logs/journal.jsonl | jq '.market_state' | sort | uniq -c

# 检查风控
cat logs/portfolio_state.json | jq '.daily_pnl_pct'
```

#### 4. 一致性审计失败

**症状**:
```
Consistency rate: 85% (低于 95% 目标)
```

**原因**: 配置变更或数据源不同步

**解决**:
```bash
# 运行一致性审计
PYTHONPATH=src python src/consistency_audit.py \
  --symbols BTCUSDT \
  --sample-rows 50

# 检查差异原因
cat logs/consistency_audit.json | jq '.mismatches[] | .delta_reason' | sort | uniq -c
```

### 日志分析

#### Journal 日志

```bash
# 查看最近 10 笔交易
tail -n 10 logs/journal.jsonl | jq '{symbol, grade, side, reason}'

# 按市场状态统计
cat logs/journal.jsonl | jq -r '.market_state.state' | sort | uniq -c

# 查看亏损交易
cat logs/journal.jsonl | jq 'select(.pnl_pct < 0)'
```

#### 监控日志

```bash
# 查看错误
grep ERROR logs/monitoring.log | tail -20

# 查看告警
grep WARN logs/monitoring.log | tail -20
```

### 性能优化

#### 内存占用高

```bash
# 检查 K 线缓存
du -sh logs/*.json

# 清理旧日志
find logs/ -name "*.json" -mtime +30 -delete
```

#### CPU 占用高

```python
# 减少数据获取频率
# config/strategy.yaml
data_provider:
  kline_cache_size: 500  # 默认 1000
  refresh_interval: 300  # 5 分钟刷新
```

---

## 附录

### A. 配置文件模板

#### .env.example
```bash
# 交易模式
TRADING_MODE=paper  # paper / live

# Binance API
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
BINANCE_TESTNET=true  # 测试网

# Telegram 通知
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# 日志级别
LOG_LEVEL=INFO  # DEBUG / INFO / WARNING / ERROR
```

### B. 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+C` | 停止当前运行 |
| `tail -f logs/journal.jsonl` | 实时查看交易日志 |
| `python src/main.py --help` | 查看帮助 |

### C. 参考资源

- [Binance API 文档](https://binance-docs.github.io/apidocs/)
- [Python 异步编程](https://docs.python.org/3/library/asyncio.html)
- [量化交易入门](https://www.quantstart.com/)

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2024-01-01 | 初始版本 |
| 1.1.0 | 2024-01-15 | 添加 Phase-2 功能 |
| 1.2.0 | 2024-02-01 | 优化风险引擎 |
| **当前** | **2024-04-12** | **胜率 72.73%, 盈利因子 1.41** |

---

**文档维护**: 系统开发团队  
**最后更新**: 2024-04-12  
**联系方式**: 通过 Telegram 通知系统
