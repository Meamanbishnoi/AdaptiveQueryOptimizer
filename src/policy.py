"""
Measurement-driven policy: for each query template and load level, run the query with
PostgreSQL's DEFAULT plan or SERIAL (parallel workers disabled), whichever is measured
to be faster.

Why only these two options: in every measurement on this project, the alternatives that
helped did so by avoiding parallel workers; other planner switches made no reliable
difference. Queries whose default plan is not parallel always run as DEFAULT, so they
cannot be slowed down by the optimizer.

Per (template, load level):
  1. Explore: alternate DEFAULT / SERIAL until each has MIN_SAMPLES runs.
  2. Decide: use SERIAL only if its median is >= WIN_MARGIN faster than DEFAULT's AND it
     is faster in >= DOMINANCE of all (serial run, default run) pairs. Latency on this
     hardware is often bimodal, so a median alone can mistake noise for a win. If the
     median says "faster" but the pairwise test is not yet convincing, keep alternating
     (up to MAX_SAMPLES runs each) instead of deciding on too little data.
  3. Stop exploring SERIAL early once two of its runs are REJECT_RATIO x slower than DEFAULT. The
     rejection is sticky: it is only revisited by the periodic re-check (step 4), so noise
     in later DEFAULT runs cannot restart exploration of a plan known to be slow.
  4. Every RECHECK_EVERY runs, measure the other option once so decisions follow changes.
"""

import json
import os
import statistics
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.config import (
    DOMINANCE, MAX_SAMPLES, MIN_SAMPLES, POLICY_FILE, RECHECK_EVERY, REJECT_RATIO, WIN_MARGIN, WINDOW,
)

DEFAULT, SERIAL = "default", "serial"


def dominance(faster: List[float], slower: List[float]) -> float:
    """Share of pairs (x, y) with x < y (ties count half): 0.5 = no difference, 1.0 = always faster."""
    if not faster or not slower:
        return 0.0
    wins = sum(1.0 if x < y else 0.5 if x == y else 0.0 for x in faster for y in slower)
    return wins / (len(faster) * len(slower))


