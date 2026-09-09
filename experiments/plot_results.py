"""
Results Visualization Script.
Generates publication-quality charts for the project report:
1. Latency comparison bar chart (Baseline vs OS-Aware)
2. Latency over time under stress injection
3. Feature importance plot from the trained XGBoost model
"""

import sys
import json
from pathlib import Path
import matplotlib.pyplot as plt
import seaborn as sns

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import REPORTS_DIR
from src.ml_engine.model import OSOptimizerModel


def set_plotting_style():
    """Sets a clean aesthetic for research diagrams."""
    sns.set_theme(style="whitegrid")
    plt.rcParams.update(
        {
            "font.size": 11,
            "axes.labelsize": 12,
            "axes.titlesize": 14,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 11,
            "figure.titlesize": 15,
        }
    )


def plot_latency_comparison(results_file: Path):
    """Generates comparative bar chart (Average & P95 latency)."""
    if not results_file.exists():
        print(f"[Plot] Results file not found at {results_file}")
        return

    with open(results_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    res = data["results"]
    conditions = ["Normal Load", "Under CPU Stress"]
    default_avg = [res["baseline_normal"]["avg_latency_ms"], res["baseline_stressed"]["avg_latency_ms"]]
    osaware_avg = [res["os_aware_normal"]["avg_latency_ms"], res["os_aware_stressed"]["avg_latency_ms"]]

    default_p95 = [res["baseline_normal"]["p95_latency_ms"], res["baseline_stressed"]["p95_latency_ms"]]
    osaware_p95 = [res["os_aware_normal"]["p95_latency_ms"], res["os_aware_stressed"]["p95_latency_ms"]]

    import numpy as np
    x = np.arange(len(conditions))
    width = 0.35

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    # Subplot 1: Average Latency
    rects1 = ax1.bar(x - width/2, default_avg, width, label="Default PostgreSQL", color="#e74c3c", alpha=0.9)
    rects2 = ax1.bar(x + width/2, osaware_avg, width, label="OS-Aware (QuerySync)", color="#2ecc71", alpha=0.9)
    ax1.set_ylabel("Average Latency (ms)")
    ax1.set_title("Average Query Latency Comparison")
    ax1.set_xticks(x)
    ax1.set_xticklabels(conditions)
    ax1.legend()
    ax1.bar_label(rects1, padding=3, fmt="%.1f ms")
    ax1.bar_label(rects2, padding=3, fmt="%.1f ms")

    # Subplot 2: P95 Latency
    rects3 = ax2.bar(x - width/2, default_p95, width, label="Default PostgreSQL", color="#c0392b", alpha=0.9)
    rects4 = ax2.bar(x + width/2, osaware_p95, width, label="OS-Aware (QuerySync)", color="#27ae60", alpha=0.9)
    ax2.set_ylabel("P95 Tail Latency (ms)")
    ax2.set_title("95th Percentile (Tail) Latency Comparison")
    ax2.set_xticks(x)
    ax2.set_xticklabels(conditions)
    ax2.legend()
    ax2.bar_label(rects3, padding=3, fmt="%.1f ms")
    ax2.bar_label(rects4, padding=3, fmt="%.1f ms")

    plt.tight_layout()
    out_path = REPORTS_DIR / "latency_comparison_bar_chart.png"
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"[Plot] Saved latency bar chart to: {out_path}")


def plot_latency_timeline(results_file: Path):
    """Plots latency progression comparing baseline vs OS-aware under stress."""
    if not results_file.exists():
        return

    with open(results_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    res = data["results"]
    b_stressed_lats = res["baseline_stressed"].get("raw_latencies", [])
    o_stressed_lats = res["os_aware_stressed"].get("raw_latencies", [])

    if not b_stressed_lats or not o_stressed_lats:
        return

    plt.figure(figsize=(10, 5))
    queries_idx = range(1, len(b_stressed_lats) + 1)

    plt.plot(queries_idx, b_stressed_lats, marker="o", linewidth=2, color="#e74c3c", label="Default PostgreSQL (Under Stress)")
    plt.plot(queries_idx, o_stressed_lats, marker="s", linewidth=2, color="#2ecc71", label="OS-Aware Adaptive Optimizer")

    plt.title("Query Latency Profile During Background Resource Contention")
    plt.xlabel("Query Sequence Index")
    plt.ylabel("Execution Latency (ms)")
    plt.legend()
    plt.grid(True, linestyle="--", alpha=0.6)

    plt.tight_layout()
    out_path = REPORTS_DIR / "latency_over_time_under_stress.png"
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"[Plot] Saved timeline plot to: {out_path}")


def plot_feature_importance():
    """Plots top feature importances of the XGBoost decision model."""
    model = OSOptimizerModel()
    importances = model.get_feature_importances()
    if not importances:
        print("[Plot] No feature importances available (model not trained).")
        return

    sorted_items = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:10]
    names = [k for k, v in reversed(sorted_items)]
    scores = [v for k, v in reversed(sorted_items)]

    plt.figure(figsize=(9, 5.5))
    colors = ["#3498db" if n.startswith("os_") else "#e67e22" for n in names]
    bars = plt.barh(names, scores, color=colors, alpha=0.85)

    plt.xlabel("Relative Importance Score")
    plt.title("Top-10 Feature Importances in ML Decision Engine\n(Blue: OS Telemetry, Orange: Query Structure)")
    plt.bar_label(bars, fmt="%.3f", padding=3)

    plt.tight_layout()
    out_path = REPORTS_DIR / "model_feature_importance.png"
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"[Plot] Saved feature importance plot to: {out_path}")


def generate_all_plots():
    set_plotting_style()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    results_file = REPORTS_DIR / "benchmark_results.json"
    plot_latency_comparison(results_file)
    plot_latency_timeline(results_file)
    plot_feature_importance()


if __name__ == "__main__":
    generate_all_plots()
