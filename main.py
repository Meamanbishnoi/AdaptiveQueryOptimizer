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
from src.query_processor.feature_cache import default_feature_cache
from src.ml_engine.train import run_training
from src.ml_engine.dataset_generator import generate_training_dataset
from experiments.run_benchmark import execute_full_benchmark
from experiments.run_tpch_benchmark import run_full_tpch_benchmark
from experiments.plot_results import generate_all_plots


def cmd_monitor(args):
    """Streams live OS telemetry to console."""
    print("=" * 65)
    print("QuerySync: Live OS Telemetry Monitor")
    print("=" * 65)
    interval = getattr(args, "interval", 0.5)
    max_count = getattr(args, "count", None)
    mon = OSTelemetryMonitor(sample_interval=interval)
    iteration = 0
    try:
        while True:
            m = mon.sample_metrics()
            stress_flag = " [STRESSED]" if (m["is_cpu_stressed"] or m["is_mem_stressed"]) else ""
            print(
                f"\rCPU: {m['cpu_percent']:5.1f}% | "
                f"RAM: {m['mem_used_mb']:7.1f} MB ({m['mem_percent']:4.1f}%) | "
                f"MPS: {m.get('memory_pressure_score', 0):.2f} | "
                f"CtxSw: {m.get('ctx_switches_per_sec', 0):8.0f}/s | "
                f"Procs: {m['process_count']:3d}{stress_flag}    ",
                end="",
                flush=True,
            )
            iteration += 1
            if max_count is not None and iteration >= max_count:
                print("\nMonitoring sample limit reached.")
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")


def cmd_query(args):
    """Executes a single SQL query through the OS-Aware Query Mediator."""
    sql = args.sql
    engine = args.engine.lower()
    print("=" * 70)
    print(f"QuerySync: OS-Aware Query Execution (Engine: {engine.upper()})")
    print("=" * 70)
    print(f"Incoming SQL:\n{sql.strip()}\n")

    mediator = QueryMediator(default_engine=engine)

    # 1. Run with OS-Aware Optimizer
    res_opt = mediator.execute_query(sql, force_strategy=None, engine_type=engine)

    # 2. Run with Default PostgreSQL planner for comparison
    res_def = mediator.execute_query(sql, force_strategy=0)

    print("-" * 70)
    print(f"System State       : CPU {res_opt['os_snapshot']['cpu_percent']:.1f}%, RAM {res_opt['os_snapshot']['mem_percent']:.1f}%, MPS={res_opt['os_snapshot'].get('memory_pressure_score', 0):.2f}")
    print(f"Query Features     : Joins={res_opt['query_features']['join_count']}, Tables={res_opt['query_features']['table_count']}, Agg={res_opt['query_features']['has_aggregation']}")
    print(f"Plan Hash Cache    : {'HIT' if res_opt['is_cache_hit'] else 'MISS'} (Hash: {res_opt['query_hash']})")
    print("-" * 70)
    print(f"Decision Engine    : {res_opt['engine_type'].upper()}")
    print(f"Chosen Strategy    : {res_opt['strategy_name']} (ID: {res_opt['strategy_id']})")
    if res_opt.get("bandit_reward") is not None:
        print(f"Bandit RL Reward   : {res_opt['bandit_reward']:+.3f}")
    else:
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


def cmd_cache_stats(args):
    """Displays LRU Plan Hash Cache efficiency and hit statistics."""
    stats = default_feature_cache.get_stats()
    print("=" * 60)
    print("QuerySync: LRU Query Feature & Plan Hash Cache Stats")
    print("=" * 60)
    print(f"Cache Capacity     : {stats['capacity']} query templates")
    print(f"Cached Templates   : {stats['current_size']}")
    print(f"Total Lookups      : {stats['total_lookups']}")
    print(f"Cache Hits         : {stats['hits']}")
    print(f"Cache Misses       : {stats['misses']}")
    print(f"Hit Ratio          : {stats['hit_ratio_percent']:.1f}%")
    print("=" * 60)


def cmd_tpch(args):
    """Executes the full 22-query canonical TPC-H decision support benchmark."""
    run_full_tpch_benchmark()


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


def cmd_ui(args):
    """Launches the QuerySync interactive Web Dashboard and REST API."""
    from src.ui.server import start_ui_server
    port = getattr(args, "port", 5000)
    open_browser = not getattr(args, "no_browser", False)
    start_ui_server(port=port, open_browser=open_browser)


def main():
    parser = argparse.ArgumentParser(
        description="QuerySync: Cross-Layer OS-Aware Adaptive Query Optimization System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # ui
    ui_parser = subparsers.add_parser("ui", help="Launch the interactive Web Dashboard")
    ui_parser.add_argument("--port", type=int, default=5000, help="Port to bind the server (default: 5000)")
    ui_parser.add_argument("--no-browser", action="store_true", help="Do not automatically open default web browser")

    # monitor
    monitor_parser = subparsers.add_parser("monitor", help="Stream real-time OS telemetry")
    monitor_parser.add_argument("--interval", type=float, default=0.5, help="Sampling interval in seconds (default: 0.5)")
    monitor_parser.add_argument("--count", type=int, default=None, help="Number of samples to collect before exiting (default: infinite)")

    # query
    query_parser = subparsers.add_parser("query", help="Execute SQL query with OS-Aware Optimizer")
    query_parser.add_argument("sql", type=str, help="SQL query to execute")
    query_parser.add_argument(
        "--engine",
        type=str,
        choices=["xgboost", "linucb"],
        default="xgboost",
        help="ML decision engine to use: 'xgboost' (supervised) or 'linucb' (reinforcement learning)",
    )

    # cache-stats
    subparsers.add_parser("cache-stats", help="Inspect LRU feature cache efficiency")

    # tpch
    subparsers.add_parser("tpch", help="Run the 22-query canonical TPC-H benchmark suite")

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
        "ui": cmd_ui,
        "monitor": cmd_monitor,
        "query": cmd_query,
        "cache-stats": cmd_cache_stats,
        "tpch": cmd_tpch,
        "collect": cmd_collect,
        "train": cmd_train,
        "benchmark": cmd_benchmark,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