class Policy:
    def __init__(self, path: Optional[Path] = POLICY_FILE, min_samples: int = MIN_SAMPLES,
                 max_samples: int = MAX_SAMPLES,
                 win_margin: float = WIN_MARGIN, dom: float = DOMINANCE,
                 reject_ratio: float = REJECT_RATIO, recheck_every: int = RECHECK_EVERY,
                 window: int = WINDOW):
        self.path = Path(path) if path else None
        self.min_samples, self.max_samples = min_samples, max_samples
        self.win_margin, self.dom = win_margin, dom
        self.reject_ratio, self.recheck_every, self.window = reject_ratio, recheck_every, window
        self._lock = threading.RLock()
        # {template: {"label": str, "parallel": bool, "levels": {level: {"default": [...], "serial": [...], "runs": int}}}}
        self.templates: Dict[str, dict] = {}
        if self.path and self.path.exists():
            self.templates = json.loads(self.path.read_text(encoding="utf-8"))

    # -- templates ---------------------------------------------------------------
    def is_known(self, template: str) -> bool:
        return template in self.templates

    def register(self, template: str, label: str, parallel: bool):
        with self._lock:
            self.templates[template] = {"label": label, "parallel": parallel, "levels": {}}

    def _level(self, template: str, level: str) -> dict:
        return self.templates[template]["levels"].setdefault(level, {DEFAULT: [], SERIAL: [], "runs": 0})

    # -- decisions ---------------------------------------------------------------
    def evaluate(self, d: List[float], s: List[float]) -> Tuple[str, str]:
        """Steady-state choice from measurements alone."""
        md, ms = statistics.median(d), statistics.median(s)
        dom = dominance(s, d)
        if ms <= (1 - self.win_margin) * md and dom >= self.dom:
            return SERIAL, f"serial measured {1 - ms / md:.0%} faster ({ms:.0f} vs {md:.0f} ms)"
        if ms > self.reject_ratio * md:
            return DEFAULT, f"serial measured slower ({ms:.0f} vs {md:.0f} ms)"
        return DEFAULT, f"no reliable difference ({ms:.0f} vs {md:.0f} ms)"

    def choose(self, template: str, level: str) -> Tuple[str, str]:
        with self._lock:
            t = self.templates[template]
            if not t["parallel"]:
                return DEFAULT, "default plan is not parallel - nothing to change"
            st = self._level(template, level)
            d, s = st[DEFAULT], st[SERIAL]
            rejected = st.get("serial_rejected", False)
            if not rejected and (len(d) < self.min_samples or len(s) < self.min_samples):
                arm = DEFAULT if len(d) <= len(s) else SERIAL
                return arm, f"learning ({len(d)}/{self.min_samples} default, {len(s)}/{self.min_samples} serial runs)"
            if rejected:
                best, why = DEFAULT, f"serial rejected: measured {statistics.median(s) / statistics.median(d):.1f}x slower"
            else:
                best, why = self.evaluate(d, s)
                promising = statistics.median(s) <= (1 - self.win_margin) * statistics.median(d)
                if best == DEFAULT and promising and min(len(d), len(s)) < self.max_samples:
                    arm = DEFAULT if len(d) <= len(s) else SERIAL
                    return arm, f"learning: serial looks faster, confirming ({len(d)} default, {len(s)} serial runs)"

            if self.recheck_every and st["runs"] % self.recheck_every == self.recheck_every - 1:
                other = SERIAL if best == DEFAULT else DEFAULT
                return other, f"periodic re-check of {other} (current choice: {best})"
            return best, why

    def record(self, template: str, level: str, arm: str, ms: float):
        with self._lock:
            st = self._level(template, level)
            st[arm] = (st[arm] + [round(ms, 2)])[-self.window:]
            st["runs"] += 1
            # (Re)decide rejection only when SERIAL itself was measured: at least two SERIAL
            # runs must be slow, and they must be the majority. One noisy run under load (seen:
            # 969 ms vs a 440 ms default, for a plan that is really 2.7x faster) must not
            # reject a good plan.
            if arm == SERIAL and st[DEFAULT]:
                limit = self.reject_ratio * statistics.median(st[DEFAULT])
                slow = sum(1 for x in st[SERIAL] if x > limit)
                st["serial_rejected"] = slow >= 2 and slow > len(st[SERIAL]) / 2

    def converged(self, template: str, level: str) -> bool:
        with self._lock:
            t = self.templates.get(template)
            if t is None:
                return False
            if not t["parallel"]:
                return True
            st = t["levels"].get(level)
            if not st:
                return False
            d, s = st[DEFAULT], st[SERIAL]
            if st.get("serial_rejected", False):
                return True
            if len(d) < self.min_samples or len(s) < self.min_samples:
                return False
            best, _ = self.evaluate(d, s)
            promising = statistics.median(s) <= (1 - self.win_margin) * statistics.median(d)
            return best == SERIAL or not promising or min(len(d), len(s)) >= self.max_samples

    def summary(self) -> List[dict]:
        rows = []
        with self._lock:
            for tpl, t in sorted(self.templates.items(), key=lambda kv: kv[1]["label"]):
                if not t["parallel"]:
                    rows.append({"template": tpl, "label": t["label"], "parallel": False, "level": "-",
                                 "choice": DEFAULT, "reason": "default plan is not parallel",
                                 "default_ms": None, "serial_ms": None, "runs": None})
                    continue
                for level, st in sorted(t["levels"].items()):
                    d, s = st[DEFAULT], st[SERIAL]
                    if st.get("serial_rejected"):
                        choice, reason = DEFAULT, f"serial rejected: measured {statistics.median(s) / statistics.median(d):.1f}x slower"
                    elif d and s and self.converged(tpl, level):
                        choice, reason = self.evaluate(d, s)
                    else:
                        choice, reason = None, "still learning"
                    rows.append({"template": tpl, "label": t["label"], "parallel": True, "level": level,
                                 "choice": choice, "reason": reason,
                                 "default_ms": round(statistics.median(d), 1) if d else None,
                                 "serial_ms": round(statistics.median(s), 1) if s else None,
                                 "runs": f"{len(d)}+{len(s)}"})
        return rows

    # -- persistence -------------------------------------------------------------
    def save(self):
        if not self.path:
            return
        with self._lock:
            text = json.dumps(self.templates, indent=1)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)

    def reset(self):
        with self._lock:
            self.templates = {}
        self.save()
