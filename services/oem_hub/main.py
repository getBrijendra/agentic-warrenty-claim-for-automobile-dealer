"""oem-hub :8200 — OemConnector registry (entry points + tenant config) behind a middleware chain:
auth → rate limiter → circuit breaker → retry/backoff (in client) → idempotency → integration log."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from importlib.metadata import entry_points

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from wc_core.http import ServiceClient
from wc_core.infra import require_service
from wc_core.logging import setup_logging
from wc_core.settings import get_settings
from wc_core.vocab import ALL_PROGRAMMES
from wc_db.db import connect, fetchone, j
from wc_rules.packs import load_pack

log = setup_logging("oem-hub")
app = FastAPI(title="oem-hub", version="0.1.0")
S = get_settings()
svc = require_service({"orchestrator-worker", "scheduler", "api"})


def _dealer_codes() -> dict:
    row = fetchone("core", "select settings from ops.tenants where company_id=%s", [S.company_id])
    return ((row or {}).get("settings") or {}).get("dealer_codes", {})


class Registry:
    def __init__(self):
        self._eps = {ep.name: ep for ep in entry_points(group="wc.oem_connectors")}
        self._cache: dict[str, object] = {}

    def get(self, programme: str):
        if programme not in self._cache:
            mode = getattr(S, f"oem_connector_{programme.lower()}", None) or S.oem_connector_default
            ep = self._eps.get(f"{programme}.{mode}")
            if ep is None:
                raise HTTPException(404, f"no {mode} connector for {programme} (rules pending)")
            self._cache[programme] = ep.load()(_dealer_codes().get(programme, "UNSET"))
        return self._cache[programme]


registry = Registry()


class Breaker:
    def __init__(self, threshold: int = 5, cool_s: float = 10):
        self.fails, self.opened, self.threshold, self.cool = 0, 0.0, threshold, cool_s
        self.lock = threading.Lock()

    def check(self):
        with self.lock:
            if self.fails >= self.threshold and time.time() - self.opened < self.cool:
                raise HTTPException(503, "circuit open for this OEM connector")

    def ok(self):
        with self.lock:
            self.fails = 0

    def fail(self):
        with self.lock:
            self.fails += 1
            if self.fails >= self.threshold:
                self.opened = time.time()


class Bucket:
    def __init__(self, per_min: int):
        self.cap, self.tokens, self.t, self.lock = per_min, float(per_min), time.time(), threading.Lock()

    def take(self):
        with self.lock:
            now = time.time()
            self.tokens = min(self.cap, self.tokens + (now - self.t) * self.cap / 60)
            self.t = now
            if self.tokens < 1:
                raise HTTPException(429, "OEM rate limit")
            self.tokens -= 1


breakers = {p: Breaker() for p in ALL_PROGRAMMES}
buckets = {p: Bucket(600) for p in ALL_PROGRAMMES}


def guarded(programme: str, method: str, path: str, fn, *args):
    buckets[programme].take()
    breakers[programme].check()
    t0 = time.perf_counter()
    status = 200
    try:
        out = fn(*args)
        breakers[programme].ok()
        if isinstance(out, dict) and out.get("accepted_for_processing") is False:
            status = int(out.get("status", 400))
        return out
    except HTTPException:
        raise
    except Exception as e:
        status = 502
        breakers[programme].fail()
        log.error("connector_error", programme=programme, path=path, error=str(e))
        raise HTTPException(502, f"OEM connector error: {e}")
    finally:
        with connect("core") as c:
            c.execute("""insert into ops.integration_log (company_id, service, target, method, path, request_hash, status, latency_ms)
                         values (%s,'oem-hub',%s,%s,%s,%s,%s,%s)""",
                      [S.company_id, f"{programme}:{'sim' if getattr(registry.get(programme), 'simulated', True) else 'real'}",
                       method, path, hashlib.sha256(json.dumps(args, default=str).encode()).hexdigest()[:16], status,
                       int((time.perf_counter() - t0) * 1000)])


def idempotent(key: str, fn):
    row = fetchone("core", "select response from ops.idempotency_keys where key=%s", [key])
    if row:
        return row["response"]
    out = fn()
    if isinstance(out, dict) and out.get("oem_claim_id"):
        with connect("core") as c:
            c.execute("insert into ops.idempotency_keys (key, company_id, response) values (%s,%s,%s) on conflict do nothing",
                      [key, S.company_id, j(out)])
    return out


@app.get("/healthz")
def healthz():
    return {"ok": True, "connectors": sorted(registry._eps)}


@app.get("/oem/v1/programmes")
def programmes(_=Depends(svc)):
    out = []
    for p in ALL_PROGRAMMES:
        pack = load_pack(p)
        if pack.status != "active":
            out.append({"programme": p, "status": "pending", "simulated": True, "display_name": pack.display_name})
            continue
        con = registry.get(p)
        out.append({**con.capabilities(), "status": "active", "display_name": pack.display_name,
                    "credential_spec": con.credential_spec()})
    return out


@app.get("/oem/v1/{programme}/schema")
def schema(programme: str, _=Depends(svc)):
    con = registry.get(programme)
    return {"json_schema": con.payload_schema(), "layout": con.capabilities()["layout"]}


@app.get("/oem/v1/{programme}/campaigns")
def campaigns(programme: str, vin: str, _=Depends(svc)):
    return guarded(programme, "GET", "campaigns", registry.get(programme).find_campaigns, vin)


class PayloadReq(BaseModel):
    claim: dict
    normalise: bool = False


def normalise(payload: dict) -> dict:
    p = json.loads(json.dumps(payload))
    p["vin"] = str(p.get("vin", "")).upper().strip()
    if isinstance(p.get("narrative"), dict):
        for k, mx in (("complaint", 1200), ("cause", 1200), ("correction", 1500)):
            p["narrative"][k] = (p["narrative"].get(k) or "").strip()[:mx] or "-"
    return p


@app.post("/oem/v1/{programme}/payload")
def build_payload(programme: str, body: PayloadReq, _=Depends(svc)):
    con = registry.get(programme)
    payload = con.build_payload(body.claim)
    if body.normalise:
        payload = normalise(payload)
    return {"payload": payload, "errors": con.validate_payload(payload)}


class SubmitReq(BaseModel):
    payload: dict
    idem_key: str


@app.post("/oem/v1/{programme}/claims")
def submit(programme: str, body: SubmitReq, _=Depends(svc)):
    con = registry.get(programme)
    out = idempotent(body.idem_key, lambda: guarded(programme, "POST", "claims", con.submit_claim, body.payload, body.idem_key))
    return out


@app.get("/oem/v1/{programme}/claims/{oem_claim_id}")
def claim_status(programme: str, oem_claim_id: str, _=Depends(svc)):
    return guarded(programme, "GET", "claims/status", registry.get(programme).get_claim_status, oem_claim_id)


@app.post("/oem/v1/{programme}/claims/{oem_claim_id}/resubmit")
def resubmit(programme: str, oem_claim_id: str, body: SubmitReq, _=Depends(svc)):
    con = registry.get(programme)
    return idempotent(body.idem_key, lambda: guarded(programme, "POST", "claims/resubmit", con.resubmit_claim,
                                                     oem_claim_id, body.payload, body.idem_key))


class PreAuth(BaseModel):
    claim_ref: str
    vin: str
    kind: str = "pre_auth"
    reason: str = ""


@app.post("/oem/v1/{programme}/preauth")
def preauth(programme: str, body: PreAuth, _=Depends(svc)):
    return guarded(programme, "POST", "preauth", registry.get(programme).request_pre_authorisation, body.model_dump())


@app.get("/oem/v1/{programme}/preauth/{ticket}")
def preauth_status(programme: str, ticket: str, _=Depends(svc)):
    return guarded(programme, "GET", "preauth/status", registry.get(programme).get_pre_authorisation, ticket)


class Decision(BaseModel):
    approved: bool
    actor: str


@app.post("/oem/v1/{programme}/preauth/{ticket}/decision")
def preauth_decide(programme: str, ticket: str, body: Decision,
                   _=Depends(require_service({"api", "orchestrator-worker"}))):
    return guarded(programme, "POST", "preauth/decision", registry.get(programme).decide_pre_authorisation, ticket,
                   body.approved, body.actor)


@app.get("/oem/v1/{programme}/credit-notes")
def credit_notes(programme: str, since: str | None = None, _=Depends(svc)):
    return guarded(programme, "GET", "credit-notes", registry.get(programme).list_credit_notes, since)


sim_view = ServiceClient(S.oem_sim_url, "oem-hub")


@app.get("/oem/v1/inbox")
def inbox(programme: str | None = None, _=Depends(svc)):
    return sim_view.get("/sim/inbox", params={"programme": programme} if programme else None)
