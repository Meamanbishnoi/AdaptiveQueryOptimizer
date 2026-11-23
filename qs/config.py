"""QuerySync settings. Every value can be overridden with an environment variable or .env file."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv():
    env = BASE_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

# PostgreSQL connection
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_DATABASE = os.getenv("PG_DATABASE", "tpch")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "postgres")

# Paths
PG_BIN = BASE_DIR / "pgsql" / "bin"
PG_DATA = BASE_DIR / "pgdata"
DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"
RESULTS_DIR = BASE_DIR / "results"

# Load level: external CPU = host CPU used by processes other than PostgreSQL
HIGH_LOAD_EXTERNAL_CPU = float(os.getenv("HIGH_LOAD_EXTERNAL_CPU", "45"))

# Model / selection
ENSEMBLE_SIZE = int(os.getenv("ENSEMBLE_SIZE", "5"))
# A hint set is chosen over the default plan only if its predicted latency is at least this
# much lower AND the ensemble agrees (see qs/optimizer.py).
MIN_PREDICTED_GAIN = float(os.getenv("MIN_PREDICTED_GAIN", "0.15"))

# Queries whose default plan is predicted to run shorter than this skip the plan search (its
# 10-30 ms cost could not be recovered).
SHORT_QUERY_MS = float(os.getenv("SHORT_QUERY_MS", "100"))

# OS layer
#   OS_BOOST: "auto" = raise PostgreSQL's process priority while external CPU load is high
OS_BOOST = os.getenv("OS_BOOST", "auto")
#   memory budget (MB) for concurrently admitted queries' working memory (Banker's-style admission)
MEMORY_BUDGET_MB = float(os.getenv("MEMORY_BUDGET_MB", "1024"))
