"""PostgreSQL access: timed execution with or without parallel workers, and plan inspection."""

import json
import time
from typing import Any, Dict, Tuple

import psycopg2

from src.config import PG_DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER

# Settings for the SERIAL option. SET LOCAL ends with the transaction, so nothing leaks
# into later queries and no RESET round trip is needed.
SERIAL_SETTINGS = "SET LOCAL max_parallel_workers_per_gather = 0;"


class Database:
    def __init__(self):
        try:
            self.conn = psycopg2.connect(
                host=PG_HOST, port=PG_PORT, dbname=PG_DATABASE,
                user=PG_USER, password=PG_PASSWORD, connect_timeout=3,
            )
        except psycopg2.OperationalError as ex:
            raise SystemExit(
                f"Cannot connect to PostgreSQL at {PG_HOST}:{PG_PORT}/{PG_DATABASE}.\n"
                f"Start it with:  pgsql\\pgsql\\bin\\pg_ctl.exe start -D pgdata -w\n({ex})"
            )
        self.conn.autocommit = True

    def run(self, sql: str, serial: bool) -> Tuple[float, int]:
        """
        Executes the query and returns (milliseconds, rows). SERIAL sends
        'BEGIN; SET LOCAL ...; <query>' as ONE round trip; DEFAULT sends the bare query.
        """
        sql = sql.strip().rstrip(";")
        text = f"BEGIN; {SERIAL_SETTINGS} {sql}" if serial else sql
        with self.conn.cursor() as cur:
            try:
                t0 = time.perf_counter()
                cur.execute(text)
                rows = len(cur.fetchall()) if cur.description else cur.rowcount
                ms = (time.perf_counter() - t0) * 1000.0
            except Exception:
                if serial:
                    cur.execute("ROLLBACK")
                raise
            if serial:
                cur.execute("COMMIT")
        return ms, rows

    def plan(self, sql: str) -> Dict[str, Any]:
        """Root node of the default plan (EXPLAIN without executing)."""
        with self.conn.cursor() as cur:
            cur.execute("EXPLAIN (FORMAT JSON) " + sql.strip().rstrip(";"))
            raw = cur.fetchone()[0]
        plan = json.loads(raw) if isinstance(raw, str) else raw
        return plan[0]["Plan"]

    def is_parallel(self, sql: str) -> bool:
        """True if PostgreSQL's default plan uses parallel workers (a Gather node)."""
        def has_gather(node):
            return node["Node Type"].startswith("Gather") or any(has_gather(c) for c in node.get("Plans", []))
        return has_gather(self.plan(sql))
