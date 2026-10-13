"""
Unified Feature Schema and Vector Extraction.
Shared by XGBoost model, LinUCB Bandit engine, and Feedback Store.
"""

from typing import Dict, Any, List
import numpy as np


# 25-Dimensional Feature Space aligned across all ML engines
FEATURE_NAMES: List[str] = [
    # Query Features (16)
    "q_join_count",
    "q_table_count",
    "q_has_aggregation",
    "q_has_group_by",
    "q_has_having",
    "q_agg_count",
    "q_has_order_by",
    "q_has_limit",
    "q_has_subquery",
    "q_subquery_count",
    "q_filter_count",
    "q_query_length",
    "q_token_count",
    "q_complexity_score",
    "q_estimated_cost",
    "q_estimated_rows",
    # OS Telemetry Features (9)
    "os_cpu_percent",
    "os_cpu_core_max",
    "os_mem_percent",
    "os_mem_available_mb",
    "os_disk_read_mbps",
    "os_disk_write_mbps",
    "os_load_1m",
    "os_process_count",
    "os_is_cpu_stressed",
]


def extract_feature_vector(
    query_features: Dict[str, Any], os_snapshot: Dict[str, Any]
) -> np.ndarray:
    """
    Combines query features and OS snapshot into an aligned 1D numeric numpy array.
    """
    vals = [
        # Query Features
        float(query_features.get("join_count", 0)),
        float(query_features.get("table_count", 1)),
        float(query_features.get("has_aggregation", 0)),
        float(query_features.get("has_group_by", 0)),
        float(query_features.get("has_having", 0)),
        float(query_features.get("agg_count", 0)),
        float(query_features.get("has_order_by", 0)),
        float(query_features.get("has_limit", 0)),
        float(query_features.get("has_subquery", 0)),
        float(query_features.get("subquery_count", 0)),
        float(query_features.get("filter_count", 0)),
        float(query_features.get("query_length", 0)),
        float(query_features.get("token_count", 0)),
        float(query_features.get("complexity_score", 0)),
        float(query_features.get("estimated_cost", 0)),
        float(query_features.get("estimated_rows", 0)),
        # OS Telemetry Features
        float(os_snapshot.get("cpu_percent", 0.0)),
        float(os_snapshot.get("cpu_core_max", 0.0)),
        float(os_snapshot.get("mem_percent", 0.0)),
        float(os_snapshot.get("mem_available_mb", 0.0)),
        float(os_snapshot.get("disk_read_mbps", 0.0)),
        float(os_snapshot.get("disk_write_mbps", 0.0)),
        float(os_snapshot.get("load_1m", 0.0)),
        float(os_snapshot.get("process_count", 0.0)),
        1.0 if os_snapshot.get("is_cpu_stressed", False) else 0.0,
    ]
    return np.array(vals, dtype=np.float32)
