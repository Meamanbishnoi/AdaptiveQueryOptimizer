"""PostgreSQL access: connect, run a query under a hint set, read EXPLAIN plans."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.errors

from qs.config import PG_DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER


class QueryTimeout(Exception):
    """The query exceeded its statement_timeout (only used during data collection)."""


def connect(dbname: str = PG_DATABASE):
    try:
        conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=dbname, user=PG_USER,
                                password=PG_PASSWORD, connect_timeout=5)
    except psycopg2.OperationalError as ex:
        raise SystemExit(
            f"Cannot connect to PostgreSQL at {PG_HOST}:{PG_PORT}/{dbname}.\n"
            "Start it with:  python main.py db-start\n" + str(ex)
        )
    conn.autocommit = True
    return conn


def _settings_sql(settings: Dict[str, str], timeout_ms: Optional[int]) -> str:
    parts = [f"SET LOCAL {k} = {v};" for k, v in settings.items()]
    if timeout_ms:
        parts.append(f"SET LOCAL statement_timeout = {int(timeout_ms)};")
    return " ".join(parts)


class Database:
    def __init__(self, dbname: str = PG_DATABASE):
        self.dbname = dbname
        self.conn = connect(dbname)
        self._helpers, self._pool = None, None

    def run(self, sql: str, settings: Dict[str, str], timeout_ms: Optional[int] = None) -> Tuple[float, int]:
        """
        Executes `sql` with the given planner settings applied via SET LOCAL, in ONE round trip:
            BEGIN; SET LOCAL ...; <query>     then COMMIT.
        Returns (milliseconds, rows). Raises QueryTimeout if statement_timeout fires.
        """
        sql = sql.strip().rstrip(";")
        prefix = _settings_sql(settings, timeout_ms)
        text = f"BEGIN; {prefix} {sql}" if prefix else sql
        with self.conn.cursor() as cur:
            try:
                t0 = time.perf_counter()
                cur.execute(text)
                rows = len(cur.fetchall()) if cur.description else cur.rowcount
                ms = (time.perf_counter() - t0) * 1000.0
                if prefix:
                    cur.execute("COMMIT")
                return ms, rows
            except psycopg2.errors.QueryCanceled:
                cur.execute("ROLLBACK")
                raise QueryTimeout()
            except Exception:
                if prefix:
                    cur.execute("ROLLBACK")
                raise

    def explain(self, sql: str, settings: Dict[str, str]) -> Dict[str, Any]:
        """EXPLAIN (FORMAT JSON) under the given settings. Returns the root plan node plus planning info."""
        sql = sql.strip().rstrip(";")
        with self.conn.cursor() as cur:
            cur.execute("BEGIN; " + _settings_sql(settings, None) + " EXPLAIN (FORMAT JSON) " + sql)
            raw = cur.fetchone()[0]
            cur.execute("ROLLBACK")
        doc = json.loads(raw) if isinstance(raw, str) else raw
        return doc[0]["Plan"]

    def explain_many(self, sql: str, settings_list: List[Dict[str, str]], workers: int = 4) -> List[Dict[str, Any]]:
        """EXPLAIN under several settings, spread over `workers` connections in parallel (planning only)."""
        if self._pool is None:
            self._helpers = [Database(self.dbname) for _ in range(workers - 1)]
            self._pool = ThreadPoolExecutor(workers)
        dbs = [self] + self._helpers
        chunks = [settings_list[i::len(dbs)] for i in range(len(dbs))]
        parts = list(self._pool.map(lambda d, ch: [d.explain(sql, s) for s in ch], dbs, chunks))
        out = [None] * len(settings_list)
        for i, part in enumerate(parts):
            for j, plan in enumerate(part):
                out[i + j * len(dbs)] = plan
        return out
