# QuerySync: AI-Driven, OS-Aware Query Optimization and Scheduling for PostgreSQL

QuerySync sits in front of PostgreSQL 16 and has two halves:

* **DBMS half: choose a better plan for each query.** QuerySync looks at the plans PostgreSQL
  would produce under 13 planner hint sets. A machine-learning model predicts which plan will run
  fastest under the current operating-system load, and the query runs with that plan. If the model
  is not confident of a real gain, it keeps PostgreSQL's default plan.
* **OS half: schedule the queries the way an operating system schedules processes.**
  * a long-term scheduler (admission) and a short-term scheduler (SJF, HRRN, priority with aging,
    FCFS) driven by a predicted burst time;
  * a measured degree of multiprogramming, to avoid thrashing;
  * a multilevel-queue express lane for short queries;
  * Banker's-style memory admission;
  * OS process priority for PostgreSQL's processes, including parallel workers, when other programs
    load the CPU.

The plan-steering approach follows *Bao* (Marcus et al., SIGMOD 2021). The OS layer is described
concept by concept in [docs/OS_CONCEPTS.md](docs/OS_CONCEPTS.md).

## How it works

```
                       arriving queries (many users)
                                  │
             ┌────────────────────▼─────────────────────┐
             │ LONG-TERM SCHEDULER (admission)          │
             │  EXPLAIN default plan → latency model    │  burst time (predicted ms)
             │  short query?  → keep default plan       │
             │  else EXPLAIN 12 more hint sets (4 conn. │
             │  in parallel) → plan model (5 LightGBM)  │  best plan, if confident (≥15%)
             │  memory claim from the plan              │  max work_mem need
             └────────────────────┬─────────────────────┘
                                  ▼  ready queue
             ┌──────────────────────────────────────────┐
             │ SHORT-TERM SCHEDULER (dispatcher)        │
             │  policy: SJF | HRRN | priority+aging |   │
             │          FCFS                            │
             │  MPL limit (measured, per load level)    │
             │  + 1 express slot for short queries      │
             │  Banker's-style memory safe-state check  │
             └────────────────────┬─────────────────────┘
                                  ▼
             PostgreSQL 16:  BEGIN; SET LOCAL <hints>; <query>
             under high external CPU load: all postgres.exe processes → HIGH OS priority
```

Single queries (dashboard *Compare*, `python main.py query`) go through the same decision path
and the OS priority boost, without the queue.

Hint sets (`qs/hints.py`): default, no_parallel, more_parallel, no_hashjoin, no_mergejoin,
no_nestloop, hash_joins_only, no_hash_no_merge, seq_scans_only, no_seqscan, more_work_mem,
serial_more_mem, serial_no_nestloop.

## Data and workload

* **Official TPC-H at scale factor 1** (8.66 million rows), generated with DuckDB's built-in dbgen
  and loaded into PostgreSQL 16 with primary keys, foreign-key indexes and statistics.
* **All 22 TPC-H query templates** with random substitution parameters that follow the TPC-H
  specification (`qs/workload.py`), so the models are trained and tested on different queries.
* **Multi-user traces** for the scheduler: Poisson arrivals, 60% short "interactive" queries
  (templates whose default plan runs in under 0.5 s), 40% "batch" reports, fresh parameters.

## Project layout

```
main.py                  CLI (see below)
qs/config.py             settings (overridable in .env)
qs/db.py                 PostgreSQL access: run under a hint set, (parallel) EXPLAIN
qs/hints.py              the 13 hint sets
qs/workload.py           TPC-H templates, parameter generator, multi-user traces
qs/features.py           plan + OS features; per-plan memory claim
qs/model.py              plan model (LightGBM ensemble), latency (burst-time) model
qs/optimizer.py          per-query decision, short-query bypass, execution, OS boost
qs/scheduler.py          OS-style query scheduler (policies, MPL, express lane, memory admission)
qs/osctl.py              OS process priority / CPU affinity of PostgreSQL's processes
qs/telemetry.py          background CPU/RAM sampling; external vs PostgreSQL CPU
qs/winproc.py            per-process CPU accounting and process listing (NtQuerySystemInformation)
qs/stress.py             CPU stress generator for experiments
qs/server.py, index.html dashboard
experiments/             load_tpch, collect, analyze (headroom), evaluate, validate, schedule
tests/                   unit tests (core + OS layer)
docs/OS_CONCEPTS.md      OS concepts → code → experiment
results/                 measurements and reports (*.json, *.png)
```

## Quick start (Windows)

```powershell
python -m venv venv; .\venv\Scripts\activate
pip install -r requirements.txt
python main.py db-start          # bundled PostgreSQL 16 (pgsql/, pgdata/)
python main.py load-data         # only once: TPC-H SF1 (~2.5 min)
python main.py ui                # dashboard at http://127.0.0.1:5000
```

