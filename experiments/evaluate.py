"""
Offline evaluation of the learned optimizer on held-out queries, scored with measured latencies.

    python main.py evaluate

Two generalisation tests:
  instances : train on parameter instances 0..5 of every template, test on unseen instances 6..
  templates : 5-fold cross-validation over TEMPLATES -- the test templates were never seen in training

Strategies compared on the same test queries:
  default      PostgreSQL's own plan
  rule         static OS rule: serial plan when load is high, else default
  best_fixed   the single hint set with the lowest total time on the TRAINING queries
  model        QuerySync's learned choice (ensemble mean + k*std, minimum predicted gain)
  oracle       the measured-fastest hint set per query (upper bound, not achievable)
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qs.config import MIN_PREDICTED_GAIN, RESULTS_DIR
from qs.hints import BY_ID, DEFAULT_ID, HINT_SETS
from qs.model import LatencyModel, PlanModel, build_dataset, choose, read_collected

HINT_IDS = [h["id"] for h in HINT_SETS]


def _queries(meta):
    """(level, qid) -> {hint: row index}; only queries measured under every hint set."""
    q = defaultdict(dict)
    for i, m in enumerate(meta):
        q[(m["level"], m["qid"])][m["hint"]] = i
    return {k: v for k, v in q.items() if len(v) == len(HINT_IDS)}


def _score(test_keys, queries, meta, chosen):
    """Totals and safety statistics for a mapping (level, qid) -> chosen hint id."""
    tot = d_tot = 0.0
    logs, regress, worst = [], 0, 1.0
    for key in test_keys:
        idx = queries[key]
        d_ms = meta[idx[DEFAULT_ID]]["ms"]
        ms = meta[idx[chosen[key]]]["ms"]
        tot += ms
        d_tot += d_ms
        logs.append(math.log(d_ms / ms))
        if ms > 1.2 * d_ms:
            regress += 1
        worst = max(worst, ms / d_ms)
    return {"total_s": tot / 1000, "default_s": d_tot / 1000, "reduction_pct": 100 * (1 - tot / d_tot),
            "geomean_speedup": math.exp(np.mean(logs)), "regressions": regress, "worst_slowdown": worst,
            "n": len(test_keys)}


def run_split(X, y, meta, queries, train_keys, test_keys, k, min_gain):
    train_rows = [i for key in train_keys for i in queries[key].values()]
    model = PlanModel().fit(X[train_rows], y[train_rows])
    chosen = {}
    strategies = defaultdict(dict)
    # best fixed hint, chosen on training queries only
    fixed_tot = {h: sum(meta[queries[key][h]]["ms"] for key in train_keys) for h in HINT_IDS}
    best_fixed = min(fixed_tot, key=fixed_tot.get)
    for key in test_keys:
        idx = queries[key]
        rows = [idx[h] for h in HINT_IDS]
        mean, std = model.predict(X[rows])
        chosen[key], _ = choose(mean, std, HINT_IDS, min_gain, k)
        strategies["default"][key] = DEFAULT_ID
        strategies["rule"][key] = 1 if key[0] == "stress" else DEFAULT_ID
        strategies["best_fixed"][key] = best_fixed
        strategies["oracle"][key] = min(idx, key=lambda h: meta[idx[h]]["ms"])
    strategies["model"] = chosen
    return {name: _score(test_keys, queries, meta, ch) for name, ch in strategies.items()}, model, best_fixed, chosen


def evaluate(k=0.5, min_gain=MIN_PREDICTED_GAIN, holdout_from=6, folds=5, verbose=True):
    runs, plans = read_collected()
    X, y, meta = build_dataset(runs, plans)
    queries = _queries(meta)
    levels = sorted({lv for lv, _ in queries})
    report = {"k": k, "min_gain": min_gain, "splits": {}}

    # ---- split 1: unseen parameter instances of known templates
    inst = lambda qid: int(qid.split("-")[1])
    train = [key for key in queries if inst(key[1]) < holdout_from]
    test = [key for key in queries if inst(key[1]) >= holdout_from]
    res, model, best_fixed, chosen = run_split(X, y, meta, queries, train, test, k, min_gain)
    report["splits"]["instances"] = {"overall": res, "best_fixed": BY_ID[best_fixed]["name"],
                                     "by_level": {}, "choices": defaultdict(int)}
    for lv in levels:
        tk = [key for key in test if key[0] == lv]
        report["splits"]["instances"]["by_level"][lv] = {
            name: _score(tk, queries, meta, {key: ch_map[key] for key in tk}) for name, ch_map in
            [("default", {key: DEFAULT_ID for key in tk}), ("rule", {key: (1 if lv == "stress" else 0) for key in tk}),
             ("best_fixed", {key: best_fixed for key in tk}), ("model", chosen),
             ("oracle", {key: min(queries[key], key=lambda h: meta[queries[key][h]]["ms"]) for key in tk})]}
    for key in test:
        report["splits"]["instances"]["choices"][BY_ID[chosen[key]]["name"]] += 1
    report["feature_importance"] = model.feature_importance()

    # ---- latency ("burst time") model used by the scheduler, same unseen-instance split
    from scipy.stats import spearmanr
    rows, log_ms = LatencyModel.training_rows(X, meta)
    live = lambda i: meta[i]["qid"].startswith("live")
    tr = [j for j, i in enumerate(rows) if not live(i) and inst(meta[i]["qid"]) < holdout_from]
    te = [j for j, i in enumerate(rows) if not live(i) and inst(meta[i]["qid"]) >= holdout_from]
    Xr = X[rows]
    pred = np.log(LatencyModel().fit(Xr[tr], log_ms[tr]).predict_ms(Xr[te]))
    err = np.abs(np.exp(pred - log_ms[te]) - 1)
    report["latency_model"] = {"test_plans": len(te), "median_abs_error_pct": round(float(np.median(err)) * 100, 1),
                               "within_2x_pct": round(float(np.mean(np.abs(pred - log_ms[te]) < np.log(2))) * 100, 1),
                               "rank_correlation": round(float(spearmanr(pred, log_ms[te]).correlation), 3)}

    # ---- split 2: unseen templates (k-fold over templates)
    templates = sorted({meta[next(iter(v.values()))]["template"] for v in queries.values()})
    rng = np.random.default_rng(1)
    order = list(rng.permutation(templates))
    fold_of = {t: i % folds for i, t in enumerate(order)}
    agg = defaultdict(lambda: defaultdict(float))
    tmpl_of = {key: meta[next(iter(v.values()))]["template"] for key, v in queries.items()}
    all_choices = {}
    for f in range(folds):
        test_k = [key for key in queries if fold_of[tmpl_of[key]] == f]
        train_k = [key for key in queries if fold_of[tmpl_of[key]] != f]
        if not test_k or not train_k:
            continue
        res, _, _, ch = run_split(X, y, meta, queries, train_k, test_k, k, min_gain)
        all_choices.update(ch)
    strategies = {
        "default": {key: DEFAULT_ID for key in queries},
        "rule": {key: (1 if key[0] == "stress" else 0) for key in queries},
        "model": all_choices,
        "oracle": {key: min(queries[key], key=lambda h: meta[queries[key][h]]["ms"]) for key in queries},
    }
    report["splits"]["templates"] = {"overall": {n: _score(list(queries), queries, meta, c) for n, c in strategies.items()},
                                     "by_level": {lv: {n: _score([q for q in queries if q[0] == lv], queries, meta,
                                                                 c) for n, c in strategies.items()} for lv in levels}}
    if verbose:
        print_report(report)
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "evaluation.json").write_text(json.dumps(report, indent=1, default=dict), encoding="utf-8")
    return report


def print_report(rep):
    print(f"\nselection rule: mean + {rep['k']} x std must promise >= {rep['min_gain']:.0%} gain\n")
    for split, title in (("instances", "UNSEEN PARAMETER INSTANCES"), ("templates", "UNSEEN TEMPLATES (5-fold)")):
        s = rep["splits"][split]
        print(f"=== {title} ===")
        for lv, res in s["by_level"].items():
            n = next(iter(res.values()))["n"]
            print(f"  load={lv} ({n} queries)")
            for name, r in res.items():
                print(f"    {name:10s} total {r['total_s']:7.1f} s  ({r['reduction_pct']:+6.1f}% vs default)  "
                      f"geomean {r['geomean_speedup']:.2f}x  regressions(>1.2x) {r['regressions']:2d}  "
                      f"worst {r['worst_slowdown']:.2f}x")
        if "best_fixed" in s:
            print(f"  best fixed hint on training data: {s['best_fixed']}")
            print("  model choices:", dict(s["choices"]))
        print()
    print("top features:", ", ".join(f"{n} {v:.2f}" for n, v in rep["feature_importance"][:8]))
    lm = rep.get("latency_model")
    if lm:
        print(f"latency (burst-time) model on unseen instances: median error {lm['median_abs_error_pct']}%, "
              f"within 2x {lm['within_2x_pct']}%, rank correlation {lm['rank_correlation']} ({lm['test_plans']} plans)")


if __name__ == "__main__":
    evaluate()
