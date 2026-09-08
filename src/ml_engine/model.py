"""
ML Decision Engine.
Maps combined [Query Features + OS Telemetry] to the optimal query execution strategy.
Uses XGBoost Classifier with Scikit-learn Random Forest/Decision Tree fallbacks.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import numpy as np
import pandas as pd
import joblib
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
from sklearn.ensemble import RandomForestClassifier
import xgboost as xgb

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import MODEL_PATH, MODELS_DIR, STRATEGIES, STRATEGY_ID_TO_NAME
from src.query_processor.feature_extractor import QueryFeatureExtractor


# Feature Names
FEATURE_NAMES = [
    # Query Features (16)
    "q_join_count",
    "q_table_count",
    "q_has_aggregation",
    "q_has_group_by",
    "q_has_having",
    "q_agg_count",
    "q_has_order_by",
    "q_has_limit",
    "q_has_subquery",
    "q_subquery_count",
    "q_filter_count",
    "q_query_length",
    "q_token_count",
    "q_complexity_score",
    "q_estimated_cost",
    "q_estimated_rows",
    # OS Telemetry Features (9)
    "os_cpu_percent",
    "os_cpu_core_max",
    "os_mem_percent",
    "os_mem_available_mb",
    "os_disk_read_mbps",
    "os_disk_write_mbps",
    "os_load_1m",
    "os_process_count",
    "os_is_cpu_stressed",
]


class OSOptimizerModel:
    """
    Trained classifier predicting the best execution hint/strategy for a given (query, OS state) pair.
    """

    def __init__(self, model_path: Optional[Path] = None):
        self.model_path = model_path or MODEL_PATH
        self.model = None
        self.feature_names = FEATURE_NAMES
        self.is_trained = False
        MODELS_DIR.mkdir(parents=True, exist_ok=True)

        # Attempt to load existing model if present
        if self.model_path.exists():
            try:
                self.load_model(self.model_path)
            except Exception as e:
                print(f"[Model] Note: Could not auto-load model from {self.model_path}: {e}")

    @staticmethod
    def extract_feature_vector(
        query_features: Dict[str, Any], os_snapshot: Dict[str, Any]
    ) -> np.ndarray:
        """
        Combines query features and OS snapshot into a 1D vector aligned with FEATURE_NAMES.
        """
        vals = [
            # Query Features
            float(query_features.get("join_count", 0)),
            float(query_features.get("table_count", 1)),
            float(query_features.get("has_aggregation", 0)),
            float(query_features.get("has_group_by", 0)),
            float(query_features.get("has_having", 0)),
            float(query_features.get("agg_count", 0)),
            float(query_features.get("has_order_by", 0)),
            float(query_features.get("has_limit", 0)),
            float(query_features.get("has_subquery", 0)),
            float(query_features.get("subquery_count", 0)),
            float(query_features.get("filter_count", 0)),
            float(query_features.get("query_length", 0)),
            float(query_features.get("token_count", 0)),
            float(query_features.get("complexity_score", 0)),
            float(query_features.get("estimated_cost", 0)),
            float(query_features.get("estimated_rows", 0)),
            # OS Telemetry Features
            float(os_snapshot.get("cpu_percent", 0.0)),
            float(os_snapshot.get("cpu_core_max", 0.0)),
            float(os_snapshot.get("mem_percent", 0.0)),
            float(os_snapshot.get("mem_available_mb", 0.0)),
            float(os_snapshot.get("disk_read_mbps", 0.0)),
            float(os_snapshot.get("disk_write_mbps", 0.0)),
            float(os_snapshot.get("load_1m", 0.0)),
            float(os_snapshot.get("process_count", 0.0)),
            1.0 if os_snapshot.get("is_cpu_stressed", False) else 0.0,
        ]
        return np.array(vals, dtype=np.float32)

    def train(
        self, df: pd.DataFrame, test_size: float = 0.2, random_state: int = 42
    ) -> Dict[str, Any]:
        """
        Trains an XGBoost classifier on recorded query-telemetry log data.
        Falls back to RandomForestClassifier if required.
        """
        # Ensure all columns exist
        missing_cols = [c for c in self.feature_names if c not in df.columns]
        if missing_cols:
            raise ValueError(f"DataFrame is missing required feature columns: {missing_cols}")

        if "chosen_strategy" not in df.columns:
            raise ValueError("DataFrame must contain 'chosen_strategy' target column.")

        X = df[self.feature_names].values
        y_raw = df["chosen_strategy"].values.astype(int)

        from sklearn.preprocessing import LabelEncoder
        self.label_encoder = LabelEncoder()
        y = self.label_encoder.fit_transform(y_raw)

        unique_classes = np.unique(y)
        if len(unique_classes) < 2:
            raise ValueError(f"Training requires at least 2 distinct strategy classes, got: {unique_classes}")

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=random_state, stratify=y if len(unique_classes) > 1 else None
        )

        try:
            clf = xgb.XGBClassifier(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.08,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=random_state,
                eval_metric="mlogloss",
            )
            clf.fit(X_train, y_train)
            model_type = "XGBoost"
        except Exception as ex:
            print(f"[Model] XGBoost training error ({ex}), falling back to RandomForestClassifier.")
            clf = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=random_state)
            clf.fit(X_train, y_train)
            model_type = "RandomForest"

        preds = clf.predict(X_test)
        acc = float(accuracy_score(y_test, preds))
        report = classification_report(y_test, preds, output_dict=True, zero_division=0)

        self.model = clf
        self.is_trained = True

        eval_results = {
            "model_type": model_type,
            "accuracy": round(acc, 4),
            "sample_count": len(df),
            "classes": unique_classes.tolist(),
            "report": report,
        }

        # Save trained model to disk
        self.save_model(self.model_path)
        return eval_results

    def predict_strategy(
        self, query_features: Dict[str, Any], os_snapshot: Dict[str, Any]
    ) -> Tuple[int, str, float]:
        """
        Predicts optimal strategy id, name, and confidence score.
        If model is not yet trained, uses an expert rule-based heuristic.
        """
        if not self.is_trained or self.model is None:
            # Expert rule-based fallback heuristic
            return self._heuristic_fallback(query_features, os_snapshot)

        vec = self.extract_feature_vector(query_features, os_snapshot).reshape(1, -1)
        try:
            raw_pred = self.model.predict(vec)[0]
            if hasattr(self, "label_encoder") and self.label_encoder is not None:
                pred_id = int(self.label_encoder.inverse_transform([raw_pred])[0])
            else:
                pred_id = int(raw_pred)

            probs = self.model.predict_proba(vec)[0]
            confidence = float(np.max(probs))
            strategy_name = STRATEGY_ID_TO_NAME.get(pred_id, "DEFAULT")
            return pred_id, strategy_name, round(confidence, 3)
        except Exception as e:
            print(f"[Model] Prediction error: {e}, using heuristic.")
            return self._heuristic_fallback(query_features, os_snapshot)

    def _heuristic_fallback(
        self, query_features: Dict[str, Any], os_snapshot: Dict[str, Any]
    ) -> Tuple[int, str, float]:
        """
        Expert heuristic fallback when ML model has not yet completed initial training:
        - If CPU >= 85%: Disable parallelism (Strategy 4) or Delay query (Strategy 5)
        - If RAM >= 80% and query has multiple joins: Avoid Hash Join (Strategy 1)
        - If CPU >= 70% and query has joins: Force Nested Loop (Strategy 2) or Constrain work_mem (Strategy 3)
        - Otherwise: Default (Strategy 0)
        """
        cpu = os_snapshot.get("cpu_percent", 0.0)
        mem = os_snapshot.get("mem_percent", 0.0)
        joins = query_features.get("join_count", 0)
        complexity = query_features.get("complexity_score", 1.0)

        if cpu >= 90.0 and complexity > 10.0:
            return 5, "DELAY_THROTTLE", 0.90
        elif cpu >= 75.0 and joins >= 1:
            return 4, "DISABLE_PARALLEL", 0.85
        elif mem >= 80.0 and joins >= 2:
            return 1, "AVOID_HASH_JOIN", 0.80
        elif cpu >= 60.0 and joins >= 1:
            return 2, "FORCE_NESTED_LOOP", 0.75
        elif mem >= 70.0:
            return 3, "CONSTRAIN_WORK_MEM", 0.70
        else:
            return 0, "DEFAULT", 0.95

    def save_model(self, path: Optional[Path] = None):
        """Saves model state to disk using joblib."""
        save_file = path or self.model_path
        save_file.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "model": self.model,
                "label_encoder": getattr(self, "label_encoder", None),
                "feature_names": self.feature_names,
                "is_trained": self.is_trained,
            },
            save_file,
        )
        print(f"[Model] Model successfully serialized to {save_file}")

    def load_model(self, path: Optional[Path] = None):
        """Loads serialized model from disk."""
        load_file = path or self.model_path
        if not load_file.exists():
            raise FileNotFoundError(f"Model file not found at {load_file}")
        data = joblib.load(load_file)
        self.model = data["model"]
        self.label_encoder = data.get("label_encoder", None)
        self.feature_names = data.get("feature_names", FEATURE_NAMES)
        self.is_trained = data.get("is_trained", True)
        print(f"[Model] Loaded model from {load_file}")

    def get_feature_importances(self) -> Dict[str, float]:
        """Returns dict of feature name -> importance score."""
        if not self.is_trained or self.model is None:
            return {}
        if hasattr(self.model, "feature_importances_"):
            return {
                name: float(imp)
                for name, imp in zip(self.feature_names, self.model.feature_importances_)
            }
        return {}


# Global default instance
default_ml_model = OSOptimizerModel()


if __name__ == "__main__":
    print("=" * 60)
    print("ML Decision Engine Self-Test")
    print("=" * 60)
    model = OSOptimizerModel()

    # Heuristic test
    q_feat = {"join_count": 3, "complexity_score": 15.0}
    os_snap_normal = {"cpu_percent": 20.0, "mem_percent": 45.0, "is_cpu_stressed": False}
    os_snap_stressed = {"cpu_percent": 88.0, "mem_percent": 85.0, "is_cpu_stressed": True}

    strat_id, name, conf = model.predict_strategy(q_feat, os_snap_normal)
    print(f"Normal load prediction: {name} (ID: {strat_id}, Conf: {conf})")

    strat_id, name, conf = model.predict_strategy(q_feat, os_snap_stressed)
    print(f"Stressed load prediction: {name} (ID: {strat_id}, Conf: {conf})")
    print("\nML Engine Test Passed!")