Full pipeline: `collect` → `headroom` → `train` → `evaluate` → `validate` → `schedule`.
`python -m unittest discover -s tests` runs the 21 unit tests.

## Results

All numbers are real measurements on one 4-core Windows 11 laptop running PostgreSQL 16.15 and
TPC-H SF1. Two load conditions are used:

* **"Stress"**: a synthetic program keeps 3 of the 4 cores busy at 90%.
* **"Idle"**: no synthetic stress. The laptop's normal applications (browser, downloads) were still
  running, so external CPU was measured at 10–77% depending on the moment. Each result states the
  measured value.

### A. Plan choice (DBMS half)

**1. Headroom: is there anything to gain?** Every query instance (176) was run under every hint
set (`python main.py headroom`; 4,576 executions, 109 min):

| load | default PostgreSQL | best single fixed hint | oracle (best hint per query) |
|---|---|---|---|
| idle   | 238.3 s | 193.3 s (−18.9%, `no_hash_no_merge`) | 142.6 s (−40.2%) |
| stress | 324.9 s | 279.1 s (−14.1%, `no_hash_no_merge`) | 221.0 s (−32.0%) |

The best hint differs per query and per load, so a learned, per-query choice is needed. For example:
* Q17: `no_hashjoin` is about 10× faster;
* Q04 and Q07: serial plans win;
* Q01 under stress: *more* parallel workers win.

**2. Offline evaluation** (`python main.py evaluate`). The models were trained on parameter
instances 0–5 and tested on unseen instances 6–7. "Regressions" means queries more than 1.2×
slower than default.

| load | default | best fixed hint | static rule (serial if busy) | **AI model** | oracle |
|---|---|---|---|---|---|
| idle   | 56.6 s | −13.7% (5 regr.) | ±0% | **−33.3% (0 regr.)** | −40.9% |
| stress | 80.7 s | −11.5% (8 regr.) | +31.8% slower (13 regr.) | **−28.5% (0 regr.)** | −31.8% |

On unseen *templates* (5-fold; the model never saw that query shape) the gain is −6.9% idle with
12 regressions, and −1.7% under stress with 8 regressions.

The **latency (burst-time) model** used by the scheduler was tested on unseen instances:
* median error 8.9%;
* 99.6% of predictions within 2× of the real time;
* rank correlation with the real latency 0.985.

**3. Live validation** (`python main.py validate --rounds 5`). 44 held-out queries, each run 5
times per strategy in random interleaved order.
* AI latency **includes the decision time** (EXPLAINs + model).
* Win/loss means a difference above 10% **and** permutation p < 0.05.
* The A/A row runs the default plan twice, to show what pure noise looks like.
* "AI + OS" also raises PostgreSQL's processes to HIGH priority when external CPU is high.

| load | strategy | total | vs default | geomean speedup | wins | losses |
|---|---|---|---|---|---|---|
| idle (18.7% ext. CPU) | default | 49.85 s | — | 1.00× | — | — |
|  | A/A control | 52.96 s | +6.2% | 1.01× | 0 | 0 |
|  | static rule | 50.63 s | +1.6% | 1.01× | 0 | 0 |
|  | AI plans | 38.28 s | -23.2% | 1.34× | 9 | 0 |
|  | **AI + OS priority (QuerySync)** | **39.03 s** | **-21.7%** | 1.30× | 11 | 0 |
| stress (57.8% ext. CPU) | default | 71.44 s | — | 1.00× | — | — |
|  | A/A control | 71.31 s | -0.2% | 1.00× | 0 | 0 |
|  | static rule | 74.84 s | +4.8% | 1.00× | 9 | 8 |
|  | AI plans | 56.62 s | -20.7% | 1.25× | 17 | 1 |
|  | **AI + OS priority (QuerySync)** | **49.90 s** | **-30.2%** | 1.35× | 29 | 2 |

Under stress, the OS layer adds to the plan choice: AI plans alone give −20.7%, AI + HIGH priority
gives **−30.2%** (29 significant wins). At low load the boost is not triggered (`OS_BOOST=auto`), so
AI and AI + OS differ only by noise. In this run background noise was higher (the A/A control drifted
+6.2%), so fewer idle wins reached significance than in the earlier run (`results/validation_v2.json`:
−24.9% idle, 19 wins / 1 loss). The losses are short Q11/Q20 instances (150–200 ms), where the AI
mostly kept the default plan and only the decision time remained.

Largest live gains: Q17 about 7× (`no_hash_no_merge`), Q18 1.5–2.2×, Q07 about 2×, Q21 about 1.6×,
Q13 1.3–1.7×. Chart: `results/validation.png`. A first run with a feature-extraction bug (150 ms
decision time) is kept in `results/validation_v1_slow_decision.json`.

### B. OS layer (`python main.py schedule`)

**4. Degree of multiprogramming and thrashing.** A batch of 30 queries was submitted at once
(one run per setting):

