# How to run QuerySync

## 1. Setup (once)

```powershell
.\venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env          # edit if your PostgreSQL settings differ
```

Start PostgreSQL (portable install in this folder):
```powershell
pgsql\pgsql\bin\pg_ctl.exe start -D pgdata -w
```
Stop it when done with `pgsql\pgsql\bin\pg_ctl.exe stop -D pgdata`.

Rebuilding the database from scratch is optional and **drops the benchmark tables**:
```powershell
python data\seed_data.py
```
This creates 316,000 rows, the TPC-H names (`data/tpch_compat.sql`) and planner statistics.

## 2. Commands

| Command | What it does |
|---|---|
| `python main.py ui` | Dashboard at http://127.0.0.1:5000: live load, a CPU-stress button, query comparison, and the learned decisions |
| `python main.py query "SELECT ..."` | Runs the query 5 times as default PostgreSQL and 5 times through QuerySync, alternating, and compares medians |
| `python main.py bench` | Full benchmark: 22 queries x {default, A/A control, rule, adaptive} x 5 rounds, at normal load and under CPU stress. Writes `reports/benchmark.json` and `reports/speedup_by_query.png` |
| `python main.py policy` | Table of what QuerySync has learned per query and load level |
| `python main.py policy --reset` | Forget everything learned |
| `python main.py monitor` | Live CPU: total, external (other programs) and PostgreSQL |
| `python -m unittest discover -s tests` | Unit tests (no database needed) |

## 3. Reading the results

- **"Default plan kept"**: the query's default plan does not use parallel workers.
  QuerySync then runs it exactly like plain PostgreSQL, adding about 0.2 ms of decision time.
- **"Learning"**: the first ~6 runs of a parallel query alternate between the default plan
  and the serial plan to measure both. Some of these runs can be slower; that is the cost
  of measuring.
- **"x times faster"**: the serial plan was measured to be reliably faster for this query
  at this load level.
- **"No measurable difference"**: the change is within noise. On this laptop, the same plan
  can vary by up to ~1.8x between runs, so always compare medians over several runs.
- Every 25th run of a learned query re-measures the other plan once, so decisions follow
  changes in load or data.
