"""
Contextual Multi-Armed Bandit (LinUCB) Online Reinforcement Learning Engine.
Autonomously explores and exploits query optimization strategies using real-time
system context and execution feedback without requiring static pre-training datasets.
"""

import os
import sys
import numpy as np
import joblib
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import STRATEGIES, STRATEGY_ID_TO_NAME, MODELS_DIR
from src.ml_engine.features import FEATURE_NAMES, extract_feature_vector


BANDIT_MODEL_PATH = MODELS_DIR / "linucb_bandit.pkl"


class LinUCBBandit:
    """
    Contextual Multi-Armed Bandit using the LinUCB algorithm with disjoint linear models.
    Each strategy arm maintains its own ridge regression parameters (A_a, b_a).
    """

    def __init__(
        self,
        num_actions: int = len(STRATEGIES),
        feature_dim: int = len(FEATURE_NAMES),
        alpha: float = 0.30,
        model_path: Optional[Path] = None,
    ):
        self.num_actions = num_actions
        self.feature_dim = feature_dim
        self.alpha = alpha
        self.model_path = model_path or BANDIT_MODEL_PATH
        self.feature_names = FEATURE_NAMES

        # Initialize Disjoint LinUCB matrices for each arm a in A:
        # A_a = d x d identity matrix (ridge regularization)
        # b_a = d-dimensional zero vector
        self.A = [np.identity(feature_dim, dtype=np.float32) for _ in range(num_actions)]
        self.b = [np.zeros(feature_dim, dtype=np.float32) for _ in range(num_actions)]

        # Telemetry & operation metrics
        self.action_counts = [0] * num_actions
        self.total_steps = 0
        self.reward_history: List[float] = []

        # Attempt to load existing bandit state from disk if available
        if self.model_path.exists():
            try:
                self.load(self.model_path)
            except Exception as ex:
                print(f"[Bandit] Note: Could not auto-load bandit from {self.model_path}: {ex}")

    def select_action(
        self, query_features: Dict[str, Any], os_snapshot: Dict[str, Any]
    ) -> Tuple[int, str, float]:
        """
        Selects the optimal strategy arm using the Upper Confidence Bound rule:
        a_t = argmax_a [ x_t^T * theta_a + alpha * sqrt(x_t^T * A_a^-1 * x_t) ]
        Returns: (arm_id, strategy_name, estimated_ucb_score)
        """
        x_t = extract_feature_vector(query_features, os_snapshot)
        # Normalize context vector to unit norm to prevent numerical instability in matrix inversion
        norm = np.linalg.norm(x_t)
        if norm > 1e-6:
            x_t = x_t / norm

        p_t = np.zeros(self.num_actions, dtype=np.float32)

        for a in range(self.num_actions):
            A_inv = np.linalg.inv(self.A[a])
            theta_a = np.dot(A_inv, self.b[a])

            # Expected payoff
            mean_payoff = float(np.dot(theta_a, x_t))
            # Uncertainty / exploration bonus
            variance = float(np.dot(x_t, np.dot(A_inv, x_t)))
            uncertainty = self.alpha * np.sqrt(max(1e-8, variance))

            p_t[a] = mean_payoff + uncertainty

        chosen_arm = int(np.argmax(p_t))
        best_score = float(p_t[chosen_arm])
        strategy_name = STRATEGY_ID_TO_NAME.get(chosen_arm, "DEFAULT")

        return chosen_arm, strategy_name, round(best_score, 4)

    def update(
        self,
        chosen_arm: int,
        query_features: Dict[str, Any],
        os_snapshot: Dict[str, Any],
        baseline_latency: float,
        actual_latency: float,
    ) -> float:
        """
        Updates the bandit model for the chosen arm based on observed latency reduction:
        Reward: r_t = (T_baseline - T_actual) / T_baseline, clipped to [-1.0, 1.0].
        A_a <- A_a + x_t * x_t^T
        b_a <- b_a + r_t * x_t
        """
        if baseline_latency <= 0:
            reward = 0.0
        else:
            reward = (baseline_latency - actual_latency) / max(1e-4, baseline_latency)
            reward = float(np.clip(reward, -1.0, 1.0))

        x_t = extract_feature_vector(query_features, os_snapshot)
        norm = np.linalg.norm(x_t)
        if norm > 1e-6:
            x_t = x_t / norm

        # Online policy update
        self.A[chosen_arm] += np.outer(x_t, x_t)
        self.b[chosen_arm] += reward * x_t

        self.action_counts[chosen_arm] += 1
        self.total_steps += 1
        self.reward_history.append(reward)

        # Periodically persist state
        if self.total_steps % 10 == 0:
            self.save(self.model_path)

        return reward

    def get_stats(self) -> Dict[str, Any]:
        """Returns action selection counts, exploration stats, and average rewards."""
        avg_reward = (
            float(np.mean(self.reward_history[-50:]))
            if self.reward_history
            else 0.0
        )
        arm_distribution = {
            STRATEGY_ID_TO_NAME.get(a, f"ARM_{a}"): cnt
            for a, cnt in enumerate(self.action_counts)
        }
        return {
            "total_steps": self.total_steps,
            "alpha_exploration": self.alpha,
            "recent_avg_reward": round(avg_reward, 3),
            "arm_distribution": arm_distribution,
        }

    def save(self, path: Optional[Path] = None):
        """Serializes bandit matrices to disk."""
        target_file = path or self.model_path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "A": self.A,
                "b": self.b,
                "action_counts": self.action_counts,
                "total_steps": self.total_steps,
                "alpha": self.alpha,
                "reward_history": self.reward_history[-500:],
            },
            target_file,
        )

    def load(self, path: Optional[Path] = None):
        """Loads bandit matrices from disk."""
        target_file = path or self.model_path
        if not target_file.exists():
            return
        data = joblib.load(target_file)
        self.A = data["A"]
        self.b = data["b"]
        self.action_counts = data.get("action_counts", [0] * self.num_actions)
        self.total_steps = data.get("total_steps", 0)
        self.alpha = data.get("alpha", self.alpha)
        self.reward_history = data.get("reward_history", [])


# Global singleton instance
default_bandit = LinUCBBandit()


if __name__ == "__main__":
    print("=" * 60)
    print("LinUCB Contextual Bandit Self-Test")
    print("=" * 60)

    bandit = LinUCBBandit(alpha=0.25)
    dummy_q = {"join_count": 2, "table_count": 3, "complexity_score": 12.0}
    dummy_os = {"cpu_percent": 88.0, "mem_percent": 65.0, "is_cpu_stressed": True}

    # Action selection
    arm, strat, ucb = bandit.select_action(dummy_q, dummy_os)
    print(f"Action Selected : {strat} (Arm: {arm}, UCB Score: {ucb})")

    # Reward update
    reward = bandit.update(arm, dummy_q, dummy_os, baseline_latency=120.0, actual_latency=35.0)
    print(f"Reward Received : {reward:.3f} (Speedup: 120ms -> 35ms)")

    stats = bandit.get_stats()
    print("Bandit Stats    :", stats)

    # Persistence check
    test_path = MODELS_DIR / "test_bandit.pkl"
    bandit.save(test_path)
    assert test_path.exists(), "Bandit file must exist after save"
    test_path.unlink()
    print("\nLinUCB Bandit Self-Test Passed Successfully!")
