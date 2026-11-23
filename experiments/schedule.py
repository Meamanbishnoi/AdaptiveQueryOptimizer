"""
OS-layer experiments (all on the real PostgreSQL server, real time):

  python main.py schedule              runs all three, writes results/scheduling.json + scheduling.png
  python main.py schedule --part mpl   (or: policies, priority)

1. Degree of multiprogramming / thrashing (--part mpl)
   A batch of 30 queries is submitted at once and executed with at most MPL queries running at the same
   time, for MPL = 1, 2, 3, 4, 6, 8, plus plain PostgreSQL (default plans, everything at once).
   Throughput (queries/min) vs MPL shows where adding concurrency stops helping (CPU contention);
   the best MPL per load level is saved to models/mpl.json and used by the scheduler's "auto" MPL.

2. Scheduling policies (--part policies)
   A multi-user Poisson arrival trace (60 queries, 60% interactive) is replayed against:
     postgres     plain PostgreSQL: default plans, every query starts immediately (no scheduler)
     ai_only      AI plans, no scheduler (no MPL limit, no memory admission, no OS boost)
     fcfs / sjf / hrrn / priority   AI plans + QuerySync scheduler (auto MPL + express slot for short
                                    queries, memory admission, OS priority boost under high load)
   The arrival rate is 85% of plain PostgreSQL's measured capacity at that load level (stage 1), so
   the baseline is busy but stable. Same trace for every strategy; strategy order is shuffled.
   Metrics: average / p95 turnaround, waiting time, interactive-query turnaround, max wait (starvation).

3. OS process priority (--part priority)
   22 held-out queries with AI plans, run interleaved with PostgreSQL's processes at NORMAL vs HIGH
   priority (and HIGH pinned to 2 cores, CPU affinity), idle and under CPU stress.
"""

import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qs.config import MODELS_DIR, RESULTS_DIR
from qs.workload import instances, make_trace

OUT = RESULTS_DIR / "scheduling.json"
MPLS = (1, 2, 3, 4, 6, 8)
STRATEGIES = {
    "postgres": dict(policy="fcfs", mpl=32, plan_mode="default", os_boost="off", memory_budget_mb=1e9),
    "ai_only": dict(policy="fcfs", mpl=32, plan_mode="ai", os_boost="off", memory_budget_mb=1e9),
    "fcfs": dict(policy="fcfs", mpl="auto", plan_mode="ai", os_boost="auto"),
    "sjf": dict(policy="sjf", mpl="auto", plan_mode="ai", os_boost="auto"),
    "hrrn": dict(policy="hrrn", mpl="auto", plan_mode="ai", os_boost="auto"),
    "priority": dict(policy="priority", mpl="auto", plan_mode="ai", os_boost="auto"),
}


def _load():
    return json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}


def _save(rep):
    RESULTS_DIR.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")


def run_trace(trace, opt, progress=None, **strategy):
    """Replays a trace (arrival offsets in seconds) through a QueryScheduler; returns (jobs, scheduler)."""
    from qs.scheduler import QueryScheduler
    s = QueryScheduler(optimizer=opt, **strategy).start()
    t_start = time.perf_counter()
    for k, item in enumerate(trace):
        delay = t_start + item["at"] - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        s.submit(item["sql"], item["template"], item["cls"])
    n = len(trace)
    while True:
        jobs = s.wait(n, timeout=1.0)
        if progress:
            progress(len(jobs), n)
        if len(jobs) >= n:
            break
    s.stop()
    return jobs, s


def _level_ctx(level, stress):
    """Sets the load level; returns the measured external CPU % (other programs)."""
    from qs.telemetry import shared_telemetry
    if level == "stress":
        stress.start_stress(duty_cycle=0.9)
    else:
        stress.stop_stress()
    time.sleep(5)
    return shared_telemetry().snapshot()["external_cpu"]


