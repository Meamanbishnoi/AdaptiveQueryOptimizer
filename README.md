# QuerySync: OS-Aware Query Optimization for PostgreSQL

QuerySync sits between an application and PostgreSQL 16. For every query it decides one
thing: run it with PostgreSQL's **default plan**, or as a **serial plan** (parallel workers
disabled). The choice is made per query template and per load level, from latencies
QuerySync has actually measured on this machine.

**Why that one decision?** Earlier versions of this project tried six planner strategies,
XGBoost and a LinUCB bandit. Measurements showed that every reliable speed-up came from
avoiding parallel workers; nothing else made a significant difference, and the ML models
did not generalize. See [PROJECT_JOURNAL_AND_EVOLUTION.md](PROJECT_JOURNAL_AND_EVOLUTION.md),
Milestones 11 and 12.

## How it works

```
query ──► template (SQL with literals removed)
            │
            ├─ first time: EXPLAIN - does the default plan use parallel workers?
            │     no  ──► always DEFAULT (QuerySync never changes these queries)
            │     yes ──► policy for (template, load level)
            │
load level ◄─ telemetry: CPU used by programs other than PostgreSQL (>= 45% = "high")
            │
policy:  learning  - alternate DEFAULT / SERIAL until each has 3 runs
         decided   - SERIAL only if its median is >=10% faster AND it wins >=75% of run pairs;
                     otherwise DEFAULT
         re-check  - every 25th run measures the other plan once
            │
execute: SERIAL = "BEGIN; SET LOCAL max_parallel_workers_per_gather = 0; <query>" (one round trip)
```

Decision overhead is about 0.2 ms per query.

## Project layout

```
main.py              CLI: ui | query | bench | policy | monitor
src/config.py        settings (all overridable in .env)
src/telemetry.py     background CPU/RAM sampling, load level
src/winproc.py       Windows: CPU per process in one system call (external vs PostgreSQL)
src/db.py            PostgreSQL: run(sql, serial), is_parallel(sql)
src/policy.py        the DEFAULT-vs-SERIAL decision (stored in models/policy.json)
src/optimizer.py     ties telemetry, policy and database together
src/server.py        dashboard backend;  src/index.html  dashboard page
experiments/         benchmark.py (evaluation + chart), stress.py (CPU stress workers)
data/                schema, seeder, TPC-H-style queries, TPC-H compatibility views
tests/               unit tests for the policy
docs/archive/        documents describing earlier versions
```

## Quick start

```powershell
.\venv\Scripts\activate
pip install -r requirements.txt
pgsql\pgsql\bin\pg_ctl.exe start -D pgdata -w
python main.py ui
```
More detail: [HOW_TO_RUN.md](HOW_TO_RUN.md).

## Results

`python main.py bench`: 22 TPC-H-style queries; each mode runs every query 5 times,
interleaved with the other modes in random order. "Stressed" = CPU workers on 3 of 4 cores at
a 90% duty cycle (median external CPU ~63%). A query counts as a **win/loss** only if it is
>10% faster/slower than default **and** a permutation test gives p < 0.05.
Source: `reports/benchmark.json`, chart `reports/speedup_by_query.png`.

| Mode | Normal load: total time | geomean | wins / losses | Stressed: total time | geomean | wins / losses |
|---|---:|:---:|:---:|---:|:---:|:---:|
| Default PostgreSQL | 2230 ms | 1.00x | – | 3647 ms | 1.00x | – |
| A/A control (default vs itself) | 2550 ms | 0.93x | 0 / 0 | 3621 ms | 0.98x | 0 / 0 |
| Rule (serial when load is high) | 2462 ms | 0.94x | 0 / 0 | 2558 ms | 1.24x | 5 / **1** (Q01 1.7x slower) |
| **QuerySync adaptive** | **1667 ms (−25%)** | **1.17x** | 2 / 1\* | **2412 ms (−34%)** | **1.22x** | **5 / 0** |

\* The one "loss" is Q12, which has no parallel plan: QuerySync ran PostgreSQL's own plan,
and the 0.4 vs 0.4 ms difference is noise. With 22 queries tested at p < 0.05, about one
such false positive per mode is expected; the A/A row shows the noise level.

What this shows:
- 8 of the 22 queries have parallel plans. Five of them (Q06, Q11, Q14, Q15, Q21) run
  **2.5–3.2x faster** serially under stress, and 2–3.5x at normal load. For Q01, Q02 and Q18
  QuerySync measured no reliable gain and mostly keeps the default plan. The 14 queries
  without parallel plans run exactly as in plain PostgreSQL.
- **Load-awareness matters for Q01**: a fixed "go serial under load" rule makes it 1.7x slower;
  QuerySync measured that and kept the parallel plan.
- Run-to-run noise on this laptop is large (A/A averages vary by up to 7%), so compare runs
  using the medians and significance marks, not single executions.

## Limitations

- The data is a TPC-H-*shaped* synthetic schema (316k rows, several constant columns),
  not dbgen TPC-H. Call the results "TPC-H-style".
- One 4-core Windows laptop. Latency there is bimodal (the same plan varies up to ~1.8x),
  and part of the serial-plan benefit is likely the cost of parallel workers on Windows.
- Load is synthetic CPU stress; realistic co-located workloads are future work.

## Authors
- **Aman Bishnoi** (Lead) – BTech CSE (AIML), Reg No: 240211396
- **Pranjali Bhatt** – BTech CSE (AIML), Reg No: 240221381
