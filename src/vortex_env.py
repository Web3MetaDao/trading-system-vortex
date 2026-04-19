import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces


class VortexTradingEnv(gym.Env):
    """
    Vortex Custom Trading Environment for RL
    State: OHLCV + Indicators (RSI, MACD, EMA, ATR)
    Action: 0=Hold, 1=Buy, 2=Sell
    Reward: PnL + Sharpe Ratio improvement
    """

    def __init__(self, df: pd.DataFrame, initial_balance: float = 1000.0):
        super().__init__()
        self.df = df.reset_index(drop=True)
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.current_step = 0
        self.position = 0  # 0=None, 1=Long, -1=Short
        self.entry_price = 0.0

        # Observation Space: OHLCV + Indicators (8 features)
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(8,), dtype=np.float32)
        # Action Space: 0=Hold, 1=Buy, 2=Sell
        self.action_space = spaces.Discrete(3)

    def _get_obs(self):
        row = self.df.iloc[self.current_step]
        # Normalized features for RL stability
        obs = np.array(
            [
                row["close"],
                row["volume"],
                row["rsi"],
                row["macd"],
                row["atr"],
                row["ema_fast"],
                row["ema_slow"],
                self.position,
            ],
            dtype=np.float32,
        )
        return obs

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.balance = self.initial_balance
        self.current_step = 30  # Start after indicator warmup
        self.position = 0
        self.entry_price = 0.0
        return self._get_obs(), {}

    def step(self, action):
        row = self.df.iloc[self.current_step]
        current_price = row["close"]
        reward = 0.0
        done = False

        # Execute Action
        if action == 1 and self.position == 0:  # Buy
            self.position = 1
            self.entry_price = current_price
        elif action == 2 and self.position == 1:  # Sell (Close Long)
            profit = (current_price - self.entry_price) / self.entry_price
            reward = profit * 100  # PnL reward
            self.balance *= 1 + profit
            self.position = 0
            self.entry_price = 0.0
        elif action == 0 and self.position == 1:  # Hold Long
            # Small penalty for holding to encourage timely exits
            reward = -0.01

        self.current_step += 1
        if self.current_step >= len(self.df) - 1:
            done = True

        return self._get_obs(), reward, done, False, {}


if __name__ == "__main__":
    # Test Environment
    df = pd.DataFrame(
        np.random.randn(100, 7),
        columns=["close", "volume", "rsi", "macd", "atr", "ema_fast", "ema_slow"],
    )
    env = VortexTradingEnv(df)
    obs, _ = env.reset()
    print(f"Initial Obs: {obs}")
    obs, reward, done, _, _ = env.step(1)
    print(f"Step 1 (Buy) - Obs: {obs}, Reward: {reward}")
