"""Policy unit tests (no database). Run: python -m unittest discover -s tests -v"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.optimizer import normalize_sql, template_of
from src.policy import DEFAULT, SERIAL, Policy, dominance


def policy(**kw):
    kw.setdefault("recheck_every", 0)
    return Policy(path=None, min_samples=3, max_samples=8, **kw)


def drive(p, latency, steps, tpl="t", level="low"):
    """Runs choose/record against fixed latencies per option; returns the options chosen."""
    chosen = []
    for _ in range(steps):
        arm, _ = p.choose(tpl, level)
        p.record(tpl, level, arm, latency[arm])
        chosen.append(arm)
    return chosen


class PolicyTests(unittest.TestCase):
    def test_non_parallel_query_always_default(self):
        p = policy()
        p.register("t", "Q03", parallel=False)
        self.assertEqual(drive(p, {DEFAULT: 100, SERIAL: 10}, 10), [DEFAULT] * 10)

    def test_learns_alternating_then_picks_faster_serial(self):
        p = policy()
        p.register("t", "Q06", parallel=True)
        chosen = drive(p, {DEFAULT: 100, SERIAL: 40}, 12)
        self.assertEqual(chosen[:6], [DEFAULT, SERIAL] * 3)
        self.assertEqual(set(chosen[6:]), {SERIAL})

    def test_keeps_default_when_serial_slower(self):
        p = policy()
        p.register("t", "Q01", parallel=True)
        self.assertEqual(set(drive(p, {DEFAULT: 100, SERIAL: 130}, 12)[6:]), {DEFAULT})

    def test_slow_serial_rejected_after_two_runs(self):
        p = policy()
        p.register("t", "Q01", parallel=True)
        self.assertEqual(drive(p, {DEFAULT: 100, SERIAL: 400}, 12).count(SERIAL), 2)

    def test_single_slow_outlier_does_not_reject(self):
        # Q11 under stress: first serial run is an outlier, the plan is really much faster
        p = policy()
        p.register("t", "Q11", parallel=True)
        p.record("t", "high", DEFAULT, 440)
        p.record("t", "high", SERIAL, 969)
        chosen = drive(p, {DEFAULT: 440, SERIAL: 170}, 12, level="high")
        self.assertEqual(chosen[-1], SERIAL)

    def test_rejection_is_sticky_despite_noisy_default_runs(self):
        # Q01 under stress: first SERIAL run 1.6x slower -> rejected. Later DEFAULT runs are
        # noisy and push the ratio below 1.5x; SERIAL must still not be explored again.
        p = policy()
        p.register("t", "Q01", parallel=True)
        p.record("t", "high", DEFAULT, 440)
        p.record("t", "high", SERIAL, 700)
        p.record("t", "high", SERIAL, 720)
        for ms in (520, 610, 590, 640, 600):
            arm, _ = p.choose("t", "high")
            self.assertEqual(arm, DEFAULT)
            p.record("t", "high", arm, ms)

    def test_rejection_can_be_lifted_by_recheck(self):
        p = Policy(path=None, min_samples=3, recheck_every=4)
        p.register("t", "Q", parallel=True)
        p.record("t", "low", DEFAULT, 100)
        p.record("t", "low", SERIAL, 200)
        p.record("t", "low", SERIAL, 210)              # rejected
        chosen = drive(p, {DEFAULT: 100, SERIAL: 40}, 40)
        self.assertEqual(chosen[-1], SERIAL)           # serial got faster; re-checks noticed

    def test_small_or_noisy_difference_keeps_default(self):
        p = policy()
        p.register("t", "Q", parallel=True)
        for x in (25, 50, 51, 52, 24):       # bimodal latencies, same distribution
            p.record("t", "low", DEFAULT, x)
        for x in (24, 26, 51, 25, 53):
            p.record("t", "low", SERIAL, x)
        self.assertEqual(p.choose("t", "low")[0], DEFAULT)

    def test_decision_is_per_load_level(self):
        p = policy()
        p.register("t", "Q", parallel=True)
        low = drive(p, {DEFAULT: 100, SERIAL: 40}, 10, level="low")
        high = drive(p, {DEFAULT: 100, SERIAL: 160}, 10, level="high")
        self.assertEqual((low[-1], high[-1]), (SERIAL, DEFAULT))

    def test_periodic_recheck(self):
        p = Policy(path=None, min_samples=3, recheck_every=5)
        p.register("t", "Q", parallel=True)
        chosen = drive(p, {DEFAULT: 100, SERIAL: 40}, 20)
        self.assertIn(DEFAULT, chosen[6:])          # re-checks still happen
        self.assertGreater(chosen[6:].count(SERIAL), 10)

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = Policy(path=Path(d) / "p.json", min_samples=3, recheck_every=0)
            p.register("t", "Q06", parallel=True)
            drive(p, {DEFAULT: 100, SERIAL: 40}, 6)
            p.save()
            q = Policy(path=Path(d) / "p.json", min_samples=3, recheck_every=0)
            self.assertEqual(q.choose("t", "low")[0], SERIAL)
            self.assertEqual(q.summary()[0]["label"], "Q06")


class HelperTests(unittest.TestCase):
    def test_dominance(self):
        self.assertEqual(dominance([1, 2], [3, 4]), 1.0)
        self.assertEqual(dominance([2], [2]), 0.5)

    def test_template_ignores_literals_whitespace_and_semicolon(self):
        a = "SELECT * FROM t WHERE x = 5 AND y = 'abc';"
        b = "select *  from t\nwhere x = 70 and y = 'zz'"
        self.assertEqual(normalize_sql(a), normalize_sql(b))
        self.assertEqual(template_of(a), template_of(b))
        self.assertNotEqual(template_of(a), template_of("SELECT * FROM u WHERE x = 5"))


if __name__ == "__main__":
    unittest.main()
