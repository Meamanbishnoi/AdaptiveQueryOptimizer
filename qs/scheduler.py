"""
QuerySync query scheduler: operating-system scheduling applied to database queries.

When many users send queries at once, something must decide WHICH query runs, WHEN, and HOW MANY
run together. QuerySync does this the way an OS schedules processes:

  long-term scheduler (admission thread)
      for every arriving query: choose its plan (AI), predict its run time ("burst time", latency
      model) and its maximum working-memory claim; then put it in the ready queue
  short-term scheduler (dispatcher)
      whenever an execution slot is free, pick the next job from the ready queue:
        fcfs      first come, first served
        sjf       shortest (predicted) job first, non-preemptive
        hrrn      highest response ratio next, ratio = (waiting + burst) / burst:
                  favours short jobs like SJF, but a waiting job's ratio keeps growing (no starvation)
        priority  class priority (interactive before batch) with aging: every AGING_S seconds of
                  waiting raises a job by one priority level (no starvation)
  multilevel queue: express lane for short queries
      the MPL limit protects throughput, but short interactive queries would then wait behind long
      reports. As in multilevel-queue scheduling, short jobs (predicted < EXPRESS_MS) form a
      foreground class with EXPRESS_SLOTS reserved extra slots that long jobs may not use.
  degree of multiprogramming (MPL)
      how many queries may run at the same time. Too many -> they fight for CPU/memory and total
      throughput falls (thrashing). "auto" sets it from the current external CPU load using the
      table measured by `python main.py schedule --mpl-sweep` (models/mpl.json).
  memory admission (Banker's-style safe state)
      each job declares its maximum working-memory claim (from its plan); a job is dispatched only
      if the claims of all running jobs plus its own fit in MEMORY_BUDGET_MB. A job whose claim alone
      exceeds the budget runs only when nothing else runs, so the system can never deadlock.
  OS priority boost
      under high external load, PostgreSQL's processes run at HIGH priority (qs/osctl.py).

Running queries are never preempted: SQL execution cannot be paused, only cancelled and restarted
from scratch (wasting the work done), so all policies are non-preemptive.
"""

import json
import queue
import threading
import time
from typing import Dict, List, Optional, Union

from qs.config import MEMORY_BUDGET_MB, MODELS_DIR, OS_BOOST
from qs.db import Database
from qs.hints import BY_ID
from qs.optimizer import Optimizer, boost_wanted, set_boost
from qs.telemetry import shared_telemetry

POLICIES = ("fcfs", "sjf", "hrrn", "priority")
CLASS_PRIORITY = {"interactive": 0, "batch": 1}
AGING_S = 5.0
EXPRESS_SLOTS = 1      # extra slots reserved for short queries (multilevel queue)
EXPRESS_MS = 500.0     # "short" = predicted run time below this
MPL_FILE = MODELS_DIR / "mpl.json"


