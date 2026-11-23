"""
QuerySync runtime optimizer.

For each incoming query:
  1. read the OS load (telemetry, background thread)
  2. EXPLAIN the default plan; the latency model predicts its run time ("burst time").
     Queries predicted shorter than SHORT_QUERY_MS keep the default plan: searching would cost
     more than it could save.
  3. otherwise EXPLAIN the query under the other 12 hint sets (in parallel, plans only)
  4. the plan model predicts, for each plan, how much faster/slower than the default plan it runs;
     the fastest plan is used only if the model confidently predicts a real gain
  5. OS priority (os_boost): under high external CPU load, PostgreSQL's processes are raised to
     HIGH priority so the OS scheduler runs the query ahead of the competing programs
  6. the query runs once with the chosen settings (SET LOCAL, one round trip)
  7. the measured latency is logged, so the model can be retrained on live data (python main.py train)

Modes: "ai" (the learned model), "default" (plain PostgreSQL), "rule" (serial plan under high load),
or an explicit hint-set id. os_boost: "off" | "auto" (boost under high load) | "on".
"""

import hashlib
import json
import time
from typing import Any, Dict, Optional, Union

import numpy as np

from qs.config import MIN_PREDICTED_GAIN, RESULTS_DIR, SHORT_QUERY_MS
from qs.db import Database
from qs.features import feature_names, feature_vector, memory_need_mb
from qs.hints import BY_ID, DEFAULT_ID, HINT_SETS
from qs.model import LatencyModel, PlanModel, choose
from qs.telemetry import Telemetry, shared_telemetry


def query_id(sql: str) -> str:
    return "live-" + hashlib.sha1(" ".join(sql.split()).encode()).hexdigest()[:12]


def boost_wanted(os_boost: str, load: Dict[str, Any]) -> bool:
    return os_boost == "on" or (os_boost == "auto" and load["level"] == "high")


def set_boost(on: bool):
    """Turns the OS priority boost on/off (creates the booster only when first needed)."""
    from qs import osctl
    if on or osctl._shared is not None:
        osctl.shared_booster().set(on)


