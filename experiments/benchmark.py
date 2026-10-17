"""
Benchmark: default PostgreSQL vs QuerySync on the 22 TPC-H-style queries, at normal load
and under CPU stress.

Modes compared:
  default  : plain PostgreSQL
  aa       : plain PostgreSQL again - an A/A control. Any "win" or "loss" it shows is
             measurement noise, which calibrates every other column.
  rule     : static rule (SERIAL when load is high and the plan is parallel)
  adaptive : QuerySync measured policy

Method:
  1. Warm-up: one untimed run per query.
  2. Learning: adaptive mode runs each query until its policy has decided (reported).
  3. Evaluation: ROUNDS rounds; each round runs every query once per mode, in random order.
  4. Per query: median latency per mode. Win / loss only if the difference is > 10% AND an
     exact permutation test gives p < 0.05.
Outputs reports/benchmark.json and reports/speedup_by_query.png.
"""

import json
import math
import random
import re
import statistics
import sys
import time
from itertools import combinations
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import DATA_DIR, REPORTS_DIR

MODES = ["default", "aa", "rule", "adaptive"]
BAND, ALPHA = 0.10, 0.05


def load_queries() -> List[Dict[str, str]]:
    text = (DATA_DIR / "tpch_benchmark.sql").read_text(encoding="utf-8")
    parts = re.split(r"--\s*TPC-H\s+(Q\d+):", text)
    out = []
    for i in range(1, len(parts), 2):
        body = parts[i + 1].strip()
        nl = body.find("\n")
        sql = "\n".join(line for line in body[nl:].splitlines() if not line.strip().startswith("--"))
        out.append({"id": parts[i].strip(), "title": body[:nl].strip(), "sql": sql.strip().rstrip(";")})
    return sorted(out, key=lambda q: q["id"])


def permutation_p(a: List[float], b: List[float]) -> float:
    """Two-sided exact permutation test on mean log-latency."""
    la, lb = [math.log(x) for x in a], [math.log(x) for x in b]
    pooled, n = la + lb, len(la)
    total = sum(pooled)
    observed = abs(sum(la) / n - sum(lb) / len(lb))
    splits = list(combinations(range(len(pooled)), n))
    extreme = 0
    for idx in splits:
        s = sum(pooled[i] for i in idx)
        extreme += abs(s / n - (total - s) / (len(pooled) - n)) >= observed - 1e-12
    return extreme / len(splits)


def run_mode(opt, q, mode):
    if mode == "aa":
        return opt.execute(q["sql"], mode="default", label=q["id"], learn=False)
    return opt.execute(q["sql"], mode=mode, label=q["id"], learn=(mode == "adaptive"))


def learn(opt, queries, max_passes=20) -> int:
    runs = 0
    for _ in range(max_passes):
        level = opt.telemetry.snapshot()["level"]
        todo = [q for q in queries if not opt.policy.converged(_tpl(q), level)]
        if not todo:
            break
        for q in todo:
            opt.execute(q["sql"], mode="adaptive", label=q["id"])
            runs += 1
    opt.save()
    return runs


def _tpl(q):
    from src.optimizer import template_of
    return template_of(q["sql"])


def evaluate(opt, queries, rounds, rng, modes=None, progress=None) -> Dict:
    """Runs every query once per mode per round (random mode order). progress(done, total) is optional."""
    modes = modes or MODES
    total, done = rounds * len(queries) * len(modes), 0
    lat = {q["id"]: {m: [] for m in modes} for q in queries}
    opts = {q["id"]: {m: [] for m in modes} for q in queries}
    ext = []
    for r in range(rounds):
        for q in queries:
            order = list(modes)
            rng.shuffle(order)
            for m in order:
                res = run_mode(opt, q, m)
                lat[q["id"]][m].append(res["latency_ms"])
                opts[q["id"]][m].append(res["option"])
                ext.append(res["load"]["external_cpu"])
                done += 1
                if progress:
                    progress(done, total)
        print(f"    round {r + 1}/{rounds}", flush=True)
    opt.save()

    per_query, summary = [], {}
    for q in queries:
        base = statistics.median(lat[q["id"]]["default"])
        row = {"id": q["id"], "modes": {}}
        for m in modes:
            med = statistics.median(lat[q["id"]][m])
            p = 1.0 if m == "default" else permutation_p(lat[q["id"]][m], lat[q["id"]]["default"])
            ratio = med / base
            outcome = ("win" if ratio < 1 - BAND and p < ALPHA else
                       "loss" if ratio > 1 + BAND and p < ALPHA else "same")
            row["modes"][m] = {"median_ms": round(med, 2), "speedup": round(base / med, 3), "p": round(p, 4),
                               "outcome": outcome, "runs_ms": lat[q["id"]][m],
                               "serial_runs": opts[q["id"]][m].count("serial")}
        per_query.append(row)
    for m in modes:
        sp = [r["modes"][m]["speedup"] for r in per_query]
        oc = [r["modes"][m]["outcome"] for r in per_query]
        summary[m] = {"geomean_speedup": round(math.exp(statistics.mean(map(math.log, sp))), 3),
                      "total_ms": round(sum(r["modes"][m]["median_ms"] for r in per_query), 1),
                      "wins": oc.count("win"), "losses": oc.count("loss")}
    return {"external_cpu_median": round(statistics.median(ext), 1), "summary": summary, "per_query": per_query}


