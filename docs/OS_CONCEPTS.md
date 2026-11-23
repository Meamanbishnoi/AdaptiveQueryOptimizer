# Operating-system concepts in QuerySync

QuerySync has two halves:

* the **DBMS half** picks a better execution plan for each query;
* the **OS half** decides **which query runs when, how many run together, how much memory and
  CPU priority they get**, exactly as an operating system does for processes.

This file maps every OS concept to the code that implements it and to the experiment that
measures it. All results are real measurements: see the README's Results section and the dashboard's
*Results & charts → OS scheduling* tab.

| # | OS concept (textbook topic) | Where in QuerySync | Code | Measured by |
|---|---|---|---|---|
| 1 | **CPU scheduling algorithms**: FCFS, SJF, HRRN, Priority | Short-term scheduler: picks the next query for a free execution slot | `qs/scheduler.py` (`_key`, `_pick`) | `python main.py schedule --part policies` |
| 2 | **Burst-time prediction** (for SJF / HRRN) | A LightGBM model predicts each query's run time from its plan and the OS load (textbook: exponential averaging of past bursts) | `qs/model.py` (`LatencyModel`) | `python main.py evaluate` (median error, rank correlation) |
| 3 | **Starvation and aging** | HRRN's ratio (waiting + burst) / burst grows while a job waits; Priority scheduling raises a waiting job's priority every 5 s | `qs/scheduler.py` (`AGING_S`) | "max wait" column; `tests/test_os.py` |
| 4 | **Long-term vs short-term scheduler** | Admission thread (long-term): plan, burst and memory claim per query. Dispatcher (short-term): ready queue → slot | `QueryScheduler._admission`, `_worker` | Gantt charts (dashboard) |
| 5 | **Degree of multiprogramming, thrashing** | At most MPL queries run at once. Too many fight for CPU and memory and throughput falls. MPL per load level comes from a measured sweep | `auto_mpl_table`, `models/mpl.json` | `python main.py schedule --part mpl` |
| 6 | **Multilevel queue scheduling** | Short queries (predicted < 500 ms) get a reserved express slot that long reports may not use | `EXPRESS_SLOTS`, `_pick` | policy experiment (interactive turnaround) |
| 7 | **Resource allocation, Banker's algorithm (safe state)** | Each query declares a maximum working-memory claim from its plan. It is dispatched only if all running claims plus its own fit in the budget. An oversized job runs alone, so the system can never deadlock | `qs/features.py` (`memory_need_mb`), `_pick` | `memory_waits`; `tests/test_os.py` |
| 8 | **Process priority (priority scheduling inside the OS kernel)** | Under high external CPU load, all PostgreSQL processes (and QuerySync itself) are raised to HIGH priority, so the Windows scheduler runs them before competing programs | `qs/osctl.py` (`PriorityBooster`) | `python main.py schedule --part priority`; `validate` (mode ai_os) |
| 9 | **Processes vs threads; process creation** | PostgreSQL uses one process per connection and creates new processes for parallel workers. Windows children do NOT inherit the parent's priority class, so a daemon thread boosts new worker processes within 30 ms | `qs/osctl.py`, `qs/winproc.py` | verified: all postgres.exe at priority 128 (HIGH) |
| 10 | **CPU affinity** | Pinning PostgreSQL to 2 cores was measured and **rejected**: it was slower, because it removes cores the planner's parallel workers could use | `PriorityBooster(cores=...)` | priority experiment ("high pinned 2 cores") |
| 11 | **System calls, per-process CPU accounting** | `NtQuerySystemInformation` reads every process's CPU time in one call, so CPU used by PostgreSQL and by other programs can be separated | `qs/winproc.py` | dashboard "External CPU" |
| 12 | **Synchronization**: mutex, condition variable, producer–consumer | Ready queue guarded by a condition variable; admission thread = producer, worker threads = consumers; a lock serializes dashboard measurements | `threading.Condition` in `qs/scheduler.py`; `_run_lock` in `qs/server.py` | `tests/test_os.py` |
| 13 | **Thread pool / parallelism** | The 13 EXPLAINs per query run on 4 connections in parallel (≈ 150 ms → 10–30 ms decision time) | `Database.explain_many` (`qs/db.py`) | decision time in the dashboard |
| 14 | **Non-preemptive scheduling (why)** | A running SQL query cannot be paused, only cancelled and restarted, which loses its work. So all policies are non-preemptive. Round-robin and MLFQ with time slices are therefore not used | docstring of `qs/scheduler.py` | — |
| 15 | **CPU contention, time slicing** | The stress generator keeps 3 of 4 cores busy at a 90% duty cycle. Parallel plans lose under contention, which is why serial plans win under load | `qs/stress.py`; model feature `os_external_cpu` | live validation (stress) |

## How to explain it in one paragraph (viva)

> "Our project is a small operating system for database queries. When many users send queries, the
> **long-term scheduler** admits each one: our AI picks its execution plan and predicts its **burst
> time** and **memory claim**. The **short-term scheduler** then dispatches queries from the ready
> queue with **SJF, HRRN or priority-with-aging**. Only a measured number of queries run at once
> (**degree of multiprogramming**), because running everything together causes **thrashing**. Short
> queries get a reserved slot (**multilevel queue**). Memory is admitted with a **Banker's-style safe-state
> check**, so it can never be over-committed or deadlock. When other programs load the CPU, we raise
> PostgreSQL's **OS process priority**, including the parallel worker processes it creates. Every one of
> these was measured on real PostgreSQL. Where something did not help, for example CPU pinning or
> FCFS under load, we report that too."
