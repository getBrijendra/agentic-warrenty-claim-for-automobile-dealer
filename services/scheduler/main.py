"""scheduler — single leader. Sim clock ticks, closed-WIP ingestion, OEM status polling, payment-run checks,
submission-window watcher, run completion."""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from wc_core.http import ServiceClient
from wc_core.logging import setup_logging
from wc_core.settings import get_settings
from wc_core.vocab import CLAIM_PREFIX, TERMINAL_STATUSES
from wc_db.claims import ClaimRepository
from wc_db.clock import db_clock
from wc_db.db import connect, fetchall, fetchone, j, tx
from wc_events.bus import PgJobQueue, emit, publish
from wc_rules.helpers import window_remaining_days
from wc_rules.packs import load_pack

from orchestrator.transitions import transition

log = setup_logging("scheduler")
S = get_settings()
clock = db_clock()
dms = ServiceClient(S.dms_url, "scheduler")
oem = ServiceClient(S.oem_hub_url, "scheduler")
repo = ClaimRepository()
queue = PgJobQueue()
ME = f"scheduler-{os.getpid()}"


def leader() -> bool:
    """Lease-based leader election (works on PGlite and Postgres)."""
    with tx("core") as c:
        row = c.execute("select value, updated_at from ops.sync_state where company_id=%s and key='scheduler_leader' for update",
                        [S.company_id]).fetchone()
        now = datetime.now(timezone.utc)
        if row and row["value"] != ME and row["updated_at"] > now - timedelta(seconds=10):
            return False
        c.execute("""insert into ops.sync_state (company_id, key, value) values (%s,'scheduler_leader',%s)
                     on conflict (company_id, key) do update set value=excluded.value, updated_at=now()""", [S.company_id, ME])
        return True


def ingest() -> int:
    existing = {r["wip_no"] for r in fetchall("core", "select wip_no from warranty.claims where company_id=%s", [S.company_id])}
    new = 0
    for w in dms.get("/dms/v1/wips/closed"):
        if w["wip_no"] in existing:
            continue
        n = fetchone("core", "select count(*) as n from warranty.claims where programme=%s", [w["programme"]])["n"]
        ref = f"WC-{CLAIM_PREFIX[w['programme']]}-2026-{120 + n + 1:06d}"
        closed = datetime.fromisoformat(w["closed_at"])
        window = load_pack(w["programme"]).window_days
        with tx("core") as c:
            c.execute("""insert into warranty.claims (id, company_id, claim_ref, wip_no, repair_order_id, vin, model, programme,
                         status, sim_closed_at, window_due_at) values (%s,%s,%s,%s,%s,%s,%s,%s,'wip_closed',%s,%s)
                         on conflict (company_id, wip_no) do nothing""",
                      [str(uuid.uuid4()), S.company_id, ref, w["wip_no"], w["repair_order_id"], w["vin"],
                       f"{w['make']} {w['model']}", w["programme"], closed, closed + timedelta(days=window)])
            emit(c, "claim.status", {"claim_ref": ref, "wip_no": w["wip_no"], "programme": w["programme"], "from": None,
                                     "to": "wip_closed", "reason": "closed WIP ingested from Autoline"})
        new += 1
    return new