def print_level(name, res):
    print(f"\n  [{name}] median external CPU {res['external_cpu_median']}%")
    print("  " + "Query".ljust(6) + "".join(m.rjust(20) for m in MODES))
    mark = {"win": "+", "loss": "!", "same": " "}
    for r in res["per_query"]:
        cells = "".join(f"{d['median_ms']:9.1f} ({d['speedup']:.2f}x){mark[d['outcome']]}".rjust(20)
                        for d in (r["modes"][m] for m in MODES))
        print("  " + r["id"].ljust(6) + cells)
    for m in MODES:
        s = res["summary"][m]
        print(f"  {m:9s} geomean {s['geomean_speedup']:.2f}x  total {s['total_ms']:8.1f} ms  "
              f"wins {s['wins']:2d}  losses {s['losses']:2d}")
    print("  + significantly faster than default, ! significantly slower (>10% and p<0.05)")


def plot(report, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    levels = report["levels"]
    shown = [m for m in MODES if m != "default"]
    colors = {"aa": "#95a5a6", "rule": "#e67e22", "adaptive": "#2e86de"}
    fig, axes = plt.subplots(len(levels), 1, figsize=(13, 4 * len(levels)), squeeze=False)
    for ax, (name, res) in zip(axes[:, 0], levels.items()):
        ids = [r["id"] for r in res["per_query"]]
        w = 0.8 / len(shown)
        for i, m in enumerate(shown):
            xs = [k + (i - (len(shown) - 1) / 2) * w for k in range(len(ids))]
            ax.bar(xs, [r["modes"][m]["speedup"] for r in res["per_query"]], w, color=colors[m],
                   label=f"{m} (geomean {res['summary'][m]['geomean_speedup']:.2f}x)")
        ax.axhline(1, color="black", lw=1)
        ax.axhspan(1 / 1.1, 1.1, color="grey", alpha=0.15)
        ax.set_yscale("log", base=2)
        ax.set_xticks(range(len(ids)))
        ax.set_xticklabels(ids)
        ax.set_ylabel("speedup vs default")
        ax.set_title(f"{name} load (external CPU {res['external_cpu_median']}%), "
                     f"median of {report['rounds']} interleaved runs")
        ax.legend(loc="upper left", bbox_to_anchor=(1.005, 1.0), fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _acquire_lock():
    """Two benchmarks at once corrupt each other (one's stress phase becomes the other's load)."""
    import os
    import psutil
    REPORTS_DIR.mkdir(exist_ok=True)
    lock = REPORTS_DIR / ".benchmark.lock"
    if lock.exists():
        try:
            other = int(lock.read_text())
        except ValueError:
            other = -1
        if other != os.getpid() and psutil.pid_exists(other):
            raise SystemExit(f"Another benchmark is running (pid {other}). Wait for it or stop it first.")
    lock.write_text(str(os.getpid()))
    return lock


def run(rounds: int = 5, levels=("normal", "stressed"), duty: float = 0.9, seed: int = 7):
    lock = _acquire_lock()
    try:
        return _run(rounds, levels, duty, seed)
    finally:
        lock.unlink(missing_ok=True)


def _run(rounds, levels, duty, seed):
    from src.optimizer import Optimizer
    from experiments.stress import StressGenerator

    opt = Optimizer()
    queries = load_queries()
    rng = random.Random(seed)
    print(f"Benchmark: {len(queries)} queries, modes {MODES}, {rounds} rounds, levels {list(levels)}")
    for q in queries:
        opt.execute(q["sql"], mode="default", label=q["id"], learn=False)  # warm-up + plan probe

    report = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "rounds": rounds, "levels": {}}
    stress = StressGenerator()
    try:
        for level in levels:
            if level == "stressed":
                stress.start_stress(duty_cycle=duty)
            else:
                stress.stop_stress()
            time.sleep(3)
            print(f"\n== {level} load: {opt.telemetry.snapshot()} ==")
            n = learn(opt, queries)
            print(f"  learning runs: {n}")
            res = evaluate(opt, queries, rounds, rng)
            res["learning_runs"] = n
            report["levels"][level] = res
            print_level(level, res)
    finally:
        stress.stop_stress()

    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "benchmark.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    plot(report, REPORTS_DIR / "speedup_by_query.png")
    print(f"\nSaved reports/benchmark.json and reports/speedup_by_query.png")
    return report


if __name__ == "__main__":
    run()
