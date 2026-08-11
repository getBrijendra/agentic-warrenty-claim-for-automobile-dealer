"""Deterministic claim state machine (NOT an LLM). Every transition writes warranty.claim_events and an outbox
event in the same transaction; optimistic concurrency on claims.version_no prevents two workers moving a claim."""
from __future__ import annotations

import uuid
from typing import Any

from wc_core.http import ServiceClient
from wc_core.infra import to_jsonable
from wc_core.logging import setup_logging
from wc_core.settings import get_settings
from wc_db.db import fetchone, j, tx
from wc_events.bus import PgJobQueue, emit

log = setup_logging("orchestrator")

ALLOWED: dict[str, set[str]] = {
    "wip_closed": {"triage"},
    "triage": {"withdrawn", "escalated", "building"},
    "building": {"checking", "escalated"},
    "checking": {"blocked", "ready", "escalated", "checking"},
    "blocked": {"checking", "escalated"},
    "ready": {"submitted", "escalated"},
    "submitted": {"accepted", "rejected", "info_requested", "escalated"},
    "info_requested": {"checking"},
    "rejected": {"resolving"},
    "resolving": {"resubmitted", "building", "escalated", "withdrawn", "written_off"},
    "resubmitted": {"accepted", "rejected"},
    "accepted": {"paid"},
    "escalated": {"ready", "checking", "written_off", "withdrawn", "building", "resubmitted", "submitted"},
    "paid": set(), "written_off": set(), "withdrawn": set(),
}

queue = PgJobQueue()
_dms: ServiceClient | None = None


class TransitionError(RuntimeError):
    pass


def dms() -> ServiceClient:
    global _dms
    if _dms is None:
        _dms = ServiceClient(get_settings().dms_url, "orchestrator-worker")
    return _dms


def transition(claim: dict, to: str, reason: str, *, sim_at, actor: str = "orchestrator",
               updates: dict[str, Any] | None = None, job: tuple[str, dict, int] | None = None,
               escalation: dict | None = None) -> dict:
    frm = claim["status"]
    if to not in ALLOWED.get(frm, set()):
        raise TransitionError(f"{claim['claim_ref']}: {frm} → {to} not allowed")
    updates = dict(updates or {})
    cols = ", ".join(f"{k}=%s" for k in updates)
    with tx("core") as c:
        cur = c.execute(f"""update warranty.claims set status=%s, version_no=version_no+1, updated_at=now()
                            {(', ' + cols) if cols else ''} where id=%s and version_no=%s returning *""",
                        [to, *[j(v) if isinstance(v, dict) else v for v in updates.values()], claim["id"], claim["version_no"]])
        row = cur.fetchone()
        if row is None:
            raise TransitionError(f"{claim['claim_ref']}: concurrent modification (version_no {claim['version_no']})")
        c.execute("""insert into warranty.claim_events (company_id, claim_id, from_status, to_status, reason, actor, sim_at)
                     values (%s,%s,%s,%s,%s,%s,%s)""", [row["company_id"], claim["id"], frm, to, reason[:500], actor, sim_at])
        emit(c, "claim.status", {"claim_ref": row["claim_ref"], "wip_no": row["wip_no"], "programme": row["programme"],
                                 "from": frm, "to": to, "reason": reason[:300], "amount": row["amount_claimed"],
                                 "claim_type": row["claim_type"], "priority": row["priority"], "sim_at": sim_at,
                                 "model": row["model"]})
        if escalation:
            eid = str(uuid.uuid4())
            c.execute("""insert into warranty.escalations (id, company_id, claim_id, kind, reason_code, agent_recommendation,
                         confidence, tried, draft_message, source_agent) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      [eid, row["company_id"], claim["id"], escalation.get("kind", "review"), escalation.get("reason_code"),
                       escalation.get("recommendation"), escalation.get("confidence"), j(escalation.get("tried") or []),
                       escalation.get("draft_message"), escalation.get("source_agent")])
            emit(c, "escalation.created", {"claim_ref": row["claim_ref"], "escalation_id": eid,
                                           "reason_code": escalation.get("reason_code"), "kind": escalation.get("kind")})
        if job:
            kind, payload, prio = job
            queue.enqueue(kind, claim["id"], payload, f"{kind}:{claim['id']}:{row['version_no']}", priority=prio, conn=c)
    try:  # write-back to the DMS warranty module (what a real DMS integration would show)
        dms().post(f"/dms/v1/warranty/claims/{row['claim_ref']}/status",
                   {"wip_no": row["wip_no"], "status": to, "amount": float(row["amount_claimed"]) if row["amount_claimed"] else None},
                   retries=2)
    except Exception as e:
        log.warning("dms_writeback_failed", claim_ref=row["claim_ref"], error=str(e))
    log.info("transition", claim_ref=row["claim_ref"], frm=frm, to=to, reason=reason[:120])
    return dict(row)


def reload(claim_id: str) -> dict:
    return fetchone("core", "select * from warranty.claims where id=%s", [claim_id])
