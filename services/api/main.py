"""api :8000 — BFF for the web UI: REST + SSE, run control, review actions, KPIs, admin. The browser only talks
to this service. No business logic in Next.js."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from wc_core.http import ServiceClient
from wc_core.infra import to_jsonable
from wc_core.logging import setup_logging
from wc_core.settings import get_settings
from wc_core.vocab import ACTIVE_PROGRAMMES
from wc_db.claims import ClaimRepository
from wc_db.clock import db_clock, read_state, set_state
from wc_db.db import connect, fetchall, fetchone, j, migrate, tx
from wc_db.seeding import seed_external
from wc_events.bus import PgJobQueue, emit, last_event_id, publish, read_events
from wc_rules.packs import all_packs

from . import kpi, views

log = setup_logging("api")
S = get_settings()
app = FastAPI(title="Warranty Claims Agent Platform — api", version="0.1.0", openapi_url="/api/v1/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=[S.web_origin, "http://127.0.0.1:3000"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
repo = ClaimRepository()
queue = PgJobQueue()
clock = db_clock()
dms = ServiceClient(S.dms_url, "api")
oem = ServiceClient(S.oem_hub_url, "api")
llm = ServiceClient(S.llm_gateway_url, "api")
workshop = ServiceClient(S.workshop_url, "api")
P = "/api/v1"


@app.on_event("startup")
def startup() -> None:
    for db in ("core", "dms", "sim"):
        migrate(db)


def mode() -> str:
    row = fetchone("core", "select value from ops.sync_state where company_id=%s and key='mode'", [S.company_id])
    return (row or {}).get("value") or S.warranty_mode


def audit(actor: str, action: str, subject: str, detail: dict) -> None:
    with connect("core") as c:
        c.execute("insert into ops.audit_log (company_id, actor, action, subject, detail) values (%s,%s,%s,%s,%s)",
                  [S.company_id, actor, action, subject, j(detail)])


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    out = {}
    for name, cl in (("dms", dms), ("oem_hub", oem), ("llm", llm), ("workshop", workshop)):
        try:
            cl.request("GET", "/healthz", retries=0)
            out[name] = "ok"
        except Exception as e:
            out[name] = f"down: {e}"
    return {"ready": all(v == "ok" for v in out.values()), **out}


@app.get(P + "/meta")
def meta():
    tenant = fetchone("core", "select * from ops.tenants where company_id=%s", [S.company_id]) or {}
    return {"brand": {"displayName": tenant.get("display_name", S.brand_display_name), "currency": S.currency,
                      "locale": "en-AE", "timeZone": "Asia/Dubai", "vatRate": S.vat_rate},
            "mode": mode(), "llm_provider": S.llm_provider, "fake_llm": S.llm_provider == "fake",
            "dms_adapter": S.dms_adapter, "oem_connector": S.oem_connector_default, "simulated_oem": True,
            "models": {"main": S.warranty_agent_model, "fast": S.warranty_fast_model},
            "programmes": [{"programme": p.programme, "display_name": p.display_name, "colour": p.colour,
                            "status": p.status, "prefix": p.claim_prefix} for p in all_packs()],
            "max_parallel": S.warranty_max_parallel}


# ------------------------------------------------------------------------------- sim clock / mode
@app.get(P + "/simclock")
def simclock():
    return read_state().to_json()


class ClockReq(BaseModel):
    action: str  # pause | resume | speed
    multiplier: float | None = None


@app.post(P + "/simclock")
def simclock_set(body: ClockReq):
    if body.action == "pause":
        st = set_state(paused=True)
    elif body.action == "resume":
        st = set_state(paused=False)
    elif body.action == "speed":
        st = set_state(speed=S.warranty_sim_speed * (body.multiplier or 1))
    else:
        raise HTTPException(400, "unknown action")
    clock.invalidate()
    publish("simclock.tick", st.to_json())
    return st.to_json()


class ModeReq(BaseModel):
    mode: str


@app.post(P + "/mode")
def set_mode(body: ModeReq):
    if body.mode not in ("live", "replay"):
        raise HTTPException(400, "mode must be live or replay")
    if body.mode == "replay" and not fetchone("core", "select 1 as x from agent.agent_replays limit 1"):
        raise HTTPException(409, "no recordings yet — complete a live run first")
    with connect("core") as c:
        c.execute("""insert into ops.sync_state (company_id, key, value) values (%s,'mode',%s)
                     on conflict (company_id, key) do update set value=excluded.value, updated_at=now()""", [S.company_id, body.mode])
    publish("mode.changed", {"mode": body.mode})
    return {"mode": body.mode}


# ------------------------------------------------------------------------------- runs
class RunReq(BaseModel):
    scope: str = "batch"  # one | batch
    claim_ref: str | None = None
    programme: str | None = None


@app.post(P + "/runs")
def start_run(body: RunReq):
    q = "select * from warranty.claims where status='wip_closed'"
    args: list = []
    if body.scope == "one":
        if not body.claim_ref:
            raise HTTPException(400, "claim_ref required")
        q += " and claim_ref=%s"
        args.append(body.claim_ref)
    elif body.programme:
        q += " and programme=%s"
        args.append(body.programme)
    claims = fetchall("core", q, args)
    if not claims:
        raise HTTPException(409, "no closed WIPs waiting (reset to start again)")
    rid = str(uuid.uuid4())
    m = mode()  # NB: never open a second connection inside a transaction (pglite-socket serialises transactions)
    with tx("core") as c:
        c.execute("insert into ops.runs (id, company_id, scope, filter, claim_refs, mode) values (%s,%s,%s,%s,%s,%s)",
                  [rid, S.company_id, body.scope, j(body.model_dump()), [x["claim_ref"] for x in claims], m])
        for cl in claims:
            queue.enqueue("claim.triage", cl["id"], {"run_id": rid}, f"claim.triage:{cl['id']}:{rid}", priority=50, conn=c)
        emit(c, "run.started", {"run_id": rid, "claims": len(claims), "mode": m})
    set_state(paused=False)
    clock.invalidate()
    audit("presenter", "run.start", rid, body.model_dump())
    return {"run_id": rid, "claims": len(claims), "mode": mode()}


@app.get(P + "/runs/{run_id}")
def get_run(run_id: str):
    r = fetchone("core", "select * from ops.runs where id=%s", [run_id])
    if not r:
        raise HTTPException(404, "run not found")
    claims = fetchall("core", "select claim_ref, status from warranty.claims where claim_ref = any(%s)", [r["claim_refs"]])
    cost = fetchone("core", "select coalesce(sum(cost_usd),0) as c, coalesce(sum(input_tokens+output_tokens),0) as t from llm.llm_calls")
    return to_jsonable({**r, "claims": claims, "cost_usd": cost["c"], "tokens": cost["t"]})


@app.post(P + "/claims/{claim_ref}/run")
def run_one(claim_ref: str):
    return start_run(RunReq(scope="one", claim_ref=claim_ref))


# ------------------------------------------------------------------------------- claims
@app.get(P + "/claims")
def claims(programme: str | None = None):
    b = views.board()
    if programme:
        b["cards"] = [c for c in b["cards"] if c["programme"] == programme]
    return b


@app.get(P + "/claims/{claim_ref}")
def claim(claim_ref: str):
    w = views.workspace(claim_ref, dms, oem)
    if not w:
        raise HTTPException(404, "claim not found")
    return w


@app.get(P + "/claims/{claim_ref}/versions/{a}/diff/{b}")
def claim_diff(claim_ref: str, a: int, b: int):
    d = views.diff(claim_ref, a, b)
    if not d:
        raise HTTPException(404, "version not found")
    return to_jsonable(d)


# ------------------------------------------------------------------------------- review queue
@app.get(P + "/review")
def review():
    return views.review_queue()


class ReviewAction(BaseModel):
    action: str  # approve | edit | override | write_off
    actor: str = "warranty_admin (demo)"
    draft_message: str | None = None
    note: str | None = None


@app.post(P + "/review/{escalation_id}/actions")
def review_action(escalation_id: str, body: ReviewAction):
    if body.action not in ("approve", "edit", "override", "write_off"):
        raise HTTPException(400, "unknown action")
    esc = fetchone("core", "select * from warranty.escalations where id=%s", [escalation_id])
    if not esc or esc["status"] != "open":
        raise HTTPException(409, "escalation not open")
    claim = repo.by_id(str(esc["claim_id"]))
    status = {"approve": "approved", "edit": "approved", "override": "overridden", "write_off": "closed"}[body.action]
    with tx("core") as c:
        c.execute("""insert into warranty.review_actions (id, company_id, escalation_id, action, actor, payload)
                     values (%s,%s,%s,%s,%s,%s)""", [str(uuid.uuid4()), S.company_id, escalation_id, body.action, body.actor,
                                                     j(body.model_dump())])
        c.execute("""update warranty.escalations set status=%s, resolved_by=%s, resolution=%s,
                     draft_message=coalesce(%s, draft_message), updated_at=now() where id=%s""",
                  [status, body.actor, j(body.model_dump()), body.draft_message, escalation_id])
        queue.enqueue("claim.escalation_resumed", str(esc["claim_id"]),
                      {"escalation_id": escalation_id, "action": body.action, "actor": body.actor},
                      f"resume:{escalation_id}", priority=5, conn=c)
        emit(c, "escalation.resolved", {"claim_ref": claim["claim_ref"], "escalation_id": escalation_id,
                                        "action": body.action, "actor": body.actor})
    set_state(paused=False)
    clock.invalidate()
    audit(body.actor, f"review.{body.action}", claim["claim_ref"], body.model_dump())
    return {"ok": True, "status": status}


# ------------------------------------------------------------------------------- rules, gateway, kpis
@app.get(P + "/oem/rules-matrix")
def rules_matrix():
    return to_jsonable(views.rules_matrix())


@app.get(P + "/oem/programmes")
def programmes():
    return oem.get("/oem/v1/programmes")


@app.get(P + "/gateway/inbox")
def gateway_inbox(programme: str | None = None):
    return oem.get("/oem/v1/inbox", params={"programme": programme} if programme else None)


@app.get(P + "/kpis")
def kpis():
    return to_jsonable({"agent": kpi.agent_kpis(clock.now()), "baseline": kpi.baseline_kpis(), "sim_now": clock.now()})


# ------------------------------------------------------------------------------- admin
@app.post(P + "/admin/reset")
def reset(slug: str = "default"):
    set_state(paused=True, sim_at=datetime.fromisoformat(S.sim_start), speed=S.warranty_sim_speed)
    clock.invalidate()
    with tx("core") as c:
        for t in ("warranty.claims", "ops.jobs", "ops.outbox", "ops.runs", "ops.idempotency_keys", "ops.integration_log",
                  "llm.llm_calls", "agent.agent_runs"):
            c.execute(f"delete from {t}")
        c.execute("delete from ops.sync_state where key like 'cn:%%'")
    seed_external(slug)  # restore fake Autoline WIPs + simulated OEM side exactly as seeded (keeps agent_replays)
    publish("reset", {"at": datetime.now(timezone.utc).isoformat()})
    audit("presenter", "admin.reset", slug, {})
    return {"ok": True, "sim_now": clock.now().isoformat()}


@app.get(P + "/admin/cost")
def cost():
    rows = fetchall("core", """select agent, role, model, provider, count(*) as calls, sum(input_tokens) as input_tokens,
                               sum(output_tokens) as output_tokens, sum(cost_usd) as cost_usd from llm.llm_calls
                               group by agent, role, model, provider order by agent""")
    return to_jsonable({"rows": rows, "total_usd": sum(float(r["cost_usd"] or 0) for r in rows),
                        "caps": {"per_claim": S.llm_max_tokens_per_claim, "per_batch": S.llm_max_tokens_per_batch},
                        "note": "Estimated as if real models were used; the fake provider makes no paid calls."})


@app.get(P + "/admin/accuracy")
def accuracy():
    out = []
    for c in repo.all():
        agent_codes = set(c["flags"] or [])
        for r in fetchall("core", """select distinct r.code from warranty.rule_results r join warranty.claim_versions v
                                     on v.id=r.claim_version_id where v.claim_id=%s and r.result in ('fail','warn')
                                     and r.code is not null""", [c["id"]]):
            agent_codes.add(r["code"])
        for code in ((c["triage"] or {}).get("reason_codes") or []):
            agent_codes.add(code)
        if c["priority"] <= 10:
            agent_codes.add("R02")
        gw = set()
        for a in fetchall("core", "select rejection_codes from warranty.adjudication_snapshots where claim_id=%s", [c["id"]]):
            gw.update(a["rejection_codes"])
        out.append({"wip_no": c["wip_no"], "claim_ref": c["claim_ref"], "status": c["status"],
                    "agent_codes": sorted(agent_codes), "gateway_codes": sorted(gw),
                    "final_ok": c["status"] in ("paid", "accepted")})
    return workshop.post("/workshop/v1/scoring", {"claims": out})


# ------------------------------------------------------------------------------- SSE
@app.get(P + "/stream")
async def stream(request: Request):
    last = request.headers.get("last-event-id") or request.query_params.get("after")
    last_id = int(last) if last and last.isdigit() else await asyncio.to_thread(last_event_id)

    async def gen():
        nonlocal last_id
        last_kpi = 0.0
        dirty = True
        while True:
            if await request.is_disconnected():
                break
            rows = await asyncio.to_thread(read_events, last_id)
            for r in rows:
                last_id = r["id"]
                if r["topic"] in ("claim.status", "credit_note.posted", "reset"):
                    dirty = True
                yield {"id": str(r["id"]), "event": r["topic"], "data": json.dumps(to_jsonable(r["payload"]))}
            loop = asyncio.get_event_loop().time()
            if dirty and loop - last_kpi > 1.0:
                data = await asyncio.to_thread(kpis)
                yield {"event": "kpi.update", "data": json.dumps(data)}
                last_kpi, dirty = loop, False
            await asyncio.sleep(0.3)

    return EventSourceResponse(gen(), ping=15)
