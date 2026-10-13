"""
Query Feature Extractor.
Analyzes SQL queries syntactically using sqlparse and extracts plan-level
metrics using PostgreSQL EXPLAIN (FORMAT JSON) when available.
"""

import re
from typing import Dict, Any, List, Optional
import sqlparse
from sqlparse.sql import IdentifierList, Identifier, Where, Parenthesis
from sqlparse.tokens import Keyword, DML


from src.query_processor.feature_cache import default_feature_cache, QueryFeatureCache


class QueryFeatureExtractor:
    """
    Extracts structural and complexity features from SQL queries,
    accelerated by an LRU Plan Hash Cache.
    """

    def __init__(
        self,
        db_connection=None,
        cache: Optional[QueryFeatureCache] = None,
    ):
        self.db_connection = db_connection
        self.cache = cache if cache is not None else default_feature_cache

    def extract_features(
        self, sql_query: str, bypass_cache: bool = False
    ) -> Dict[str, Any]:
        """
        Parses SQL query and returns a dictionary of numerical and categorical features.
        Utilizes the LRU Feature Cache to bypass expensive AST parsing on recurring templates.
        """
        if not bypass_cache and self.cache is not None:
            features, is_hit, q_hash = self.cache.get_or_compute(
                sql_query, self._compute_features
            )
            features["is_cache_hit"] = 1 if is_hit else 0
            features["query_hash"] = q_hash[:16]
            return features

        features = self._compute_features(sql_query)
        features["is_cache_hit"] = 0
        features["query_hash"] = ""
        return features

    def _compute_features(self, sql_query: str) -> Dict[str, Any]:
        """
        Performs full syntactic AST traversal and metric extraction via sqlparse.
        """
        cleaned_sql = sql_query.strip().rstrip(";")
        formatted = sqlparse.format(cleaned_sql, keyword_case="upper")
        parsed = sqlparse.parse(formatted)

        if not parsed:
            return self._empty_features(cleaned_sql)

        statement = parsed[0]

        # 1. Join Count & Types
        join_matches = re.findall(
            r"\b(INNER\s+JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|FULL\s+JOIN|CROSS\s+JOIN|JOIN)\b",
            formatted,
            re.IGNORECASE,
        )
        join_count = len(join_matches)

        # 2. Aggregations (GROUP BY / Aggregate Functions)
        has_group_by = 1 if re.search(r"\bGROUP\s+BY\b", formatted, re.IGNORECASE) else 0
        has_having = 1 if re.search(r"\bHAVING\b", formatted, re.IGNORECASE) else 0
        agg_matches = re.findall(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", formatted, re.IGNORECASE)
        agg_count = len(agg_matches)
        has_aggregation = 1 if (has_group_by or agg_count > 0 or has_having) else 0

        # 3. Sorting & Limits
        has_order_by = 1 if re.search(r"\bORDER\s+BY\b", formatted, re.IGNORECASE) else 0
        has_limit = 1 if re.search(r"\bLIMIT\b", formatted, re.IGNORECASE) else 0

        # 4. Filters & Predicates
        where_match = re.search(r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|$)", formatted, re.IGNORECASE | re.DOTALL)
        filter_count = 0
        if where_match:
            where_clause = where_match.group(1)
            # Count boolean operators AND, OR, comparison operators
            filter_count = 1 + len(re.findall(r"\b(AND|OR)\b", where_clause, re.IGNORECASE))

        # 5. Subqueries
        subquery_count = len(re.findall(r"\(\s*SELECT\b", formatted, re.IGNORECASE))
        has_subquery = 1 if subquery_count > 0 else 0

        # 6. Table Count Estimation
        table_count = self._estimate_table_count(formatted, join_count)

        # 7. Basic textual / token features
        query_length = len(cleaned_sql)
        token_count = len([t for t in statement.flatten() if not t.is_whitespace])

        # 8. Composite complexity score
        complexity_score = (
            join_count * 3.0
            + table_count * 1.5
            + (2.0 if has_group_by else 0.0)
            + (1.5 if has_order_by else 0.0)
            + (3.0 if has_subquery else 0.0)
            + filter_count * 0.5
            + agg_count * 1.0
        )

        # 9. Cost & Rows estimates (from EXPLAIN if available, else heuristic fallback)
        estimated_cost, estimated_rows, has_gather = self._get_explain_estimates(cleaned_sql, complexity_score)

        return {
            "join_count": join_count,
            "table_count": table_count,
            "has_aggregation": has_aggregation,
            "has_group_by": has_group_by,
            "has_having": has_having,
            "agg_count": agg_count,
            "has_order_by": has_order_by,
            "has_limit": has_limit,
            "has_subquery": has_subquery,
            "subquery_count": subquery_count,
            "filter_count": filter_count,
            "query_length": query_length,
            "token_count": token_count,
            "complexity_score": round(complexity_score, 2),
            "estimated_cost": estimated_cost,
            "estimated_rows": estimated_rows,
            "has_gather": has_gather,
            "default_cost": estimated_cost,
        }

    def _estimate_table_count(self, formatted_sql: str, join_count: int) -> int:
        """Estimates the number of involved tables."""
        # Simple heuristic: at least 1 table + number of joins
        from_match = re.search(r"\bFROM\s+([a-zA-Z0-9_,\s]+)(?:\bWHERE\b|\bJOIN\b|\bGROUP\b|\bORDER\b|$)", formatted_sql, re.IGNORECASE)
        comma_tables = 0
        if from_match:
            tables_part = from_match.group(1).split(",")
            comma_tables = max(0, len(tables_part) - 1)
        return max(1, 1 + join_count + comma_tables)

    def _get_explain_estimates(self, sql: str, complexity_score: float) -> tuple:
        """
        Attempts to run EXPLAIN (FORMAT JSON) on PostgreSQL connection if available.
        Extracts cost, rows, and whether the plan uses parallel workers (Gather).
        Returns: (estimated_cost, estimated_rows, has_gather)
        """
        if self.db_connection:
            try:
                import json
                cursor = self.db_connection.cursor()
                cursor.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                result = cursor.fetchone()
                if result:
                    raw_data = result[0]
                    if isinstance(raw_data, str):
                        plan_data = json.loads(raw_data)
                    else:
                        plan_data = raw_data
                    plan_str = json.dumps(plan_data)
                    root_plan = plan_data[0].get("Plan", {}) if isinstance(plan_data, list) else plan_data.get("Plan", {})
                    cost = root_plan.get("Total Cost", 0.0)
                    rows = root_plan.get("Plan Rows", 0)
                    has_gather = 1 if "Gather" in plan_str else 0
                    return float(cost), int(rows), has_gather
            except Exception:
                pass

        # Heuristic fallback based on query complexity score
        base_cost = round(10.0 + (complexity_score ** 2) * 25.0, 1)
        base_rows = int(max(10, complexity_score * 500))
        has_gather = 1 if complexity_score >= 12.0 else 0
        return base_cost, base_rows, has_gather

    def _empty_features(self, cleaned_sql: str) -> Dict[str, Any]:
        return {
            "join_count": 0,
            "table_count": 1,
            "has_aggregation": 0,
            "has_group_by": 0,
            "has_having": 0,
            "agg_count": 0,
            "has_order_by": 0,
            "has_limit": 0,
            "has_subquery": 0,
            "subquery_count": 0,
            "filter_count": 0,
            "query_length": len(cleaned_sql),
            "token_count": 0,
            "complexity_score": 1.0,
            "estimated_cost": 10.0,
            "estimated_rows": 100,
            "has_gather": 0,
            "default_cost": 10.0,
        }

    @staticmethod
    def get_feature_vector(features: Dict[str, Any]) -> List[float]:
        """Returns ordered list of numeric features for ML model input."""
        ordered_keys = [
            "join_count",
            "table_count",
            "has_aggregation",
            "has_group_by",
            "has_having",
            "agg_count",
            "has_order_by",
            "has_limit",
            "has_subquery",
            "subquery_count",
            "filter_count",
            "query_length",
            "token_count",
            "complexity_score",
            "estimated_cost",
            "estimated_rows",
        ]
        return [float(features.get(k, 0.0)) for k in ordered_keys]


# Global instance
default_extractor = QueryFeatureExtractor()


if __name__ == "__main__":
    print("=" * 60)
    print("Query Feature Extractor Test")
    print("=" * 60)
    test_sql = """
    SELECT c.c_name, COUNT(o.o_orderkey) AS order_cnt, SUM(o.o_totalprice) AS total_spent
    FROM customer c
    JOIN orders o ON c.c_custkey = o.o_custkey
    LEFT JOIN lineitem l ON o.o_orderkey = l.l_orderkey
    WHERE o.o_orderdate >= '2023-01-01' AND c.c_acctbal > 1000
    GROUP BY c.c_name
    HAVING COUNT(o.o_orderkey) > 5
    ORDER BY total_spent DESC
    LIMIT 20;
    """
    extractor = QueryFeatureExtractor()
    feat = extractor.extract_features(test_sql)
    for k, v in feat.items():
        print(f"  {k:20s}: {v}")
    vec = extractor.get_feature_vector(feat)
    print(f"\nVector length: {len(vec)}")
    print(f"Vector values: {vec}")
    print("\nFeature Extractor Test Passed!")
