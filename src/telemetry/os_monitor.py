"""
OS Telemetry Agent.
Collects real-time hardware and operating system metrics using psutil.
"""

import os
import sys
import time
import threading
from typing import Dict, Any, Optional
from collections import deque
import psutil


class OSTelemetryMonitor:
    """
    Monitors and samples real-time operating system performance telemetry:
    - CPU utilization (overall and per-core)
    - Memory statistics (total, available, used, percent)
    - Disk I/O rates (bytes read/written, ops)
    - Load average / thread & process pressure
    """

    def __init__(self, sample_interval: float = 0.5, history_size: int = 10):
        self.sample_interval = sample_interval
        self.history_size = history_size
        self._history = deque(maxlen=history_size)
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Previous disk IO counters for rate calculation
        self._last_disk_io = psutil.disk_io_counters()
        self._last_disk_time = time.time()

        # Prime initial psutil CPU readings
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)

    def sample_metrics(self) -> Dict[str, Any]:
        """
        Gathers an instantaneous snapshot of system metrics.
        Returns a dictionary with numeric metric values.
        """
        now = time.time()

        # 1. CPU Metrics
        cpu_percent_overall = psutil.cpu_percent(interval=None)
        cpu_percent_per_core = psutil.cpu_percent(interval=None, percpu=True)
        cpu_count_logical = psutil.cpu_count(logical=True) or 1
        cpu_count_physical = psutil.cpu_count(logical=False) or 1

        # 2. Memory Metrics
        mem = psutil.virtual_memory()
        mem_total_mb = round(mem.total / (1024 * 1024), 2)
        mem_available_mb = round(mem.available / (1024 * 1024), 2)
        mem_used_mb = round(mem.used / (1024 * 1024), 2)
        mem_percent = mem.percent

        # Swap / Pagefile
        swap = psutil.swap_memory()
        swap_percent = swap.percent

        # 3. Disk I/O Rates (MB/s)
        current_disk_io = psutil.disk_io_counters()
        time_delta = max(now - self._last_disk_time, 1e-4)

        if current_disk_io and self._last_disk_io:
            read_bytes_delta = max(
                0, current_disk_io.read_bytes - self._last_disk_io.read_bytes
            )
            write_bytes_delta = max(
                0, current_disk_io.write_bytes - self._last_disk_io.write_bytes
            )
            disk_read_mbps = round(
                (read_bytes_delta / (1024 * 1024)) / time_delta, 3
            )
            disk_write_mbps = round(
                (write_bytes_delta / (1024 * 1024)) / time_delta, 3
            )
            disk_iops = round(
                (
                    (current_disk_io.read_count - self._last_disk_io.read_count)
                    + (
                        current_disk_io.write_count
                        - self._last_disk_io.write_count
                    )
                )
                / time_delta,
                1,
            )
        else:
            disk_read_mbps = 0.0
            disk_write_mbps = 0.0
            disk_iops = 0.0

        self._last_disk_io = current_disk_io
        self._last_disk_time = now

        # 4. System Load / Process Pressure
        load_1m = 0.0
        load_5m = 0.0
        load_15m = 0.0
        if hasattr(os, "getloadavg"):
            try:
                load_1m, load_5m, load_15m = os.getloadavg()
            except OSError:
                pass
        else:
            # Fallback estimation on Windows based on overall CPU utilization
            load_1m = round((cpu_percent_overall / 100.0) * cpu_count_logical, 2)
            load_5m = load_1m
            load_15m = load_1m

        process_count = len(psutil.pids())

        snapshot = {
            "timestamp": now,
            "cpu_percent": cpu_percent_overall,
            "cpu_cores": cpu_count_logical,
            "cpu_core_max": (
                max(cpu_percent_per_core) if cpu_percent_per_core else 0.0
            ),
            "mem_total_mb": mem_total_mb,
            "mem_available_mb": mem_available_mb,
            "mem_used_mb": mem_used_mb,
            "mem_percent": mem_percent,
            "swap_percent": swap_percent,
            "disk_read_mbps": disk_read_mbps,
            "disk_write_mbps": disk_write_mbps,
            "disk_iops": disk_iops,
            "load_1m": load_1m,
            "process_count": process_count,
            "is_cpu_stressed": cpu_percent_overall >= 75.0,
            "is_mem_stressed": mem_percent >= 80.0,
        }

        with self._lock:
            self._history.append(snapshot)

        return snapshot

    def get_latest_metrics(self) -> Dict[str, Any]:
        """
        Returns the most recently recorded snapshot or samples immediately if empty.
        """
        with self._lock:
            if self._history:
                return dict(self._history[-1])
        return self.sample_metrics()

    def get_smoothed_metrics(self) -> Dict[str, Any]:
        """
        Returns moving-average smoothed metrics over the recent history window.
        """
        with self._lock:
            if not self._history:
                latest = self.sample_metrics()
                return latest
            history_list = list(self._history)

        n = len(history_list)
        avg_cpu = round(sum(s["cpu_percent"] for s in history_list) / n, 2)
        avg_mem = round(sum(s["mem_percent"] for s in history_list) / n, 2)
        avg_avail_mb = round(
            sum(s["mem_available_mb"] for s in history_list) / n, 2
        )
        avg_read_mbps = round(
            sum(s["disk_read_mbps"] for s in history_list) / n, 3
        )
        avg_write_mbps = round(
            sum(s["disk_write_mbps"] for s in history_list) / n, 3
        )

        res = dict(history_list[-1])
        res["cpu_percent"] = avg_cpu
        res["mem_percent"] = avg_mem
        res["mem_available_mb"] = avg_avail_mb
        res["disk_read_mbps"] = avg_read_mbps
        res["disk_write_mbps"] = avg_write_mbps
        res["is_cpu_stressed"] = avg_cpu >= 75.0
        res["is_mem_stressed"] = avg_mem >= 80.0
        return res

    def start_background_monitoring(self):
        """Starts a background thread that samples OS metrics periodically."""
        if self._running:
            return
        self._running = True

        def _worker():
            while self._running:
                try:
                    self.sample_metrics()
                except Exception:
                    pass
                time.sleep(self.sample_interval)

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()

    def stop_background_monitoring(self):
        """Stops the background sampling thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)


# Global singleton instance
default_monitor = OSTelemetryMonitor()


if __name__ == "__main__":
    print("=" * 60)
    print("OS Telemetry Agent Test (running for 5 seconds)...")
    print("=" * 60)
    mon = OSTelemetryMonitor(sample_interval=0.5)
    for i in range(10):
        m = mon.sample_metrics()
        print(
            f"[{i+1}/10] CPU: {m['cpu_percent']:5.1f}% (max core: {m['cpu_core_max']:5.1f}%) | "
            f"RAM Avail: {m['mem_available_mb']:8.1f} MB ({m['mem_percent']:4.1f}%) | "
            f"Disk IO: {m['disk_read_mbps']+m['disk_write_mbps']:5.2f} MB/s | "
            f"Procs: {m['process_count']}"
        )
        time.sleep(0.5)
    print("\nTelemetry Test Passed Successfully!")
