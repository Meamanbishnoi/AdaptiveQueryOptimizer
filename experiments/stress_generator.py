"""
System Stress Testing Agent.
Generates controlled CPU and memory load on the operating system
to simulate realistic background resource contention.
"""

import sys
import time
import math
import multiprocessing
from typing import List, Optional
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import psutil


def _cpu_worker(stop_event: multiprocessing.Event, duty_cycle: float = 0.85):
    """
    Worker process running busy loops interspersed with small sleeps
    to maintain a predictable CPU duty cycle without freezing the OS.
    """
    work_interval = 0.08 * duty_cycle
    sleep_interval = 0.08 * (1.0 - duty_cycle)

    while not stop_event.is_set():
        t_start = time.perf_counter()
        # Intensive arithmetic loop
        while (time.perf_counter() - t_start) < work_interval:
            _ = [math.sqrt(x) * math.sin(x) for x in range(100, 500)]
        if sleep_interval > 0.001:
            time.sleep(sleep_interval)


def _memory_worker(stop_event: multiprocessing.Event, target_mb: int = 500):
    """
    Allocates and touches byte arrays to induce physical RAM pressure.
    """
    try:
        # Allocate array in 50MB increments
        chunks = []
        chunk_size = 50 * 1024 * 1024  # 50 MB
        num_chunks = max(1, target_mb // 50)
        for _ in range(num_chunks):
            if stop_event.is_set():
                break
            chunks.append(bytearray(chunk_size))
            time.sleep(0.05)

        # Hold memory until stop signal
        while not stop_event.is_set():
            time.sleep(0.5)
    except MemoryError:
        pass


class StressGenerator:
    """
    Manages background stress worker processes for CPU and Memory contention experiments.
    """

    def __init__(self):
        self.workers: List[multiprocessing.Process] = []
        self.stop_event: Optional[multiprocessing.Event] = None

    def start_stress(
        self,
        num_cpu_cores: Optional[int] = None,
        duty_cycle: float = 0.85,
        target_memory_mb: int = 0,
    ):
        """
        Launches background worker processes.
        """
        if self.is_active():
            print("[StressGenerator] Warning: Stress is already running.")
            return

        self.stop_event = multiprocessing.Event()
        total_cores = multiprocessing.cpu_count()
        cores_to_use = num_cpu_cores or max(1, total_cores - 1)

        print(f"[StressGenerator] Starting CPU stress on {cores_to_use}/{total_cores} cores (duty cycle: {duty_cycle:.0%})...")
        for _ in range(cores_to_use):
            p = multiprocessing.Process(
                target=_cpu_worker,
                args=(self.stop_event, duty_cycle),
                daemon=True,
            )
            p.start()
            self.workers.append(p)

        if target_memory_mb > 0:
            print(f"[StressGenerator] Allocating ~{target_memory_mb} MB memory pressure...")
            p_mem = multiprocessing.Process(
                target=_memory_worker,
                args=(self.stop_event, target_memory_mb),
                daemon=True,
            )
            p_mem.start()
            self.workers.append(p_mem)

    def stop_stress(self):
        """Signals all workers to terminate and joins them."""
        if not self.is_active():
            return

        print("[StressGenerator] Stopping stress workers...")
        if self.stop_event:
            self.stop_event.set()

        for p in self.workers:
            p.join(timeout=1.0)
            if p.is_alive():
                p.terminate()

        self.workers.clear()
        self.stop_event = None
        print("[StressGenerator] All stress workers stopped.")

    def is_active(self) -> bool:
        return len(self.workers) > 0 and any(p.is_alive() for p in self.workers)


if __name__ == "__main__":
    print("=" * 60)
    print("Stress Testing Agent Self-Test (5 seconds stress pulse)")
    print("=" * 60)
    gen = StressGenerator()
    try:
        psutil.cpu_percent(interval=None)
        print("Pre-stress CPU:", psutil.cpu_percent(interval=1.0), "%")

        gen.start_stress(num_cpu_cores=2, duty_cycle=0.85)
        time.sleep(1.0)
        print("In-stress CPU :", psutil.cpu_percent(interval=2.0), "%")

    finally:
        gen.stop_stress()

    time.sleep(0.5)
    print("Post-stress CPU:", psutil.cpu_percent(interval=1.0), "%")
    print("Stress Generator Test Completed!")
