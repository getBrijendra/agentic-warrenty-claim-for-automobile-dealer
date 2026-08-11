"""SimClock backed by pg-core ops.sim_clock (for services that own the core DB)."""
from __future__ import annotations

from datetime import datetime, timezone

from wc_core.clock import ClockState, SimClock
from wc_core.settings import get_settings

from .db import connect, fetchone


def read_state() -> ClockState:
    r = fetchone("core", "select * from ops.sim_clock where id=1")
    if not r:
        s = get_settings()
        return ClockState(datetime.fromisoformat(s.sim_start), datetime.now(timezone.utc), s.warranty_sim_speed, True)
    return ClockState(r["base_sim"], r["base_real"], float(r["speed"]), bool(r["paused"]))


def db_clock() -> SimClock:
    return SimClock(read_state, cache_s=0.5)


def set_state(*, paused: bool | None = None, speed: float | None = None, sim_at: datetime | None = None) -> ClockState:
    st = read_state()
    now_sim = sim_at or st.now()
    new = ClockState(now_sim, datetime.now(timezone.utc), speed if speed is not None else st.speed,
                     st.paused if paused is None else paused)
    with connect("core") as c:
        c.execute("""insert into ops.sim_clock (id, company_id, base_sim, base_real, speed, paused) values (1,%s,%s,%s,%s,%s)
                     on conflict (id) do update set base_sim=excluded.base_sim, base_real=excluded.base_real,
                     speed=excluded.speed, paused=excluded.paused, updated_at=now()""",
                  [get_settings().company_id, new.base_sim, new.base_real, new.speed, new.paused])
    return new
