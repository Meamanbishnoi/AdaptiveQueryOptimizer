"""
Headroom analysis of the collected runs: how much faster could queries be with the best hint set?

    python main.py headroom

For each load level:
  default  : total time of PostgreSQL's default plans
  oracle   : total time if, for every query, the measured-fastest hint set were used (upper bound
             for any hint-choosing optimizer, including ours)
  best fixed hint : the single hint set with the lowest total time over all queries (what a DBA
             could do with one global setting -- the baseline an AI model must beat)
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qs.config import RESULTS_DIR
from qs.hints import BY_ID, DEFAULT_ID, HINT_SETS


def load_runs():
    path = RESULTS_DIR / "runs.jsonl"
    runs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    table = defaultdict(dict)   # (level, qid) -> {hint: ms}
    tmpl = {}
    for r in runs:
        table[(r["level"], r["qid"])][r["hint"]] = r["ms"]
        tmpl[r["qid"]] = r["template"]
    complete = {k: v for k, v in table.items() if len(v) == len(HINT_SETS)}
    return complete, tmpl


def headroom(level):
    table, tmpl = load_runs()
    rows = {qid: hs for (lv, qid), hs in table.items() if lv == level}
    if not rows:
        print(f"no complete data for level '{level}'")
        return None
    tot_def = sum(hs[DEFAULT_ID] for hs in rows.values())
    tot_orc = sum(min(hs.values()) for hs in rows.values())
    per_hint = {h["id"]: sum(hs[h["id"]] for hs in rows.values()) for h in HINT_SETS}
    best_fixed = min(per_hint, key=per_hint.get)
    winners = Counter(min(hs, key=hs.get) for hs in rows.values())
    big = sum(1 for hs in rows.values() if min(hs.values()) < 0.8 * hs[DEFAULT_ID])

    print(f"\n=== headroom at load level '{level}' ({len(rows)} queries) ===")
    print(f"default PostgreSQL : {tot_def / 1000:8.1f} s")
    print(f"oracle (best hint) : {tot_orc / 1000:8.1f} s   ({(1 - tot_orc / tot_def) * 100:4.1f}% less)")
    print(f"best fixed hint    : {per_hint[best_fixed] / 1000:8.1f} s   ({BY_ID[best_fixed]['name']}, "
          f"{(1 - per_hint[best_fixed] / tot_def) * 100:4.1f}% less)")
    print(f"queries with >20% possible gain: {big} of {len(rows)}")
    print("fastest hint set per query:", ", ".join(f"{BY_ID[h]['name']}={c}" for h, c in winners.most_common()))
    print("\nper template: default -> oracle (best hint)")
    by_t = defaultdict(lambda: [0.0, 0.0, Counter()])
    for qid, hs in rows.items():
        t = by_t[tmpl[qid]]
        t[0] += hs[DEFAULT_ID]
        t[1] += min(hs.values())
        t[2][min(hs, key=hs.get)] += 1
    for t, (d, o, c) in sorted(by_t.items()):
        best = ", ".join(BY_ID[h]["name"] for h, _ in c.most_common(2))
        print(f"  {t}: {d / 1000:7.2f} s -> {o / 1000:7.2f} s  ({d / o:4.2f}x)  best: {best}")
    return {"default": tot_def, "oracle": tot_orc, "best_fixed": per_hint[best_fixed], "best_fixed_id": best_fixed}


if __name__ == "__main__":
    for lv in (sys.argv[1:] or ["idle", "stress"]):
        headroom(lv)