class Optimizer:
    def __init__(self, db: Optional[Database] = None, telemetry: Optional[Telemetry] = None,
                 model: Optional[PlanModel] = None, k: float = 0.5, min_gain: float = MIN_PREDICTED_GAIN,
                 log_runs: bool = True, short_query_ms: float = SHORT_QUERY_MS):
        self.db = db or Database()
        self.telemetry = telemetry or shared_telemetry()
        self.model = model if model is not None else PlanModel.load()
        self.latency = LatencyModel.load()
        self.k, self.min_gain, self.log_runs, self.short_query_ms = k, min_gain, log_runs, short_query_ms
        self.names = feature_names()
        self.telemetry.start()
        from qs.osctl import reset_postgres_priority
        reset_postgres_priority()   # a previous run may have been killed while boosting
        self.db.explain_many("SELECT 1", [{}] * 4)  # open the EXPLAIN connections now, not on the first query

    def reload_model(self):
        self.model = PlanModel.load()
        self.latency = LatencyModel.load()

    def _x(self, plan, default_plan, hint, load):
        f = feature_vector(plan, default_plan, hint, load)
        return [f[n] for n in self.names]

    def predict_ms(self, plan, default_plan, hint, load) -> Optional[float]:
        if self.latency is None:
            return None
        return float(self.latency.predict_ms(np.array([self._x(plan, default_plan, hint, load)]))[0])

    def decide(self, sql: str, load: Optional[Dict[str, Any]] = None, search: bool = True) -> Dict[str, Any]:
        """Chooses the hint set. search=False only predicts the default plan (used for plain PostgreSQL)."""
        load = load or self.telemetry.snapshot()
        t0 = time.perf_counter()
        default_plan = self.db.explain(sql, {})
        default_ms = self.predict_ms(default_plan, default_plan, DEFAULT_ID, load)
        out = {"hint": DEFAULT_ID, "plans": {DEFAULT_ID: default_plan}, "load": load, "predictions": None,
               "predicted_default_ms": default_ms, "predicted_ms": default_ms,
               "mem_mb": memory_need_mb(default_plan, {})}
        if not search:
            out.update(reason="plain PostgreSQL", explain_ms=(time.perf_counter() - t0) * 1000)
            return out
        if self.model is None:
            out.update(reason="no trained model yet - using the default plan", explain_ms=(time.perf_counter() - t0) * 1000)
            return out
        if default_ms is not None and default_ms < self.short_query_ms:
            out.update(reason=f"short query (predicted {default_ms:.0f} ms < {self.short_query_ms:.0f} ms): "
                              "default plan, no plan search", explain_ms=(time.perf_counter() - t0) * 1000)
            return out
        others = [h for h in HINT_SETS if h["id"] != DEFAULT_ID]
        plans = {DEFAULT_ID: default_plan, **dict(zip([h["id"] for h in others],
                                                      self.db.explain_many(sql, [h["settings"] for h in others])))}
        explain_ms = (time.perf_counter() - t0) * 1000
        ids = [h["id"] for h in HINT_SETS]
        X = np.array([self._x(plans[h], default_plan, h, load) for h in ids])
        mean, std = self.model.predict(X)
        # Plans identical to the default plan (and not changing memory) are, by definition, the default plan.
        for i, h in enumerate(ids):
            if h != DEFAULT_ID and plans[h] == default_plan and "work_mem" not in BY_ID[h]["settings"]:
                mean[i], std[i] = 0.0, 0.0
        mean[ids.index(DEFAULT_ID)], std[ids.index(DEFAULT_ID)] = 0.0, 0.0
        hint, best = choose(mean, std, ids, self.min_gain, self.k)
        predicted = {BY_ID[h]["name"]: round(float(np.exp(-m)), 2) for h, m in zip(ids, mean)}
        if hint == DEFAULT_ID:
            reason = ("model predicts no confident gain over the default plan "
                      f"(best candidate {BY_ID[ids[best]]['name']}: {np.exp(-mean[best]):.2f}x)")
        else:
            reason = (f"model predicts {BY_ID[hint]['name']} is {np.exp(-mean[best]):.2f}x faster "
                      f"(uncertainty ±{std[best]:.2f})")
        if self.latency is not None:
            out["predicted_ms"] = float(self.latency.predict_ms(X[ids.index(hint)][None, :])[0])
        out.update(hint=hint, reason=reason, plans=plans, explain_ms=explain_ms, predictions=predicted,
                   mem_mb=memory_need_mb(plans[hint], BY_ID[hint]["settings"]))
        return out

    def execute(self, sql: str, mode: Union[str, int] = "ai", label: Optional[str] = None,
                learn: bool = True, os_boost: str = "off") -> Dict[str, Any]:
        t0 = time.perf_counter()
        load = self.telemetry.snapshot()
        decision = None
        if mode == "ai":
            decision = self.decide(sql, load)
            hint, reason = decision["hint"], decision["reason"]
        elif mode == "rule":
            hint = 1 if load["level"] == "high" else DEFAULT_ID
            reason = "rule: serial plan under high load" if hint else "rule: default plan"
        elif mode == "default":
            hint, reason = DEFAULT_ID, "plain PostgreSQL"
        else:
            hint, reason = int(mode), f"forced hint set {BY_ID[int(mode)]['name']}"
        boost = boost_wanted(os_boost, load)
        set_boost(boost)
        overhead_ms = (time.perf_counter() - t0) * 1000
        ms, rows = self.db.run(sql, BY_ID[hint]["settings"])
        if learn and self.log_runs and decision is not None:
            self._log(sql, label, hint, ms, rows, load, decision["plans"])
        return {"mode": mode, "hint": hint, "hint_name": BY_ID[hint]["name"], "reason": reason,
                "latency_ms": round(ms, 1), "overhead_ms": round(overhead_ms, 1), "rows": rows, "load": load,
                "os_boost": boost, "predictions": decision["predictions"] if decision else None,
                "predicted_ms": round(decision["predicted_ms"], 1) if decision and decision["predicted_ms"] else None}

    def _log(self, sql, label, hint, ms, rows, load, plans):
        """Live runs become extra training data (used by `python main.py train`)."""
        qid = query_id(sql)
        RESULTS_DIR.mkdir(exist_ok=True)
        level = "stress" if load["level"] == "high" else "idle"
        with open(RESULTS_DIR / "live_plans.jsonl", "a", encoding="utf-8") as f:
            for h, p in plans.items():
                f.write(json.dumps({"qid": qid, "template": label or "live", "hint": h, "plan": p}) + "\n")
        with open(RESULTS_DIR / "live.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"qid": qid, "template": label or "live", "level": level, "load": load,
                                "hint": hint, "ms": ms, "rows": rows, "timeout": False, "inferred": False}) + "\n")
