"""Clock port implementations.

SimClock state = (base_sim, base_real, speed, paused). sim_now = base_sim + (real_now - base_real) * speed.
The authoritative state lives in pg-core `ops.sim_clock` (owned by api/scheduler). Services in other bounded
contexts (gateway sim, workshop sim) read the parameters over HTTP and compute time locally.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

DUBAI = timezone(timedelta(hours=4))


@dataclass
class ClockState:
    base_sim: datetime
    base_real: datetime
    speed: float
    paused: bool

    def now(self) -> datetime:
        if self.paused:
            return self.base_sim
        elapsed = (datetime.now(timezone.utc) - self.base_real).total_seconds()
        return self.base_sim + timedelta(seconds=elapsed * self.speed)

    def to_json(self) -> dict:
        return {"base_sim": self.base_sim.isoformat(), "base_real": self.base_real.isoformat(),
                "speed": self.speed, "paused": self.paused, "now": self.now().isoformat()}

    @staticmethod
    def from_json(d: dict) -> "ClockState":
        return ClockState(datetime.fromisoformat(d["base_sim"]), datetime.fromisoformat(d["base_real"]),
                          float(d["speed"]), bool(d["paused"]))


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(DUBAI)


class SimClock:
    """Reads clock state from a source with a short cache (1s), computes sim time locally."""

    def __init__(self, source: Callable[[], ClockState], cache_s: float = 1.0):
        self._source = source
        self._cache_s = cache_s
        self._state: ClockState | None = None
        self._at = 0.0
        self._lock = threading.Lock()

    def state(self) -> ClockState:
        with self._lock:
            if self._state is None or time.monotonic() - self._at > self._cache_s:
                self._state = self._source()
                self._at = time.monotonic()
            return self._state

    def now(self) -> datetime:
        return self.state().now().astimezone(DUBAI)

    def invalidate(self) -> None:
        with self._lock:
            self._state = None


def http_clock(api_url: str) -> SimClock:
    from .http import ServiceClient

    client = ServiceClient(api_url, "clock-reader")

    def src() -> ClockState:
        return ClockState.from_json(client.get("/api/v1/simclock"))

    return SimClock(src)
