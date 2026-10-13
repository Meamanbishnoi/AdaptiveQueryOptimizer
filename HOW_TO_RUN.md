# QuerySync: Step-by-Step Execution & Demonstration Guide

This guide provides clear, copy-pasteable instructions for running and evaluating the **QuerySync** Cross-Layer Adaptive Query Optimization system on Windows or Linux.

---

## 📋 Table of Contents
1. [Prerequisites & Environment Setup](#1-prerequisites--environment-setup)
2. [Interactive Web Dashboard (Recommended)](#2-interactive-web-dashboard-recommended)
3. [Quick Verification (Smoke Test)](#3-quick-verification-smoke-test)
4. [Step-by-Step Feature Walkthrough](#4-step-by-step-feature-walkthrough)
   - [4.1 Live OS Telemetry Monitoring](#41-live-os-telemetry-monitoring)
   - [4.2 Single Query Execution (XGBoost vs LinUCB)](#42-single-query-execution-xgboost-vs-linucb)
   - [4.3 LRU Plan & Feature Cache Diagnostics](#43-lru-plan--feature-cache-diagnostics)
   - [4.4 Canonical TPC-H 22-Query Benchmark Suite](#44-canonical-tpc-h-22-query-benchmark-suite)
   - [4.5 Training Data Collection & Offline Training](#45-training-data-collection--offline-training)
   - [4.6 4-Phase Stress Benchmark & Chart Generation](#46-4-phase-stress-benchmark--chart-generation)
5. [Live Viva / Interactive Demonstration Workflow](#5-live-viva--interactive-demonstration-workflow)
6. [Configuration & Dual-Mode Execution](#6-configuration--dual-mode-execution)
7. [Troubleshooting & FAQs](#7-troubleshooting--faqs)

---

## 1. Prerequisites & Environment Setup

### 1.1 Requirements
- **Python**: Version 3.10, 3.11, 3.12, or 3.13
- **Operating System**: Windows 10/11 or Linux
- **PostgreSQL (Optional)**: If PostgreSQL is not installed or running, QuerySync automatically switches to **High-Fidelity Hardware Simulation Mode**, meaning all components (telemetry, caching, ML model, bandit, and benchmarks) remain 100% operational without errors.

### 1.2 Activate Virtual Environment
Open PowerShell or your terminal in the project directory (`d:\OSAwareQO`):

```powershell
# Windows PowerShell
.\venv\Scripts\activate

# Or on Linux / macOS:
# source venv/bin/activate
```

*(If using PowerShell and script execution is restricted, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first).*

### 1.3 Verify Dependencies
All dependencies are pre-installed in the virtual environment. To verify or reinstall:
```powershell
pip install -r requirements.txt
```

---

## 2. Interactive Web Dashboard (Recommended)

To launch the full enterprise-grade Web UI in your browser:

```powershell
python main.py ui
```

This launches the multithreaded web dashboard on `http://127.0.0.1:5000` and automatically opens your default web browser!

**What you can do in the Web Dashboard**:
- **Live System Telemetry**: Watch real-time CPU %, RAM %, Memory Pressure Score ($MPS$), and Context Switches per second auto-updating every 1.5 seconds.
- **Simulate System Stress**: Click the **"Simulate System Stress"** button at the top to inject synthetic background CPU/RAM load and watch telemetry gauges turn red.
- **1-Click TPC-H Presets**: Select from all 22 standard TPC-H queries from the dropdown.
- **Choose ML Engine**: Select between **XGBoost (Supervised)** and **LinUCB (Online RL Bandit)**.
- **Interactive Latency Bar Chart**: Click "Execute with OS-Aware Optimizer" to see animated side-by-side latency comparisons, speedup multiplier (e.g., 5.13x faster), and plan cache status (`HIT` / `MISS`).
- **Plan Cache Diagnostics**: Click "Plan Cache" to inspect cache hits, misses, hit ratio, and reset the cache on demand.

---

## 3. Quick Verification (Smoke Test)

Run this one-liner to verify the system end-to-end via CLI:

```powershell
python main.py query "SELECT c_custkey, c_name FROM customer LIMIT 5;" --engine linucb
```

You should see:
- Real-time CPU, RAM, and Memory Pressure Score (MPS) telemetry.
- Query feature parsing and Plan Hash Cache status.
- LinUCB decision strategy selected with an online RL reward.
- Millisecond latency comparison.

---

## 4. Step-by-Step Feature Walkthrough

### 4.1 Live OS Telemetry Monitoring
Monitor real-time system hardware metrics, kernel signals, and memory pressure scores:

```powershell
# Continuous live monitor (Press Ctrl+C to exit)
python main.py monitor

# Or capture a specific number of samples (e.g. 5 samples at 0.5s interval):
python main.py monitor --count 5 --interval 0.5
```

**Key Metrics Output**:
- `CPU %`: Overall CPU utilization across all logical cores.
- `RAM MB (%)`: Used physical memory and percentage.
- `MPS`: Composite **Memory Pressure Score** ($MPS = 0.60 \times \text{RAM\%} + 0.40 \times (1 - \text{BufferHit})$).
- `CtxSw`: Kernel context switches per second (detects CPU thrashing).
- `Procs`: Active OS process count.

---

### 4.2 Single Query Execution (XGBoost vs LinUCB)

QuerySync offers two machine learning decision engines:

#### Option A: Supervised XGBoost Engine
Uses the pre-trained gradient-boosted decision trees:
```powershell
python main.py query "SELECT c_custkey, c_name, SUM(o_totalprice) FROM customer JOIN orders ON c_custkey = o_custkey WHERE c_mktsegment = 'BUILDING' GROUP BY c_custkey, c_name ORDER BY 3 DESC LIMIT 10;" --engine xgboost
```

#### Option B: Online Reinforcement Learning (LinUCB Contextual Bandit)
Uses the Upper Confidence Bound online learning engine that learns continuously:
```powershell
python main.py query "SELECT c_custkey, c_name, SUM(o_totalprice) FROM customer JOIN orders ON c_custkey = o_custkey WHERE c_mktsegment = 'BUILDING' GROUP BY c_custkey, c_name ORDER BY 3 DESC LIMIT 10;" --engine linucb
```

#### Verifying Parameterized Cache Hits:
Execute the same template with different literal values (e.g., `'AUTOMOBILE'` instead of `'BUILDING'`):
```powershell
python main.py query "SELECT c_custkey, c_name, SUM(o_totalprice) FROM customer JOIN orders ON c_custkey = o_custkey WHERE c_mktsegment = 'AUTOMOBILE' GROUP BY c_custkey, c_name ORDER BY 3 DESC LIMIT 10;" --engine linucb
```
**Expected Observation**:
The second execution will report:
```
Plan Hash Cache    : HIT (Hash: 15ee4518e8e962ed)
```
Feature extraction completes in **0.03 ms** instead of ~6.4 ms!

---

### 4.3 LRU Plan & Feature Cache Diagnostics
Inspect the status and hit ratio of the disk-backed LRU plan hash cache:

```powershell
python main.py cache-stats
```

**Sample Output**:
```
============================================================
QuerySync: LRU Query Feature & Plan Hash Cache Stats
============================================================
Cache Capacity     : 1024 query templates
Cached Templates   : 3
Total Lookups      : 12
Cache Hits         : 11
Cache Misses       : 1
Hit Ratio          : 91.7%
============================================================
```

---

### 4.4 Canonical TPC-H 22-Query Benchmark Suite
Run the standardized 22-query decision-support benchmark comparing Default PostgreSQL against QuerySync XGBoost and QuerySync LinUCB:

```powershell
python main.py tpch
```

**What this does**:
- Executes all 22 standard TPC-H queries (`data/tpch_benchmark.sql`) covering:
  - **Aggregation-Heavy Queries** (Q01, Q06)
  - **Multi-Way Star/Snowflake Joins** (Q02, Q03, Q05, Q07, Q08, Q09, Q10)
  - **Correlated Subqueries & Nested Sets** (Q04, Q11, Q12, Q13, Q14, Q15, Q16, Q17, Q18, Q19, Q20, Q21, Q22)
- Compares latencies under both **Normal (Idle OS)** and **Stressed (High CPU + Memory Pressure)** states.
- Saves telemetry report to `reports/tpch_benchmark_results.json`.

---

### 4.5 Training Data Collection & Offline Training

If you wish to re-generate the empirical dataset or retrain the XGBoost classifier from scratch:

```powershell
# 1. Generate 400 empirical training samples under simulated OS conditions
python main.py collect --samples 400

# 2. Retrain the XGBoost decision model
python main.py train
```

The retrained model will be evaluated (accuracy ~90%) and saved to `models/os_aware_model.pkl`.

---

### 4.6 4-Phase Stress Benchmark & Chart Generation
Run the 4-phase comparative benchmark (Baseline 1, Experiment 1, Baseline 2, Experiment 2) and regenerate publication charts:

```powershell
python main.py benchmark --queries 25 --plot
```

**Output Artifacts Generated in `reports/`**:
- `reports/latency_comparison_bar_chart.png` (Default vs QuerySync latency under Normal vs Stressed)
- `reports/latency_over_time_under_stress.png` (Real-time latency spikes under background CPU stress)
- `reports/model_feature_importance.png` (Top influential features identified by XGBoost)
- `reports/benchmark_results.json` (Numerical telemetry)

---

## 5. Live Viva / Interactive Demonstration Workflow

For presenting to examiners or project evaluators:

### Step 1: Show Real-Time Telemetry
Open Terminal 1 and run:
```powershell
python main.py monitor
```
Explain the cross-layer metrics: CPU %, RAM %, Memory Pressure Score ($MPS$), and context switch rates.

### Step 2: Demonstrate Query Adaptation Under Normal Load
In Terminal 2, run a join query:
```powershell
python main.py query "SELECT c_custkey, c_name, SUM(o_totalprice) FROM customer JOIN orders ON c_custkey = o_custkey GROUP BY c_custkey, c_name LIMIT 10" --engine xgboost
```
Point out that under idle conditions, QuerySync chooses `DEFAULT` plan (PostgreSQL parallel hash joins) because plenty of CPU and RAM are available.

### Step 3: Inject Background Stress and Show Autonomous Adaptation
Run the stress generator script or simulate load:
```powershell
# In Terminal 3 (or let the benchmark trigger stress automatically)
python experiments/stress_generator.py
```
Re-run the exact same query in Terminal 2:
```powershell
python main.py query "SELECT c_custkey, c_name, SUM(o_totalprice) FROM customer JOIN orders ON c_custkey = o_custkey GROUP BY c_custkey, c_name LIMIT 10" --engine xgboost
```
Point out:
- QuerySync detects high CPU/MPS.
- The decision engine dynamically switches strategy to `AVOID_HASH_JOIN` or `DISABLE_PARALLEL`.
- Latency is maintained at **~40–50 ms**, whereas Default PostgreSQL explodes to **~220–250 ms** (**5.13x speedup, 80.5% latency reduction**).

### Step 4: Demonstrate Online Reinforcement Learning (LinUCB)
Run the LinUCB bandit engine:
```powershell
python main.py query "SELECT c_custkey, c_name, SUM(o_totalprice) FROM customer JOIN orders ON c_custkey = o_custkey GROUP BY c_custkey, c_name LIMIT 10" --engine linucb
```
Show the online confidence-bound scoring and real-time reward update ($R \in [-1.0, 1.0]$) stored in `models/linucb_bandit.pkl`.

### Step 5: Demonstrate Parameterized Cache Speedup
Show `python main.py cache-stats` displaying near-100% cache hit ratio and sub-0.05ms feature retrieval.

---

## 6. Configuration & Dual-Mode Execution

The system configuration is stored in `.env` (or configured via environment variables).

```ini
# PostgreSQL Connection Configuration
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=osaware_db
PG_USER=postgres
PG_PASSWORD=postgres

# High-Fidelity Simulation Mode
# Set to 'false' to connect to live PostgreSQL, or 'true' to run offline simulation mode
USE_MOCK_DB=false

# Hardware Stress Thresholds
CPU_STRESS_THRESHOLD=75.0
MEM_STRESS_THRESHOLD=80.0
```

> **Note**: Even when `USE_MOCK_DB=false`, if PostgreSQL is offline or unreachable, QuerySync automatically falls back gracefully to high-fidelity simulation so demonstrations never crash or hang.

---

## 7. Troubleshooting & FAQs

### Q1: `activate : File ... cannot be loaded because running scripts is disabled on this system`
**Solution**: In PowerShell, run:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\activate
```

### Q2: Can I run this without installing PostgreSQL?
**Yes!** QuerySync is designed with high-fidelity fallback calibration. If PostgreSQL is not active, all features—telemetry, AST caching, XGBoost classification, LinUCB online RL, stress generation, and TPC-H benchmarks—run seamlessly using the internal hardware performance model.

### Q3: Where are the trained models and cache files saved?
- `models/os_aware_model.pkl`: Offline trained XGBoost model.
- `models/linucb_bandit.pkl`: Online LinUCB covariance matrices and state.
- `models/query_feature_cache.pkl`: Disk-persisted LRU query template cache.
- `reports/`: Generated benchmark JSON reports and PNG plots.

### Q4: How do I clear the LRU cache?
Delete `models/query_feature_cache.pkl` or run:
```powershell
python -c "from src.query_processor.feature_cache import default_feature_cache; default_feature_cache.clear()"
```
