# QuerySync: Cross-Layer OS-Aware Adaptive Query Optimization

[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://python.org)
[![Machine Learning](https://img.shields.io/badge/ML-XGBoost%20%7C%20Scikit--Learn-orange.svg)](https://xgboost.readthedocs.io/)
[![Database](https://img.shields.io/badge/DBMS-PostgreSQL-336791.svg)](https://www.postgresql.org/)

**QuerySync** is an intelligent, cross-layer database middleware that bridges the operating system hardware metrics with database query optimization.

Traditional relational query planners (such as PostgreSQL's cost-based optimizer) operate with a static view of system resources, assuming uninhibited access to CPU cores and memory. Under realistic multi-tenant server workloads—such as background data ingestion, antivirus scans, video rendering, or noisy neighbor processes—resource-heavy query plans (e.g., multi-threaded parallel hash joins) lead to CPU core thrashing, memory exhaustion, and catastrophic query latency spikes.

QuerySync intercepts incoming SQL queries, samples real-time OS telemetry, extracts query structural features, and dynamically predicts the optimal session-level execution plan hints using machine learning (XGBoost).

---

## 🏛️ System Architecture

```
                                  +-----------------------------+
                                  |   User / Client Application |
                                  +-----------------------------+
                                                 |
                                                 | SQL Query
                                                 v
  +---------------------------------------------------------------------------------------+
  |                          QuerySync Middleware Layer (Python)                          |
  |                                                                                       |
  |  +---------------------------+                     +-------------------------------+  |
  |  |   1. OS Telemetry Agent   |                     |    2. SQL Feature Extractor   |  |
  |  |         (psutil)          |                     |          (sqlparse)           |  |
  |  | • CPU % & Core Max        |                     | • Join Count & Table Count    |  |
  |  | • RAM Available / %       |                     | • Aggregations & Group By     |  |
  |  | • Disk I/O MB/s & Ops     |                     | • Filters & Query Complexity  |  |
  |  +---------------------------+                     +-------------------------------+  |
  |                 \                                                 /                   |
  |                  \                                               /                    |
  |                   +---------------------------------------------+                     |
  |                                                 | Combined Feature Vector (25-dim)     |
  |                                                 v                                     |
  |                                +-------------------------------+                      |
  |                                |   3. ML Decision Engine       |                      |
  |                                |         (XGBoost)             |                      |
  |                                | • DEFAULT                     |                      |
  |                                | • AVOID_HASH_JOIN             |                      |
  |                                | • FORCE_NESTED_LOOP           |                      |
  |                                | • CONSTRAIN_WORK_MEM          |                      |
  |                                | • DISABLE_PARALLEL            |                      |
  |                                | • DELAY_THROTTLE              |                      |
  |                                +-------------------------------+                      |
  |                                                 |                                     |
  |                                                 v                                     |
  |  +---------------------------+       Inject Session Plan Hints                        |
  |  |    4. Feedback Store      |   (e.g., SET enable_hashjoin = off;)                   |
  |  |    (SQLite/PostgreSQL)    |<-----------------+                                     |
  |  | • Query & Execution Time  |                  |                                     |
  |  | • Telemetry & Plan Log    |                  |                                     |
  |  +---------------------------+                  |                                     |
  +-------------------------------------------------|-------------------------------------+
                                                    |
                                                    v
                                    +-------------------------------+
                                    |      PostgreSQL Database      |
                                    |   (Executes optimal plan)     |
                                    +-------------------------------+
```

---

## 🚀 Key Results & Research Highlights

In rigorous 4-phase benchmark experiments comparing Default PostgreSQL against QuerySync under synthetic CPU/Memory stress:

| Experimental Condition | Default PostgreSQL (Avg) | QuerySync OS-Aware (Avg) | Performance Improvement |
|:---|:---:|:---:|:---:|
| **Normal Load (Idle OS)** | **37.91 ms** | **43.24 ms** | Equivalent (~5 ms telemetry overhead) |
| **High Load (CPU Stress 85%)** | **223.67 ms** | **43.59 ms** | **5.13x Faster (80.5% Latency Reduction)** |

- **Top Model Features Identified**: `os_is_cpu_stressed`, `os_load_1m`, `os_cpu_percent`, `os_mem_percent`, `q_agg_count`, `q_join_count`.
- **Adaptation Behavior**: Under CPU stress, QuerySync automatically steered queries away from CPU-saturating parallel hash joins, adapting 100% of complex queries to avoid CPU contention.

Generated research charts can be inspected in the `reports/` folder:
- `reports/latency_comparison_bar_chart.png`
- `reports/latency_over_time_under_stress.png`
- `reports/model_feature_importance.png`

---

## 📁 Repository Structure

```
OSAwareQO/
├── .env.example                     # Connection and threshold environment template
├── requirements.txt                 # Python dependencies
├── main.py                          # Unified CLI entrypoint
├── src/
│   ├── config.py                    # Constants, strategy definitions, DB parameters
│   ├── telemetry/
│   │   └── os_monitor.py            # Real-time hardware telemetry agent (psutil)
│   ├── query_processor/
│   │   └── feature_extractor.py     # SQL parser and feature vector generator (sqlparse)
│   ├── ml_engine/
│   │   ├── dataset_generator.py     # Empirical workload generator
│   │   ├── model.py                 # XGBoost / Random Forest decision classifier
│   │   └── train.py                 # Offline model training & evaluation pipeline
│   ├── mediator/
│   │   └── query_mediator.py        # Core proxy interceptor & PostgreSQL hint executor
│   └── feedback/
│       └── feedback_store.py        # Persistent execution & telemetry feedback repository
├── data/
│   ├── schema.sql                   # Relational benchmark schema with indexes
│   ├── seed_data.py                 # Synthetic data populator
│   └── benchmark_queries.sql        # Decision support analytical queries
├── experiments/
│   ├── stress_generator.py          # Background multi-core CPU & RAM stress simulator
│   ├── run_benchmark.py             # 4-Phase evaluation benchmark suite
│   └── plot_results.py              # Visual charts generator
├── models/
│   └── os_aware_model.pkl           # Serialized trained XGBoost model
├── logs/
│   └── feedback.db                  # SQLite feedback store
└── reports/
    ├── benchmark_results.json       # JSON benchmark telemetry output
    ├── latency_comparison_bar_chart.png
    ├── latency_over_time_under_stress.png
    └── model_feature_importance.png
```

---

## 🛠️ Installation & Setup

### 1. Activate Environment & Install Dependencies
```bash
# Windows
.\venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and adjust your PostgreSQL credentials:
```ini
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=osaware_db
PG_USER=postgres
PG_PASSWORD=postgres
USE_MOCK_DB=false
```
*(Note: If PostgreSQL is not active, QuerySync automatically runs in high-fidelity simulation mode so all components remain fully operational).*

---

## 💻 CLI Usage Guide

### 1. Live OS Telemetry Monitoring
Monitor real-time system metrics (CPU %, per-core max, RAM MB, Disk I/O rate):
```bash
python main.py monitor
```

### 2. Execute SQL Query with OS-Aware Optimizer
Pass any SQL query to observe real-time feature extraction, model inference, and latency comparison:
```bash
python main.py query "SELECT c.customer_name, COUNT(o.order_id) FROM customers c JOIN orders o ON c.customer_id = o.customer_id GROUP BY c.customer_name;"
```

### 3. Generate Training Data
Generate empirical training samples across normal, CPU-stressed, and memory-stressed states:
```bash
python main.py collect --samples 400
```

### 4. Train the ML Decision Engine
Train the XGBoost classifier on recorded execution history:
```bash
python main.py train
```

### 5. Run the Full 4-Phase Evaluation Benchmark
Execute the baseline and stressed comparison and generate publication charts:
```bash
python main.py benchmark --queries 30 --plot
```

---

## 👥 Authors
- **Aman Bishnoi** (Lead) – BTech CSE (AIML), Reg No: 240211396
- **Pranjali Bhatt** – BTech CSE (AIML), Reg No: 240221381
- Project Proposal: *Cross-Layer Adaptive Query Processing: Using Machine Learning to Make Databases Aware of System Resources*
