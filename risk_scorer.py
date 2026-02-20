"""
ShieldBot — ML Risk Scorer
LightGBM binary classifier with SHAP explainability.
Risk score [0,1]: probability of malicious intent.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np

logger = logging.getLogger(__name__)

try:
    import lightgbm as lgb
    import shap
    LIGHTGBM_AVAILABLE = True
except ImportError:
    LIGHTGBM_AVAILABLE = False
    logger.warning("LightGBM/SHAP not available; ML scorer disabled")


# ──────────────────────────────────────────────────────────────
# Feature Vector
# ──────────────────────────────────────────────────────────────

@dataclass
class RiskFeatures:
    """Feature vector for the ML risk model."""
    account_age_days: float = 365.0           # normalized, missing → 365
    message_freq_60s: float = 0.0             # msg count in last 60s
    distinct_links_300s: float = 0.0          # unique external links in 300s
    has_profile_photo: int = 1                # 0 or 1
    username_entropy: float = 3.0             # Shannon entropy of username chars
    forward_chain_length: int = 0             # number of forwards
    previous_report_count_30d: int = 0        # reports in last 30 days
    is_premium: int = 0                       # Telegram Premium subscriber
    is_verified: int = 0                      # blue checkmark
    group_member_duration_hours: float = 0.0  # how long in group
    join_cluster_size: float = 0.0            # # joiners in same wave
    new_account_ratio: float = 0.0            # ratio of new accounts in wave
    similarity_score: float = 0.0            # Levenshtein similarity to known attackers

    def to_array(self) -> np.ndarray:
        return np.array([
            self.account_age_days,
            self.message_freq_60s,
            self.distinct_links_300s,
            self.has_profile_photo,
            self.username_entropy,
            self.forward_chain_length,
            self.previous_report_count_30d,
            self.is_premium,
            self.is_verified,
            self.group_member_duration_hours,
            self.join_cluster_size,
            self.new_account_ratio,
            self.similarity_score,
        ], dtype=np.float32).reshape(1, -1)

    @classmethod
    def feature_names(cls) -> List[str]:
        return [f.name for f in cls.__dataclass_fields__.values()]  # type: ignore


def compute_username_entropy(username: Optional[str]) -> float:
    """Shannon entropy of username character distribution."""
    if not username:
        return 0.0
    from collections import Counter
    import math
    counts = Counter(username.lower())
    total = len(username)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


# ──────────────────────────────────────────────────────────────
# Scorer
# ──────────────────────────────────────────────────────────────

class MLRiskScorer:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self._model: Optional["lgb.Booster"] = None
        self._explainer: Optional["shap.TreeExplainer"] = None
        self._background: Optional[np.ndarray] = None

    def load(self) -> bool:
        if not LIGHTGBM_AVAILABLE:
            return False
        if not os.path.exists(self.model_path):
            logger.warning(f"No ML model at {self.model_path}; using rule-only mode")
            return False
        try:
            self._model = lgb.Booster(model_file=self.model_path)
            self._explainer = shap.TreeExplainer(self._model)
            logger.info(f"ML risk model loaded from {self.model_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load ML model: {e}")
            return False

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def score(
        self,
        features: RiskFeatures,
    ) -> Tuple[float, Dict[str, float]]:
        """
        Returns (risk_score, shap_explanation_dict).
        Falls back to heuristic score if model not loaded.
        """
        if not self.is_loaded:
            return self._heuristic_score(features), {}

        arr = features.to_array()
        pred = self._model.predict(arr)[0]  # type: ignore
        risk_score = float(np.clip(pred, 0.0, 1.0))

        # SHAP explainability
        shap_vals = self._explainer.shap_values(arr)  # type: ignore
        if isinstance(shap_vals, list):
            shap_vals = shap_vals[1]  # binary class → positive class
        shap_dict = {
            name: float(val)
            for name, val in zip(RiskFeatures.feature_names(), shap_vals[0])
        }

        return risk_score, shap_dict

    @staticmethod
    def _heuristic_score(features: RiskFeatures) -> float:
        """Lightweight heuristic when model is unavailable."""
        score = 0.0
        if features.account_age_days < 7:
            score += 0.3
        if features.message_freq_60s > 20:
            score += 0.2
        if features.distinct_links_300s > 3:
            score += 0.2
        if not features.has_profile_photo:
            score += 0.1
        if features.previous_report_count_30d > 3:
            score += 0.2
        if features.username_entropy < 1.5:
            score += 0.1
        return float(min(score, 1.0))


# ──────────────────────────────────────────────────────────────
# Training Pipeline
# ──────────────────────────────────────────────────────────────

def train_model(
    X: np.ndarray,
    y: np.ndarray,
    output_path: str,
    n_estimators: int = 500,
    learning_rate: float = 0.05,
) -> None:
    """Train LightGBM classifier from labeled historical data."""
    if not LIGHTGBM_AVAILABLE:
        raise RuntimeError("LightGBM not installed")

    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import roc_auc_score, precision_recall_curve
    import lightgbm as lgb

    params = {
        "objective": "binary",
        "metric": "auc",
        "num_leaves": 63,
        "learning_rate": learning_rate,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "min_child_samples": 20,
        "verbose": -1,
        "n_estimators": n_estimators,
    }

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    aucs = []
    feature_names = RiskFeatures.feature_names()

    for fold, (train_idx, val_idx) in enumerate(skf.split(X, y)):
        X_train, X_val = X[train_idx], X[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]

        dtrain = lgb.Dataset(X_train, label=y_train, feature_name=feature_names)
        dval = lgb.Dataset(X_val, label=y_val, feature_name=feature_names)

        model = lgb.train(
            params,
            dtrain,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(50), lgb.log_evaluation(100)],
        )

        preds = model.predict(X_val)
        auc = roc_auc_score(y_val, preds)
        aucs.append(auc)
        logger.info(f"Fold {fold+1} AUC: {auc:.4f}")

    logger.info(f"Mean CV AUC: {np.mean(aucs):.4f} ± {np.std(aucs):.4f}")

    # Final model on all data
    dtrain_full = lgb.Dataset(X, label=y, feature_name=feature_names)
    final_model = lgb.train(params, dtrain_full)
    final_model.save_model(output_path)
    logger.info(f"Model saved to {output_path}")


# ──────────────────────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────────────────────

_scorer_instance: Optional[MLRiskScorer] = None


def get_scorer(model_path: Optional[str] = None) -> MLRiskScorer:
    global _scorer_instance
    if _scorer_instance is None:
        from app.core.config import get_settings
        path = model_path or get_settings().model_path
        _scorer_instance = MLRiskScorer(path)
        _scorer_instance.load()
    return _scorer_instance

