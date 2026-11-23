"""
The learned plan-quality model.

For a query, a hint set and the current OS state, the model predicts
    y = log(latency with this hint set) - log(latency with the default plan)
i.e. how many times faster (y < 0) or slower (y > 0) the hinted plan will run. Predicting the
ratio, not the absolute time, removes the 100x size differences between queries and lets the
model focus on plan shape and load.

The model is an ensemble of LightGBM gradient-boosted tree regressors trained on different
bootstrap samples; the spread of their predictions is used as an uncertainty estimate, so the
optimizer can stay with the default plan when the model is unsure.
"""

import json
import math
import pickle
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np

from qs.config import ENSEMBLE_SIZE, MODELS_DIR, RESULTS_DIR
from qs.features import feature_names, feature_vector
from qs.hints import DEFAULT_ID

MODEL_FILE = MODELS_DIR / "plan_model.pkl"
LATENCY_FILE = MODELS_DIR / "latency_model.pkl"


def build_dataset(runs: List[dict], plans: Dict[Tuple[str, int], dict]):
    """
    Turns raw runs into (X, y, meta). One row per (level, query, hint) whose default run exists.
    If a (level, query, hint) was measured more than once, the median is used.
    """
    groups = defaultdict(list)
    loads = {}
    for r in runs:
        groups[(r["level"], r["qid"], r["hint"])].append(r)
        if r["hint"] == DEFAULT_ID:
            loads[(r["level"], r["qid"])] = r["load"]
    names = feature_names()
    X, y, meta = [], [], []
    for (level, qid, hint), rs in groups.items():
        base = groups.get((level, qid, DEFAULT_ID))
        if not base or (qid, hint) not in plans:
            continue
        ms = float(np.median([r["ms"] for r in rs]))
        d_ms = float(np.median([r["ms"] for r in base]))
        f = feature_vector(plans[(qid, hint)], plans[(qid, DEFAULT_ID)], hint, loads[(level, qid)])
        X.append([f[n] for n in names])
        y.append(math.log(ms) - math.log(d_ms))
        meta.append({"level": level, "qid": qid, "template": rs[0]["template"], "hint": hint, "ms": ms,
                     "default_ms": d_ms, "timeout": any(r["timeout"] for r in rs)})
    return np.array(X, dtype=float), np.array(y, dtype=float), meta


class PlanModel:
    def __init__(self, n_models: int = ENSEMBLE_SIZE, seed: int = 0):
        self.n_models = n_models
        self.seed = seed
        self.models = []
        self.names = feature_names()

    def fit(self, X: np.ndarray, y: np.ndarray) -> "PlanModel":
        import lightgbm as lgb
        rng = np.random.default_rng(self.seed)
        self.models = []
        for i in range(self.n_models):
            idx = rng.integers(0, len(y), len(y))   # bootstrap sample -> diversity for uncertainty
            m = lgb.LGBMRegressor(n_estimators=400, learning_rate=0.05, num_leaves=31, min_child_samples=8,
                                  subsample=0.9, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
                                  random_state=self.seed + i, verbose=-1)
            m.fit(X[idx], y[idx])
            self.models.append(m)
        return self

    def predict(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Returns (mean, std) of the predicted log-ratio for each row."""
        P = np.stack([m.booster_.predict(X, num_threads=1) for m in self.models])
        return P.mean(axis=0), P.std(axis=0)

    def feature_importance(self, top: int = 12) -> List[Tuple[str, float]]:
        imp = np.mean([m.booster_.feature_importance("gain") for m in self.models], axis=0)
        order = np.argsort(imp)[::-1][:top]
        total = imp.sum() or 1.0
        return [(self.names[i], float(imp[i] / total)) for i in order]

    def save(self, path=MODEL_FILE):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"models": self.models, "names": self.names, "n_models": self.n_models}, f)

    @classmethod
    def load(cls, path=MODEL_FILE) -> Optional["PlanModel"]:
        if not path.exists():
            return None
        with open(path, "rb") as f:
            d = pickle.load(f)
        if d["names"] != feature_names():
            return None   # trained on an older feature schema
        m = cls(d["n_models"])
        m.models = d["models"]
        return m


class LatencyModel:
    """
    Predicts the absolute latency (ms) of a plan under the current OS load: the query's "burst
    time" in OS-scheduling terms. Used by the query scheduler (shortest-job-first, HRRN) and to skip
    the plan search for queries too short to benefit from it. Same features as PlanModel;
    target = log(ms). Timed-out runs (latency only a lower bound) are excluded.
    """

    def __init__(self):
        self.model = None
        self.names = feature_names()

    def fit(self, X: np.ndarray, log_ms: np.ndarray) -> "LatencyModel":
        import lightgbm as lgb
        self.model = lgb.LGBMRegressor(n_estimators=600, learning_rate=0.05, num_leaves=31, min_child_samples=8,
                                       subsample=0.9, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
                                       random_state=0, verbose=-1)
        self.model.fit(X, log_ms)
        return self

    def predict_ms(self, X: np.ndarray) -> np.ndarray:
        return np.exp(self.model.booster_.predict(X, num_threads=1))

    @staticmethod
    def training_rows(X: np.ndarray, meta: List[dict]):
        rows = [i for i, m in enumerate(meta) if not m["timeout"]]
        return rows, np.log([meta[i]["ms"] for i in rows])

    def save(self, path=LATENCY_FILE):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"model": self.model, "names": self.names}, f)

    @classmethod
    def load(cls, path=LATENCY_FILE) -> Optional["LatencyModel"]:
        if not path.exists():
            return None
        with open(path, "rb") as f:
            d = pickle.load(f)
        if d["names"] != feature_names():
            return None
        m = cls()
        m.model = d["model"]
        return m


def read_collected():
    runs = [json.loads(l) for l in (RESULTS_DIR / "runs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    live = RESULTS_DIR / "live.jsonl"
    if live.exists():
        runs += [json.loads(l) for l in live.read_text(encoding="utf-8").splitlines() if l.strip()]
    plans = {}
    for path in (RESULTS_DIR / "plans.jsonl", RESULTS_DIR / "live_plans.jsonl"):
        if path.exists():
            for l in path.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    p = json.loads(l)
                    plans[(p["qid"], p["hint"])] = p["plan"]
    return runs, plans


def choose(mean: np.ndarray, std: np.ndarray, hint_ids: List[int], min_gain: float, k: float) -> Tuple[int, int]:
    """
    Picks the hint set with the lowest conservative score (mean + k*std). It is used only if even
    that conservative estimate promises at least `min_gain` speed-up over the default plan;
    otherwise the default plan is kept. Returns (chosen hint id, index of best candidate).
    """
    score = mean + k * std
    best = int(np.argmin(score))
    if hint_ids[best] != DEFAULT_ID and score[best] <= -math.log(1 + min_gain):
        return hint_ids[best], best
    return DEFAULT_ID, best