def mpl_sweep(opt, levels, stress, n_jobs=30):
    from qs.scheduler import metrics
    batch = [dict(j, at=0.0) for j in make_trace(n_jobs, 1.0, seed=500)]
    out, best = {}, {}
    for level in levels:
        ext = _level_ctx(level, stress)
        rows = {}
        configs = [("postgres", STRATEGIES["postgres"])] + \
                  [(str(m), dict(policy="fcfs", mpl=m, plan_mode="ai", os_boost="auto")) for m in MPLS]
        for name, cfg in configs:
            jobs, _ = run_trace(batch, opt, **cfg)
            m = metrics(jobs)
            rows[name] = m
            print(f"[mpl] {level}: {name:8s} makespan {m['makespan_s']:6.1f} s  throughput {m['throughput_per_min']:5.1f}/min  "
                  f"avg turnaround {m['avg_turnaround_s']:5.1f} s", flush=True)
        ai = {int(k): v for k, v in rows.items() if k != "postgres"}
        top = max(v["throughput_per_min"] for v in ai.values())
        best_mpl = min(k for k, v in ai.items() if v["throughput_per_min"] >= 0.97 * top)  # smallest MPL within 3%
        best["high" if level == "stress" else "low"] = best_mpl
        out[level] = {"rows": rows, "best_mpl": best_mpl, "external_cpu": ext}
        print(f"[mpl] {level}: best MPL = {best_mpl}", flush=True)
    MODELS_DIR.mkdir(exist_ok=True)
    table = json.loads((MODELS_DIR / "mpl.json").read_text()) if (MODELS_DIR / "mpl.json").exists() else {}
    table.update(best)
    (MODELS_DIR / "mpl.json").write_text(json.dumps(table), encoding="utf-8")
    return out


def compare_policies(opt, levels, stress, rep, seeds=(1, 2, 3), n_jobs=60, load_factor=0.85):
    from qs.scheduler import metrics
    out = {}
    for level in levels:
        cap = rep["mpl"][level]["rows"]["postgres"]["throughput_per_min"] / 60.0
        rate = round(load_factor * cap, 3)
        ext = _level_ctx(level, stress)
        runs = {k: [] for k in STRATEGIES}
        gantt = {}
        for seed in seeds:
            trace = make_trace(n_jobs, rate, seed=seed)
            order = list(STRATEGIES)
            random.Random(seed).shuffle(order)
            for name in order:
                jobs, s = run_trace(trace, opt, **STRATEGIES[name])
                m = metrics(jobs)
                m["memory_waits"] = s.memory_waits
                runs[name].append(m)
                if seed == seeds[0]:
                    gantt[name] = [j.as_dict() for j in sorted(jobs, key=lambda j: j.id)]
                print(f"[policy] {level} seed {seed}: {name:8s} avg turnaround {m['avg_turnaround_s']:6.2f} s  "
                      f"p95 {m['p95_turnaround_s']:6.2f}  interactive {m['avg_interactive_s']:5.2f}  "
                      f"max wait {m['max_wait_s']:6.2f}", flush=True)
        summary = {}
        for name, ms in runs.items():
            summary[name] = {k: round(statistics.mean(m[k] for m in ms), 3) for k in ms[0]
                             if isinstance(ms[0][k], (int, float))}
        out[level] = {"external_cpu": ext, "rate_per_s": rate, "seeds": list(seeds), "n_jobs": n_jobs, "summary": summary,
                      "runs": runs, "gantt": gantt}
    return out


def os_priority(opt, levels, stress, rounds=None):
    from qs.osctl import PriorityBooster
    from qs import osctl
    from qs.optimizer import set_boost
    from qs.hints import BY_ID
    test = [q for q in instances(8) if q["id"].endswith("-06")]
    out = {}
    normal_booster = osctl.shared_booster()
    pinned_booster = PriorityBooster(cores=[0, 1])
    for level in levels:
        ext = _level_ctx(level, stress)
        configs = ["normal", "high"] + (["high_pinned_2_cores"] if level == "stress" else [])
        n_rounds = rounds or (3 if level == "stress" else 2)
        plans = {q["id"]: opt.decide(q["sql"])["hint"] for q in test}   # fixed AI plan per query
        lat = {q["id"]: {c: [] for c in configs} for q in test}
        rng = random.Random(3)
        for _ in range(n_rounds):
            for q in test:
                order = configs[:]
                rng.shuffle(order)
                for c in order:
                    osctl._shared = pinned_booster if c == "high_pinned_2_cores" else normal_booster
                    set_boost(c != "normal")
                    ms, _ = opt.db.run(q["sql"], BY_ID[plans[q["id"]]]["settings"])
                    lat[q["id"]][c].append(ms)
                    set_boost(False)
        osctl._shared = normal_booster
        per_query = []
        for q in test:
            med = {c: statistics.median(v) for c, v in lat[q["id"]].items()}
            per_query.append({"id": q["id"], "template": q["template"], "hint": BY_ID[plans[q["id"]]]["name"],
                              "median_ms": {c: round(v, 1) for c, v in med.items()},
                              "speedup": {c: round(med["normal"] / v, 3) for c, v in med.items()}})
        totals = {c: round(sum(p["median_ms"][c] for p in per_query) / 1000, 2) for c in configs}
        out[level] = {"external_cpu": ext, "rounds": n_rounds, "totals_s": totals, "per_query": per_query,
                      "change_pct": {c: round((totals[c] / totals["normal"] - 1) * 100, 1) for c in configs}}
        print(f"[priority] {level}: " + ", ".join(f"{c} {totals[c]} s ({out[level]['change_pct'][c]:+.1f}%)"
                                                  for c in configs), flush=True)
    return out


