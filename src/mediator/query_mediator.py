"""
Intelligent Query Mediator.
Intercepts incoming SQL queries, gathers real-time OS telemetry, queries the ML Decision Engine,
applies PostgreSQL session-level execution plan hints, executes the query, records precise execution latency,
and logs feedback for continuous learning.
"""

import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import psycopg2
from psycopg2 import sql

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import (
    PG_HOST,
    PG_PORT,
    PG_DATABASE,
    PG_USER,
    PG_PASSWORD,
    USE_MOCK_DB,
    STRATEGIES,
)
from src.telemetry.os_monitor import default_monitor, OSTelemetryMonitor
from src.query_processor.feature_extractor import default_extractor, QueryFeatureExtractor
from src.ml_engine.model import default_ml_model, OSOptimizerModel
from src.feedback.feedback_store import default_store, FeedbackStore


class QueryMediator:
    """
    Middleware proxy coordinating OS Telemetry, ML plan steering, and PostgreSQL execution.
    """

    def __init__(
        self,
        telemetry_monitor: Optional[OSTelemetryMonitor] = None,
        feature_extractor: Optional[QueryFeatureExtractor] = None,
        ml_model: Optional[OSOptimizerModel] = None,
        feedback_store: Optional[FeedbackStore] = None,
        use_mock: bool = USE_MOCK_DB,
    ):
        self.telemetry = telemetry_monitor or default_monitor
        self.extractor = feature_extractor or default_extractor
        self.model = ml_model or default_ml_model
        self.store = feedback_store or default_store
        self.use_mock = use_mock
        self._pg_conn = None

        # Start continuous telemetry sampling thread
        self.telemetry.start_background_monitoring()

        if not self.use_mock:
            self._try_connect_pg()

    def _try_connect_pg(self):
        """Attempts to establish connection to PostgreSQL with instantaneous port probe."""
        import socket
        # Fast socket probe (<0.2s) to avoid Windows TCP timeout lag when PG is not running
        try:
            with socket.create_connection((PG_HOST, PG_PORT), timeout=0.3):
                pass
        except OSError:
            # PostgreSQL is not running locally
            self.use_mock = True
            return

        try:
            self._pg_conn = psycopg2.connect(
                host=PG_HOST,
                port=PG_PORT,
                dbname=PG_DATABASE,
                user=PG_USER,
                password=PG_PASSWORD,
                connect_timeout=2,
            )
            self._pg_conn.autocommit = True
            print(f"[Mediator] Connected to PostgreSQL on {PG_HOST}:{PG_PORT}/{PG_DATABASE}")
        except Exception as e:
            print(f"[Mediator] PostgreSQL connection failed ({e}). Using simulation mode.")
            self.use_mock = True

    def execute_query(
        self,
        sql_query: str,
        force_strategy: Optional[int] = None,
        explain_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Full adaptive execution lifecycle:
        1. Receive SQL
        2. Collect current OS metrics
        3. Extract query features
        4. Ask ML model for decision (or use force_strategy if specified)
        5. Apply session hint parameters
        6. Execute query and measure latency
        7. Reset session parameters
        8. Log execution metadata into feedback store
        """
        # Step 2: Telemetry (Instantaneous snapshot of current OS load)
        os_snapshot = self.telemetry.sample_metrics()

        # Step 3: Feature Extraction
        query_features = self.extractor.extract_features(sql_query)

        # Step 4: ML Decision
        if force_strategy is not None and force_strategy in STRATEGIES:
            chosen_strategy = force_strategy
            strategy_info = STRATEGIES[chosen_strategy]
            strategy_name = strategy_info["name"]
            confidence = 1.0
        else:
            chosen_strategy, strategy_name, confidence = self.model.predict_strategy(
                query_features, os_snapshot
            )
            strategy_info = STRATEGIES[chosen_strategy]

        # Step 5: Throttling Delay (if strategy dictates)
        delay_ms = strategy_info.get("delay_ms", 0)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

        # Step 6: Query Execution & Latency Measurement
        actual_latency_ms, rows_returned, plan_text, error_msg = self._run_query_on_db(
            sql_query, strategy_info, os_snapshot=os_snapshot, explain_only=explain_only
        )

        # Step 8: Log to Feedback Store
        log_id = self.store.insert_log(
            sql_text=sql_query,
            query_features=query_features,
            os_snapshot=os_snapshot,
            chosen_strategy=chosen_strategy,
            strategy_name=strategy_name,
            actual_time=actual_latency_ms,
            predicted_time=None,
            plan_used=plan_text,
        )

        return {
            "log_id": log_id,
            "sql": sql_query,
            "strategy_id": chosen_strategy,
            "strategy_name": strategy_name,
            "strategy_desc": strategy_info["description"],
            "model_confidence": confidence,
            "actual_latency_ms": round(actual_latency_ms, 2),
            "rows_returned": rows_returned,
            "os_snapshot": os_snapshot,
            "query_features": query_features,
            "plan_summary": plan_text[:200] if plan_text else "",
            "error": error_msg,
        }

    def _run_query_on_db(
        self,
        sql_query: str,
        strategy_info: Dict[str, Any],
        os_snapshot: Dict[str, Any] = None,
        explain_only: bool = False,
    ) -> Tuple[float, int, str, Optional[str]]:
        """Executes query with strategy hints either on PostgreSQL or mock engine."""
        if not self.use_mock and self._pg_conn:
            return self._run_postgresql(sql_query, strategy_info, explain_only)
        else:
            return self._run_mock(sql_query, strategy_info, os_snapshot=os_snapshot)

    def _run_postgresql(
        self, sql_query: str, strategy_info: Dict[str, Any], explain_only: bool
    ) -> Tuple[float, int, str, Optional[str]]:
        """Runs query on active PostgreSQL connection applying session hints."""
        cursor = self._pg_conn.cursor()
        session_sqls = strategy_info.get("session_sql", [])
        reset_sqls = strategy_info.get("reset_sql", [])
        plan_text = ""
        error_msg = None
        rows = 0

        try:
            # 1. Apply session hints
            for h in session_sqls:
                cursor.execute(h)

            # 2. Get execution plan if requested
            if explain_only or True:
                try:
                    cursor.execute(f"EXPLAIN {sql_query}")
                    plan_lines = [r[0] for r in cursor.fetchall()]
                    plan_text = "\n".join(plan_lines)
                except Exception:
                    pass

            # 3. Timed Execution
            start_t = time.perf_counter()
            cursor.execute(sql_query)
            try:
                rows = len(cursor.fetchall())
            except psycopg2.ProgrammingError:
                rows = cursor.rowcount
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0

        except Exception as ex:
            elapsed_ms = 0.0
            error_msg = str(ex)
        finally:
            # Reset session settings
            for r in reset_sqls:
                try:
                    cursor.execute(r)
                except Exception:
                    pass
            cursor.close()

        return elapsed_ms, rows, plan_text, error_msg

    def _run_mock(
        self,
        sql_query: str,
        strategy_info: Dict[str, Any],
        os_snapshot: Dict[str, Any] = None,
    ) -> Tuple[float, int, str, Optional[str]]:
        """
        High-fidelity database simulator modeling PostgreSQL execution physics:
        - Evaluates query complexity (# joins, aggregations, filters).
        - Computes baseline execution time.
        - Under CPU stress:
            - DEFAULT parallel/hashjoin experiences severe CPU contention & thrashing (2.5x - 5x penalty).
            - AVOID_HASH_JOIN / FORCE_NESTED_LOOP / DISABLE_PARALLEL bypasses CPU contention and finishes faster.
        - Under RAM stress:
            - CONSTRAIN_WORK_MEM & AVOID_HASH_JOIN avoid swap thrashing.
        """
        metrics = os_snapshot or self.telemetry.sample_metrics()
        q_feat = self.extractor.extract_features(sql_query)
        complexity = q_feat["complexity_score"]
        join_count = q_feat["join_count"]

        # Base execution time (5ms to 50ms depending on complexity)
        base_ms = 5.0 + complexity * 2.5 + join_count * 4.0

        cpu = metrics["cpu_percent"]
        mem = metrics["mem_percent"]
        strat_name = strategy_info["name"]

        # Dynamic simulation physics:
        latency_multiplier = 1.0

        if cpu >= 70.0:
            if strat_name == "DEFAULT":
                # Severe contention penalty for default planner using parallel hash joins on saturated CPU
                latency_multiplier = 3.5 + (cpu - 70.0) * 0.08
            elif strat_name == "DISABLE_PARALLEL":
                # Single-threaded serialized plan avoids core thrashing
                latency_multiplier = 1.2
            elif strat_name == "FORCE_NESTED_LOOP":
                latency_multiplier = 1.4
            elif strat_name == "AVOID_HASH_JOIN":
                latency_multiplier = 1.45
            elif strat_name == "DELAY_THROTTLE":
                latency_multiplier = 1.15
        elif mem >= 80.0:
            if strat_name == "DEFAULT":
                latency_multiplier = 2.8
            elif strat_name in ("CONSTRAIN_WORK_MEM", "AVOID_HASH_JOIN"):
                latency_multiplier = 1.2
        else:
            # Under normal conditions, DEFAULT is optimal
            if strat_name == "DEFAULT":
                latency_multiplier = 1.0
            else:
                latency_multiplier = 1.25

        total_latency_ms = base_ms * latency_multiplier
        # Add small simulated execution wait
        time.sleep(min(0.04, total_latency_ms / 1000.0))

        simulated_plan = f"Simulated Execution Plan ({strat_name}): Joins={join_count}, Complexity={complexity:.1f}"
        return total_latency_ms, 100, simulated_plan, None


# Global default instance
default_mediator = QueryMediator()


if __name__ == "__main__":
    print("=" * 60)
    print("Query Mediator Self-Test")
    print("=" * 60)
    mediator = QueryMediator()
    sample_query = """
    SELECT o.order_id, c.customer_name, SUM(o.total_amount)
    FROM orders o
    JOIN customers c ON o.customer_id = c.customer_id
    JOIN order_items oi ON o.order_id = oi.order_id
    WHERE o.status = 'COMPLETED'
    GROUP BY o.order_id, c.customer_name;
    """
    res = mediator.execute_query(sample_query)
    print(f"Strategy Chosen : {res['strategy_name']} (ID: {res['strategy_id']})")
    print(f"Confidence      : {res['model_confidence']}")
    print(f"Latency         : {res['actual_latency_ms']:.2f} ms")
    print(f"Description     : {res['strategy_desc']}")
    print("\nMediator Test Passed!")
