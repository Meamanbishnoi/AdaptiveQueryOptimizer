"""Dashboard server: http://127.0.0.1:5000  (python main.py ui)."""

import json
import random
import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from qs.config import OS_BOOST, RESULTS_DIR

PAGE = Path(__file__).resolve().parent / "index.html"

_opt = None
_opt_lock = threading.Lock()
_run_lock = threading.Lock()   # one measurement at a time, so runs never compete with each other
_stress = None
_bench = {"running": False, "done": 0, "total": 0, "result": None, "error": None}
_sched = {"running": False, "stage": "", "done": 0, "total": 0, "result": None, "error": None}


def optimizer():
    global _opt
    with _opt_lock:
        if _opt is None:
            from qs.optimizer import Optimizer
            _opt = Optimizer()
        return _opt


def demo_queries():
    """One fresh instance of each TPC-H template (seed 7: not used for training)."""
    from qs.workload import instances
    return instances(1, seed=7)


def compare(sql: str, runs: int, label=None) -> dict:
    """Runs default and the AI `runs` times each, alternating, after one warm-up; compares medians."""
    opt = optimizer()
    with _run_lock:
        opt.db.run(sql, {})
        base, mine = [], []
        for i in range(runs):
            for m in (("default", "ai") if i % 2 == 0 else ("ai", "default")):
                r = opt.execute(sql, mode=m, label=label, learn=(m == "ai"),
                                os_boost=OS_BOOST if m == "ai" else "off")
                (base if m == "default" else mine).append(r)
    b = statistics.median(r["latency_ms"] for r in base)
    o = statistics.median(r["latency_ms"] + r["overhead_ms"] for r in mine)
    change = (o - b) / b * 100
    last = mine[-1]
    if all(r["hint"] == 0 for r in mine):
        verdict = "default"
    else:
        verdict = "faster" if change <= -10 else "slower" if change >= 10 else "same"
    return {"verdict": verdict, "change_pct": round(change, 1), "default_ms": round(b, 1), "ai_ms": round(o, 1),
            "default_runs": [r["latency_ms"] for r in base], "ai_runs": [r["latency_ms"] for r in mine],
            "choices": [r["hint_name"] for r in mine], "reason": last["reason"], "predictions": last["predictions"],
            "overhead_ms": round(statistics.median(r["overhead_ms"] for r in mine), 1), "load": last["load"],
            "os_boost": any(r["os_boost"] for r in mine), "predicted_ms": last["predicted_ms"]}


