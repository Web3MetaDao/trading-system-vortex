# VORTEX Trading System v2.0.0

**Institution-Grade Server-Side Quantitative Cryptocurrency Trading System**

[![GitHub Release](https://img.shields.io/github/v/release/Web3MetaDao/trading-system-vortex)](https://github.com/Web3MetaDao/trading-system-vortex/releases)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Build Status](https://github.com/Web3MetaDao/trading-system-vortex/actions/workflows/ci.yml/badge.svg)](https://github.com/Web3MetaDao/trading-system-vortex/actions)

---

## 🌟 简介

**VORTEX Trading System** 是一款专为顶级加密货币量化机构设计的纯服务端高频交易引擎。经过 V2.0 的深度重构，系统剥离了所有冗余的桌面端组件，专注于极致的性能、严苛的风控和复杂的多模态数据融合。

系统集成了基于强化学习（PPO）的参数自进化机制、多模态宏观情绪过滤（Oracle）、以及跨平台套利引擎，能够在微秒级延迟下执行复杂的缠论（Chanlun）技术分析和冰山算法（Iceberg）订单拆分。

### ✨ 核心特性

- **⚡ 极致性能架构**
  - 信号引擎 P99 延迟低至 **1.020 ms**，并发吞吐量达 **1361 eval/s**。
  - 风控引擎 P99 延迟低至 **0.028 ms**，确保极端行情下的毫秒级熔断。
  - 纯异步 I/O 设计，基于 `aiohttp` 和 `websockets` 实现无阻塞数据流。

- **🧠 AI 自进化与多模态神谕**
  - **RL Agent**：内置基于 `stable-baselines3` 的 PPO 强化学习智能体，能够根据市场波动率动态调整 ATR 乘数和止损阈值。
  - **Multimodal Oracle**：接入外部宏观情绪数据（如恐慌贪婪指数、新闻情感），通过 5 分钟异步 TTL 缓存机制无缝融入主数据流，实现宏观与微观信号的交叉验证。

- **🛡️ 机构级风控与执行**
  - **动态追踪止损**：支持基于配置文件的动态追踪止损（Trailing Stop），彻底消除硬编码。
  - **冰山算法**：大额订单自动拆分为随机大小的子订单，结合 ATR 感知的滑点模型，有效降低市场冲击成本。
  - **跨平台套利**：支持 Binance、OKX、Bybit 之间的三角套利，内置 L2 订单簿深度滑点计算和资金划转成本建模。

---

## 🚀 快速开始

### 1. 环境准备

系统要求 Python 3.11 或更高版本。建议使用虚拟环境进行隔离部署。

```bash
# 克隆仓库
git clone https://github.com/Web3MetaDao/trading-system-vortex.git
cd trading-system-vortex

# 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装核心依赖
pip install -r requirements.txt
```

### 2. 配置系统

复制环境变量模板并填入您的 API 密钥：

```bash
cp .env.example .env
```

编辑 `.env` 文件，配置 Binance API、Telegram 机器人 Token 以及运行模式（`paper` 或 `live`）。

### 3. 启动交易引擎

系统提供了统一的启动入口，将自动拉起数据流、信号引擎、风控引擎和执行模块：

```bash
python src/startup.py
```

---

## 📊 性能基准测试 (v2.0.0)

在标准的 2 核 4G 云服务器环境下，VORTEX V2.0 展现出了卓越的性能指标：

| 核心模块 | P50 延迟 | P99 延迟 | 内存峰值 |
| :--- | :--- | :--- | :--- |
| **SignalEngine.evaluate()** | 0.289 ms | 1.020 ms | 63.3 KB |
| **RiskEngine.size_position()** | 0.003 ms | 0.028 ms | N/A |
| **ChanlunEngine.evaluate_divergence()** | 1.061 ms | 2.674 ms | 76.8 KB |

*注：测试套件包含 190 个严苛的边界条件用例，当前通过率为 100%。*

---

## 📚 文档体系

完整的技术细节和架构设计请参阅 `docs/` 目录下的专业文档：

- [技术架构白皮书](docs/TECHNICAL_DOCUMENTATION.md)
- [多模态神谕集成指南](docs/MULTIMODAL_INTEGRATION.md)
- [缠论引擎数学模型](docs/CHANLUN_INTEGRATION.md)
- [冰山算法执行逻辑](docs/ICEBERG_EXECUTION.md)
- [V2.0 机构级优化报告](docs/OPTIMIZATION_REPORT.md)

---

## 🛠️ 开发者指南

我们使用 `ruff` 进行严格的静态代码分析，使用 `pytest` 进行单元测试。提交代码前，请确保通过所有检查：

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行代码格式化与静态检查
ruff check . --fix
black .
isort .

# 运行完整测试套件
pytest tests/ -v
```

---

## 📄 许可证

本项目采用 [MIT License](LICENSE) 开源协议。

**免责声明**：加密货币交易具有极高的风险。本系统提供的任何信号或自动化执行逻辑均不构成投资建议。在实盘（Live）模式下运行本系统前，请务必在模拟盘（Paper）中进行充分的测试。
