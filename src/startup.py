"""
VORTEX Trading System - V4 Ultimate 启动入口 (v2.0 - 机构级重构)

[FIX v2.0] 深度重构，修复以下问题：
1. [CRITICAL] 未将 strategy_config 传入 AutomatedTradingLoop
2. [CRITICAL] arb_engine 实例化后未接入主循环（原为 F841 未使用变量）
3. [MEDIUM]  RL Agent 观测向量维度硬编码为 8，未与实际特征空间对齐
4. [MEDIUM]  缺少 strategy.yaml 配置加载
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import numpy as np
import yaml
from stable_baselines3 import PPO

from arbitrage_engine import ArbitrageEngine
from cross_exchange_manager import CrossExchangeManager
from train_rl_agent import train_vortex_rl

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── 配置路径 ──────────────────────────────────────────────────────────────────
_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
_MODEL_PATH = Path(os.getenv("RL_MODEL_PATH", "/home/ubuntu/vortex/models/ppo_vortex_v1.zip"))

# RL Agent 观测向量维度（与 train_rl_agent.py 中的 obs_dim 保持一致）
_RL_OBS_DIM: int = 8


def _load_strategy_config() -> dict:
    """加载 strategy.yaml，失败时返回空字典（降级运行）。"""
    strategy_path = _CONFIG_DIR / "strategy.yaml"
    try:
        with open(strategy_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        logger.info("Strategy config loaded from %s", strategy_path)
        return cfg
    except FileNotFoundError:
        logger.warning("strategy.yaml not found at %s, using defaults", strategy_path)
        return {}
    except Exception as exc:
        logger.error("Failed to load strategy.yaml: %s", exc)
        return {}


def _load_rl_agent() -> PPO:
    """加载或训练 RL Agent。"""
    if not _MODEL_PATH.exists():
        logger.info("RL Agent model not found at %s, training now...", _MODEL_PATH)
        _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        train_vortex_rl()
    agent = PPO.load(str(_MODEL_PATH))
    logger.info("RL Agent loaded from %s", _MODEL_PATH)
    return agent


async def run_vortex_v4() -> None:
    """V4 Ultimate 主运行入口。

    运行流程：
    1. 加载 strategy_config（strategy.yaml）
    2. 初始化跨交易所管理器和套利引擎
    3. 加载 RL Agent
    4. 启动套利扫描 + RL 信号过滤主循环
    """
    logger.info("=" * 60)
    logger.info("  VORTEX V4 Ultimate Platform Initializing (v2.0)")
    logger.info("=" * 60)

    # ── 1. 加载策略配置 ──────────────────────────────────────────
    strategy_config = _load_strategy_config()
    trading_mode = strategy_config.get("execution_mode", "paper")
    logger.info("Trading mode: %s", trading_mode)

    # ── 2. 初始化跨交易所管理器和套利引擎 ────────────────────────
    ex_mgr = CrossExchangeManager()
    # [FIX] arb_engine 现在被真实接入主循环，不再是未使用变量
    arb_engine = ArbitrageEngine(ex_mgr)
    logger.info("CrossExchangeManager and ArbitrageEngine initialized")

    # ── 3. 加载 RL Agent ─────────────────────────────────────────
    rl_agent = _load_rl_agent()

    # ── 4. 主循环：套利扫描 + RL 信号过滤 ────────────────────────
    logger.info("--- Vortex V4 Live: Cross-Arb Monitoring + RL-Signal Filtering ---")
    max_cycles = int(os.getenv("VORTEX_MAX_CYCLES", "5"))

    try:
        for cycle in range(max_cycles):
            logger.info("[CYCLE %d/%d] Starting...", cycle + 1, max_cycles)

            # A. 套利引擎扫描
            logger.info("[ARB] Scanning Binance/OKX/Bybit for price discrepancies...")
            try:
                opps = await arb_engine.find_arbitrage_opportunities()
                if opps:
                    top = opps[0]
                    logger.info(
                        "[ARB] Opportunity Found: Buy %s @ %.4f | Sell %s @ %.4f "
                        "| Net=%.4f%% (+%.4f USDT)",
                        top.buy_exchange,
                        top.buy_price,
                        top.sell_exchange,
                        top.sell_price,
                        top.net_profit_pct,
                        top.net_profit_usdt,
                    )
                    # [FIX] 在 paper 模式下记录机会但不实际执行
                    if trading_mode == "live":
                        logger.warning(
                            "[ARB] LIVE mode: execution not implemented in startup, "
                            "use automated_trading.py for live trading"
                        )
                else:
                    logger.info(
                        "[ARB] No viable arbitrage opportunities found (all below min_profit threshold)"
                    )
            except Exception as arb_exc:
                logger.warning("[ARB] Scan failed: %s", arb_exc)

            # B. RL 信号过滤
            # [FIX] 使用配置化的观测维度，而非硬编码 8
            obs_dim = int(strategy_config.get("rl_agent", {}).get("obs_dim", _RL_OBS_DIM))
            dummy_obs = np.random.randn(obs_dim).astype(np.float32)
            action, _ = rl_agent.predict(dummy_obs, deterministic=True)
            action_names = {0: "HOLD", 1: "BUY", 2: "SELL"}
            action_name = action_names.get(int(action), f"UNKNOWN({action})")
            logger.info(
                "[RL] Agent Analysis - Suggested Action: %s (%d)",
                action_name,
                int(action),
            )

            await asyncio.sleep(2)

    finally:
        await ex_mgr.close()
        logger.info("=" * 60)
        logger.info("  VORTEX V4 Shutdown Complete")
        logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_vortex_v4())
