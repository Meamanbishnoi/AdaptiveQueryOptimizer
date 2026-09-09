# QuerySync: Comprehensive System Documentation & Technical Specification

**Project Title:** Cross-Layer Adaptive Query Processing: Using Machine Learning to Make Databases Aware of System Resources  
**Team:** QuerySync  
**Team Members:**  
- **Aman Bishnoi** (Team Lead) – Reg. No: 240211396 (aman.bishnoi7007@gmail.com)  
- **Pranjali Bhatt** – Reg. No: 240221381 (pranjalibhatt8755@gmail.com)  
**Program:** B.Tech CSE (Artificial Intelligence & Machine Learning)  
**Track:** Operating Systems & Database Management Systems (OS-DBMS)

---

## Table of Contents
1. [Executive Summary & Motivation](#1-executive-summary--motivation)
2. [System Architecture & Data Flow](#2-system-architecture--data-flow)
3. [End-to-End Query Execution Lifecycle](#3-end-to-end-query-execution-lifecycle)
4. [Module-by-Module Technical Deep Dive](#4-module-by-module-technical-deep-dive)
   - [4.1 Configuration Engine (`src/config.py`)](#41-configuration-engine-srcconfigpy)
   - [4.2 OS Telemetry Agent (`src/telemetry/os_monitor.py`)](#42-os-telemetry-agent-srctelemetryos_monitorpy)
   - [4.3 Query Feature Extractor (`src/query_processor/feature_extractor.py`)](#43-query-feature-extractor-srcquery_processorfeature_extractorpy)
   - [4.4 Feedback Store (`src/feedback/feedback_store.py`)](#44-feedback-store-srcfeedbackfeedback_storepy)
   - [4.5 Machine Learning Engine (`src/ml_engine/`)](#45-machine-learning-engine-srcml_engine)
   - [4.6 Intelligent Query Mediator (`src/mediator/query_mediator.py`)](#46-intelligent-query-mediator-srcmediatorquery_mediatorpy)
   - [4.7 Stress Testing Agent (`experiments/stress_generator.py`)](#47-stress-testing-agent-experimentsstress_generatorpy)
   - [4.8 Benchmark Evaluation Suite (`experiments/run_benchmark.py`)](#48-benchmark-evaluation-suite-experimentsrun_benchmarkpy)
   - [4.9 Visualization & Plotting Pipeline (`experiments/plot_results.py`)](#49-visualization--plotting-pipeline-experimentsplot_resultspy)
   - [4.10 Unified Command Line Interface (`main.py`)](#410-unified-command-line-interface-mainpy)
   - [4.11 Relational Schema & Benchmark Queries (`data/`)](#411-relational-schema--benchmark-queries-data)
5. [Optimization Strategies & PostgreSQL Session Hints](#5-optimization-strategies--postgresql-session-hints)
6. [Machine Learning Methodology & 25-Dimensional Feature Space](#6-machine-learning-methodology--25-dimensional-feature-space)
7. [Experimental Results & Performance Findings](#7-experimental-results--performance-findings)
8. [CLI Usage & Viva Demonstration Guide](#8-cli-usage--viva-demonstration-guide)
9. [Future Research Scope](#9-future-research-scope)

---

## 1. Executive Summary & Motivation

### The Problem: The Abstraction Wall
In classical computing, the **Operating System (OS)** and the **Database Management System (DBMS)** operate as separate abstraction layers:
- The **OS** manages CPU scheduling, physical memory, paging/swap, and disk I/O interrupts.
- The **DBMS** manages relational algebra, indexing, concurrency, and query planning.

Traditional query optimizers (such as PostgreSQL's Cost-Based Optimizer, MySQL, or Oracle) optimize queries in a vacuum. When generating an execution plan, the optimizer inspects internal database statistics (table cardinalities, column histograms, index availability). Crucially, **the optimizer assumes the host system has 100% of its CPU cores and configured RAM available for query execution**.

### Real-World Failure Mode: Resource Contention
In real-world multi-tenant production systems, virtual machines, cloud instances, or developer workstations, databases share hardware with:
- Background microservices, ETL pipelines, and automated backups.
- Video encoding, antivirus scans, and competing operating system processes.

When the OS experiences severe CPU or memory pressure:
1. **Parallel Hash Joins Fail Catastrophically**: The database planner still chooses multi-worker parallel hash joins. Under 80%+ CPU saturation, spawning multiple parallel worker threads induces massive context-switching overhead, thread synchronization locks, and cache invalidations, causing query latency to explode by **400% to 600%**.
2. **Memory Exhaustion & Swap Thrashing**: If RAM is saturated, allocating large `work_mem` hash tables forces the OS kernel to page memory to disk (swap), converting in-memory hash lookups into high-latency disk I/O.

### The QuerySync Solution
**QuerySync** is a **Cross-Layer Adaptive Query Processing Middleware**. It intercepts incoming SQL queries before they reach the database engine, samples real-time operating system telemetry, extracts syntactic query features, and evaluates a trained machine learning model (XGBoost) to inject dynamic, session-level execution plan hints. This aligns the database's execution strategy with real-time host resource availability without modifying the core database source code.

---

## 2. System Architecture & Data Flow

```
+-----------------------------------------------------------------------------------------+
|                                    Client Application                                   |
+-----------------------------------------------------------------------------------------+
                                             |
                                             | 1. SQL Query
                                             v
+-----------------------------------------------------------------------------------------+
|                           QuerySync Middleware Engine (Python)                          |
|                                                                                         |
|   +-------------------------------+             +-----------------------------------+   |
|   |    1. OS Telemetry Agent      |             |    2. SQL Feature Extractor       |   |
|   |          (psutil)             |             |           (sqlparse)              |   |
|   | • Overall CPU % & Per-Core    |             | • Join Count (Inner, Left, Cross) |   |
|   | • Memory Available / Used MB  |             | • Aggregations & Group By         |   |
|   | • Disk I/O MB/s & IOPS        |             | • Filters & Complexity Score      |   |
|   | • Load Average / Process Count|             | • EXPLAIN Total Cost & Rows       |   |
|   +-------------------------------+             +-----------------------------------+   |
|                   \                                               /                     |
|                    \                                             /                      |
|                     +-------------------------------------------+                       |
|                                           |                                             |
|                                           | 25-Dimensional Aligned Feature Vector       |
|                                           v                                             |
|                         +-----------------------------------+                           |
|                         |      3. ML Decision Engine        |                           |
|                         |            (XGBoost)              |                           |
|                         |  Predicts optimal strategy class  |                           |
|                         |  Confidence score & reasoning     |                           |
|                         +-----------------------------------+                           |
|                                           |                                             |
|                                           | Chosen Strategy & Parameter Directives      |
|                                           v                                             |
|   +-------------------------------+  4. Intelligent Mediator                            |
|   |       5. Feedback Store       |<----------------------------+                       |
|   |      (SQLite/PostgreSQL)      |  Injects Session Hints:     |                       |
|   | • Log ID & Timestamp          |  - SET enable_hashjoin=off  |                       |
|   | • Query & Feature Vector JSON |  - SET work_mem='4MB'       |                       |
|   | • OS Telemetry Snapshot JSON  |  - SET max_parallel...=0    |                       |
|   | • Execution Latency (ms)      |                             |                       |
|   +-------------------------------+                             v                       |
+-----------------------------------------------------------------|-----------------------+
                                                                  |
                                                                  | 5. Optimized Query
                                                                  v
                                                 +---------------------------------+
                                                 |       PostgreSQL Database       |
                                                 |    (Executes adapted plan)      |
                                                 +---------------------------------+
```

---

## 3. End-to-End Query Execution Lifecycle

Every query processed by QuerySync follows an 8-stage lifecycle:

1. **Ingress**: The client submits a raw SQL query string to the middleware (via CLI or Python API).
2. **Instantaneous Telemetry Sampling**: The OS Telemetry Agent samples the host hardware state in sub-millisecond time:
   - CPU utilization (overall and per-core maximum).
   - Physical memory usage and available capacity in MB.
   - Disk read and write throughput (MB/s).
   - Process count and system load average.
3. **Query Feature Extraction**: The query string is parsed using `sqlparse` into an Abstract Syntax Tree (AST). The extractor computes structural metrics:
   - Number of joins (`INNER`, `LEFT`, `RIGHT`, `FULL`, `CROSS`).
   - Tables referenced.
   - Presence of `GROUP BY`, `HAVING`, `ORDER BY`, and aggregate functions (`COUNT`, `SUM`, `AVG`, etc.).
   - Composite Query Complexity Score.
4. **Vector Assembly & Model Inference**: The 16 query features and 9 OS telemetry metrics are merged into a unified 25-dimensional numeric vector. The trained XGBoost model evaluates the vector and outputs:
   - Optimal strategy index (0 to 5).
   - Human-readable strategy name.
   - Prediction confidence score.
5. **Throttling Evaluation**: If the model selects `DELAY_THROTTLE` (under extreme 90%+ CPU saturation), the mediator introduces a controlled micro-delay (50ms) to allow transient CPU spikes to clear.
6. **Session-Level Hint Injection**: The mediator connects to PostgreSQL and applies non-invasive session-level configuration parameters:
   ```sql
   SET enable_hashjoin = off;
   SET enable_mergejoin = on;
   ```
7. **Execution & High-Resolution Timing**: The query executes against the database. Execution time is recorded using Python's monotonic high-resolution timer (`time.perf_counter`).
8. **Session Cleanup & Feedback Logging**:
   - All session settings are reset (`RESET enable_hashjoin;`).
   - The query text, telemetry snapshot, extracted features, selected strategy, and measured execution latency are logged into `logs/feedback.db` for offline retraining and model evaluation.
   - The query results and performance metrics are returned to the client.

---

## 4. Module-by-Module Technical Deep Dive

### 4.1 Configuration Engine (`src/config.py`)
- **Technologies**: `python-dotenv`, `pathlib`, `os`.
- **Functionality**:
  - Centralizes database credentials, telemetry thresholds (`CPU_STRESS_THRESHOLD = 75.0%`), and file paths.
  - Defines the 6 discrete optimization strategies and their exact PostgreSQL session commands and reset commands.
  - Controls `USE_MOCK_DB`: enables dual-mode operation (live PostgreSQL or standalone simulation).

### 4.2 OS Telemetry Agent (`src/telemetry/os_monitor.py`)
- **Technologies**: `psutil`, `threading.RLock`, `collections.deque`.
- **Functionality**:
  - Samples hardware counters: CPU %, per-core max CPU %, available RAM (MB, %), swap %, disk read/write throughput (MB/s), and process count.
  - Maintains a thread-safe rolling window average (`deque(maxlen=10)`) with reentrant locking (`threading.RLock`) to prevent deadlocks between background monitoring and query execution threads.

### 4.3 Query Feature Extractor (`src/query_processor/feature_extractor.py`)
- **Technologies**: `sqlparse`, Regular Expressions (`re`).
- **Functionality**:
  - Parses arbitrary SQL statements into structured metrics: join count, table count, aggregations (`GROUP BY`, `HAVING`, `COUNT`), filters, and subqueries.
  - Calculates a composite Query Complexity Score:
    $$\text{Complexity} = 3.0 \cdot J + 1.5 \cdot T + 2.0 \cdot G + 1.5 \cdot O + 3.0 \cdot S + 0.5 \cdot F + 1.0 \cdot A$$
  - Optionally extracts PostgreSQL `EXPLAIN (FORMAT JSON)` total cost and row estimates.

### 4.4 Feedback Store (`src/feedback/feedback_store.py`)
- **Technologies**: `sqlite3`, `pandas`, `json`.
- **Functionality**:
  - Persistent repository for query execution telemetry (`logs/feedback.db`).
  - Stores `query_log` and `model_versions`.
  - `fetch_training_data()` converts recorded JSON payloads into a flattened `pandas.DataFrame` ready for ML model training.

### 4.5 Machine Learning Engine (`src/ml_engine/`)
- **`model.py`**:
  - Multi-class `xgboost.XGBClassifier` with `LabelEncoder`.
  - Maps the 25-dimensional feature space to strategy classes.
  - Achieved **90.12% test accuracy**.
  - Includes a rule-based expert heuristic fallback for pre-training availability.
- **`dataset_generator.py`**:
  - Generates empirical workloads across normal, CPU-stressed, and memory-stressed states.
- **`train.py`**:
  - Orchestrates dataset extraction, 80/20 train/test splitting, XGBoost model training, metric evaluation, and serialization to `models/os_aware_model.pkl`.

### 4.6 Intelligent Query Mediator (`src/mediator/query_mediator.py`)
- **Technologies**: `psycopg2`, `socket`, `time.perf_counter`.
- **Functionality**:
  - Coordinates telemetry sampling, ML inference, session hint injection, and execution timing.
  - Implements an instant socket probe (`socket.create_connection` in < 2ms) to detect PostgreSQL availability, falling back gracefully to simulation mode when offline.

### 4.7 Stress Testing Agent (`experiments/stress_generator.py`)
- **Technologies**: `multiprocessing`, `psutil`, `math`.
- **Functionality**:
  - Generates controlled, reproducible CPU and RAM contention across $N-1$ cores using an 85% duty cycle loop to evaluate query behavior under stress.

### 4.8 Benchmark Evaluation Suite (`experiments/run_benchmark.py`)
- **Functionality**:
  - Executes the 4 mandatory experimental conditions (Baseline Normal, OS-Aware Normal, Baseline Stressed, OS-Aware Stressed) on 25–30 decision-support queries.
  - Records Average Latency, P95 Tail Latency, and Throughput (QPM) into `reports/benchmark_results.json`.

### 4.9 Visualization & Plotting Pipeline (`experiments/plot_results.py`)
- **Technologies**: `matplotlib`, `seaborn`.
- **Outputs**:
  - `reports/latency_comparison_bar_chart.png`: Average and P95 latency comparison.
  - `reports/latency_over_time_under_stress.png`: Latency stability timeline.
  - `reports/model_feature_importance.png`: Top-10 influential features in the decision engine.

### 4.10 Unified Command Line Interface (`main.py`)
- **Subcommands**:
  - `monitor`: Live streaming hardware telemetry dashboard.
  - `query "<SQL>"`: Executes a query with OS-Aware optimization and latency comparison.
  - `collect --samples N`: Collects empirical training data.
  - `train`: Trains the XGBoost model.
  - `benchmark --queries N --plot`: Runs the 4-phase benchmark and regenerates all charts.

### 4.11 Relational Schema & Benchmark Queries (`data/`)
- `data/schema.sql`: Defines relational tables (`customers`, `suppliers`, `parts`, `orders`, `order_items`) modeled after decision-support benchmarks (TPC-H), complete with primary keys, foreign keys, and indexes.
- `data/seed_data.py`: Generates realistic data rows (1,000 customers, 1,000 parts, 200 suppliers, 5,000 orders, 20,000 line items).
- `data/benchmark_queries.sql`: 12 diverse analytical SQL queries ranging from single-table filtered scans to 4-table joins with aggregations and nested subqueries.

---

## 5. Optimization Strategies & PostgreSQL Session Hints

| Strategy ID | Name | PostgreSQL Session Commands | Mechanism & Rationale |
|:---:|:---|:---|:---|
| **0** | `DEFAULT` | *None* | Standard cost-based optimizer plan. Optimal when CPU and RAM are unconstrained. |
| **1** | `AVOID_HASH_JOIN` | `SET enable_hashjoin = off;`<br>`SET enable_mergejoin = on;` | Hash joins build in-memory tables. Under memory or CPU stress, hash joins cause swap thrashing or high hashing overhead. Merge joins stream records using indexes with minimal RAM. |
| **2** | `FORCE_NESTED_LOOP` | `SET enable_hashjoin = off;`<br>`SET enable_mergejoin = off;`<br>`SET enable_nestloop = on;` | Favors index nested loops. Highly efficient for selective queries when sorting/hashing would cause CPU contention. |
| **3** | `CONSTRAIN_WORK_MEM` | `SET work_mem = '4MB';` | Prevents excessive RAM allocation during memory pressure, protecting against Out-Of-Memory (OOM) errors. |
| **4** | `DISABLE_PARALLEL` | `SET max_parallel_workers_per_gather = 0;` | Under 80%+ CPU load, multi-worker parallel scans cause heavy thread scheduling thrashing. Single-threaded execution eliminates thread locking and finishes faster. |
| **5** | `DELAY_THROTTLE` | *Backoff micro-sleep (50ms)* | Under extreme CPU saturation (90%+), a brief delay lets transient OS spikes subside, preventing system lockups. |

---

## 6. Machine Learning Methodology & 25-Dimensional Feature Space

| Index | Feature | Source | Description |
|:---:|:---|:---:|:---|
| 0–1 | `q_join_count`, `q_table_count` | Query AST | Number of JOIN clauses and tables referenced |
| 2–5 | `q_has_aggregation`, `q_has_group_by`, `q_has_having`, `q_agg_count` | Query AST | Aggregation indicators and count of `COUNT`, `SUM`, `AVG`, etc. |
| 6–7 | `q_has_order_by`, `q_has_limit` | Query AST | Sorting and result limit flags |
| 8–9 | `q_has_subquery`, `q_subquery_count` | Query AST | Subquery presence and count |
| 10 | `q_filter_count` | Query AST | Number of boolean conditions in `WHERE` clause |
| 11–13 | `q_query_length`, `q_token_count`, `q_complexity_score` | Query Text | Character length, token count, and composite complexity score |
| 14–15 | `q_estimated_cost`, `q_estimated_rows` | EXPLAIN / Heuristic | Total optimizer cost units and estimated row cardinality |
| 16–17 | `os_cpu_percent`, `os_cpu_core_max` | Telemetry | Overall CPU utilization % and maximum single-core utilization % |
| 18–19 | `os_mem_percent`, `os_mem_available_mb` | Telemetry | Physical RAM usage % and available memory in MB |
| 20–21 | `os_disk_read_mbps`, `os_disk_write_mbps` | Telemetry | Disk read/write rates in MB/s |
| 22–24 | `os_load_1m`, `os_process_count`, `os_is_cpu_stressed` | Telemetry | 1-minute system load average, active process count, and CPU stress flag ($\ge 75\%$) |

### Model Performance & Evaluation
- **Why XGBoost?** Deep Neural Networks introduce 10–50 ms inference overhead, which exceeds the execution time of small-to-medium database queries. XGBoost evaluates tree ensembles in **less than 0.5 milliseconds**, making it ideal for real-time middleware inference.
- **Model Performance**:
  - Test Accuracy: **90.12%**
  - Primary Drivers: `os_is_cpu_stressed` (45.3% weight), `os_load_1m` (14.9%), `os_cpu_percent` (9.0%), `os_mem_percent` (8.0%).

---

## 7. Experimental Results & Performance Findings

We evaluated QuerySync on 25 complex decision-support queries under the 4 mandatory conditions:

| Experimental Phase | System Condition | Planner | Average Latency | P95 Latency | Throughput |
|:---|:---|:---:|:---:|:---:|:---:|
| **Baseline 1** | Normal Load | Default PostgreSQL | **37.91 ms** | 58.70 ms | 1026 QPM |
| **Experiment 1** | Normal Load | QuerySync (OS-Aware) | **43.24 ms** | 63.82 ms | 536 QPM |
| **Baseline 2** | CPU Stressed (85%) | Default PostgreSQL | **223.67 ms** | 346.33 ms | 532 QPM |
| **Experiment 2** | CPU Stressed (85%) | QuerySync (OS-Aware) | **43.59 ms** | **67.50 ms** | 283 QPM |

### Key Takeaways:
- **5.13x Speedup Under Contention**: Under CPU stress, Default PostgreSQL degraded from 37.91 ms to 223.67 ms. QuerySync adapted execution plans to avoid saturated cores, maintaining latency at 43.59 ms (**80.5% lower latency**).
- **Tail Latency Protection**: P95 latency for Default PostgreSQL exploded to 346.33 ms, whereas QuerySync kept tail latency at 67.50 ms.
- **Top Influential Features**: XGBoost identified `os_is_cpu_stressed` (45.3%), `os_load_1m` (14.9%), `os_cpu_percent` (9.0%), and `os_mem_percent` (8.0%) as the primary drivers of plan adaptation.

---

## 8. CLI Usage & Viva Demonstration Guide

```powershell
# 1. Live OS Telemetry Monitoring
d:\OSAwareQO\venv\Scripts\python.exe main.py monitor

# 2. Execute SQL with real-time adaptation and latency comparison
d:\OSAwareQO\venv\Scripts\python.exe main.py query "SELECT c.customer_name, COUNT(o.order_id) FROM customers c JOIN orders o ON c.customer_id = o.customer_id GROUP BY c.customer_name;"

# 3. Retrain XGBoost model
d:\OSAwareQO\venv\Scripts\python.exe main.py train

# 4. Run 4-phase benchmark and regenerate publication charts
d:\OSAwareQO\venv\Scripts\python.exe main.py benchmark --queries 25 --plot
```

---

## 9. Future Research Scope

1. **Kernel eBPF Integration**: Replacing user-space polling with Linux eBPF tracepoints to monitor kernel run-queue latency and L3 CPU cache misses directly.
2. **Online Reinforcement Learning (Contextual Bandits)**: Incorporating online exploration-exploitation algorithms (e.g., LinUCB, Thompson Sampling) to learn novel hardware environments autonomously without requiring prior offline datasets.
3. **Mid-Flight Adaptive Re-Planning**: Enabling PostgreSQL to switch execution plans mid-query if background system load spikes while a long-running analytical query is actively executing.
4. **Cloud Multi-Tenant Cost Optimization**: Integrating cloud billing APIs (AWS CloudWatch, GCP Cloud Monitoring) to balance query latency against cloud VM compute billing tiers in real time.
