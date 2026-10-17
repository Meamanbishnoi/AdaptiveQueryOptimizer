"""Dashboard server: http://127.0.0.1:5000 (python main.py ui)."""

import json
import random
import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = Path(__file__).resolve().parent / "index.html"
REPORT = Path(__file__).resolve().parent.parent / "reports" / "benchmark.json"

_opt = None
_opt_lock = threading.Lock()
_run_lock = threading.Lock()   # one comparison at a time, so runs don't compete with each other
_stress = None
_bench = {"running": False, "done": 0, "total": 0, "result": None, "error": None}


def optimizer():
    global _opt
    with _opt_lock:
        if _opt is None:
            from src.optimizer import Optimizer
            _opt = Optimizer()
        return _opt


def compare(sql: str, mode: str, runs: int, label=None) -> dict:
    """Runs DEFAULT and `mode` alternately `runs` times each (after a warm-up), compares medians."""
    opt = optimizer()
    with _run_lock:
        opt.execute(sql, mode="default", label=label, learn=False)
        base, mine = [], []
        for i in range(runs):
            pair = ("default", mode) if i % 2 == 0 else (mode, "default")
            for m in pair:
                r = opt.execute(sql, mode=m, label=label, learn=(m == mode))
                (base if m == "default" else mine).append(r)
        opt.save()
    b = statistics.median(r["latency_ms"] for r in base)
    o = statistics.median(r["latency_ms"] + r["overhead_ms"] for r in mine)
    change = (o - b) / b * 100
    last = mine[-1]
    if all(r["option"] == "default" for r in mine):
        verdict = "default"
    elif any(r["reason"].startswith("learning") for r in mine):
        verdict = "learning"
    else:
        verdict = "faster" if change <= -10 else "slower" if change >= 10 else "same"
    return {
        "verdict": verdict, "change_pct": round(change, 1),
        "default_ms": round(b, 1), "optimized_ms": round(o, 1),
        "default_runs": [r["latency_ms"] for r in base], "optimized_runs": [r["latency_ms"] for r in mine],
        "options": [r["option"] for r in mine], "reasons": [r["reason"] for r in mine],
        "reason": last["reason"], "parallel_plan": last["parallel_plan"], "load": last["load"],
        "overhead_ms": round(statistics.median(r["overhead_ms"] for r in mine), 3), "label": last["label"],
    }


def quick_benchmark(rounds: int):
    """
    Default PostgreSQL vs QuerySync on all 22 queries at the current load: one warm-up run
    per query, then `rounds` rounds with both modes in random order. Runs in a thread; the
    page polls /api/quickbench for progress and the result.
    """
    from experiments.benchmark import evaluate, load_queries
    opt = optimizer()
    queries = load_queries()
    _bench.update(done=0, total=len(queries) * (1 + 2 * rounds), result=None, error=None)
    try:
        with _run_lock:
            for i, q in enumerate(queries, 1):
                opt.execute(q["sql"], mode="default", label=q["id"], learn=False)
                _bench["done"] = i
            res = evaluate(opt, queries, rounds, random.Random(), modes=["default", "adaptive"],
                           progress=lambda d, t: _bench.update(done=len(queries) + d))
        res.update(rounds=rounds, load=opt.telemetry.snapshot(),
                   stress=bool(_stress and _stress.is_active()),
                   finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        _bench["result"] = res
    except Exception as ex:
        _bench["error"] = str(ex).strip()
    finally:
        _bench["running"] = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/telemetry":
            snap = optimizer().telemetry.snapshot()
            snap["stress"] = bool(_stress and _stress.is_active())
            self._json(snap)
        elif self.path == "/api/queries":
            from experiments.benchmark import load_queries
            self._json(load_queries())
        elif self.path == "/api/policy":
            self._json(optimizer().policy.summary())
        elif self.path == "/api/report":
            if REPORT.exists():
                self._json(json.loads(REPORT.read_text(encoding="utf-8")))
            else:
                self._json({"error": "No full benchmark yet. Run: python main.py bench"}, 404)
        elif self.path == "/api/quickbench":
            self._json(_bench)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        global _stress
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/execute":
            sql, mode = body.get("sql", "").strip(), body.get("mode", "adaptive")
            if not sql or mode not in ("adaptive", "rule"):
                return self._json({"error": "need sql and mode adaptive|rule"}, 400)
            try:
                runs = max(1, min(15, int(body.get("runs", 5))))
                self._json(compare(sql, mode, runs, body.get("label") or None))
            except Exception as ex:
                self._json({"error": str(ex).strip()}, 400)
        elif self.path == "/api/quickbench":
            if _bench["running"]:
                return self._json({"error": "a quick benchmark is already running"}, 409)
            rounds = max(3, min(10, int(body.get("rounds", 5))))
            _bench["running"] = True
            threading.Thread(target=quick_benchmark, args=(rounds,), daemon=True).start()
            self._json({"started": True})
        elif self.path == "/api/stress":
            from experiments.stress import StressGenerator
            if _stress and _stress.is_active():
                _stress.stop_stress()
                _stress = None
            else:
                _stress = StressGenerator()
                _stress.start_stress(duty_cycle=0.9)
            self._json({"stress": bool(_stress)})
        else:
            self._json({"error": "not found"}, 404)


def serve(port: int = 5000, open_browser: bool = True):
    optimizer()  # connect + start telemetry before accepting requests
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"QuerySync dashboard: {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if _stress:
            _stress.stop_stress()
        optimizer().save()
