"""QuerySync settings. Every value can be overridden in .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# PostgreSQL connection
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_DATABASE = os.getenv("PG_DATABASE", "osaware_db")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "postgres")

# Load level. "External CPU" = host CPU used by processes other than PostgreSQL, so the
# optimizer reacts to other workloads, not to the load its own queries create.
HIGH_LOAD_EXTERNAL_CPU = float(os.getenv("HIGH_LOAD_EXTERNAL_CPU", "45"))

# Policy (see src/policy.py)
MIN_SAMPLES = int(os.getenv("MIN_SAMPLES", "3"))           # runs per option before deciding
MAX_SAMPLES = int(os.getenv("MAX_SAMPLES", "8"))           # keep measuring up to this while a win is promising but unproven
WIN_MARGIN = float(os.getenv("WIN_MARGIN", "0.10"))        # SERIAL must be >=10% faster (median)...
DOMINANCE = float(os.getenv("DOMINANCE", "0.75"))          # ...and faster in >=75% of run pairs
REJECT_RATIO = float(os.getenv("REJECT_RATIO", "1.5"))     # stop trying SERIAL if 1.5x slower
RECHECK_EVERY = int(os.getenv("RECHECK_EVERY", "25"))      # re-measure the other option every N runs
WINDOW = int(os.getenv("WINDOW", "20"))                    # recent runs kept per option

DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"
REPORTS_DIR = BASE_DIR / "reports"
POLICY_FILE = MODELS_DIR / "policy.json"
