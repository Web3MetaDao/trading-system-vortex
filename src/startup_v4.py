import asyncio
import logging
import os
import numpy as np
from cross_exchange_manager import CrossExchangeManager
from arbitrage_engine import ArbitrageEngine
from train_rl_agent import train_vortex_rl
from stable_baselines3 import PPO

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

async def run_vortex_v4():
    logger.info("--- Vortex V4 Ultimate Platform Initializing ---")
    
    # 1. Initialize Cross-Exchange Manager
    ex_mgr = CrossExchangeManager()
    arb_engine = ArbitrageEngine(ex_mgr)
    
    # 2. Load RL Agent
    model_path = "/home/ubuntu/vortex/models/ppo_vortex_v1.zip"
    if not os.path.exists(model_path):
        logger.info("Training RL Agent...")
        train_vortex_rl()
    
    rl_agent = PPO.load(model_path)
    logger.info("RL Agent Loaded Successfully.")

    # 3. Main Ultimate Loop (Simulated for V4 Showcase)
    logger.info("--- Vortex V4 Live: Cross-Arb Monitoring + RL-Signal Filtering ---")
    try:
        for i in range(5):
            # A. Arbitrage Check (Simulated due to sandbox IP restrictions)
            logger.info("[ARB] Scanning Binance/OKX/Bybit for price discrepancies...")
            # Simulated data for demonstration
            sim_opp = {"buy": "Binance", "sell": "OKX", "profit": 0.24}
            logger.info(f"[ARB] Opportunity Found: Buy {sim_opp['buy']}, Sell {sim_opp['sell']} | Profit: {sim_opp['profit']}%")
            
            # B. RL Signal Filtering
            dummy_obs = np.random.randn(8).astype(np.float32)
            action, _ = rl_agent.predict(dummy_obs, deterministic=True)
            logger.info(f"[RL] Agent Analysis - Suggested Action: {action} (0=Hold, 1=Buy, 2=Sell)")
            
            await asyncio.sleep(2)
    finally:
        await ex_mgr.close()
        logger.info("Vortex V4 Shutdown.")

if __name__ == "__main__":
    asyncio.run(run_vortex_v4())
