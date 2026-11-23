"""
Turns an EXPLAIN plan (+ OS telemetry + hint set) into a fixed-length feature vector for the model.

Plan features describe the plan PostgreSQL would run under a hint set: estimated cost and rows,
how many of each operator it uses and how much data flows through them, parallel workers, depth.
OS features describe the machine at that moment (CPU used by other programs, memory pressure).
"""

import math
from typing import Any, Dict, List

from qs.hints import HINT_SETS

NODE_TYPES = [
    "Seq Scan", "Index Scan", "Index Only Scan", "Bitmap Heap Scan", "Bitmap Index Scan",
    "Nested Loop", "Hash Join", "Merge Join", "Hash", "Sort", "Incremental Sort", "Aggregate",
    "Gather", "Gather Merge", "Materialize", "Memoize", "Limit", "CTE Scan", "Unique", "Subquery Scan",
]
_log = lambda x: math.log1p(max(0.0, float(x)))


def plan_stats(plan: Dict[str, Any]) -> Dict[str, float]:
    counts = {t: 0 for t in NODE_TYPES}
    rows = {t: 0.0 for t in NODE_TYPES}
    agg = {"workers": 0, "depth": 0, "subplans": 0, "nodes": 0, "max_rows": 0.0, "hash_agg": 0, "sort_agg": 0}

    def walk(n, depth):
        t = n.get("Node Type", "")
        agg["nodes"] += 1
        agg["depth"] = max(agg["depth"], depth)
        agg["max_rows"] = max(agg["max_rows"], float(n.get("Plan Rows", 0)))
        if t in counts:
            counts[t] += 1
            rows[t] += float(n.get("Plan Rows", 0))
        if t == "Aggregate":
            agg["hash_agg"] += n.get("Strategy") == "Hashed"
            agg["sort_agg"] += n.get("Strategy") == "Sorted"
        agg["workers"] = max(agg["workers"], int(n.get("Workers Planned", 0)))
        if n.get("Parent Relationship") in ("SubPlan", "InitPlan"):
            agg["subplans"] += 1
        for c in n.get("Plans", []):
            walk(c, depth + 1)

    walk(plan, 1)
    f = {
        "total_cost": _log(plan.get("Total Cost", 0)),
        "startup_cost": _log(plan.get("Startup Cost", 0)),
        "plan_rows": _log(plan.get("Plan Rows", 0)),
        "plan_width": float(plan.get("Plan Width", 0)),
        "max_rows": _log(agg["max_rows"]),
        "nodes": float(agg["nodes"]),
        "depth": float(agg["depth"]),
        "workers": float(agg["workers"]),
        "subplans": float(agg["subplans"]),
        "hash_agg": float(agg["hash_agg"]),
        "sort_agg": float(agg["sort_agg"]),
    }
    for t in NODE_TYPES:
        key = t.lower().replace(" ", "_")
        f[f"n_{key}"] = float(counts[t])
        f[f"rows_{key}"] = _log(rows[t])
    return f


def feature_vector(plan: Dict[str, Any], default_plan: Dict[str, Any], hint_id: int,
                   load: Dict[str, float]) -> Dict[str, float]:
    f = plan_stats(plan)
    d = plan_stats(default_plan)
    f["cost_ratio_vs_default"] = f["total_cost"] - d["total_cost"]   # log-ratio of estimated costs
    f["default_total_cost"] = d["total_cost"]
    f["default_workers"] = d["workers"]
    f["same_plan_as_default"] = float(plan == default_plan)
    for h in HINT_SETS:
        f[f"hint_{h['id']}"] = float(hint_id == h["id"])
    f["os_external_cpu"] = float(load.get("external_cpu", 0.0))
    f["os_total_cpu"] = float(load.get("cpu", 0.0))
    f["os_mem"] = float(load.get("mem", 0.0))
    f["os_high_load"] = float(load.get("level") == "high")
    return f


def feature_names() -> List[str]:
    dummy = {"Node Type": "Seq Scan", "Total Cost": 1, "Plan Rows": 1}
    return list(feature_vector(dummy, dummy, 0, {}).keys())


def memory_need_mb(plan: Dict[str, Any], settings: Dict[str, str]) -> float:
    """
    Upper bound on the working memory a plan may claim, the way PostgreSQL budgets it: every sort,
    hash table, hashed aggregate or materialize node may use up to work_mem (hash tables up to
    hash_mem_multiplier x work_mem = 2x), in every process (leader + parallel workers).
    The scheduler uses it as the job's declared maximum claim for Banker's-style admission.
    """
    wm = float(str(settings.get("work_mem", "'4MB'")).strip("'").upper().replace("MB", ""))
    units = 0.0

    def walk(n):
        nonlocal units
        t = n.get("Node Type", "")
        if t == "Hash" or (t == "Aggregate" and n.get("Strategy") in ("Hashed", "Mixed")):
            units += 2.0
        elif t in ("Sort", "Incremental Sort", "Materialize", "Memoize"):
            units += 1.0
        for c in n.get("Plans", []):
            walk(c)

    walk(plan)
    procs = 1 + plan_stats(plan)["workers"]
    return round(max(1.0, units) * wm * procs, 1)
