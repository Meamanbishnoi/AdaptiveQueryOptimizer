"""
QuerySync optimizer: picks DEFAULT or SERIAL for each query, runs it, and learns from
the measured latency.

Modes:
  adaptive : measured policy (src/policy.py) - the system itself
  rule     : static baseline - SERIAL whenever load is high and the plan is parallel
  default  : plain PostgreSQL (baseline)
  serial   : always SERIAL (for experiments)
"""

import hashlib
import re
import time
from typing import Any, Dict, Optional

from src.db import Database
from src.policy import DEFAULT, SERIAL, Policy
from src.telemetry import Telemetry, shared_telemetry

MODES = ("adaptive", "rule", "default", "serial")


def normalize_sql(sql: str) -> str:
    """Query template: comments, whitespace, literals and trailing ';' removed."""
    s = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    s = re.sub(r"'(?:[^']|'')*'", "?", s)
    s = re.sub(r"\b\d+(?:\.\d+)?\b", "?", s)
    return " ".join(s.split()).rstrip("; ").upper()


def template_of(sql: str) -> str:
    return hashlib.sha1(normalize_sql(sql).encode("utf-8")).hexdigest()[:12]


class Optimizer:
    def __init__(self, policy: Optional[Policy] = None, telemetry: Optional[Telemetry] = None,
                 db: Optional[Database] = None, save_every: int = 10):
        self.policy = policy or Policy()
        self.telemetry = telemetry or shared_telemetry()
        self.db = db or Database()
        self.save_every = save_every
        self._unsaved = 0
        self.telemetry.start()

    def execute(self, sql: str, mode: str = "adaptive", label: Optional[str] = None,
                learn: bool = True) -> Dict[str, Any]:
        """
        Runs one query. learn=False keeps the run out of the policy (used for baseline runs
        in comparisons, so the adaptive mode only learns from its own executions).
        """
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        t0 = time.perf_counter()
        load = self.telemetry.snapshot()
        template = template_of(sql)

        probe_ms = 0.0
        if not self.policy.is_known(template):
            p0 = time.perf_counter()
            parallel = self.db.is_parallel(sql)
            probe_ms = (time.perf_counter() - p0) * 1000.0
            self.policy.register(template, label or normalize_sql(sql)[:60], parallel)
        elif label and self.policy.templates[template]["label"] != label:
            self.policy.templates[template]["label"] = label
        parallel = self.policy.templates[template]["parallel"]

        if mode == "adaptive":
            arm, reason = self.policy.choose(template, load["level"])
        elif mode == "rule":
            arm = SERIAL if parallel and load["level"] == "high" else DEFAULT
            reason = "rule: high load and parallel plan" if arm == SERIAL else "rule: default"
        else:
            arm, reason = (SERIAL if mode == "serial" else DEFAULT), f"forced {mode}"
        overhead_ms = (time.perf_counter() - t0) * 1000.0 - probe_ms

        latency_ms, rows = self.db.run(sql, serial=(arm == SERIAL))

        if learn and parallel:
            self.policy.record(template, load["level"], arm, latency_ms)
            self._unsaved += 1
            if self._unsaved >= self.save_every:
                self.save()

        return {
            "mode": mode, "option": arm, "reason": reason,
            "latency_ms": round(latency_ms, 2), "overhead_ms": round(overhead_ms, 3),
            "probe_ms": round(probe_ms, 1), "rows": rows,
            "template": template, "label": self.policy.templates[template]["label"],
            "parallel_plan": parallel, "load": load,
        }

    def save(self):
        self.policy.save()
        self._unsaved = 0
