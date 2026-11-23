"""Unit tests (no database needed).  Run:  python -m unittest discover -s tests -v"""

import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qs.features import feature_names, feature_vector, plan_stats
from qs.hints import BY_ID, DEFAULT_ID, HINT_SETS
from qs.model import PlanModel, choose
from qs.workload import TEMPLATES, instances

PLAN = {"Node Type": "Aggregate", "Strategy": "Hashed", "Total Cost": 1000.0, "Startup Cost": 900.0,
        "Plan Rows": 10, "Plan Width": 8, "Plans": [
            {"Node Type": "Gather", "Workers Planned": 2, "Total Cost": 950.0, "Plan Rows": 5000, "Plans": [
                {"Node Type": "Hash Join", "Total Cost": 800.0, "Plan Rows": 5000, "Plans": [
                    {"Node Type": "Seq Scan", "Total Cost": 300.0, "Plan Rows": 100000},
                    {"Node Type": "Hash", "Total Cost": 50.0, "Plan Rows": 200, "Plans": [
                        {"Node Type": "Index Scan", "Total Cost": 40.0, "Plan Rows": 200}]}]}]}]}


class WorkloadTests(unittest.TestCase):
    def test_22_templates_deterministic(self):
        self.assertEqual(len(TEMPLATES), 22)
        a, b = instances(3, seed=1), instances(3, seed=1)
        self.assertEqual([q["sql"] for q in a], [q["sql"] for q in b])
        self.assertEqual(len(a), 66)

    def test_prefix_stability(self):
        # the first k instances must not change when more are generated (pilot data stays valid)
        self.assertEqual([q["sql"] for q in instances(2)], [q["sql"] for q in instances(8) if int(q["id"][-2:]) < 2])

    def test_q02_uses_same_region_in_subquery(self):
        sql = TEMPLATES["Q02"](__import__("random").Random(3))
        regions = [part.split("'")[1] for part in sql.split("r_name = ")[1:]]
        self.assertEqual(len(set(regions)), 1)


class FeatureTests(unittest.TestCase):
    def test_plan_stats(self):
        f = plan_stats(PLAN)
        self.assertEqual(f["n_hash_join"], 1)
        self.assertEqual(f["n_seq_scan"], 1)
        self.assertEqual(f["workers"], 2)
        self.assertEqual(f["depth"], 5)
        self.assertEqual(f["hash_agg"], 1)
        self.assertAlmostEqual(f["total_cost"], math.log1p(1000.0))

    def test_vector_is_aligned(self):
        f = feature_vector(PLAN, PLAN, 3, {"external_cpu": 60.0, "level": "high"})
        self.assertEqual(list(f), feature_names())
        self.assertEqual(f["hint_3"], 1.0)
        self.assertEqual(f["same_plan_as_default"], 1.0)
        self.assertEqual(f["os_high_load"], 1.0)
        self.assertEqual(f["cost_ratio_vs_default"], 0.0)


class SelectionTests(unittest.TestCase):
    ids = [h["id"] for h in HINT_SETS]

    def test_confident_win_is_taken(self):
        mean = np.zeros(len(self.ids)); std = np.zeros(len(self.ids))
        mean[5] = math.log(1 / 2.0)               # predicted 2x faster
        self.assertEqual(choose(mean, std, self.ids, 0.15, 0.5)[0], self.ids[5])

    def test_uncertain_win_keeps_default(self):
        mean = np.zeros(len(self.ids)); std = np.zeros(len(self.ids))
        mean[5], std[5] = math.log(1 / 1.3), 0.6  # looks 1.3x faster but very uncertain
        self.assertEqual(choose(mean, std, self.ids, 0.15, 0.5)[0], DEFAULT_ID)

    def test_small_win_keeps_default(self):
        mean = np.zeros(len(self.ids)); std = np.zeros(len(self.ids))
        mean[5] = math.log(1 / 1.05)
        self.assertEqual(choose(mean, std, self.ids, 0.15, 0.0)[0], DEFAULT_ID)


class ModelTests(unittest.TestCase):
    def test_learns_simple_pattern(self):
        rng = np.random.default_rng(0)
        n = len(feature_names())
        X = rng.normal(size=(400, n))
        y = 0.8 * X[:, 0]                          # target depends on one feature
        m = PlanModel(n_models=2).fit(X, y)
        mean, std = m.predict(X[:50])
        self.assertGreater(np.corrcoef(mean, y[:50])[0, 1], 0.9)
        self.assertTrue((std >= 0).all())


class HintTests(unittest.TestCase):
    def test_ids_unique_and_default_empty(self):
        self.assertEqual(len(BY_ID), len(HINT_SETS))
        self.assertEqual(BY_ID[DEFAULT_ID]["settings"], {})


if __name__ == "__main__":
    unittest.main()
