"""
Background OS telemetry.

Samples every 0.5 s and reports averages over the last ~2 s:
  cpu           total host CPU %
  external_cpu  CPU % used by processes other than PostgreSQL (the load signal)
  postgres_cpu  CPU % used by PostgreSQL itself
  mem           RAM %
On non-Windows systems external_cpu falls back to total CPU.
"""

import threading
import time
from collections import deque
from typing import Dict

import psutil

from src.config import HIGH_LOAD_EXTERNAL_CPU


def load_level(external_cpu: float) -> str:
    return "high" if external_cpu >= HIGH_LOAD_EXTERNAL_CPU else "low"


class Telemetry:
    def __init__(self, interval: float = 0.5, window: int = 4):
        self.interval = interval
        self._samples = deque(maxlen=window)
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._thread = None
        try:
            from src.winproc import ProcessCpuSampler
            self._split = ProcessCpuSampler()
            self._split.sample()
        except OSError:
            self._split = None
        psutil.cpu_percent(None)

    def _sample(self) -> Dict[str, float]:
        cpu = psutil.cpu_percent(None)
        if self._split is not None:
            s = self._split.sample()
            ext, pg = s["external_cpu_percent"], s["postgres_cpu_percent"]
        else:
            ext, pg = cpu, 0.0
        return {"cpu": cpu, "external_cpu": ext, "postgres_cpu": pg, "mem": psutil.virtual_memory().percent}

    def start(self):
        """
        Starts sampling and blocks until a full window (~2 s) is available. The first
        interval after a process starts is discarded: it is dominated by the process's own
        startup (antivirus scanning, module compilation) and once mislabelled an idle
        machine as 60% loaded.
        """
        with self._start_lock:
            if self._thread is None:
                self._fill_and_start()

    def _fill_and_start(self):
        time.sleep(self.interval)
        self._sample()  # discard startup interval
        for _ in range(self._samples.maxlen):
            time.sleep(self.interval)
            self._samples.append(self._sample())

        def loop():
            while True:
                time.sleep(self.interval)
                sample = self._sample()
                with self._lock:
                    self._samples.append(sample)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def snapshot(self) -> Dict[str, float]:
        self.start()
        with self._lock:
            samples = list(self._samples)
        snap = {k: round(sum(s[k] for s in samples) / len(samples), 1) for k in samples[0]}
        snap["level"] = load_level(snap["external_cpu"])
        return snap


_shared = None


def shared_telemetry() -> Telemetry:
    global _shared
    if _shared is None:
        _shared = Telemetry()
    return _shared
