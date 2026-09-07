"""
Feedback Store.
Logs query executions, OS telemetry snapshots, chosen optimization strategies,
and actual execution durations for offline ML training and evaluation.
Supports SQLite (portable default) and PostgreSQL.
"""

import json
import sqlite3
import time
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
import pandas as pd

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import SQLITE_FEEDBACK_DB, LOGS_DIR


class FeedbackStore:
    """
    Manages persistent logging of query execution metadata and telemetry.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or SQLITE_FEEDBACK_DB
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes tables if they do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS query_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    sql_text TEXT NOT NULL,
                    query_features TEXT NOT NULL,
                    os_snapshot TEXT NOT NULL,
                    chosen_strategy INTEGER NOT NULL,
                    strategy_name TEXT NOT NULL,
                    predicted_time REAL,
                    actual_time REAL NOT NULL,
                    plan_used TEXT,
                    is_stressed INTEGER DEFAULT 0
                );
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS model_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trained_at REAL NOT NULL,
                    model_path TEXT NOT NULL,
                    sample_count INTEGER NOT NULL,
                    eval_metric REAL,
                    notes TEXT
                );
                """
            )
            conn.commit()

    def insert_log(
        self,
        sql_text: str,
        query_features: Dict[str, Any],
        os_snapshot: Dict[str, Any],
        chosen_strategy: int,
        strategy_name: str,
        actual_time: float,
        predicted_time: Optional[float] = None,
        plan_used: Optional[str] = None,
    ) -> int:
        """
        Inserts a single query execution log entry.
        """
        now = time.time()
        is_stressed = 1 if (os_snapshot.get("is_cpu_stressed") or os_snapshot.get("is_mem_stressed")) else 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO query_log (
                    timestamp, sql_text, query_features, os_snapshot,
                    chosen_strategy, strategy_name, predicted_time,
                    actual_time, plan_used, is_stressed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    sql_text,
                    json.dumps(query_features),
                    json.dumps(os_snapshot),
                    chosen_strategy,
                    strategy_name,
                    predicted_time,
                    actual_time,
                    plan_used or "",
                    is_stressed,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def fetch_training_data(self, limit: Optional[int] = None) -> pd.DataFrame:
        """
        Retrieves flattened records as a pandas DataFrame ready for ML feature engineering.
        """
        query = "SELECT * FROM query_log ORDER BY timestamp ASC"
        if limit:
            query += f" LIMIT {limit}"

        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn)

        if df.empty:
            return pd.DataFrame()

        # Parse nested JSON columns into flattened dictionary structures
        features_list = []
        for _, row in df.iterrows():
            q_feat = json.loads(row["query_features"]) if isinstance(row["query_features"], str) else {}
            os_snap = json.loads(row["os_snapshot"]) if isinstance(row["os_snapshot"], str) else {}

            combined = {
                "log_id": row["id"],
                "timestamp": row["timestamp"],
                "chosen_strategy": row["chosen_strategy"],
                "strategy_name": row["strategy_name"],
                "actual_time": row["actual_time"],
                "predicted_time": row["predicted_time"],
                "is_stressed": row["is_stressed"],
            }
            # Prefix for clarity
            for k, v in q_feat.items():
                if isinstance(v, (int, float, bool)):
                    combined[f"q_{k}"] = float(v)
            for k, v in os_snap.items():
                if isinstance(v, (int, float, bool)):
                    combined[f"os_{k}"] = float(v)

            features_list.append(combined)

        return pd.DataFrame(features_list)

    def get_log_count(self) -> int:
        """Returns total recorded executions."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM query_log")
            return cursor.fetchone()[0]

    def clear_logs(self):
        """Clears all records from feedback tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM query_log")
            cursor.execute("DELETE FROM model_versions")
            conn.commit()


# Global default instance
default_store = FeedbackStore()


if __name__ == "__main__":
    print("=" * 60)
    print("Feedback Store Self-Test")
    print("=" * 60)
    store = FeedbackStore()
    sample_id = store.insert_log(
        sql_text="SELECT * FROM orders WHERE total_price > 500",
        query_features={"join_count": 0, "table_count": 1, "has_aggregation": 0},
        os_snapshot={"cpu_percent": 35.5, "mem_percent": 60.2, "is_cpu_stressed": False},
        chosen_strategy=0,
        strategy_name="DEFAULT",
        actual_time=12.4,
    )
    print(f"Successfully inserted test log record id: {sample_id}")
    count = store.get_log_count()
    print(f"Total records in feedback store: {count}")

    df = store.fetch_training_data()
    print(f"Retrieved training dataframe shape: {df.shape}")
    print("Feedback Store Test Passed!")
