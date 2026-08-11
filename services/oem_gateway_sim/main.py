"""oem-gateway-sim :8400 — SIMULATED OEM warranty portals (one endpoint set per programme). Not connected to any
real OEM system. Shape validation → 400 field errors; deterministic adjudication after a simulated delay;
reviewer comment written from reason codes only; weekly payment runs create credit notes."""
from __future__ import annotations

import threading
from datetime import datetime

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from wc_core.clock import http_clock
from wc_core.http import ServiceClient
from wc_core.ids import stable_hash
from wc_core.logging import setup_logging
from wc_core.settings import get_settings
from wc_core.vocab import CLAIM_PREFIX, REJECTION_CODES
from wc_db.db import connect, fetchall, fetchone, j, tx
from wc_oem.adapters.registry import ADAPTERS, adapter_for
from wc_oem.credentials import API_KEY_SETTING
from wc_rules.packs import load_pack

from .adjudicate import adjudicate, decided_at, payment_date

log = setup_logging("oem-gateway-sim")
app = FastAPI(title="oem-gateway-sim (SIMULATED)", version="0.1.0")
S = get_settings()
clock = http_clock(S.api_url)
llm = ServiceClient(S.llm_gateway_url, "oem-gateway-sim", timeout=60)


def auth(programme: str, key: str | None) -> None:
    if programme not in ADAPTERS:
        raise HTTPException(404, f"programme {programme} not available (rules pending)")
    if key != getattr(S, API_KEY_SETTING[programme]):
        raise HTTPException(401, "invalid API key")


def reviewer_comment(programme: str, claim_ref: str, version: int, decision: str, codes: list[str]) -> str:
    pack = load_pack(programme)
    wording = {c: pack.rejection_wording.get(c, REJECTION_CODES.get(c, (c,))[0]) for c in codes}
    fixable = all(REJECTION_CODES.get(c, ("", False))[1] for c in codes)
    task = {"programme": programme, "decision": decision, "codes": codes, "wording": wording, "fixable": fixable}
    import json
    body = {"agent": "oem_reviewer", "role": "fast", "run_key": f"{claim_ref}:reviewer:v{version}", "seq": 0,
            "claim_ref": None, "programme": programme,
            "messages": [{"type": "system", "data": {"content": "You are a terse OEM warranty reviewer. Write one short "
                                                                  "comment from the reason codes only."}},
                         {"type": "human", "data": {"content": json.dumps(task)}}], "tools": []}
    try:
        resp = llm.post("/llm/v1/chat", body, retries=2)
        return json.loads(resp["message"]["data"]["content"])["comment"]
    except Exception as e:  # reviewer wording is cosmetic; the decision never depends on it
        log.warning("reviewer_fallback", error=str(e))
        return " ".join(f"{c}: {w}" for c, w in wording.items()) or "Approved."


def _receipt(sub: dict) -> dict:
    return {"accepted_for_processing": True, "oem_claim_id": sub["oem_claim_id"], "version": sub["version"],
            "received_at_sim": sub["received_at_sim"].isoformat(), "status": "received", "simulated": True}


_store_lock = threading.Lock()


def _store(programme: str, payload: dict, idem: str | None, oem_claim_id: str | None) -> dict:
    # NB: no nested connections inside a transaction (pglite-socket serialises transactions across clients)
    claim_ref = adapter_for(programme).claim_ref_of(payload)
    with _store_lock:
        now = clock.now()
        if idem:
            prev = fetchone("sim", "select * from oem_sim.oem_submissions where idem_key=%s", [idem])
            if prev:
                return _receipt(prev)
        subs = fetchall("sim", "select * from oem_sim.oem_submissions where claim_ref=%s order by version", [claim_ref])
        if oem_claim_id is None and subs:
            raise HTTPException(409, f"claim {claim_ref} already submitted as {subs[0]['oem_claim_id']} — use resubmit")
        if oem_claim_id is not None:
            if not subs or subs[0]["oem_claim_id"] != oem_claim_id:
                raise HTTPException(404, "unknown OEM claim id")
            if len(subs) - 1 >= load_pack(programme).max_resubmissions:
                raise HTTPException(409, f"resubmission limit ({load_pack(programme).max_resubmissions}) reached")
        version = len(subs) + 1
        if oem_claim_id is None:
            n = fetchone("sim", "select count(distinct claim_ref) as n from oem_sim.oem_submissions where programme=%s",
                         [programme])["n"]
            oem_claim_id = f"{CLAIM_PREFIX[programme]}-OEM-{700000 + n + 1}"
        window_ref = subs[0]["received_at_sim"] if subs else now
        result = adjudicate(programme, payload, window_ref)
        decided = decided_at(programme, claim_ref, version, now)
        sid = f"SUB-{oem_claim_id}-{version}"
        comment = reviewer_comment(programme, claim_ref, version, result["decision"], result["codes"])
        _persist(programme, payload, idem, claim_ref, oem_claim_id, version, sid, now, result, decided, comment)
    log.info("submission", programme=programme, claim_ref=claim_ref, version=version, decision=result["decision"],
             codes=result["codes"])
    return _receipt(fetchone("sim", "select * from oem_sim.oem_submissions where id=%s", [sid]))


