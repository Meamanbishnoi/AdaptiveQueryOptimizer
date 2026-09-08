"""
Model Training Script.
Fetches recorded training data from FeedbackStore, trains the XGBoost model,
evaluates performance metrics, displays feature importances, and saves to disk.
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.feedback.feedback_store import FeedbackStore
from src.ml_engine.model import OSOptimizerModel


def run_training():
    print("=" * 65)
    print("OS-Aware Query Optimizer: Model Training Pipeline")
    print("=" * 65)

    store = FeedbackStore()
    df = store.fetch_training_data()

    if df.empty or len(df) < 50:
        print(f"[Train] Insufficient training data ({len(df)} samples). Generating dataset first...")
        from src.ml_engine.dataset_generator import generate_training_dataset
        generate_training_dataset(target_samples=400, store=store)
        df = store.fetch_training_data()

    print(f"[Train] Loaded {len(df)} execution records from feedback store.")
    print(f"[Train] Strategy distribution in dataset:")
    strat_counts = df["strategy_name"].value_counts()
    for name, cnt in strat_counts.items():
        print(f"   - {name:<20s}: {cnt:4d} ({cnt/len(df):.1%})")

    model = OSOptimizerModel()
    results = model.train(df)

    print("\n" + "=" * 65)
    print(f"Training Results ({results['model_type']}):")
    print("=" * 65)
    print(f"Test Accuracy     : {results['accuracy']:.2%}")
    print(f"Evaluated Samples : {results['sample_count']}")

    print("\nTop 10 Influential Features:")
    importances = model.get_feature_importances()
    sorted_features = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:10]
    for rank, (feat, score) in enumerate(sorted_features, 1):
        print(f"   {rank:2d}. {feat:<25s}: {score:.4f}")

    print(f"\nModel successfully saved to: {model.model_path}")
    return results


if __name__ == "__main__":
    run_training()
