import logging
import os
from typing import Any

import joblib
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split

logger = logging.getLogger(__name__)


class MLSignalFilter:
    """
    Vortex ML Signal Filter
    Uses XGBoost to classify signals into high-confidence (A+) or low-confidence.
    """

    def __init__(self, model_path: str = "/home/ubuntu/vortex/models/signal_filter_v1.xgb"):
        self.model_path = model_path
        self.model = None
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        self._load_model()

    def _load_model(self):
        if os.path.exists(self.model_path):
            self.model = joblib.load(self.model_path)
            logger.info(f"Loaded ML Signal Filter model from {self.model_path}")
        else:
            logger.warning("ML Signal Filter model not found. Using fallback heuristic.")

    def prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Extract ML features from kline history"""
        features = pd.DataFrame()
        features["rsi"] = df["rsi"]
        features["macd"] = df["macd"]
        features["atr_pct"] = (df["atr"] / df["close"]) * 100
        features["ema_dist"] = (df["close"] - df["ema_slow"]) / df["ema_slow"]
        features["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
        return features.dropna()

    def train(self, df: pd.DataFrame, labels: pd.Series):
        """Train XGBoost model on historical outcomes"""
        X = self.prepare_features(df)
        y = labels.iloc[X.index]

        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

        self.model = xgb.XGBClassifier(n_estimators=100, max_depth=5, learning_rate=0.05)
        self.model.fit(X_train, y_train)

        joblib.dump(self.model, self.model_path)
        logger.info(f"Trained and saved ML Signal Filter model to {self.model_path}")

    def predict_confidence(self, features: dict[str, Any]) -> float:
        """Predict probability of a winning signal"""
        if self.model is None:
            return 0.5  # Neutral fallback

        feat_df = pd.DataFrame([features])
        prob = self.model.predict_proba(feat_df)[0][1]  # Prob of class 1 (win)
        return float(prob)


if __name__ == "__main__":
    ml = MLSignalFilter()
    print("ML Signal Filter initialized.")
