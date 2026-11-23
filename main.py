"""
QuerySync command line.

  python main.py db-start | db-stop | db-status     manage the bundled PostgreSQL 16 server
  python main.py load-data                          generate + load TPC-H SF1 (8.66 million rows)
  python main.py collect                            measure all queries x hint sets x load levels
  python main.py headroom                           best-possible gain (oracle) from collected data
  python main.py train                              train the AI model on all collected data
  python main.py evaluate                           held-out evaluation (unseen instances / templates)
  python main.py validate                           live, interleaved re-measurement on held-out queries
  python main.py schedule [--part mpl|policies|priority]   OS-layer experiments (scheduling, MPL, priority)
  python main.py query "SELECT ..." [--mode ai]     run one query through QuerySync
  python main.py ui                                 dashboard at http://127.0.0.1:5000
  python main.py monitor                            live CPU split (total / external / PostgreSQL)

Imports happen inside commands: on Windows, stress-generator worker processes re-import this file.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _pg(*args):
    from qs.config import BASE_DIR, PG_BIN, PG_DATA
    return subprocess.run([str(PG_BIN / "pg_ctl.exe"), *args, "-D", str(PG_DATA)], cwd=BASE_DIR,
                          capture_output=True, text=True)


def cmd_db_start(a):
    from qs.config import BASE_DIR
    r = _pg("start", "-w", "-t", "60", "-l", str(BASE_DIR / "pg.log"))
    print((r.stdout + r.stderr).strip())


def cmd_db_stop(a):
    print((_pg("stop", "-m", "fast", "-w").stdout).strip())


def cmd_db_status(a):
    print((_pg("status").stdout or _pg("status").stderr).strip().splitlines()[0])


def cmd_load(a):
    from experiments.load_tpch import load
    load(a.sf)


def cmd_collect(a):
    from experiments.collect import collect
    collect(a.per_template, a.levels.split(","))


def cmd_headroom(a):
    from experiments.analyze import headroom
    for lv in a.levels.split(","):
        headroom(lv)


def cmd_train(a):
    from qs.model import LatencyModel, PlanModel, build_dataset, read_collected
    runs, plans = read_collected()
    X, y, meta = build_dataset(runs, plans)
    t0 = time.time()
    m = PlanModel().fit(X, y)
    m.save()
    print(f"trained on {len(y)} (query, hint, load) examples in {time.time() - t0:.1f} s -> models/plan_model.pkl")
    print("top features:", ", ".join(f"{n} {v:.2f}" for n, v in m.feature_importance()[:8]))
    rows, log_ms = LatencyModel.training_rows(X, meta)
    LatencyModel().fit(X[rows], log_ms).save()
    print(f"latency (burst-time) model trained on {len(rows)} examples -> models/latency_model.pkl")


def cmd_evaluate(a):
    from experiments.evaluate import evaluate
    evaluate(k=a.k, min_gain=a.min_gain)


def cmd_validate(a):
    from experiments.validate import validate
    validate(a.rounds, a.levels.split(","))


def cmd_schedule(a):
    from experiments.schedule import run
    parts = tuple(a.part.split(",")) if a.part else ("mpl", "policies", "priority")
    run(parts, tuple(a.levels.split(",")))


def cmd_query(a):
    from qs.optimizer import Optimizer
    opt = Optimizer()
    mode = a.mode if a.mode in ("ai", "rule", "default") else int(a.mode)
    r = opt.execute(a.sql, mode=mode, os_boost=a.os_boost)
    print(f"load      : {r['load']['level']} (external CPU {r['load']['external_cpu']}%)")
    print(f"plan      : {r['hint_name']}  -- {r['reason']}")
    if r["predictions"]:
        top = sorted(r["predictions"].items(), key=lambda kv: -kv[1])[:4]
        print("predicted :", ", ".join(f"{n} {v}x" for n, v in top))
    print(f"OS boost  : {'on (high load: PostgreSQL at HIGH priority)' if r['os_boost'] else 'off'}")
    print(f"latency   : {r['latency_ms']} ms (+{r['overhead_ms']} ms decision), {r['rows']} rows")


def cmd_ui(a):
    from qs.server import serve
    serve(port=a.port, open_browser=not a.no_browser)


def cmd_monitor(a):
    from qs.telemetry import Telemetry
    t = Telemetry()
    try:
        for _ in range(a.count or 10 ** 9):
            s = t.snapshot()
            print(f"\rCPU {s['cpu']:5.1f}% | external {s['external_cpu']:5.1f}% | postgres {s['postgres_cpu']:5.1f}% "
                  f"| RAM {s['mem']:4.1f}% | load {s['level']:4s}", end="", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    print()


def main():
    from qs.config import MIN_PREDICTED_GAIN
    ap = argparse.ArgumentParser(description="QuerySync: AI-driven, OS-aware query optimization for PostgreSQL")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("db-start", "db-stop", "db-status", "headroom", "train"):
        p = sub.add_parser(name)
        if name == "headroom":
            p.add_argument("--levels", default="idle,stress")
    p = sub.add_parser("load-data"); p.add_argument("--sf", type=int, default=1)
    p = sub.add_parser("collect"); p.add_argument("--per-template", type=int, default=8)
    p.add_argument("--levels", default="idle,stress")
    p = sub.add_parser("evaluate"); p.add_argument("--k", type=float, default=0.5)
    p.add_argument("--min-gain", type=float, default=MIN_PREDICTED_GAIN)
    p = sub.add_parser("validate"); p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--levels", default="idle,stress")
    p = sub.add_parser("query"); p.add_argument("sql")
    p.add_argument("--mode", default="ai", help="ai | rule | default | <hint id>")
    p.add_argument("--os-boost", default="auto", help="auto | on | off")
    p = sub.add_parser("schedule"); p.add_argument("--part", default="", help="mpl,policies,priority")
    p.add_argument("--levels", default="idle,stress")
    p = sub.add_parser("ui"); p.add_argument("--port", type=int, default=5000); p.add_argument("--no-browser", action="store_true")
    p = sub.add_parser("monitor"); p.add_argument("--count", type=int)
    a = ap.parse_args()
    {"db-start": cmd_db_start, "db-stop": cmd_db_stop, "db-status": cmd_db_status, "load-data": cmd_load,
     "collect": cmd_collect, "headroom": cmd_headroom, "train": cmd_train, "evaluate": cmd_evaluate,
     "validate": cmd_validate, "schedule": cmd_schedule, "query": cmd_query, "ui": cmd_ui, "monitor": cmd_monitor}[a.cmd](a)


if __name__ == "__main__":
    main()
