"""
QuerySync: Cross-Layer OS-Aware Adaptive Query Optimization System.
Main Unified Command Line Interface.
"""

import sys
import time
import argparse
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.telemetry.os_monitor import OSTelemetryMonitor
from src.mediator.query_mediator import QueryMediator
from src.ml_engine.train import run_training
from src.ml_engine.dataset_generator import generate_training_dataset
from experiments.run_benchmark import execute_full_benchmark
from experiments.plot_results import generate_all_plots


def cmd_monitor(args):
    """Streams live OS telemetry to console."""
    print("=" * 65)
    print("QuerySync: Live OS Telemetry Monitor (Press Ctrl+C to exit)")
    print("=" * 65)
    mon = OSTelemetryMonitor(sample_interval=0.5)
    try:
        while True:
            m = mon.sample_metrics()
            stress_flag = " [STRESSED]" if (m["is_cpu_stressed"] or m["is_mem_stressed"]) else ""
            print(
                f"\rCPU: {m['cpu_percent']:5.1f}% | "
                f"RAM: {m['mem_used_mb']:7.1f} MB ({m['mem_percent']:4.1f}%) | "
                f"Disk IO: {m['disk_read_mbps']+m['disk_write_mbps']:5.2f} MB/s | "
                f"Procs: {m['process_count']:3d}{stress_flag}    ",
                end="",
                flush=True,
            )
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")


def cmd_query(args):
    """Executes a single SQL query through the OS-Aware Query Mediator."""
    sql = args.sql
    print("=" * 70)
    print("QuerySync: OS-Aware Query Execution")
    print("=" * 70)
    print(f"Incoming SQL:\n{sql.strip()}\n")

    mediator = QueryMediator()

    # 1. Run with OS-Aware Optimizer
    res_opt = mediator.execute_query(sql, force_strategy=None)

    # 2. Run with Default PostgreSQL planner for comparison
    res_def = mediator.execute_query(sql, force_strategy=0)

    print("-" * 70)
    print(f"System State       : CPU {res_opt['os_snapshot']['cpu_percent']:.1f}%, RAM {res_opt['os_snapshot']['mem_percent']:.1f}%")
    print(f"Query Features     : Joins={res_opt['query_features']['join_count']}, Tables={res_opt['query_features']['table_count']}, Agg={res_opt['query_features']['has_aggregation']}")
    print("-" * 70)
    print(f"Chosen Strategy    : {res_opt['strategy_name']} (ID: {res_opt['strategy_id']})")
    print(f"Model Confidence   : {res_opt['model_confidence']:.1%}")
    print(f"Strategy Rationale : {res_opt['strategy_desc']}")
    print("-" * 70)
    print(f"OS-Aware Latency   : {res_opt['actual_latency_ms']:.2f} ms")
    print(f"Default Latency    : {res_def['actual_latency_ms']:.2f} ms")

    if res_def['actual_latency_ms'] > 0:
        diff = res_def['actual_latency_ms'] - res_opt['actual_latency_ms']
        if diff > 0:
            print(f"Improvement        : {diff:.2f} ms faster ({diff/res_def['actual_latency_ms']:.1%} reduction)")
        else:
            print(f"Performance delta  : {diff:+.2f} ms")
    print("=" * 70)


def cmd_collect(args):
    """Collects training data under varying synthetic load."""
    from src.feedback.feedback_store import FeedbackStore
    store = FeedbackStore()
    generate_training_dataset(target_samples=args.samples, store=store)


def cmd_train(args):
    """Trains or retrains the XGBoost model."""
    run_training()


def cmd_benchmark(args):
    """Runs full comparative benchmark across baseline and stress conditions."""
    execute_full_benchmark(query_count=args.queries)
    if args.plot:
        print("\nGenerating publication charts...")
        generate_all_plots()


def main():
    parser = argparse.ArgumentParser(
        description="QuerySync: Cross-Layer OS-Aware Adaptive Query Optimization System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # monitor
    subparsers.add_parser("monitor", help="Stream real-time OS telemetry")

    # query
    query_parser = subparsers.add_parser("query", help="Execute SQL query with OS-Aware Optimizer")
    query_parser.add_argument("sql", type=str, help="SQL query to execute")

    # collect
    collect_parser = subparsers.add_parser("collect", help="Generate training data")
    collect_parser.add_argument("--samples", type=int, default=400, help="Number of training samples")

    # train
    subparsers.add_parser("train", help="Train XGBoost decision model")

    # benchmark
    bench_parser = subparsers.add_parser("benchmark", help="Run comparative benchmark suite")
    bench_parser.add_argument("--queries", type=int, default=30, help="Queries per experiment phase")
    bench_parser.add_argument("--plot", action="store_true", default=True, help="Generate charts")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "monitor": cmd_monitor,
        "query": cmd_query,
        "collect": cmd_collect,
        "train": cmd_train,
        "benchmark": cmd_benchmark,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
