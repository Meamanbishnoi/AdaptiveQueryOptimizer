"""
QuerySync command line.

  python main.py monitor                 live load (total / external / PostgreSQL CPU)
  python main.py query "SELECT ..."      compare default PostgreSQL vs QuerySync on one query
  python main.py bench                   full benchmark (22 queries, normal + stressed load)
  python main.py policy [--reset]        what QuerySync has learned
  python main.py ui                      dashboard at http://127.0.0.1:5000

Imports happen inside the commands: on Windows the stress generator's worker processes
re-import this file, and must not open database connections.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def cmd_monitor(args):
    from src.telemetry import Telemetry
    t = Telemetry()
    try:
        for _ in range(args.count or 10 ** 9):
            s = t.snapshot()
            print(f"\rCPU {s['cpu']:5.1f}% | external {s['external_cpu']:5.1f}% | postgres {s['postgres_cpu']:5.1f}% "
                  f"| RAM {s['mem']:4.1f}% | load {s['level']:4s}", end="", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    print()


def cmd_query(args):
    from src.server import compare, optimizer
    d = compare(args.sql, args.mode, args.runs)
    optimizer().save()
    print(f"Load        : {d['load']['level']} (external CPU {d['load']['external_cpu']}%)")
    print(f"Parallel    : {'yes' if d['parallel_plan'] else 'no - QuerySync always keeps the default plan'}")
    print(f"Default     : median {d['default_ms']:8.1f} ms   runs {d['default_runs']}")
    print(f"QuerySync   : median {d['optimized_ms']:8.1f} ms   runs {d['optimized_runs']}")
    print(f"Plans used  : {d['options']}")
    print(f"Result      : {d['verdict']} ({d['change_pct']:+.1f}%) - {d['reason']}")


def cmd_bench(args):
    from experiments.benchmark import run
    run(rounds=args.rounds, levels=args.levels.split(","))


def cmd_policy(args):
    from src.policy import Policy
    p = Policy()
    if args.reset:
        p.reset()
        print("Policy cleared.")
        return
    rows = p.summary()
    if not rows:
        print("Nothing learned yet. Run `python main.py bench` or use the dashboard.")
        return
    fmt = lambda v: f"{v:7.1f}ms" if v is not None else "        -"
    print(f"{'Query':28s} {'Load':5s} {'Choice':8s} {'Default':>9s} {'Serial':>9s} {'Runs':>6s}  Why")
    for r in rows:
        print(f"{r['label'][:28]:28s} {r['level']:5s} {(r['choice'] or '...'):8s} {fmt(r['default_ms'])} "
              f"{fmt(r['serial_ms'])} {(r['runs'] or '-'):>6s}  {r['reason']}")


def cmd_ui(args):
    from src.server import serve
    serve(port=args.port, open_browser=not args.no_browser)


def main():
    ap = argparse.ArgumentParser(description="QuerySync: OS-aware query optimization for PostgreSQL")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("monitor", help="live load")
    p.add_argument("--count", type=int)
    p = sub.add_parser("query", help="compare default vs QuerySync on one query")
    p.add_argument("sql")
    p.add_argument("--mode", choices=["adaptive", "rule"], default="adaptive")
    p.add_argument("--runs", type=int, default=5)
    p = sub.add_parser("bench", help="full benchmark")
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--levels", default="normal,stressed")
    p = sub.add_parser("policy", help="what QuerySync has learned")
    p.add_argument("--reset", action="store_true")
    p = sub.add_parser("ui", help="dashboard")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    {"monitor": cmd_monitor, "query": cmd_query, "bench": cmd_bench,
     "policy": cmd_policy, "ui": cmd_ui}[args.cmd](args)


if __name__ == "__main__":
    main()
