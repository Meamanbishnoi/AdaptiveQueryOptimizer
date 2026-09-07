"""
Configuration and constants for OS-Aware Adaptive Query Optimization.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Base Directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables
load_dotenv(BASE_DIR / ".env")

# Database Settings
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_DATABASE = os.getenv("PG_DATABASE", "osaware_db")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "postgres")
USE_MOCK_DB = os.getenv("USE_MOCK_DB", "false").lower() in ("true", "1", "yes")

# Database Connection URL
DATABASE_URL = (
    f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
)

# Telemetry Settings
TELEMETRY_SAMPLE_INTERVAL_SEC = float(
    os.getenv("TELEMETRY_SAMPLE_INTERVAL_SEC", "0.5")
)
CPU_STRESS_THRESHOLD = float(os.getenv("CPU_STRESS_THRESHOLD", "75.0"))
MEMORY_STRESS_THRESHOLD = float(os.getenv("MEMORY_STRESS_THRESHOLD", "80.0"))

# Paths
DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"
LOGS_DIR = BASE_DIR / "logs"
REPORTS_DIR = BASE_DIR / "reports"

SQLITE_FEEDBACK_DB = BASE_DIR / os.getenv("SQLITE_FEEDBACK_DB", "logs/feedback.db")
MODEL_PATH = BASE_DIR / os.getenv("MODEL_PATH", "models/os_aware_model.pkl")

# Available Optimization Strategies and their PostgreSQL session commands
STRATEGIES = {
    0: {
        "name": "DEFAULT",
        "description": "Default PostgreSQL cost-based optimizer plan",
        "session_sql": [],
        "reset_sql": [
            "RESET enable_hashjoin;",
            "RESET enable_mergejoin;",
            "RESET enable_nestloop;",
            "RESET work_mem;",
            "RESET max_parallel_workers_per_gather;",
        ],
        "delay_ms": 0,
    },
    1: {
        "name": "AVOID_HASH_JOIN",
        "description": "Disable hash joins to prevent massive memory allocations during RAM/CPU stress",
        "session_sql": [
            "SET enable_hashjoin = off;",
            "SET enable_mergejoin = on;",
        ],
        "reset_sql": ["RESET enable_hashjoin;", "RESET enable_mergejoin;"],
        "delay_ms": 0,
    },
    2: {
        "name": "FORCE_NESTED_LOOP",
        "description": "Favor index nested loops for filtered queries when CPU is busy with sorting/hashing",
        "session_sql": [
            "SET enable_hashjoin = off;",
            "SET enable_mergejoin = off;",
            "SET enable_nestloop = on;",
        ],
        "reset_sql": [
            "RESET enable_hashjoin;",
            "RESET enable_mergejoin;",
            "RESET enable_nestloop;",
        ],
        "delay_ms": 0,
    },
    3: {
        "name": "CONSTRAIN_WORK_MEM",
        "description": "Restrict work_mem to reduce memory pressure during high RAM usage",
        "session_sql": ["SET work_mem = '4MB';"],
        "reset_sql": ["RESET work_mem;"],
        "delay_ms": 0,
    },
    4: {
        "name": "DISABLE_PARALLEL",
        "description": "Turn off parallel worker execution when CPU cores are saturated by background processes",
        "session_sql": ["SET max_parallel_workers_per_gather = 0;"],
        "reset_sql": ["RESET max_parallel_workers_per_gather;"],
        "delay_ms": 0,
    },
    5: {
        "name": "DELAY_THROTTLE",
        "description": "Briefly queue/delay query (e.g. 50ms) to let peak CPU transient subside",
        "session_sql": [],
        "reset_sql": [],
        "delay_ms": 50,
    },
}

STRATEGY_NAME_TO_ID = {v["name"]: k for k, v in STRATEGIES.items()}
STRATEGY_ID_TO_NAME = {k: v["name"] for k, v in STRATEGIES.items()}
