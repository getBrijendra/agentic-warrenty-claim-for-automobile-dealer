"""orchestrator-worker — leases claim-step jobs and runs them with bounded concurrency (WARRANTY_MAX_PARALLEL).
Pauses when the sim clock is paused, so the control-room pause button stops the whole desk."""
from __future__ import annotations

import signal
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from wc_core.settings import get_settings
from wc_db.clock import db_clock
from wc_events.bus import PgJobQueue

from .steps import handle
from .transitions import log

S = get_settings()
queue = PgJobQueue()
clock = db_clock()
stop = threading.Event()


def run_job(job: dict) -> None:
    t0 = time.perf_counter()
    try:
        handle(job)
        queue.ack(job["id"])
        log.info("job_done", kind=job["kind"], job=job["id"], ms=int((time.perf_counter() - t0) * 1000))
    except Exception as e:
        log.error("job_failed", kind=job["kind"], job=job["id"], error=str(e), tb=traceback.format_exc()[-1500:])
        queue.nack(job["id"], f"{type(e).__name__}: {e}", retry_in_s=min(30, 2 ** job["attempts"]))


def main() -> None:
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    pool = ThreadPoolExecutor(S.warranty_max_parallel, thread_name_prefix="claim")
    inflight: set = set()
    lock = threading.Lock()
    log.info("worker_started", max_parallel=S.warranty_max_parallel)
    while not stop.is_set():
        try:
            if clock.state().paused:
                time.sleep(0.4)
                continue
            with lock:
                free = S.warranty_max_parallel - len(inflight)
            jobs = queue.lease(free, ttl_s=300) if free > 0 else []
            for job in jobs:
                fut = pool.submit(run_job, job)
                with lock:
                    inflight.add(fut)
                fut.add_done_callback(lambda f: (lock.acquire(), inflight.discard(f), lock.release()))
            time.sleep(0.05 if jobs else 0.25)
        except Exception as e:
            log.error("worker_loop_error", error=str(e))
            time.sleep(1)
    pool.shutdown(wait=True)


if __name__ == "__main__":
    main()
