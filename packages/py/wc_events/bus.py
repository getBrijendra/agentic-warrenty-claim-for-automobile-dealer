"""PgJobQueue + PgOutboxEventBus.

- Outbox rows are written in the SAME transaction as the state change (no lost / phantom events).
- outbox-relay copies committed rows into ops.event_log (monotonic ids → SSE Last-Event-ID resume).
- Jobs: one job = one claim step; leased with FOR UPDATE SKIP LOCKED; idempotent on idem_key.
"""
from __future__ import annotations

from typing import Any

from wc_core.infra import to_jsonable
from wc_core.settings import get_settings
from wc_db.db import connect, fetchall, j, tx


def emit(conn, topic: str, payload: dict) -> None:
    conn.execute("insert into ops.outbox (company_id, topic, payload) values (%s,%s,%s)",
                 [get_settings().company_id, topic, j(to_jsonable(payload))])


def publish(topic: str, payload: dict) -> None:
    with connect("core") as c:
        emit(c, topic, payload)


class PgJobQueue:
    def enqueue(self, kind: str, claim_id: str | None, payload: dict, idem_key: str, priority: int = 100,
                delay_s: float = 0, conn=None) -> None:
        sql = """insert into ops.jobs (company_id, kind, claim_id, payload, priority, run_at, idem_key)
                 values (%s,%s,%s,%s,%s, now() + make_interval(secs => %s), %s) on conflict (idem_key) do nothing"""
        args = [get_settings().company_id, kind, claim_id, j(payload), priority, delay_s, idem_key]
        if conn is not None:
            conn.execute(sql, args)
        else:
            with connect("core") as c:
                c.execute(sql, args)

    def lease(self, n: int, ttl_s: int = 120) -> list[dict]:
        if n <= 0:
            return []
        with tx("core") as c:
            return c.execute("""update ops.jobs set status='leased', attempts=attempts+1,
                                lease_until=now() + make_interval(secs => %s), updated_at=now()
                                where id in (select id from ops.jobs
                                             where (status='queued' and run_at <= now())
                                                or (status='leased' and lease_until < now())
                                             order by priority, run_at, id limit %s for update skip locked)
                                returning *""", [ttl_s, n]).fetchall()

    def ack(self, job_id: int) -> None:
        with connect("core") as c:
            c.execute("update ops.jobs set status='done', updated_at=now() where id=%s", [job_id])

    def nack(self, job_id: int, error: str, retry_in_s: float, max_attempts: int = 5) -> None:
        with connect("core") as c:
            c.execute("""update ops.jobs set status = case when attempts >= %s then 'dead' else 'queued' end,
                         run_at = now() + make_interval(secs => %s), last_error=%s, updated_at=now() where id=%s""",
                      [max_attempts, retry_in_s, error[:2000], job_id])

    def depth(self) -> dict:
        rows = fetchall("core", "select status, count(*) as n from ops.jobs group by status")
        return {r["status"]: r["n"] for r in rows}


def read_events(after_id: int, limit: int = 500) -> list[dict[str, Any]]:
    return fetchall("core", "select id, topic, payload, created_at from ops.event_log where id > %s order by id limit %s",
                    [after_id, limit])


def last_event_id() -> int:
    rows = fetchall("core", "select coalesce(max(id),0) as m from ops.event_log")
    return rows[0]["m"]