def plot(rep, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 2, figsize=(15, 9))
    a = ax[0, 0]
    for level, color in (("idle", "#2f81f7"), ("stress", "#e0a020")):
        if level in rep.get("mpl", {}):
            rows = rep["mpl"][level]["rows"]
            a.plot(MPLS, [rows[str(m)]["throughput_per_min"] for m in MPLS], "o-", color=color, label=f"QuerySync ({level})")
            a.axhline(rows["postgres"]["throughput_per_min"], color=color, ls="--", lw=1,
                      label=f"plain PostgreSQL, all at once ({level})")
    a.set_xlabel("degree of multiprogramming (queries running at once)")
    a.set_ylabel("throughput (queries / min)")
    a.set_title("1. Multiprogramming: throughput vs MPL")
    a.legend(fontsize=8)
    for col, (metric, title) in enumerate((("avg_turnaround_s", "2. Average turnaround time (s)"),
                                            ("avg_interactive_s", "3. Interactive queries: avg turnaround (s)"))):
        a = ax[0, 1] if col == 0 else ax[1, 0]
        names = list(STRATEGIES)
        w = 0.38
        for i, (level, color) in enumerate((("idle", "#2f81f7"), ("stress", "#e0a020"))):
            if level in rep.get("policies", {}):
                s = rep["policies"][level]["summary"]
                a.bar([k + (i - 0.5) * w for k in range(len(names))], [s[n][metric] for n in names], w,
                      color=color, label=level)
        a.set_xticks(range(len(names)))
        a.set_xticklabels(names)
        a.set_title(title)
        a.legend(fontsize=8)
    a = ax[1, 1]
    if "priority" in rep:
        levels = list(rep["priority"])
        configs = ["normal", "high", "high_pinned_2_cores"]
        w = 0.27
        for i, c in enumerate(configs):
            vals = [rep["priority"][lv]["totals_s"].get(c, 0) for lv in levels]
            a.bar([k + (i - 1) * w for k in range(len(levels))], vals, w, label=c)
        a.set_xticks(range(len(levels)))
        a.set_xticklabels(levels)
        a.set_ylabel("total time, 22 queries (s)")
        a.set_title("4. OS process priority of PostgreSQL")
        a.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def run(parts=("mpl", "policies", "priority"), levels=("idle", "stress")):
    from qs.optimizer import Optimizer, set_boost
    from qs.stress import StressGenerator
    rep = _load()
    opt = Optimizer(log_runs=False)
    stress = StressGenerator()
    try:
        if "mpl" in parts:
            rep["mpl"] = {**rep.get("mpl", {}), **mpl_sweep(opt, levels, stress)}
            _save(rep)
        if "policies" in parts:
            rep["policies"] = {**rep.get("policies", {}), **compare_policies(opt, levels, stress, rep)}
            _save(rep)
        if "priority" in parts:
            rep["priority"] = {**rep.get("priority", {}), **os_priority(opt, levels, stress)}
            _save(rep)
    finally:
        stress.stop_stress()
        set_boost(False)
    rep["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(rep)
    plot(rep, RESULTS_DIR / "scheduling.png")
    return rep


if __name__ == "__main__":
    run()