def track() -> None:
    for cl in fetchall("core", "select * from warranty.claims where status in ('submitted','resubmitted') and oem_claim_id is not null"):
        st = oem.get(f"/oem/v1/{cl['programme']}/claims/{cl['oem_claim_id']}")
        if st.get("status") not in ("accepted", "rejected", "partial"):
            continue
        if fetchone("core", "select 1 as x from warranty.adjudication_snapshots where claim_id=%s and version=%s",
                    [cl["id"], st["version"]]):
            continue
        with connect("core") as c:
            c.execute("""insert into warranty.adjudication_snapshots (id, company_id, claim_id, version, decision, rejection_codes,
                         reviewer_comment, approved_amount, decided_at_sim, raw) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                         on conflict do nothing""",
                      [str(uuid.uuid4()), S.company_id, cl["id"], st["version"], st["decision"], st.get("rejection_codes") or [],
                       st.get("reviewer_comment"), st.get("approved_amount"), st.get("decided_at_sim"), j(st)])
            emit(c, "oem.adjudicated", {"claim_ref": cl["claim_ref"], "decision": st["decision"],
                                        "codes": st.get("rejection_codes") or [], "comment": st.get("reviewer_comment"),
                                        "sim_at": st.get("decided_at_sim")})
        first = cl["first_pass_result"] or ("rejected" if st["decision"] == "rejected" else "accepted")
        sim_at = datetime.fromisoformat(st["decided_at_sim"])
        if st["decision"] in ("accepted", "partial"):
            transition(cl, "accepted", f"OEM {st['decision']}: {st.get('reviewer_comment') or ''}", sim_at=sim_at,
                       actor="oem-gateway", updates={"first_pass_result": first, "sim_accepted_at": sim_at})
        else:
            codes = st.get("rejection_codes") or []
            transition(cl, "rejected", f"OEM rejected {', '.join(codes)}: {st.get('reviewer_comment') or ''}", sim_at=sim_at,
                       actor="oem-gateway", updates={"first_pass_result": first},
                       job=("claim.resolve", {"codes": codes}, cl["priority"]))


def payments() -> None:
    accepted = {c["claim_ref"]: c for c in fetchall("core", "select * from warranty.claims where status='accepted'")}
    if not accepted:
        return
    for prog in sorted({c["programme"] for c in accepted.values()}):
        for cn in oem.get(f"/oem/v1/{prog}/credit-notes"):
            cl = accepted.get(cn["claim_ref"])
            if not cl:
                continue
            paid_at = datetime.fromisoformat(cn["sim_paid_at"])
            with connect("core") as c:
                c.execute("""insert into warranty.credit_notes (id, company_id, claim_id, oem_credit_ref, amount, sim_paid_at)
                             values (%s,%s,%s,%s,%s,%s) on conflict (oem_credit_ref) do nothing""",
                          [str(uuid.uuid4()), S.company_id, cl["id"], cn["oem_credit_ref"], cn["amount"], paid_at])
                emit(c, "credit_note.posted", {"claim_ref": cl["claim_ref"], "amount": cn["amount"],
                                               "ref": cn["oem_credit_ref"], "sim_at": cn["sim_paid_at"]})
            transition(cl, "paid", f"credit note {cn['oem_credit_ref']} (payment run)", sim_at=paid_at, actor="oem-gateway",
                       updates={"amount_paid": cn["amount"], "sim_paid_at": paid_at})
            dms.post("/dms/v1/warranty/credit-notes", {"claim_ref": cl["claim_ref"], "amount": cn["amount"],
                                                       "ref": cn["oem_credit_ref"], "paid_at": cn["sim_paid_at"]})


def window_watch() -> None:
    now = clock.now()
    for cl in fetchall("core", """select * from warranty.claims where status in ('wip_closed','triage','building','checking','ready')
                                  and priority > 10"""):
        if window_remaining_days(cl["sim_closed_at"], now, load_pack(cl["programme"]).window_days) <= 3:
            with connect("core") as c:
                c.execute("update warranty.claims set priority=10 where id=%s", [cl["id"]])
                c.execute("update ops.jobs set priority=10 where claim_id=%s and status='queued'", [cl["id"]])


def runs() -> None:
    for r in fetchall("core", "select * from ops.runs where status='running'"):
        rows = fetchall("core", "select status from warranty.claims where claim_ref = any(%s)", [r["claim_refs"]])
        if rows and all(x["status"] in TERMINAL_STATUSES | {"escalated"} for x in rows):
            with connect("core") as c:
                c.execute("update ops.runs set status='finished', ended_at=now() where id=%s", [r["id"]])
                emit(c, "run.finished", {"run_id": str(r["id"]), "claims": len(rows)})


def main() -> None:
    log.info("scheduler_started", me=ME)
    last_tick = 0.0
    while True:
        try:
            if not leader():
                time.sleep(2)
                continue
            ingest()
            if not clock.state().paused:
                track()
                payments()
                window_watch()
                runs()
            if time.time() - last_tick > 3:
                publish("simclock.tick", clock.state().to_json())
                last_tick = time.time()
        except Exception as e:
            log.error("scheduler_error", error=str(e))
        time.sleep(0.5)


if __name__ == "__main__":
    main()