def auto_mpl_table() -> Dict[str, int]:
    """MPL per load level, measured by the thrashing experiment; falls back to a CPU-count rule."""
    if MPL_FILE.exists():
        return json.loads(MPL_FILE.read_text(encoding="utf-8"))
    import os
    n = os.cpu_count() or 4
    return {"low": n, "high": max(1, n // 2)}


class Job:
    def __init__(self, jid: int, sql: str, template: str = "", cls: str = "batch", arrival: float = 0.0):
        self.id, self.sql, self.template, self.cls, self.arrival = jid, sql, template, cls, arrival
        self.hint, self.hint_name, self.predicted_ms, self.mem_mb = 0, "default", None, 0.0
        self.ready = self.start = self.end = None
        self.latency_ms, self.slot, self.error, self.boosted = None, None, None, False

    def as_dict(self) -> dict:
        r = lambda x: None if x is None else round(x, 4)
        return {"id": self.id, "template": self.template, "cls": self.cls, "hint": self.hint_name,
                "predicted_ms": r(self.predicted_ms), "mem_mb": self.mem_mb, "arrival": r(self.arrival),
                "ready": r(self.ready), "start": r(self.start), "end": r(self.end), "latency_ms": r(self.latency_ms),
                "slot": self.slot, "boosted": self.boosted, "error": self.error}


class QueryScheduler:
    def __init__(self, policy: str = "sjf", mpl: Union[int, str] = "auto", plan_mode: str = "ai",
                 os_boost: str = OS_BOOST, memory_budget_mb: float = MEMORY_BUDGET_MB, max_slots: int = 8,
                 optimizer: Optional[Optimizer] = None, express_slots: Optional[int] = None,
                 express_ms: float = EXPRESS_MS):
        assert policy in POLICIES, policy
        self.policy, self.mpl, self.plan_mode, self.os_boost = policy, mpl, plan_mode, os_boost
        self.memory_budget_mb = memory_budget_mb
        # a fixed MPL (experiments, plain-PostgreSQL baseline) means exactly that many slots, no express lane
        self.express_slots = express_slots if express_slots is not None else (0 if isinstance(mpl, int) else EXPRESS_SLOTS)
        self.express_ms = express_ms
        self.max_slots = (mpl if isinstance(mpl, int) else max(max_slots, max(auto_mpl_table().values()))) + self.express_slots
        self.telemetry = shared_telemetry()
        self.optimizer = optimizer or Optimizer(log_runs=False)
        self.mpl_table = auto_mpl_table()
        self._cond = threading.Condition()
        self._incoming: "queue.Queue[Optional[Job]]" = queue.Queue()
        self.ready: List[Job] = []
        self.done: List[Job] = []
        self.running, self.mem_in_use, self._free_slots = 0, 0.0, set()
        self._memory_blocked = set()
        self._stop = False
        self._threads: List[threading.Thread] = []
        self._next_id = 0
        self.t0 = time.perf_counter()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> "QueryScheduler":
        self._free_slots = set(range(self.max_slots))
        dbs = [Database() for _ in range(self.max_slots)]   # open all connections before the clock starts
        self.t0 = time.perf_counter()
        for db in dbs:
            t = threading.Thread(target=self._worker, args=(db,), daemon=True)
            t.start()
            self._threads.append(t)
        t = threading.Thread(target=self._admission, daemon=True)
        t.start()
        self._threads.append(t)
        return self

    def stop(self):
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        self._incoming.put(None)
        set_boost(False)

    def now(self) -> float:
        return time.perf_counter() - self.t0

    def submit(self, sql: str, template: str = "", cls: str = "batch") -> Job:
        job = Job(self._next_id, sql, template, cls, self.now())
        self._next_id += 1
        self._incoming.put(job)
        return job

    def wait(self, n_jobs: int, timeout: float = 3600) -> List[Job]:
        deadline = time.time() + timeout
        with self._cond:
            while len(self.done) < n_jobs and time.time() < deadline:
                self._cond.wait(0.2)
        return list(self.done)

    # ---------------------------------------------------------------- long-term scheduler
    def _admission(self):
        while True:
            job = self._incoming.get()
            if job is None:
                return
            try:
                d = self.optimizer.decide(job.sql, search=(self.plan_mode == "ai"))
                job.hint, job.hint_name = d["hint"], BY_ID[d["hint"]]["name"]
                job.predicted_ms, job.mem_mb = d["predicted_ms"] or 1000.0, d["mem_mb"]
            except Exception as ex:  # a bad query still goes through and reports its error
                job.predicted_ms, job.error = 1000.0, str(ex)[:200]
            job.ready = self.now()
            with self._cond:
                self.ready.append(job)
                self._cond.notify_all()

    # ---------------------------------------------------------------- short-term scheduler
    def current_mpl(self) -> int:
        if isinstance(self.mpl, int):
            return self.mpl
        return min(self.max_slots, int(self.mpl_table[self.telemetry.snapshot()["level"]]))

    def _key(self, job: Job, now: float):
        wait = now - job.arrival
        if self.policy == "fcfs":
            return job.arrival
        if self.policy == "sjf":
            return job.predicted_ms
        if self.policy == "hrrn":
            burst = job.predicted_ms / 1000.0
            return -(wait + burst) / burst
        return (CLASS_PRIORITY.get(job.cls, 1) - wait / AGING_S, job.arrival)   # priority with aging

    def _pick(self) -> Optional[Job]:
        """Called with the lock held. Returns the next job to dispatch, or None."""
        limit = self.current_mpl()
        if not self.ready or self.running >= limit + self.express_slots:
            return None
        now = self.now()
        order = sorted(self.ready, key=lambda j: self._key(j, now))
        for job in order:
            if self.running >= limit and job.predicted_ms >= self.express_ms:
                continue   # normal slots full: only short jobs may use the express slots
            if self.running == 0 or self.mem_in_use + job.mem_mb <= self.memory_budget_mb:
                return job
            self._memory_blocked.add(job.id)   # dispatching it now would make the memory state unsafe
        return None

    @property
    def memory_waits(self) -> int:
        """Number of jobs that had to wait at least once because of the memory (safe-state) check."""
        return len(self._memory_blocked)

    def _worker(self, db: Database):
        while True:
            with self._cond:
                job = None
                while not self._stop:
                    job = self._pick()
                    if job is not None:
                        break
                    self._cond.wait(0.5)
                if self._stop:
                    db.conn.close()
                    return
                self.ready.remove(job)
                self.running += 1
                self.mem_in_use += job.mem_mb
                job.slot = min(self._free_slots)
                self._free_slots.discard(job.slot)
                job.start = self.now()
            load = self.telemetry.snapshot()
            job.boosted = boost_wanted(self.os_boost, load)
            set_boost(job.boosted)
            try:
                if job.error is None:
                    ms, _ = db.run(job.sql, BY_ID[job.hint]["settings"])
                    job.latency_ms = ms
            except Exception as ex:
                job.error = str(ex)[:200]
                try:
                    db.conn.rollback()
                except Exception:
                    pass
            with self._cond:
                job.end = self.now()
                self.running -= 1
                self.mem_in_use -= job.mem_mb
                self._free_slots.add(job.slot)
                self.done.append(job)
                self._cond.notify_all()


def metrics(jobs: List[Job]) -> dict:
    """OS scheduling metrics (seconds): turnaround = end - arrival, waiting = start - arrival."""
    import statistics
    ok = [j for j in jobs if j.end is not None and j.error is None]
    if not ok:
        return {}
    tat = sorted(j.end - j.arrival for j in ok)
    wait = [j.start - j.arrival for j in ok]
    span = max(j.end for j in ok) - min(j.arrival for j in ok)
    inter = [j.end - j.arrival for j in ok if j.cls == "interactive"]
    batch = [j.end - j.arrival for j in ok if j.cls != "interactive"]
    # slowdown = turnaround / own execution time: how much a job was delayed relative to its size
    slow = [(j.end - j.arrival) / max(1e-3, j.end - j.start) for j in ok]
    p = lambda xs, q: xs[min(len(xs) - 1, int(q * len(xs)))]
    return {"jobs": len(ok), "errors": len(jobs) - len(ok),
            "avg_turnaround_s": round(statistics.mean(tat), 3), "p95_turnaround_s": round(p(tat, 0.95), 3),
            "avg_wait_s": round(statistics.mean(wait), 3), "max_wait_s": round(max(wait), 3),
            "avg_interactive_s": round(statistics.mean(inter), 3) if inter else None,
            "avg_batch_s": round(statistics.mean(batch), 3) if batch else None,
            "avg_slowdown": round(statistics.mean(slow), 2),
            "makespan_s": round(span, 2), "throughput_per_min": round(len(ok) / span * 60, 1),
            "total_exec_s": round(sum(j.end - j.start for j in ok), 2)}
