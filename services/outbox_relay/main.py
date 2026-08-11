"""outbox-relay — publishes committed outbox rows to the event log (the EventBus the api fans out over SSE)."""
from __future__ import annotations

import time

from wc_core.logging import setup_logging
from wc_core.settings import get_settings
from wc_db.db import j, tx

log = setup_logging("outbox-relay")
S = get_settings()


def relay_once(batch: int = 300) -> int:
    with tx("core") as c:
        rows = c.execute("select * from ops.outbox where published_at is null order by id limit %s for update skip locked",
                         [batch]).fetchall()
        for r in rows:
            c.execute("""insert into ops.event_log (company_id, outbox_id, topic, payload) values (%s,%s,%s,%s)
                         on conflict (outbox_id) do nothing""", [r["company_id"], r["id"], r["topic"], j(r["payload"])])
        if rows:
            c.execute("update ops.outbox set published_at=now() where id = any(%s)", [[r["id"] for r in rows]])
    return len(rows)


def main() -> None:
    log.info("relay_started")
    while True:
        try:
            n = relay_once()
            time.sleep(0.05 if n else 0.2)
        except Exception as e:
            log.error("relay_error", error=str(e))
            time.sleep(1)


if __name__ == "__main__":
    main()
