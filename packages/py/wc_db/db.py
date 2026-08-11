"""Database access: psycopg 3 (sync) connection pools per bounded context + plain-SQL migrations.

Works against PGlite (via pglite-socket) and real Postgres unchanged. Server-side prepared statements are
disabled because pglite-socket multiplexes all client connections onto one PGlite session.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from wc_core.infra import to_jsonable
from wc_core.settings import get_settings

MIGRATIONS = Path(__file__).parent / "migrations"
_pools: dict[str, ConnectionPool] = {}
_lock = threading.Lock()


def _url(db: str) -> str:
    s = get_settings()
    return {"core": s.database_url_core, "dms": s.database_url_dms, "sim": s.database_url_sim}[db]


def pool(db: str) -> ConnectionPool:
    with _lock:
        if db not in _pools:
            _pools[db] = ConnectionPool(
                _url(db), min_size=1, max_size=max(1, get_settings().db_pool_size), open=True, timeout=60,
                kwargs={"prepare_threshold": None, "autocommit": True, "row_factory": dict_row},
            )
        return _pools[db]


_held = threading.local()


class NestedConnectionError(RuntimeError):
    """Opening a second connection to the same database while holding one can deadlock (pool exhaustion, and on
    PGlite a held transaction blocks every other client). Resolve values before taking the connection."""


@contextmanager
def _guard(db: str):
    held = getattr(_held, "dbs", None)
    if held is None:
        held = _held.dbs = set()
    if db in held:
        raise NestedConnectionError(f"nested connection to '{db}' on the same thread")
    held.add(db)
    try:
        yield
    finally:
        held.discard(db)


@contextmanager
def connect(db: str) -> Iterator[psycopg.Connection]:
    with _guard(db), pool(db).connection() as conn:
        yield conn


@contextmanager
def tx(db: str) -> Iterator[psycopg.Connection]:
    with _guard(db), pool(db).connection() as conn:
        with conn.transaction():
            yield conn


def j(v: Any) -> Jsonb:
    return Jsonb(to_jsonable(v))


def fetchall(db: str, sql: str, params: Any = None) -> list[dict]:
    with connect(db) as c:
        return c.execute(sql, params).fetchall()


def fetchone(db: str, sql: str, params: Any = None) -> dict | None:
    with connect(db) as c:
        return c.execute(sql, params).fetchone()


def execute(db: str, sql: str, params: Any = None) -> None:
    with connect(db) as c:
        c.execute(sql, params)


def migrate(db: str) -> list[str]:
    applied: list[str] = []
    with connect(db) as c:
        c.execute("create table if not exists public.schema_migrations (name text primary key, applied_at timestamptz default now())")
        done = {r["name"] for r in c.execute("select name from public.schema_migrations").fetchall()}
        for f in sorted((MIGRATIONS / db).glob("*.sql")):
            if f.name in done:
                continue
            with c.transaction():
                c.execute(f.read_text(encoding="utf-8"))
                c.execute("insert into public.schema_migrations(name) values (%s)", [f.name])
            applied.append(f.name)
    return applied
