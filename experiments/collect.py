"""
Data collection ("oracle" experiment): run every query instance under every hint set, at each
load level, and record the measured latency.

    python main.py collect --per-template 8 --levels idle,stress

Output (append-only, resumable):
  results/plans.jsonl  EXPLAIN plan of each (query, hint set)
  results/runs.jsonl   one record per execution

Rules that keep the data honest and the run time bounded:
  * warm-up: one untimed default run per query before measuring (data in cache)
  * the default plan is measured first; other hint sets get a timeout of max(3 x default, default + 2 s)
    -- a timed-out run is recorded with timeout=true and latency = the timeout (a lower bound)
  * hint sets whose plan is identical to the default plan (and that change no memory setting)
    are not re-executed; they reuse the default measurement and are flagged inferred=true
  * hint sets run in random order per query, so slow drifts in load affect all of them alike
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qs.config import RESULTS_DIR
from qs.db import Database, QueryTimeout
from qs.hints import DEFAULT_ID, HINT_SETS
from qs.workload import instances

PLANS = RESULTS_DIR / "plans.jsonl"
RUNS = RESULTS_DIR / "runs.jsonl"


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _append(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def ensure_plans(db, queries):
    """EXPLAIN every (query, hint set) once; plans do not depend on load."""
    have = {(p["qid"], p["hint"]) for p in _read_jsonl(PLANS)}
    n = 0
    for q in queries:
        for h in HINT_SETS:
            if (q["id"], h["id"]) in have:
                continue
            plan = db.explain(q["sql"], h["settings"])
            _append(PLANS, {"qid": q["id"], "template": q["template"], "hint": h["id"], "plan": plan})
            n += 1
    if n:
        print(f"[collect] explained {n} (query, hint) pairs", flush=True)
    return {(p["qid"], p["hint"]): p["plan"] for p in _read_jsonl(PLANS)}


def collect(per_template=8, levels=("idle", "stress"), seed=42, stress_duty=0.9, templates=None,
            timeout_factor=3.0, hint_ids=None):
    from qs.stress import StressGenerator
    from qs.telemetry import Telemetry

    RESULTS_DIR.mkdir(exist_ok=True)
    db = Database()
    tel = Telemetry()
    tel.start()
    queries = instances(per_template, seed, templates)
    plans = ensure_plans(db, queries)
    done = {(r["qid"], r["hint"], r["level"]) for r in _read_jsonl(RUNS)}
    rng = random.Random(seed)
    stress = StressGenerator()
    t_start = time.time()
    try:
        for level in levels:
            if level == "stress":
                stress.start_stress(duty_cycle=stress_duty)
            else:
                stress.stop_stress()
            time.sleep(4)
            todo = [q for q in queries if any((q["id"], h["id"], level) not in done for h in HINT_SETS)]
            rng.shuffle(todo)
            print(f"[collect] level={level}: {len(todo)} queries to measure; load={tel.snapshot()}", flush=True)
            for i, q in enumerate(todo, 1):
                db.run(q["sql"], {}, timeout_ms=600000)  # warm-up
                load = tel.snapshot()
                d_ms, d_rows = db.run(q["sql"], {}, timeout_ms=600000)
                rec = {"qid": q["id"], "template": q["template"], "level": level, "load": load, "ts": time.time()}
                _append(RUNS, {**rec, "hint": DEFAULT_ID, "ms": d_ms, "rows": d_rows, "timeout": False, "inferred": False})
                timeout = max(timeout_factor * d_ms, d_ms + 2000)
                others = [h for h in HINT_SETS if h["id"] != DEFAULT_ID and (hint_ids is None or h["id"] in hint_ids)]
                rng.shuffle(others)
                for h in others:
                    if (q["id"], h["id"], level) in done:
                        continue
                    same = plans[(q["id"], h["id"])] == plans[(q["id"], DEFAULT_ID)]
                    if same and "work_mem" not in h["settings"]:
                        _append(RUNS, {**rec, "hint": h["id"], "ms": d_ms, "rows": d_rows, "timeout": False,
                                       "inferred": True})
                        continue
                    load = tel.snapshot()
                    try:
                        ms, rows = db.run(q["sql"], h["settings"], timeout_ms=timeout)
                        to = False
                    except QueryTimeout:
                        ms, rows, to = timeout, -1, True
                    _append(RUNS, {**rec, "load": load, "ts": time.time(), "hint": h["id"], "ms": ms, "rows": rows, "timeout": to,
                                   "inferred": False})
                if i % 10 == 0 or i == len(todo):
                    el = time.time() - t_start
                    print(f"[collect] {level}: {i}/{len(todo)} queries done ({el / 60:.1f} min elapsed)", flush=True)
    finally:
        stress.stop_stress()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-template", type=int, default=8)
    ap.add_argument("--levels", default="idle,stress")
    ap.add_argument("--templates", default="")
    a = ap.parse_args()
    collect(a.per_template, a.levels.split(","), templates=[t for t in a.templates.split(",") if t] or None)
