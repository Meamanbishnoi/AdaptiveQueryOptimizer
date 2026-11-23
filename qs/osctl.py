"""
OS-level control of PostgreSQL processes: process priority and CPU affinity.

PostgreSQL runs one OS process per connection (a "backend") and starts extra processes
("parallel workers") for parallel plans. When other programs compete for the CPU, the operating
system's priority scheduler decides who runs. QuerySync can raise the priority class of every
PostgreSQL process so the scheduler runs the database first.

On Windows a new process does NOT inherit its creator's priority class (unless that is below
normal), so parallel workers start at NORMAL priority even when their leader was boosted. The
PriorityBooster therefore runs a small daemon thread that, while boosting is on, lists the
PostgreSQL processes every 30 ms with one syscall (~3 ms) and boosts the new ones.

    booster = shared_booster()
    booster.set(True)    # raise all PostgreSQL processes (now, and new workers as they appear)
    booster.set(False)   # restore NORMAL priority

The booster only changes PostgreSQL's processes and QuerySync's own process (which plans and
dispatches the queries); other programs are never touched.
"""

import atexit
import os
import sys
import threading
import time
from typing import Iterable, List, Optional, Set

import psutil

if sys.platform == "win32":
    NORMAL = psutil.NORMAL_PRIORITY_CLASS
    PRIORITIES = {"normal": psutil.NORMAL_PRIORITY_CLASS, "above_normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
                  "high": psutil.HIGH_PRIORITY_CLASS}
else:  # Unix nice values (raising priority needs privileges)
    NORMAL = 0
    PRIORITIES = {"normal": 0, "above_normal": -5, "high": -10}


def _postgres_pids_slow() -> List[int]:
    return [p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().startswith("postgres")]


class PriorityBooster:
    def __init__(self, priority: str = "high", interval: float = 0.03, cores: Optional[Iterable[int]] = None):
        self.priority = PRIORITIES[priority]
        self.interval = interval
        self.cores = list(cores) if cores is not None else None
        self._on = False
        self._boosted: Set[int] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        try:
            from qs.winproc import PostgresProcessLister
            self._list = PostgresProcessLister().pids
        except (OSError, AttributeError):
            self._list = _postgres_pids_slow
        self.scans = 0
        threading.Thread(target=self._loop, daemon=True).start()
        atexit.register(self.set, False)

    @property
    def active(self) -> bool:
        return self._on

    def set(self, on: bool):
        """Turns boosting on/off. Synchronous: when it returns, all current processes are updated."""
        with self._lock:
            if on == self._on:
                return
            self._on = on
            if on:
                self._boost_new()
                self._wake.set()
            else:
                for pid in self._boosted:
                    self._apply(pid, NORMAL, all_cores=True)
                self._boosted.clear()

    def _apply(self, pid: int, prio, all_cores: bool = False):
        try:
            p = psutil.Process(pid)
            p.nice(prio)
            if self.cores is not None:
                p.cpu_affinity(list(range(os.cpu_count() or 1)) if all_cores else self.cores)
        except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
            pass

    def _boost_new(self):
        live = set(self._list()) | {os.getpid()}   # QuerySync's own process too: it plans and dispatches
        self._boosted &= live          # forget processes that have exited
        for pid in live - self._boosted:
            self._apply(pid, self.priority)
            self._boosted.add(pid)
        self.scans += 1

    def _loop(self):
        while True:
            if not self._on:
                self._wake.wait()
                self._wake.clear()
            time.sleep(self.interval)
            with self._lock:
                if self._on:
                    self._boost_new()


def reset_postgres_priority():
    """Puts every PostgreSQL process back to NORMAL priority on all cores. Called when QuerySync starts,
    in case an earlier run was killed while boosting (its exit handler could not run)."""
    try:
        from qs.winproc import PostgresProcessLister
        pids = PostgresProcessLister().pids()
    except (OSError, AttributeError):
        pids = _postgres_pids_slow()
    for pid in pids:
        try:
            p = psutil.Process(pid)
            if p.nice() != NORMAL:
                p.nice(NORMAL)
                p.cpu_affinity(list(range(os.cpu_count() or 1)))
        except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
            pass


_shared: Optional[PriorityBooster] = None
_shared_lock = threading.Lock()


def shared_booster() -> PriorityBooster:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = PriorityBooster()
        return _shared
