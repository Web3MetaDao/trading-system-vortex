import logging
import os

import numpy as np
import pandas as pd
from stable_baselines3 import PPO

from vortex_env import VortexTradingEnv

logger = logging.getLogger(__name__)


def train_vortex_rl():
    # 1. Generate Synthetic Data for Demonstration (In production, use SQLite history)
    dates = pd.date_range(end=pd.Timestamp.now(), periods=1000, freq="1h")
    df = pd.DataFrame(
        {
            "close": 70000 + np.cumsum(np.random.randn(1000) * 100),
            "volume": np.random.rand(1000) * 1000,
            "rsi": np.random.rand(1000) * 100,
            "macd": np.random.randn(1000),
            "atr": np.random.rand(1000) * 500,
            "ema_fast": 70000 + np.cumsum(np.random.randn(1000) * 80),
            "ema_slow": 70000 + np.cumsum(np.random.randn(1000) * 50),
        },
        index=dates,  # 使用时间索引，方便回测时间对齐
    )

    # 2. Initialize Environment
    env = VortexTradingEnv(df)

    # 3. Initialize PPO Agent
    model = PPO("MlpPolicy", env, verbose=1, learning_rate=0.0003, n_steps=128)

    # 4. Train the Agent
    logger.info("--- Starting Vortex RL Agent Training (PPO) ---")
    model.learn(total_timesteps=5000)

    # 5. Save the Model
    model_path = "/home/ubuntu/vortex/models/ppo_vortex_v1"
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    model.save(model_path)
    logger.info(f"RL Agent trained and saved to {model_path}")

    # 6. Evaluate
    obs, _ = env.reset()
    for _ in range(10):
        action, _states = model.predict(obs, deterministic=True)
        obs, reward, done, _, _ = env.step(action)
        if done:
            break
        logger.info(f"Evaluation Step - Action: {action}, Reward: {reward:.4f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    train_vortex_rl()
