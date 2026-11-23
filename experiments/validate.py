"""
Live validation: re-run held-out queries for real, interleaved, comparing strategies.

    python main.py validate --rounds 5

The model is trained ONLY on parameter instances 0..5 of each template; the test queries are the
unseen instances 6.. . For each load level, every round runs every test query once under each
strategy, in random order:
  default      PostgreSQL's own plan
  aa           PostgreSQL's own plan again (A/A control: shows pure noise)
  rule         serial plan under high load, else default
  ai           QuerySync's live decision (EXPLAIN all hint sets -> model -> choice); the reported
               latency INCLUDES the decision time (EXPLAINs + prediction)
  ai_os        ai + OS layer: under high external load PostgreSQL's processes run at HIGH priority
Per query: median of the rounds; a win/loss needs >10% difference AND permutation-test p < 0.05.
Writes results/validation.json and results/validation.png.
"""

import json
import math
import random
import statistics
import sys
import time
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qs.config import RESULTS_DIR
from qs.hints import DEFAULT_ID
from qs.model import LatencyModel, PlanModel, build_dataset, read_collected
from qs.workload import instances

MODES = ["default", "aa", "rule", "ai", "ai_os"]
BAND, ALPHA = 0.10, 0.05


def permutation_p(a, b):
    la, lb = [math.log(x) for x in a], [math.log(x) for x in b]
    pooled, n = la + lb, len(la)
    total = sum(pooled)
    obs = abs(sum(la) / n - sum(lb) / len(lb))
    splits = list(combinations(range(len(pooled)), n))
    hits = sum(abs(sum(pooled[i] for i in s) / n - (total - sum(pooled[i] for i in s)) / (len(pooled) - n)) >= obs - 1e-12
               for s in splits)
    return hits / len(splits)


def train_holdout_model(holdout_from):
    runs, plans = read_collected()
    runs = [r for r in runs if not r["qid"].startswith("live")]
    X, y, meta = build_dataset(runs, plans)
    rows = [i for i, m in enumerate(meta) if int(m["qid"].split("-")[1]) < holdout_from]
    lat_rows, log_ms = LatencyModel.training_rows(X, meta)
    keep = [k for k, i in enumerate(lat_rows) if int(meta[i]["qid"].split("-")[1]) < holdout_from]
    latency = LatencyModel().fit(X[lat_rows][keep], log_ms[keep])
    return PlanModel().fit(X[rows], y[rows]), latency


def validate(rounds=5, levels=("idle", "stress"), per_template=8, holdout_from=6, seed=11, stress_duty=0.9):
    from qs.optimizer import Optimizer
    from qs.stress import StressGenerator

    model, latency = train_holdout_model(holdout_from)
    opt = Optimizer(model=model, log_runs=False)
    opt.latency = latency   # held-out latency model too (short-query bypass), no test query seen in training
    test = [q for q in instances(per_template) if int(q["id"].split("-")[1]) >= holdout_from]
    rng = random.Random(seed)
    stress = StressGenerator()
    report = {"rounds": rounds, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "levels": {}}
    try:
        for level in levels:
            stress.start_stress(duty_cycle=stress_duty) if level == "stress" else stress.stop_stress()
            time.sleep(4)
            for q in test:  # warm-up
                opt.db.run(q["sql"], {})
            lat = {q["id"]: {m: [] for m in MODES} for q in test}
            choice = {q["id"]: [] for q in test}
            ext = []
            for rd in range(rounds):
                for q in test:
                    order = MODES[:]
                    rng.shuffle(order)
                    for m in order:
                        mode = {"aa": "default", "ai_os": "ai"}.get(m, m)
                        r = opt.execute(q["sql"], mode=mode, learn=False, os_boost="auto" if m == "ai_os" else "off")
                        lat[q["id"]][m].append(r["latency_ms"] + (r["overhead_ms"] if mode == "ai" else 0.0))
                        ext.append(r["load"]["external_cpu"])
                        if m == "ai":
                            choice[q["id"]].append(r["hint_name"])
                print(f"[validate] {level}: round {rd + 1}/{rounds}", flush=True)
            report["levels"][level] = summarize(test, lat, choice, ext)
            print_level(level, report["levels"][level])
    finally:
        stress.stop_stress()
        from qs.optimizer import set_boost
        set_boost(False)
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "validation.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    plot(report, RESULTS_DIR / "validation.png")
    return report


