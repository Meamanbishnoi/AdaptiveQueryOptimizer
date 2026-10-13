"""
TPC-H Benchmark Evaluation Suite.
Executes the full 22-query canonical TPC-H decision-support benchmark across:
1. Default PostgreSQL (Cost-Based Baseline)
2. QuerySync (Supervised XGBoost Classifier)
3. QuerySync (Online Reinforcement Learning LinUCB Bandit)
Evaluates across Normal OS Load and CPU/RAM Stress.
"""

import sys
import re
import time
import json
from pathlib import Path
from typing import Dict, Any, List, Tuple
import numpy as np

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import REPORTS_DIR, DATA_DIR
from src.mediator.query_mediator import QueryMediator
from experiments.stress_generator import StressGenerator


def load_tpch_queries() -> List[Dict[str, Any]]:
    """
    Parses and categorizes individual TPC-H queries from data/tpch_benchmark.sql.
    """
    sql_path = DATA_DIR / "tpch_benchmark.sql"
    with open(sql_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split on comments matching '-- TPC-H QXX:'
    query_blocks = re.split(r"--\s*TPC-H\s+(Q\d+):", content)
    queries = []

    # Format: [preamble, 'Q01', sql1, 'Q02', sql2, ...]
    for i in range(1, len(query_blocks), 2):
        qid = query_blocks[i].strip()
        sql_body = query_blocks[i+1].strip()
        # Clean title line
        first_line_end = sql_body.find("\n")
        title = sql_body[:first_line_end].strip() if first_line_end != -1 else ""
        sql_text = sql_body[first_line_end:].strip() if first_line_end != -1 else sql_body

        # Categorization based on structural patterns
        if qid in ("Q01", "Q06", "Q14"):
            category = "Agg-Heavy"
        elif qid in ("Q02", "Q03", "Q05", "Q07", "Q08", "Q09", "Q10"):
            category = "Multi-Join"
        else:
            category = "Subquery-Nested"

        queries.append({
            "id": qid,
            "title": title,
            "category": category,
            "sql": sql_text.rstrip(";"),
        })

    return queries


def execute_tpch_suite(
    mediator: QueryMediator,
    queries: List[Dict[str, Any]],
    mode: str = "default",
    engine_type: str = "xgboost",
) -> Dict[str, Any]:
    """
    Executes a workload pass over the TPC-H query set.
    mode: 'default' (force strategy 0) or 'adaptive'
    """
    results = []
    category_latencies: Dict[str, List[float]] = {
        "Agg-Heavy": [],
        "Multi-Join": [],
        "Subquery-Nested": [],
    }
    strategies_used: Dict[str, int] = {}
    cache_hits = 0

    force_strat = 0 if mode == "default" else None

    for item in queries:
        qid = item["id"]
        cat = item["category"]
        sql_text = item["sql"]

        res = mediator.execute_query(
            sql_text,
            force_strategy=force_strat,
            engine_type=engine_type,
        )

        lat = res["actual_latency_ms"]
        strat = res["strategy_name"]
        is_hit = res.get("is_cache_hit", 0)

        results.append({
            "id": qid,
            "category": cat,
            "strategy": strat,
            "latency_ms": lat,
            "cache_hit": is_hit,
        })
        category_latencies[cat].append(lat)
        strategies_used[strat] = strategies_used.get(strat, 0) + 1
        if is_hit:
            cache_hits += 1

    all_latencies = [r["latency_ms"] for r in results]
    avg_lat = float(np.mean(all_latencies))
    p95_lat = float(np.percentile(all_latencies, 95))

    cat_averages = {
        cat: round(float(np.mean(lats)), 2) if lats else 0.0
        for cat, lats in category_latencies.items()
    }

    return {
        "avg_latency_ms": round(avg_lat, 2),
        "p95_latency_ms": round(p95_lat, 2),
        "category_averages": cat_averages,
        "strategies_used": strategies_used,
        "cache_hit_rate_pct": round((cache_hits / len(queries)) * 100, 1),
        "query_results": results,
    }


def run_full_tpch_benchmark(repeats: int = 1) -> Dict[str, Any]:
    """
    Runs canonical TPC-H benchmark comparing:
    - Default PostgreSQL
    - QuerySync (XGBoost)
    - QuerySync (LinUCB Bandit)
    Under both Normal load and Stressed conditions.
    """
    print("=" * 75)
    print("CANONICAL TPC-H BENCHMARK SUITE (Q01 - Q22)")
    print("Evaluating: Default PostgreSQL vs. XGBoost vs. LinUCB Bandit")
    print("=" * 75)

    mediator = QueryMediator()
    stress = StressGenerator()
    queries = load_tpch_queries()
    print(f"[TPC-H] Loaded {len(queries)} benchmark queries across 3 categories:")
    for cat in ("Agg-Heavy", "Multi-Join", "Subquery-Nested"):
        cnt = sum(1 for q in queries if q["category"] == cat)
        print(f"   - {cat:<18s}: {cnt:2d} queries")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_data = {}

    try:
        # -------------------------------------------------------------
        # 1. Normal System Load Evaluation
        # -------------------------------------------------------------
        print("\n" + "-" * 75)
        print("STAGE 1: Normal Host Conditions (Idle Background)")
        print("-" * 75)

        print("1.1 Default PostgreSQL Planner...")
        norm_default = execute_tpch_suite(mediator, queries, mode="default")
        print(f"    Avg: {norm_default['avg_latency_ms']:6.2f} ms | P95: {norm_default['p95_latency_ms']:6.2f} ms | By Cat: {norm_default['category_averages']}")

        print("1.2 QuerySync (XGBoost Classifier)...")
        norm_xgb = execute_tpch_suite(mediator, queries, mode="adaptive", engine_type="xgboost")
        print(f"    Avg: {norm_xgb['avg_latency_ms']:6.2f} ms | P95: {norm_xgb['p95_latency_ms']:6.2f} ms | Cache Hit: {norm_xgb['cache_hit_rate_pct']}%")

        print("1.3 QuerySync (LinUCB Online Bandit)...")
        norm_bandit = execute_tpch_suite(mediator, queries, mode="adaptive", engine_type="linucb")
        print(f"    Avg: {norm_bandit['avg_latency_ms']:6.2f} ms | P95: {norm_bandit['p95_latency_ms']:6.2f} ms | Cache Hit: {norm_bandit['cache_hit_rate_pct']}%")

        # -------------------------------------------------------------
        # 2. High CPU Stress Evaluation
        # -------------------------------------------------------------
        print("\n" + "-" * 75)
        print("STAGE 2: High CPU Contention (85% Core Saturation)")
        print("-" * 75)
        print("[Stress] Starting CPU stress workers across cores...")
        stress.start_stress(duty_cycle=0.85)
        time.sleep(2.5)

        print("2.1 Default PostgreSQL Planner (Under Stress)...")
        stress_default = execute_tpch_suite(mediator, queries, mode="default")
        print(f"    Avg: {stress_default['avg_latency_ms']:6.2f} ms | P95: {stress_default['p95_latency_ms']:6.2f} ms | By Cat: {stress_default['category_averages']}")

        print("2.2 QuerySync (XGBoost Classifier Under Stress)...")
        stress_xgb = execute_tpch_suite(mediator, queries, mode="adaptive", engine_type="xgboost")
        print(f"    Avg: {stress_xgb['avg_latency_ms']:6.2f} ms | P95: {stress_xgb['p95_latency_ms']:6.2f} ms | Strats: {stress_xgb['strategies_used']}")

        print("2.3 QuerySync (LinUCB Online Bandit Under Stress)...")
        stress_bandit = execute_tpch_suite(mediator, queries, mode="adaptive", engine_type="linucb")
        print(f"    Avg: {stress_bandit['avg_latency_ms']:6.2f} ms | P95: {stress_bandit['p95_latency_ms']:6.2f} ms | Strats: {stress_bandit['strategies_used']}")

    finally:
        stress.stop_stress()

    # Compute Comparative Metrics
    xgb_speedup = round(stress_default["avg_latency_ms"] / max(1e-4, stress_xgb["avg_latency_ms"]), 2)
    bandit_speedup = round(stress_default["avg_latency_ms"] / max(1e-4, stress_bandit["avg_latency_ms"]), 2)
    xgb_reduction = round(((stress_default["avg_latency_ms"] - stress_xgb["avg_latency_ms"]) / stress_default["avg_latency_ms"]) * 100, 1)

    report_data = {
        "normal": {
            "default_postgres": norm_default,
            "querysync_xgboost": norm_xgb,
            "querysync_linucb": norm_bandit,
        },
        "stressed": {
            "default_postgres": stress_default,
            "querysync_xgboost": stress_xgb,
            "querysync_linucb": stress_bandit,
        },
        "comparisons": {
            "xgboost_speedup_under_stress": xgb_speedup,
            "linucb_speedup_under_stress": bandit_speedup,
            "xgboost_latency_reduction_pct": xgb_reduction,
        },
    }

    out_file = REPORTS_DIR / "tpch_benchmark_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    print("\n" + "=" * 75)
    print("TPC-H BENCHMARK EXECUTIVE SUMMARY")
    print("=" * 75)
    print(f"Default Postgres (Normal)    : {norm_default['avg_latency_ms']:6.2f} ms (P95: {norm_default['p95_latency_ms']:6.2f} ms)")
    print(f"Default Postgres (Stressed)  : {stress_default['avg_latency_ms']:6.2f} ms (P95: {stress_default['p95_latency_ms']:6.2f} ms)")
    print("-" * 75)
    print(f"QuerySync XGBoost (Stressed) : {stress_xgb['avg_latency_ms']:6.2f} ms ({xgb_speedup}x speedup | {xgb_reduction}% reduction)")
    print(f"QuerySync LinUCB  (Stressed) : {stress_bandit['avg_latency_ms']:6.2f} ms ({bandit_speedup}x speedup)")
    print(f"Feature Cache Hit Rate       : {stress_xgb['cache_hit_rate_pct']}%")
    print(f"Results saved to             : {out_file}")
    print("=" * 75)

    return report_data


if __name__ == "__main__":
    run_full_tpch_benchmark()