def _persist(programme, payload, idem, claim_ref, oem_claim_id, version, sid, now, result, decided, comment):
    with tx("sim") as c:
        c.execute("""insert into oem_sim.oem_submissions (id, company_id, programme, claim_ref, oem_claim_id, version,
                     payload, idem_key, received_at_sim) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  [sid, S.company_id, programme, claim_ref, oem_claim_id, version, j(payload), idem, now])
        c.execute("""insert into oem_sim.oem_adjudications (id, company_id, submission_id, decision, rejection_codes,
                     rule_trace, reviewer_comment, approved_amount, decided_at_sim) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  [f"ADJ-{sid}", S.company_id, sid, result["decision"], result["codes"], j(result["trace"]), comment,
                   result["amount"], decided])
        if result["decision"] in ("accepted", "partial"):
            ref = f"CN-{CLAIM_PREFIX[programme]}-{stable_hash(claim_ref, version) % 900000 + 100000}"
            c.execute("""insert into oem_sim.oem_credit_notes (oem_credit_ref, company_id, programme, claim_ref, oem_claim_id,
                         amount, sim_paid_at) values (%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                      [ref, S.company_id, programme, claim_ref, oem_claim_id, result["amount"],
                       payment_date(programme, decided)])


@app.get("/healthz")
def healthz():
    return {"ok": True, "simulated": True}


@app.post("/sim/{programme}/claims", status_code=202)
def submit(programme: str, payload: dict = Body(...), x_api_key: str | None = Header(default=None),
           idempotency_key: str | None = Header(default=None)):
    auth(programme, x_api_key)
    errors = adapter_for(programme).validate(payload)
    if errors:
        return JSONResponse(status_code=400, content={"errors": errors})
    return _store(programme, payload, idempotency_key, None)


@app.post("/sim/{programme}/claims/{oem_claim_id}/resubmit", status_code=202)
def resubmit(programme: str, oem_claim_id: str, payload: dict = Body(...), x_api_key: str | None = Header(default=None),
             idempotency_key: str | None = Header(default=None)):
    auth(programme, x_api_key)
    errors = adapter_for(programme).validate(payload)
    if errors:
        return JSONResponse(status_code=400, content={"errors": errors})
    return _store(programme, payload, idempotency_key, oem_claim_id)


def _status(sub: dict, adj: dict | None, now: datetime) -> dict:
    out = {"oem_claim_id": sub["oem_claim_id"], "claim_ref": sub["claim_ref"], "version": sub["version"],
           "received_at_sim": sub["received_at_sim"].isoformat(), "simulated": True, "status": "under_review"}
    if adj and adj["decided_at_sim"] <= now:
        out.update(status=adj["decision"], decision=adj["decision"], rejection_codes=adj["rejection_codes"],
                   reviewer_comment=adj["reviewer_comment"], approved_amount=float(adj["approved_amount"] or 0),
                   decided_at_sim=adj["decided_at_sim"].isoformat())
    elif adj:
        out["expected_by_sim"] = adj["decided_at_sim"].isoformat()
    return out


@app.get("/sim/{programme}/claims/{oem_claim_id}")
def status(programme: str, oem_claim_id: str, x_api_key: str | None = Header(default=None)):
    auth(programme, x_api_key)
    sub = fetchone("sim", "select * from oem_sim.oem_submissions where oem_claim_id=%s order by version desc limit 1",
                   [oem_claim_id])
    if not sub:
        raise HTTPException(404, "unknown OEM claim id")
    adj = fetchone("sim", "select * from oem_sim.oem_adjudications where submission_id=%s", [sub["id"]])
    out = _status(sub, adj, clock.now())
    out["resubmissions"] = sub["version"] - 1
    out["max_resubmissions"] = load_pack(programme).max_resubmissions
    return out


@app.get("/sim/{programme}/credit-notes")
def credit_notes(programme: str, since: str | None = None, x_api_key: str | None = Header(default=None)):
    auth(programme, x_api_key)
    rows = fetchall("sim", """select * from oem_sim.oem_credit_notes where programme=%s and sim_paid_at <= %s
                              and (%s::timestamptz is null or sim_paid_at > %s) order by sim_paid_at""",
                    [programme, clock.now(), since, since])
    return [{"oem_credit_ref": r["oem_credit_ref"], "claim_ref": r["claim_ref"], "oem_claim_id": r["oem_claim_id"],
             "amount": float(r["amount"]), "sim_paid_at": r["sim_paid_at"].isoformat()} for r in rows]


@app.get("/sim/{programme}/campaigns")
def campaigns(programme: str, vin: str, x_api_key: str | None = Header(default=None)):
    auth(programme, x_api_key)
    rows = fetchall("sim", "select * from oem_sim.oem_campaigns where programme=%s and status='open'", [programme])
    return [{"campaign_code": r["campaign_code"], "title": r["title"], "labour_op": r["labour_op"], "status": r["status"],
             "affected_vins": [vin] if vin in r["affected_vins"] else []} for r in rows if vin in r["affected_vins"]]


class PreAuthReq(BaseModel):
    claim_ref: str
    vin: str
    kind: str = "pre_auth"
    reason: str = ""


@app.post("/sim/{programme}/preauth")
def preauth(programme: str, body: PreAuthReq, x_api_key: str | None = Header(default=None)):
    auth(programme, x_api_key)
    existing = fetchone("sim", "select * from oem_sim.oem_preauths where claim_ref=%s and kind=%s", [body.claim_ref, body.kind])
    if existing:
        return {"ticket": existing["ticket"], "status": existing["status"], "ref": existing["ref"]}
    n = fetchone("sim", "select count(*) as n from oem_sim.oem_preauths where programme=%s", [programme])["n"]
    ticket = f"{'TA' if body.kind == 'ta_case' else 'PA'}-{CLAIM_PREFIX[programme]}-{8000 + n + 1}"
    with connect("sim") as c:
        c.execute("""insert into oem_sim.oem_preauths (ticket, company_id, programme, vin, claim_ref, kind, status, request)
                     values (%s,%s,%s,%s,%s,%s,'pending',%s)""",
                  [ticket, S.company_id, programme, body.vin, body.claim_ref, body.kind, j(body.model_dump())])
    return {"ticket": ticket, "status": "pending", "ref": None}


@app.get("/sim/{programme}/preauth/{ticket}")
def preauth_status(programme: str, ticket: str, x_api_key: str | None = Header(default=None)):
    auth(programme, x_api_key)
    r = fetchone("sim", "select * from oem_sim.oem_preauths where ticket=%s", [ticket])
    if not r:
        raise HTTPException(404, "unknown ticket")
    return {"ticket": r["ticket"], "status": r["status"], "ref": r["ref"], "kind": r["kind"]}


class Decision(BaseModel):
    approved: bool
    actor: str = "simulated-oem"


@app.post("/sim/{programme}/preauth/{ticket}/decision")
def preauth_decide(programme: str, ticket: str, body: Decision, x_api_key: str | None = Header(default=None)):
    auth(programme, x_api_key)
    r = fetchone("sim", "select * from oem_sim.oem_preauths where ticket=%s", [ticket])
    if not r:
        raise HTTPException(404, "unknown ticket")
    prefix = CLAIM_PREFIX[programme]
    ref = (f"{prefix}-TA-{stable_hash(ticket) % 90000 + 10000}" if r["kind"] == "ta_case"
           else f"{prefix}-PA-{stable_hash(ticket) % 90000 + 10000}") if body.approved else None
    with connect("sim") as c:
        c.execute("update oem_sim.oem_preauths set status=%s, ref=%s, decided_by=%s, updated_at=now() where ticket=%s",
                  ["approved" if body.approved else "declined", ref, body.actor, ticket])
    return {"ticket": ticket, "status": "approved" if body.approved else "declined", "ref": ref, "kind": r["kind"]}


@app.get("/sim/inbox")
def inbox(programme: str | None = None):
    """Gateway inbox view for the UI (read-only): payloads received, adjudications (revealed once decided), credit notes."""
    now = clock.now()
    subs = fetchall("sim", """select * from oem_sim.oem_submissions where (%s::text is null or programme=%s)
                              order by received_at_sim desc""", [programme, programme])
    adjs = {a["submission_id"]: a for a in fetchall("sim", "select * from oem_sim.oem_adjudications")}
    cns = fetchall("sim", """select * from oem_sim.oem_credit_notes where (%s::text is null or programme=%s) and sim_paid_at <= %s
                             order by sim_paid_at desc""", [programme, programme, now])
    pas = fetchall("sim", "select * from oem_sim.oem_preauths where (%s::text is null or programme=%s) and coalesce(decided_by, '') <> 'seed'",
                   [programme, programme])
    return {"simulated": True, "now": now.isoformat(),
            "submissions": [{**_status(s, adjs.get(s["id"]), now), "programme": s["programme"], "payload": s["payload"],
                             "rule_trace": (adjs[s["id"]]["rule_trace"] if s["id"] in adjs and adjs[s["id"]]["decided_at_sim"] <= now else None)}
                            for s in subs],
            "credit_notes": [{"oem_credit_ref": c["oem_credit_ref"], "claim_ref": c["claim_ref"], "programme": c["programme"],
                              "amount": float(c["amount"]), "sim_paid_at": c["sim_paid_at"].isoformat()} for c in cns],
            "preauths": [{"ticket": p["ticket"], "programme": p["programme"], "claim_ref": p["claim_ref"], "kind": p["kind"],
                          "status": p["status"], "ref": p["ref"]} for p in pas]}
