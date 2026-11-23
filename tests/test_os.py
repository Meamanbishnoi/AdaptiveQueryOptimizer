"""Unit tests for the OS layer (no database needed): scheduling policies, aging, memory admission, metrics."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qs.features import memory_need_mb
from qs.scheduler import AGING_S, Job, QueryScheduler, metrics
from qs.workload import INTERACTIVE, make_trace


def sched(policy, mpl=2, budget=1000.0):
    s = QueryScheduler(policy=policy, mpl=mpl, optimizer=object(), memory_budget_mb=budget)
    s.now = lambda: s.clock
    s.clock = 0.0
    return s


def job(jid, arrival, ms, cls="batch", mem=10.0):
    j = Job(jid, "select 1", "Q00", cls, arrival)
    j.predicted_ms, j.mem_mb = ms, mem
    return j


class Policies(unittest.TestCase):
    def test_fcfs_picks_earliest_arrival(self):
        s = sched("fcfs")
        s.ready = [job(1, 2.0, 100), job(2, 1.0, 9000), job(3, 3.0, 50)]
        s.clock = 5.0
        self.assertEqual(s._pick().id, 2)

    def test_sjf_picks_shortest_predicted(self):
        s = sched("sjf")
        s.ready = [job(1, 0.0, 5000), job(2, 1.0, 120), job(3, 2.0, 800)]
        s.clock = 3.0
        self.assertEqual(s._pick().id, 2)

    def test_hrrn_prefers_short_but_ages_long_jobs(self):
        s = sched("hrrn")
        long_job, short_job = job(1, 0.0, 4000), job(2, 0.0, 200)
        s.ready = [long_job, short_job]
        s.clock = 0.5
        self.assertEqual(s._pick().id, 2)            # equal waits: short job has the higher ratio
        short_job.arrival = 59.9                    # a new short job vs a long job waiting a minute
        s.clock = 60.0
        self.assertEqual(s._pick().id, 1)            # (60+4)/4 = 16  >  (0.1+0.2)/0.2 = 1.5

    def test_priority_with_aging(self):
        s = sched("priority")
        s.ready = [job(1, 0.0, 3000, "batch"), job(2, 0.5, 300, "interactive")]
        s.clock = 1.0
        self.assertEqual(s._pick().id, 2)            # interactive first
        s.ready = [job(1, 0.0, 3000, "batch"), job(2, 2 * AGING_S, 300, "interactive")]
        s.clock = 2 * AGING_S
        self.assertEqual(s._pick().id, 1)            # batch waited 2 aging periods -> overtakes

    def test_mpl_limit(self):
        s = sched("fcfs", mpl=1)
        s.ready = [job(1, 0.0, 100)]
        s.running = 1
        self.assertIsNone(s._pick())

    def test_express_slot_only_for_short_jobs(self):
        s = QueryScheduler(policy="fcfs", mpl=2, optimizer=object(), express_slots=1, express_ms=500)
        s.now = lambda: 10.0
        s.running = 2                                 # normal slots full
        s.ready = [job(1, 0.0, 4000), job(2, 5.0, 200)]
        self.assertEqual(s._pick().id, 2)            # the short job may use the express slot
        s.ready = [job(1, 0.0, 4000)]
        self.assertIsNone(s._pick())                 # a long job may not
        s.running = 3
        s.ready = [job(2, 5.0, 200)]
        self.assertIsNone(s._pick())                 # express slot taken too


class MemoryAdmission(unittest.TestCase):
    def test_unsafe_job_waits_and_smaller_job_goes_first(self):
        s = sched("fcfs", mpl=4, budget=500.0)
        s.running, s.mem_in_use = 1, 400.0
        big, small = job(1, 0.0, 100, mem=300.0), job(2, 1.0, 100, mem=50.0)
        s.ready = [big, small]
        self.assertEqual(s._pick().id, 2)            # big would exceed the budget (unsafe state)
        s.ready = [big]
        self.assertIsNone(s._pick())
        self.assertEqual(s.memory_waits, 1)            # one distinct job delayed by memory

    def test_oversized_job_runs_alone(self):
        s = sched("fcfs", mpl=4, budget=100.0)
        s.ready = [job(1, 0.0, 100, mem=900.0)]
        self.assertEqual(s._pick().id, 1)            # nothing running -> may run alone (no deadlock)

    def test_memory_need_counts_hash_sort_and_workers(self):
        plan = {"Node Type": "Gather", "Workers Planned": 2, "Plans": [
            {"Node Type": "Hash Join", "Plans": [{"Node Type": "Seq Scan"}, {"Node Type": "Hash", "Plans": [{"Node Type": "Seq Scan"}]}]},
            {"Node Type": "Sort", "Plans": []}]}
        self.assertEqual(memory_need_mb(plan, {}), (2 + 1) * 4 * 3)              # (hash 2x + sort) x 4MB x 3 procs
        self.assertEqual(memory_need_mb(plan, {"work_mem": "'64MB'"}), (2 + 1) * 64 * 3)


class Metrics(unittest.TestCase):
    def test_turnaround_wait_and_classes(self):
        a, b = job(1, 0.0, 0, "interactive"), job(2, 1.0, 0, "batch")
        a.start, a.end, b.start, b.end = 0.5, 1.5, 1.5, 4.0
        m = metrics([a, b])
        self.assertAlmostEqual(m["avg_turnaround_s"], (1.5 + 3.0) / 2)
        self.assertAlmostEqual(m["avg_wait_s"], (0.5 + 0.5) / 2)
        self.assertEqual(m["avg_interactive_s"], 1.5)
        self.assertEqual(m["avg_batch_s"], 3.0)


class Trace(unittest.TestCase):
    def test_deterministic_poisson_trace(self):
        t1, t2 = make_trace(50, 2.0, seed=4), make_trace(50, 2.0, seed=4)
        self.assertEqual([x["sql"] for x in t1], [x["sql"] for x in t2])
        self.assertTrue(all(b["at"] >= a["at"] for a, b in zip(t1, t1[1:])))
        share = sum(x["cls"] == "interactive" for x in make_trace(400, 2.0, seed=5)) / 400
        self.assertTrue(0.5 < share < 0.7)
        self.assertTrue(all((x["template"] in INTERACTIVE) == (x["cls"] == "interactive") for x in t1))


if __name__ == "__main__":
    unittest.main()
