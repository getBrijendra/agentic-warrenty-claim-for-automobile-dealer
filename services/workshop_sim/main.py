"""workshop-sim :8500 — technician persona for ask_workshop (SIMULATED). The only service allowed to read the
hidden ground truth. Answers come from the WIP record + that WIP's hidden facts; never invented."""
from __future__ import annotations

import json

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from wc_core.clock import http_clock
from wc_core.http import ServiceClient
from wc_core.ids import stable_hash
from wc_core.infra import require_service
from wc_core.logging import setup_logging
from wc_core.settings import get_settings
from wc_groundtruth.repo import issues_for, save_score, score

log = setup_logging("workshop-sim")
app = FastAPI(title="workshop-sim (SIMULATED)", version="0.1.0")
S = get_settings()
dms = ServiceClient(S.dms_url, "workshop-sim")
llm = ServiceClient(S.llm_gateway_url, "workshop-sim", timeout=60)
clock = http_clock(S.api_url)

PERSONA = ("You are {name}, {role} at the dealer workshop. Answer the warranty desk's question in one or two short "
           "sentences, in workshop voice. Use ONLY the WIP record and the facts provided. If a fact is not provided, "
           "say it is not recorded. Never invent measurements, photos, IDs or parts.")


class Ask(BaseModel):
    wip_no: str
    question: str
    about: str | None = None
    claim_ref: str | None = None


@app.get("/healthz")
def healthz():
    return {"ok": True, "simulated": True}


@app.post("/workshop/v1/ask")
def ask(body: Ask, _=Depends(require_service({"orchestrator-worker"}))):
    ro = dms.get(f"/dms/v1/repair-orders/{body.wip_no}")
    tech = (ro.get("technicians") or [{}])[0] or {}
    grounding = {"question": body.question, "about": body.about, "technician": tech,
                 "wip": {k: ro.get(k) for k in ("wip_no", "technician_story", "customer_complaint", "dtc_codes",
                                                 "diagnostic_session_id", "odometer_out", "closed_at")},
                 "hidden_facts": issues_for(ro["id"])}
    run_key = f"{body.claim_ref or body.wip_no}:persona:{stable_hash(body.question, body.about) % 10**8}"
    resp = llm.post("/llm/v1/chat", {
        "agent": "technician_persona", "role": "fast", "run_key": run_key, "seq": 0, "claim_ref": body.claim_ref,
        "messages": [{"type": "system", "data": {"content": PERSONA.format(name=tech.get("name", "Technician"),
                                                                             role=tech.get("role", "technician"))}},
                     {"type": "human", "data": {"content": json.dumps(grounding, default=str)}}],
        "tools": []})
    out = json.loads(resp["message"]["data"]["content"])
    attachment = None
    if out.get("document"):
        d = out["document"]
        attachment = dms.post(f"/dms/v1/repair-orders/{body.wip_no}/attachments",
                              {"kind": d["kind"], "title": d["title"], "caption": d.get("caption", ""),
                               "captured_at": clock.now().isoformat(), "uploaded_by": "workshop-sim"})
    for field in ("diagnostic_session_id", "tech_support_ref"):
        if (out.get("provides") or {}).get(field):
            dms.post(f"/dms/v1/repair-orders/{body.wip_no}/facts", {"field": field, "value": out["provides"][field]})
    log.info("ask", wip=body.wip_no, about=body.about, found=out.get("found"))
    return {"technician": {"id": tech.get("id"), "name": tech.get("name"), "role": tech.get("role")},
            "answer": out["answer"], "provides": out.get("provides") or {}, "attachment": attachment,
            "found": bool(out.get("found"))}


class ScoreReq(BaseModel):
    claims: list[dict]


@app.post("/workshop/v1/scoring")
def scoring(body: ScoreReq, _=Depends(require_service({"api"}))):
    result = score(body.claims)
    save_score(S.company_id, result)
    return result
