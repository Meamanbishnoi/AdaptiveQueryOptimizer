"""
Hint sets: the alternative plans QuerySync can choose from.

Each hint set is a group of PostgreSQL planner settings applied with SET LOCAL (only for that
query's transaction). The idea follows Bao (Marcus et al., SIGMOD 2021): instead of building plans
ourselves, we steer PostgreSQL's own planner by switching operators on or off, and a learned
model predicts which hint set gives the fastest plan for the current query and OS load.
"""

from typing import Dict, List

HINT_SETS: List[Dict] = [
    {"id": 0, "name": "default", "settings": {}},
    {"id": 1, "name": "no_parallel", "settings": {"max_parallel_workers_per_gather": "0"}},
    {"id": 2, "name": "more_parallel", "settings": {"max_parallel_workers_per_gather": "4"}},
    {"id": 3, "name": "no_hashjoin", "settings": {"enable_hashjoin": "off"}},
    {"id": 4, "name": "no_mergejoin", "settings": {"enable_mergejoin": "off"}},
    {"id": 5, "name": "no_nestloop", "settings": {"enable_nestloop": "off"}},
    {"id": 6, "name": "hash_joins_only", "settings": {"enable_nestloop": "off", "enable_mergejoin": "off"}},
    {"id": 7, "name": "no_hash_no_merge", "settings": {"enable_hashjoin": "off", "enable_mergejoin": "off"}},
    {"id": 8, "name": "seq_scans_only", "settings": {"enable_indexscan": "off", "enable_bitmapscan": "off",
                                                     "enable_indexonlyscan": "off"}},
    {"id": 9, "name": "no_seqscan", "settings": {"enable_seqscan": "off"}},
    {"id": 10, "name": "more_work_mem", "settings": {"work_mem": "'64MB'"}},
    {"id": 11, "name": "serial_more_mem", "settings": {"max_parallel_workers_per_gather": "0", "work_mem": "'64MB'"}},
    {"id": 12, "name": "serial_no_nestloop", "settings": {"max_parallel_workers_per_gather": "0",
                                                         "enable_nestloop": "off"}},
]

BY_ID = {h["id"]: h for h in HINT_SETS}
DEFAULT_ID = 0
