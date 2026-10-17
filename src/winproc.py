"""
Windows per-process CPU attribution in a single syscall (~5 ms for ~300 processes).

Reads cumulative user+kernel CPU time for every process via
NtQuerySystemInformation(SystemProcessInformation) and splits host CPU usage into:
  - PostgreSQL's own load (postgres.exe backends and parallel workers)
  - External load (every other process: noisy neighbours, background jobs)

External load is the contention signal the optimizer should react to. Total CPU %
also counts PostgreSQL's own parallel workers, so back-to-back queries would make an
idle machine look "stressed".

Caveat: CPU time used by processes that started and exited between two samples is not
attributed. That mostly affects short-lived PostgreSQL parallel workers (counted on the
PostgreSQL side) and does not affect long-lived external contention.
"""

import ctypes
import os
import sys
import time
from typing import Dict, Optional, Tuple

_SYSTEM_PROCESS_INFORMATION = 5
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004

# x64 SYSTEM_PROCESS_INFORMATION field offsets
_OFF_NEXT = 0
_OFF_USER_TIME = 40
_OFF_KERNEL_TIME = 48
_OFF_NAME_LEN = 56
_OFF_NAME_BUF = 64
_OFF_PID = 80

POSTGRES_IMAGE_PREFIX = "postgres"


class ProcessCpuSampler:
    """Computes external vs PostgreSQL CPU % between consecutive calls to sample()."""

    def __init__(self):
        if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise OSError("ProcessCpuSampler requires 64-bit Windows")
        self._ntdll = ctypes.WinDLL("ntdll")
        self._buf_size = 1 << 20
        self._buf = ctypes.create_string_buffer(self._buf_size)
        self._ncpu = os.cpu_count() or 1
        self._self_pid = os.getpid()
        self._prev: Optional[Dict[int, Tuple[int, bool]]] = None
        self._prev_t = 0.0

    def _snapshot(self) -> Dict[int, Tuple[int, bool]]:
        """Returns {pid: (cumulative_cpu_100ns, is_postgres)}."""
        ret_len = ctypes.c_ulong(0)
        while True:
            status = self._ntdll.NtQuerySystemInformation(
                _SYSTEM_PROCESS_INFORMATION, self._buf, self._buf_size, ctypes.byref(ret_len)
            ) & 0xFFFFFFFF
            if status == _STATUS_INFO_LENGTH_MISMATCH:
                self._buf_size = max(self._buf_size * 2, ret_len.value + 65536)
                self._buf = ctypes.create_string_buffer(self._buf_size)
                continue
            if status != 0:
                raise OSError(f"NtQuerySystemInformation failed: 0x{status:08X}")
            break

        raw = self._buf.raw
        base = ctypes.addressof(self._buf)
        out: Dict[int, Tuple[int, bool]] = {}
        off = 0
        while True:
            nxt = int.from_bytes(raw[off + _OFF_NEXT: off + _OFF_NEXT + 4], "little")
            pid = int.from_bytes(raw[off + _OFF_PID: off + _OFF_PID + 8], "little")
            user = int.from_bytes(raw[off + _OFF_USER_TIME: off + _OFF_USER_TIME + 8], "little")
            kern = int.from_bytes(raw[off + _OFF_KERNEL_TIME: off + _OFF_KERNEL_TIME + 8], "little")
            name_len = int.from_bytes(raw[off + _OFF_NAME_LEN: off + _OFF_NAME_LEN + 2], "little")
            name_ptr = int.from_bytes(raw[off + _OFF_NAME_BUF: off + _OFF_NAME_BUF + 8], "little")
            name = ""
            if name_ptr and name_len:
                rel = name_ptr - base
                if 0 <= rel < len(raw):
                    name = raw[rel: rel + name_len].decode("utf-16-le", errors="ignore")
            if pid != 0:  # pid 0 is the System Idle Process
                out[pid] = (user + kern, name.lower().startswith(POSTGRES_IMAGE_PREFIX))
            if nxt == 0:
                break
            off += nxt
        return out

    def sample(self) -> Dict[str, float]:
        """
        Returns {"external_cpu_percent", "postgres_cpu_percent"} over the interval since
        the previous call, as a percentage of total host capacity (all cores = 100%).
        The first call primes the counters and returns zeros.
        """
        now = time.perf_counter()
        snap = self._snapshot()
        result = {"external_cpu_percent": 0.0, "postgres_cpu_percent": 0.0}
        if self._prev is not None:
            wall_100ns = (now - self._prev_t) * 1e7 * self._ncpu
            ext = pg = 0
            for pid, (cpu, is_pg) in snap.items():
                if pid == self._self_pid:
                    continue
                prev = self._prev.get(pid)
                if prev is None:
                    continue
                delta = cpu - prev[0]
                if delta <= 0:
                    continue
                if is_pg:
                    pg += delta
                else:
                    ext += delta
            if wall_100ns > 0:
                result["external_cpu_percent"] = round(min(100.0, ext * 100.0 / wall_100ns), 1)
                result["postgres_cpu_percent"] = round(min(100.0, pg * 100.0 / wall_100ns), 1)
        self._prev = snap
        self._prev_t = now
        return result


if __name__ == "__main__":
    s = ProcessCpuSampler()
    s.sample()
    for _ in range(5):
        time.sleep(0.5)
        t = time.perf_counter()
        r = s.sample()
        print(r, f"cost={(time.perf_counter() - t) * 1000:.2f} ms")