| throughput (queries/min) | plain PostgreSQL, all 30 at once | MPL 1 | **MPL 2** | MPL 3 | MPL 4 | MPL 6 | MPL 8 |
|---|---|---|---|---|---|---|---|
| idle (56% ext. CPU) | 46.4 | 67.7 | **88.2** | 86.9 | 83.6 | 73.6 | 83.1 |
| stress (97% ext. CPU) | 59.5 | 48.6 | **80.2** | 76.1 | 76.1 | 67.0 | 60.9 |

Running every query at once (plain PostgreSQL) gives the lowest throughput. That is thrashing: the
queries and their parallel workers fight for 4 cores. Limiting execution to 2 queries at a time
nearly doubles throughput, so QuerySync's "auto" MPL is 2 at both load levels (`models/mpl.json`).

**5. Scheduling policies on a multi-user trace.** 60 queries per run, 3 traces per load level. The
arrival rate is 85% of plain PostgreSQL's measured capacity. Turnaround = finish − arrival, averaged
over all queries.

| | plain PostgreSQL | AI plans, no scheduler | QuerySync FCFS | **QuerySync SJF** | QuerySync HRRN | QuerySync Priority+aging |
|---|---|---|---|---|---|---|
| **idle**: avg turnaround | 2.42 s | 1.39 s (−43%) | 1.26 s (−48%) | **1.11 s (−54%)** | 1.39 s (−43%) | 1.21 s (−50%) |
| interactive queries | 0.98 s | 0.64 s | 0.49 s | **0.43 s** | 0.57 s | 0.47 s |
| p95 turnaround | 9.74 s | 4.60 s | 5.15 s | 4.80 s | 5.51 s | 5.24 s |
| **stress**: avg turnaround | 3.46 s | 2.68 s (−23%) | 2.27 s (−34%) | **2.04 s (−41%)** | 2.43 s (−30%) | 3.19 s (−8%) |
| interactive queries | 1.16 s | 0.99 s | 0.77 s | **0.72 s** | 0.80 s | 0.98 s |
| p95 turnaround | 13.08 s | 10.95 s | 8.17 s | 9.73 s | 10.50 s | 10.04 s |
| max wait in queue | 0.26 s | 0.38 s | 4.92 s | 5.64 s | 6.46 s | 7.85 s |

**SJF with predicted burst times is the best policy at both load levels**, and it is the default.
The AI plans alone explain most of the idle gain. The scheduler adds 11 points at idle and 18 points
under stress.

The price of scheduling is waiting: some long reports wait up to 5–8 s in the queue. They still
finish sooner on average, because they no longer compete with everything else.

Two earlier observations shaped the final design. Both are in `results/scheduling_v1.json`:
* Without the express slot, short interactive queries were *slower* under stress than in plain
  PostgreSQL, because they waited behind long reports. Adding one reserved slot for queries predicted
  under 500 ms (a multilevel queue) fixed this: 0.72 s versus 1.16 s.
* FCFS with a tight MPL suffered head-of-line blocking: one trace was 28% *slower* than plain
  PostgreSQL. The scheduling policy matters, not only the limit.

**6. OS process priority.** 22 held-out queries with fixed AI plans were run interleaved, with
PostgreSQL's processes at NORMAL vs HIGH priority:

| load | NORMAL priority | **HIGH priority** | HIGH + pinned to 2 cores (CPU affinity) |
|---|---|---|---|
| idle (77% ext. CPU from other apps) | 21.00 s | **18.57 s (−11.6%)** | — |
| stress (83% ext. CPU) | 28.38 s | **23.11 s (−18.6%)** | 31.45 s (+10.8%, rejected) |

The parallel worker processes PostgreSQL starts do *not* inherit the leader's priority on Windows.
QuerySync's priority daemon finds and boosts them within 30 ms; this was verified, with every
postgres.exe at priority class HIGH. CPU pinning was slower, so QuerySync does not use it.

## Limitations

* One 4-core Windows laptop. Load is synthetic CPU stress plus whatever the laptop was running;
  every result records the measured external CPU.
* TPC-H SF1 only. Generalisation to unseen query templates is weak (see A.2), and to other schemas
  it is untested. Live runs are logged to `results/live.jsonl`, so the model can be retrained
  (`python main.py train`).
* Scheduling is **non-preemptive**: a running SQL query cannot be paused, only cancelled and
  restarted, so round-robin and MLFQ with time slices are not applicable.
* SJF can make long reports wait (max wait 5–6 s here). HRRN and priority-with-aging bound that
  wait but gave a smaller average gain in our runs.
* The MPL sweep uses one run per setting. The oracle uses one measurement per hint set, so it is
  slightly optimistic.
* HIGH OS priority favours the database over the user's other programs while the machine is busy.
  That is the intended trade-off. It is applied only under high external load (`OS_BOOST=auto`) and
  can be turned off (`OS_BOOST=off`).