def quick_benchmark(rounds: int):
    """Default vs AI on one instance of each template at the current load, interleaved."""
    from experiments.validate import permutation_p
    opt = optimizer()
    qs = demo_queries()
    _bench.update(done=0, total=len(qs) * (1 + 2 * rounds), result=None, error=None)
    try:
        lat = {q["id"]: {"default": [], "ai": []} for q in qs}
        choice = {q["id"]: [] for q in qs}
        with _run_lock:
            for q in qs:
                opt.db.run(q["sql"], {})
                _bench["done"] += 1
            for _ in range(rounds):
                for q in qs:
                    for m in random.sample(["default", "ai"], 2):
                        r = opt.execute(q["sql"], mode=m, label=q["template"], learn=False,
                                        os_boost=OS_BOOST if m == "ai" else "off")
                        lat[q["id"]][m].append(r["latency_ms"] + (r["overhead_ms"] if m == "ai" else 0))
                        if m == "ai":
                            choice[q["id"]].append(r["hint_name"])
                        _bench["done"] += 1
        rows = []
        for q in qs:
            d, a = statistics.median(lat[q["id"]]["default"]), statistics.median(lat[q["id"]]["ai"])
            p = permutation_p(lat[q["id"]]["ai"], lat[q["id"]]["default"])
            ratio = a / d
            rows.append({"id": q["template"], "default_ms": round(d, 1), "ai_ms": round(a, 1), "speedup": round(d / a, 3),
                         "p": round(p, 4), "choice": max(set(choice[q["id"]]), key=choice[q["id"]].count),
                         "outcome": "win" if ratio < 0.9 and p < 0.05 else "loss" if ratio > 1.1 and p < 0.05 else "same"})
        import math
        _bench["result"] = {"rows": rows, "rounds": rounds, "load": opt.telemetry.snapshot(),
                            "default_s": round(sum(r["default_ms"] for r in rows) / 1000, 2),
                            "ai_s": round(sum(r["ai_ms"] for r in rows) / 1000, 2),
                            "geomean": round(math.exp(statistics.mean(math.log(r["speedup"]) for r in rows)), 3),
                            "wins": sum(r["outcome"] == "win" for r in rows),
                            "losses": sum(r["outcome"] == "loss" for r in rows),
                            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as ex:
        _bench["error"] = str(ex).strip()
    finally:
        _bench["running"] = False


def scheduler_demo(policy: str, n_jobs: int):
    """
    Live multi-user demo: the same arrival trace is replayed twice - plain PostgreSQL (default plans,
    every query starts at once) and QuerySync (AI plans + OS scheduler with `policy`).
    """
    from experiments.schedule import STRATEGIES, run_trace
    from qs.scheduler import metrics
    from qs.workload import make_trace
    opt = optimizer()
    try:
        level = opt.telemetry.snapshot()["level"]
        rep = _read_json("scheduling.json") or {}
        rate = (rep.get("policies", {}).get("stress" if level == "high" else "idle", {}) or {}).get("rate_per_s", 1.0)
        trace = make_trace(n_jobs, rate, seed=int(time.time()) % 1000 + 100)
        _sched.update(total=2 * n_jobs, done=0, result=None, error=None)
        out = {"policy": policy, "rate_per_s": rate, "n_jobs": n_jobs, "load": opt.telemetry.snapshot(), "runs": {}}
        with _run_lock:
            for k, name in enumerate(("postgres", policy)):
                _sched["stage"] = "plain PostgreSQL" if name == "postgres" else f"QuerySync ({policy})"
                prog = lambda d, n, k=k: _sched.update(done=k * n_jobs + d)
                jobs, s = run_trace(trace, opt, progress=prog, **STRATEGIES[name])
                out["runs"]["postgres" if name == "postgres" else "querysync"] = {
                    "metrics": metrics(jobs), "jobs": [j.as_dict() for j in sorted(jobs, key=lambda j: j.id)]}
        out["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _sched["result"] = out
    except Exception as ex:
        _sched["error"] = str(ex).strip()
    finally:
        _sched["running"] = False


def _read_json(name):
    p = RESULTS_DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


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
            snap["model"] = optimizer().model is not None
            from qs import osctl
            if snap["level"] == "low" and not _run_lock.locked() and osctl._shared and osctl._shared.active:
                osctl._shared.set(False)   # load is gone and nothing is running: back to NORMAL priority
            snap["os_boost"] = bool(osctl._shared and osctl._shared.active)
            snap["os_boost_mode"] = OS_BOOST
            self._json(snap)
        elif self.path == "/api/queries":
            self._json([{"id": q["template"], "sql": q["sql"]} for q in demo_queries()])
        elif self.path == "/api/report":
            self._json({"validation": _read_json("validation.json"), "evaluation": _read_json("evaluation.json"),
                        "scheduling": _read_json("scheduling.json")})
        elif self.path == "/api/model":
            m = optimizer().model
            self._json({"trained": m is not None, "features": m.feature_importance(10) if m else []})
        elif self.path == "/api/quickbench":
            self._json(_bench)
        elif self.path == "/api/schedule":
            self._json(_sched)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        global _stress
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/execute":
            sql = body.get("sql", "").strip()
            if not sql:
                return self._json({"error": "empty SQL"}, 400)
            try:
                self._json(compare(sql, max(1, min(9, int(body.get("runs", 3)))), body.get("label")))
            except Exception as ex:
                self._json({"error": str(ex).strip()}, 400)
        elif self.path == "/api/quickbench":
            if _bench["running"] or _sched["running"]:
                return self._json({"error": "already running"}, 409)
            _bench["running"] = True
            threading.Thread(target=quick_benchmark, args=(max(5, min(9, int(body.get("rounds", 5)))),),
                             daemon=True).start()
            self._json({"started": True})
        elif self.path == "/api/schedule":
            from qs.scheduler import POLICIES
            policy = body.get("policy", "sjf")
            if policy not in POLICIES:
                return self._json({"error": f"policy must be one of {POLICIES}"}, 400)
            if _sched["running"] or _bench["running"]:
                return self._json({"error": "already running"}, 409)
            _sched["running"] = True
            threading.Thread(target=scheduler_demo, args=(policy, max(10, min(60, int(body.get("n_jobs", 24))))),
                             daemon=True).start()
            self._json({"started": True})
        elif self.path == "/api/stress":
            from qs.stress import StressGenerator
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
    optimizer()
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
        from qs.optimizer import set_boost
        set_boost(False)
