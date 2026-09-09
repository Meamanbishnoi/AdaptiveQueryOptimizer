"""
Benchmark Evaluation Suite.
Executes the four mandatory roadmap experiments:
1. Baseline 1: Default PostgreSQL (Normal load, no AI)
2. Baseline 2: Default PostgreSQL under CPU Stress
3. Experiment 1: OS-Aware System (Normal load)
4. Experiment 2: OS-Aware System under CPU Stress
Records Average Latency, P95 Latency, Throughput (QPM), and Plan Adaptation frequency.
"""

import sys
import time
import json
from pathlib import Path
from typing import Dict, Any, List
import numpy as np

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import REPORTS_DIR
from src.mediator.query_mediator import QueryMediator
from src.ml_engine.dataset_generator import generate_queries
from experiments.stress_generator import StressGenerator


def run_workload(
    mediator: QueryMediator,
    queries: List[str],
    force_strategy: int = None,
    mode_name: str = "Benchmark",
) -> Dict[str, Any]:
    """Runs a batch of queries and records performance metrics."""
    latencies = []
    strategies_used = {}
    start_wall = time.perf_counter()

    for idx, q in enumerate(queries, 1):
        res = mediator.execute_query(q, force_strategy=force_strategy)
        lat = res["actual_latency_ms"]
        strat = res["strategy_name"]
        latencies.append(lat)
        strategies_used[strat] = strategies_used.get(strat, 0) + 1

    total_time_sec = time.perf_counter() - start_wall
    avg_latency = float(np.mean(latencies))
    p95_latency = float(np.percentile(latencies, 95))
    throughput_qpm = float(len(queries) / max(1e-4, total_time_sec / 60.0))

    # Adaptation count (how many times plan was shifted away from DEFAULT)
    adapted_count = sum(cnt for s, cnt in strategies_used.items() if s != "DEFAULT")
    adaptation_rate = float(adapted_count / len(queries))

    return {
        "mode": mode_name,
        "query_count": len(queries),
        "total_time_sec": round(total_time_sec, 3),
        "avg_latency_ms": round(avg_latency, 2),
        "p95_latency_ms": round(p95_latency, 2),
        "throughput_qpm": round(throughput_qpm, 1),
        "strategies_used": strategies_used,
        "adaptation_rate": round(adaptation_rate * 100, 1),
        "raw_latencies": [round(x, 2) for x in latencies],
    }


def execute_full_benchmark(query_count: int = 30) -> Dict[str, Any]:
    """Runs all 4 mandatory experiments and saves structured report."""
    print("=" * 70)
    print("OS-Aware Adaptive Query Optimizer: Evaluation Benchmark")
    print(f"Workload size: {query_count} analytical queries per experimental phase")
    print("=" * 70)

    mediator = QueryMediator()
    stress = StressGenerator()
    queries = generate_queries(count=query_count)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    results = {}

    try:
        # -------------------------------------------------------------
        # Phase 1: Baseline 1 (Default Postgres, Normal Load)
        # -------------------------------------------------------------
        print("\n[Exp 1/4] Running Baseline 1: Default PostgreSQL (Normal Load)...")
        time.sleep(1.0)
        res_b1 = run_workload(mediator, queries, force_strategy=0, mode_name="Baseline Normal")
        results["baseline_normal"] = res_b1
        print(f"   Avg Latency: {res_b1['avg_latency_ms']:6.2f} ms | P95: {res_b1['p95_latency_ms']:6.2f} ms | Throughput: {res_b1['throughput_qpm']:.0f} QPM")

        # -------------------------------------------------------------
        # Phase 2: Experiment 1 (OS-Aware System, Normal Load)
        # -------------------------------------------------------------
        print("\n[Exp 2/4] Running OS-Aware Optimizer (Normal Load)...")
        time.sleep(1.0)
        res_e1 = run_workload(mediator, queries, force_strategy=None, mode_name="OS-Aware Normal")
        results["os_aware_normal"] = res_e1
        print(f"   Avg Latency: {res_e1['avg_latency_ms']:6.2f} ms | P95: {res_e1['p95_latency_ms']:6.2f} ms | Throughput: {res_e1['throughput_qpm']:.0f} QPM")

        # -------------------------------------------------------------
        # Phase 3: Baseline 2 (Default Postgres under CPU Stress)
        # -------------------------------------------------------------
        print("\n[Exp 3/4] Starting CPU Stress workers...")
        stress.start_stress(duty_cycle=0.85)
        time.sleep(2.5)  # Allow stress to saturate cores

        print("   Running Baseline 2: Default PostgreSQL (Under Stress)...")
        res_b2 = run_workload(mediator, queries, force_strategy=0, mode_name="Baseline Stressed")
        results["baseline_stressed"] = res_b2
        print(f"   Avg Latency: {res_b2['avg_latency_ms']:6.2f} ms | P95: {res_b2['p95_latency_ms']:6.2f} ms | Throughput: {res_b2['throughput_qpm']:.0f} QPM")

        # -------------------------------------------------------------
        # Phase 4: Experiment 2 (OS-Aware System under CPU Stress)
        # -------------------------------------------------------------
        print("\n[Exp 4/4] Running OS-Aware Optimizer (Under Stress)...")
        res_e2 = run_workload(mediator, queries, force_strategy=None, mode_name="OS-Aware Stressed")
        results["os_aware_stressed"] = res_e2
        print(f"   Avg Latency: {res_e2['avg_latency_ms']:6.2f} ms | P95: {res_e2['p95_latency_ms']:6.2f} ms | Throughput: {res_e2['throughput_qpm']:.0f} QPM")
        print(f"   Strategies Adapted Under Stress: {res_e2['strategies_used']} (Adaptation Rate: {res_e2['adaptation_rate']}%)")

    finally:
        stress.stop_stress()

    # Performance Improvement Analysis
    speedup_normal = round(res_b1["avg_latency_ms"] / max(1e-4, res_e1["avg_latency_ms"]), 2)
    speedup_stressed = round(res_b2["avg_latency_ms"] / max(1e-4, res_e2["avg_latency_ms"]), 2)
    latency_reduction_pct = round(((res_b2["avg_latency_ms"] - res_e2["avg_latency_ms"]) / res_b2["avg_latency_ms"]) * 100, 1)

    summary = {
        "speedup_normal": speedup_normal,
        "speedup_stressed": speedup_stressed,
        "latency_reduction_pct_under_stress": latency_reduction_pct,
        "results": results,
    }

    report_path = REPORTS_DIR / "benchmark_results.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\n" + "=" * 70)
    print("BENCHMARK EXECUTIVE SUMMARY")
    print("=" * 70)
    print(f"Baseline Normal Latency   : {res_b1['avg_latency_ms']} ms")
    print(f"OS-Aware Normal Latency   : {res_e1['avg_latency_ms']} ms")
    print(f"Baseline Stressed Latency : {res_b2['avg_latency_ms']} ms")
    print(f"OS-Aware Stressed Latency : {res_e2['avg_latency_ms']} ms")
    print("-" * 70)
    print(f"Performance Speedup Under Stress  : {speedup_stressed}x faster")
    print(f"Latency Reduction Under Stress     : {latency_reduction_pct}% lower latency")
    print(f"Detailed results written to       : {report_path}")
    print("=" * 70)

    return summary


if __name__ == "__main__":
    execute_full_benchmark(query_count=30)