def summarize(test, lat, choice, ext):
    per_query, summary = [], {}
    for q in test:
        base = statistics.median(lat[q["id"]]["default"])
        row = {"id": q["id"], "template": q["template"], "modes": {}, "ai_choice": choice[q["id"]]}
        for m in MODES:
            med = statistics.median(lat[q["id"]][m])
            p = 1.0 if m == "default" else permutation_p(lat[q["id"]][m], lat[q["id"]]["default"])
            ratio = med / base
            row["modes"][m] = {"median_ms": round(med, 1), "speedup": round(base / med, 3), "p": round(p, 4),
                               "outcome": "win" if ratio < 1 - BAND and p < ALPHA else
                               "loss" if ratio > 1 + BAND and p < ALPHA else "same",
                               "runs_ms": [round(x, 1) for x in lat[q["id"]][m]]}
        per_query.append(row)
    for m in MODES:
        sp = [r["modes"][m]["speedup"] for r in per_query]
        oc = [r["modes"][m]["outcome"] for r in per_query]
        summary[m] = {"total_s": round(sum(r["modes"][m]["median_ms"] for r in per_query) / 1000, 2),
                      "geomean": round(math.exp(statistics.mean(map(math.log, sp))), 3),
                      "wins": oc.count("win"), "losses": oc.count("loss")}
    return {"external_cpu_median": round(statistics.median(ext), 1), "summary": summary, "per_query": per_query}


def print_level(level, res):
    s = res["summary"]
    d = s["default"]["total_s"]
    print(f"\n[validate] level={level} (external CPU {res['external_cpu_median']}%), {len(res['per_query'])} held-out queries")
    for m in MODES:
        print(f"   {m:8s} total {s[m]['total_s']:7.2f} s  ({(s[m]['total_s'] / d - 1) * 100:+6.1f}%)  "
              f"geomean {s[m]['geomean']:.2f}x  wins {s[m]['wins']:2d}  losses {s[m]['losses']:2d}")


def plot(report, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    levels = report["levels"]
    fig, axes = plt.subplots(len(levels), 1, figsize=(14, 4.2 * len(levels)), squeeze=False)
    colors = {"aa": "#9aa0a6", "rule": "#e0a020", "ai": "#2f81f7", "ai_os": "#3fb950"}
    for ax, (lv, res) in zip(axes[:, 0], levels.items()):
        ids = [r["id"] for r in res["per_query"]]
        w = 0.2
        for i, m in enumerate(["aa", "rule", "ai", "ai_os"]):
            xs = [k + (i - 1.5) * w for k in range(len(ids))]
            ax.bar(xs, [r["modes"][m]["speedup"] for r in res["per_query"]], w, color=colors[m],
                   label=f"{m} (geomean {res['summary'][m]['geomean']:.2f}x)")
        ax.axhline(1, color="black", lw=1)
        ax.axhspan(1 / 1.1, 1.1, color="grey", alpha=0.15)
        ax.set_yscale("log", base=2)
        ax.set_xticks(range(len(ids)))
        ax.set_xticklabels(ids, rotation=90, fontsize=7)
        ax.set_ylabel("speedup vs default")
        ax.set_title(f"{lv} load (external CPU {res['external_cpu_median']}%), held-out queries, "
                     f"median of {report['rounds']} interleaved runs")
        ax.legend(loc="upper left", bbox_to_anchor=(1.005, 1.0), fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    validate()
