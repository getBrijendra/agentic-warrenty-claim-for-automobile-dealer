"""dms-connector :8100 — DmsPort over HTTP (anti-corruption layer for the dealer DMS)."""
from __future__ import annotations

from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from wc_core.infra import require_service
from wc_core.logging import setup_logging
from wc_core.settings import get_settings

from .adapters import make_adapter

log = setup_logging("dms-connector")
app = FastAPI(title="dms-connector", version="0.1.0")
dms = make_adapter(get_settings().dms_adapter)
svc = require_service()


def _nf(fn, *a):
    try:
        return fn(*a)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/healthz")
def healthz():
    return {"ok": True, "adapter": get_settings().dms_adapter}


@app.get("/dms/v1/capabilities")
def capabilities(_=Depends(svc)):
    return dms.capabilities()


@app.get("/dms/v1/wips/closed")
def closed_wips(since: datetime | None = None, _=Depends(svc)):
    return dms.list_closed_wips(since)


@app.get("/dms/v1/repair-orders/{wip_no}")
def repair_order(wip_no: str, _=Depends(svc)):
    return _nf(dms.get_repair_order, wip_no)


@app.get("/dms/v1/repair-orders/{wip_no}/job-card")
def job_card(wip_no: str, _=Depends(svc)):
    return _nf(dms.get_job_card, wip_no)


@app.get("/dms/v1/repair-orders/{wip_no}/parts")
def parts(wip_no: str, _=Depends(svc)):
    return _nf(dms.get_parts, wip_no)


@app.get("/dms/v1/repair-orders/{wip_no}/attachments")
def attachments(wip_no: str, _=Depends(svc)):
    return dms.list_attachments(wip_no)


class Upload(BaseModel):
    kind: str
    title: str
    caption: str = ""
    captured_at: str
    uploaded_by: str = "workshop"


@app.post("/dms/v1/repair-orders/{wip_no}/attachments")
def upload(wip_no: str, body: Upload, _=Depends(require_service({"workshop-sim", "api"}))):
    return dms.upload_attachment(wip_no, body.kind, body.title, body.caption, body.captured_at, body.uploaded_by)


class Fact(BaseModel):
    field: str
    value: str


@app.post("/dms/v1/repair-orders/{wip_no}/facts")
def record_fact(wip_no: str, body: Fact, _=Depends(require_service({"workshop-sim", "api"}))):
    try:
        return dms.record_fact(wip_no, body.field, body.value)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/dms/v1/vehicles/{vin}")
def vehicle(vin: str, _=Depends(svc)):
    return _nf(dms.get_vehicle, vin)


@app.get("/dms/v1/vehicles/{vin}/service-history")
def service_history(vin: str, _=Depends(svc)):
    return dms.get_service_history(vin)


@app.get("/dms/v1/vehicles/{vin}/contracts")
def contracts(vin: str, _=Depends(svc)):
    return dms.get_coverage_contracts(vin)


@app.get("/dms/v1/labour-ops")
def labour_ops(programme: str, q: str | None = None, codes: list[str] | None = Query(default=None), _=Depends(svc)):
    return dms.get_labour_ops(programme, q, codes)


@app.get("/dms/v1/condition-codes")
def condition_codes(programme: str, system: str | None = None, _=Depends(svc)):
    return dms.get_condition_codes(programme, system)


@app.get("/dms/v1/staff/{sid}")
def staff(sid: str, _=Depends(svc)):
    s = dms.get_staff(sid)
    if not s:
        raise HTTPException(404, "staff not found")
    return s


class ClaimStatus(BaseModel):
    wip_no: str
    status: str
    amount: float | None = None


@app.post("/dms/v1/warranty/claims/{claim_ref}/status")
def write_status(claim_ref: str, body: ClaimStatus, _=Depends(svc)):
    dms.write_claim_status(body.wip_no, claim_ref, body.status, body.amount)
    return {"ok": True}


class CreditNote(BaseModel):
    claim_ref: str
    amount: float
    ref: str
    paid_at: str


@app.post("/dms/v1/warranty/credit-notes")
def credit_note(body: CreditNote, _=Depends(svc)):
    dms.post_credit_note(body.claim_ref, body.amount, body.ref, body.paid_at)
    return {"ok": True}


@app.post("/dms/v1/admin/reset-writeback")
def reset_writeback(_=Depends(require_service({"api"}))):
    dms.reset_writeback()
    return {"ok": True}


@app.get("/dms/v1/blobs/{key:path}")
def blob(key: str):
    # public read for generated placeholder evidence (demo only)
    if ".." in key:
        raise HTTPException(400, "bad key")
    try:
        data = dms.blobs.get(key)
    except FileNotFoundError:
        raise HTTPException(404, "not found")
    return Response(data, media_type="image/svg+xml" if key.endswith(".svg") else "application/octet-stream")
