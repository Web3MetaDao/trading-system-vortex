"""
Vortex ML Signal Filter — XGBoost 信号置信度分类器（可选组件）

依赖：xgboost, joblib, scikit-learn（可选，不在核心启动链中）
若未安装 xgboost，模块可正常导入，但会回退到启发式规则。
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

try:
    import joblib
    from sklearn.model_selection import train_test_split

    _SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover
    joblib = None  # type: ignore[assignment]
    train_test_split = None  # type: ignore[assignment]
    _SKLEARN_AVAILABLE = False

try:
    import xgboost as xgb

    _XGBOOST_AVAILABLE = True
except ImportError:  # pragma: no cover
    xgb = None  # type: ignore[assignment]
    _XGBOOST_AVAILABLE = False

logger = logging.getLogger(__name__)


class MLSignalFilter:
    """
    Vortex ML Signal Filter

    Uses XGBoost to classify signals into high-confidence (A+) or low-confidence.
    Falls back to a neutral heuristic (0.5) if xgboost is not installed or model
    file is not found.
    """

    def __init__(
        self, model_path: str = "/home/ubuntu/vortex/models/signal_filter_v1.xgb"
    ):
        self.model_path = model_path
        self.model = None
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        self._load_model()

    def _load_model(self) -> None:
        if not _SKLEARN_AVAILABLE or not _XGBOOST_AVAILABLE:
            logger.warning(
                "xgboost/scikit-learn not installed — MLSignalFilter will use neutral fallback (0.5). "
                "Install with: pip install xgboost scikit-learn"
            )
            return
        if os.path.exists(self.model_path):
            self.model = joblib.load(self.model_path)
            logger.info("Loaded ML Signal Filter model from %s", self.model_path)
        else:
            logger.warning(
                "ML Signal Filter model not found at %s. Using fallback heuristic.",
                self.model_path,
            )

    def prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Extract ML features from kline history"""
        features = pd.DataFrame()
        features["rsi"] = df["rsi"]
        features["macd"] = df["macd"]
        features["atr_pct"] = (df["atr"] / df["close"]) * 100
        features["ema_dist"] = (df["close"] - df["ema_slow"]) / df["ema_slow"]
        features["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
        return features.dropna()

    def train(self, df: pd.DataFrame, labels: pd.Series) -> None:
        """Train XGBoost model on historical outcomes"""
        if not _SKLEARN_AVAILABLE or not _XGBOOST_AVAILABLE:
            raise RuntimeError(
                "xgboost and scikit-learn are required for training. "
                "Install with: pip install xgboost scikit-learn"
            )

        X = self.prepare_features(df)
        y = labels.iloc[X.index]

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        self.model = xgb.XGBClassifier(
            n_estimators=100, max_depth=5, learning_rate=0.05
        )
        self.model.fit(X_train, y_train)

        joblib.dump(self.model, self.model_path)
        logger.info("Trained and saved ML Signal Filter model to %s", self.model_path)

    def predict_confidence(self, features: dict[str, Any]) -> float:
        """Predict probability of a winning signal"""
        if self.model is None:
            return 0.5  # Neutral fallback when model unavailable

        feat_df = pd.DataFrame([features])
        prob = self.model.predict_proba(feat_df)[0][1]  # Prob of class 1 (win)
        return float(prob)


if __name__ == "__main__":
    ml = MLSignalFilter()
    print("ML Signal Filter initialized.")
