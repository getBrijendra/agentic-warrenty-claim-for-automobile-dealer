"""llm-gateway :8300 — LLMProvider behind one door: role routing, token budgets, usage + cost, record/replay."""
from __future__ import annotations

import hashlib
import json
import time

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from wc_core.infra import require_service
from wc_core.logging import setup_logging
from wc_core.settings import get_settings
from wc_db.db import connect, fetchall, fetchone, j
from wc_llm.providers import PRICE, make_provider

log = setup_logging("llm-gateway")
app = FastAPI(title="llm-gateway", version="0.1.0")
S = get_settings()
provider = make_provider(S.llm_provider)


class ChatReq(BaseModel):
    agent: str
    role: str
    run_key: str
    seq: int
    claim_ref: str | None = None
    programme: str | None = None
    messages: list[dict]
    tools: list[dict] = []


def mode() -> str:
    row = fetchone("core", "select value from ops.sync_state where company_id=%s and key='mode'", [S.company_id])
    return (row or {}).get("value") or S.warranty_mode


def model_for(role: str) -> str:
    return S.warranty_agent_model if role == "main" else S.warranty_fast_model


@app.get("/healthz")
def healthz():
    return {"ok": True, "provider": provider.name}


@app.get("/llm/v1/info")
def info(_=Depends(require_service())):
    return {**provider.info(), "mode": mode()}


@app.post("/llm/v1/chat")
def chat(req: ChatReq, _=Depends(require_service({"orchestrator-worker", "workshop-sim", "oem-gateway-sim"}))):
    m = mode()
    t0 = time.perf_counter()
    if req.claim_ref:
        used = fetchone("core", "select coalesce(sum(input_tokens+output_tokens),0) as n from llm.llm_calls where claim_ref=%s",
                        [req.claim_ref])["n"]
        if used > S.llm_max_tokens_per_claim:
            raise HTTPException(429, f"BUDGET_EXCEEDED: claim {req.claim_ref} used {used} tokens")
    batch = fetchone("core", "select coalesce(sum(input_tokens+output_tokens),0) as n from llm.llm_calls")["n"]
    if batch > S.llm_max_tokens_per_batch:
        raise HTTPException(429, f"BUDGET_EXCEEDED: batch used {batch} tokens")
    rhash = hashlib.sha256(json.dumps(req.messages[-1:], sort_keys=True, default=str).encode()).hexdigest()[:16]
    if m == "replay":
        rec = fetchone("core", "select response, latency_ms from agent.agent_replays where run_key=%s and seq=%s",
                       [req.run_key, req.seq])
        if not rec:
            raise HTTPException(404, f"no recording for {req.run_key}#{req.seq} — record a live run first")
        time.sleep(min(rec["latency_ms"], 5000) / 1000)
        resp = rec["response"]
        prov = "replay"
    else:
        resp = provider.chat(req.model_dump())
        prov = provider.name
    latency = int((time.perf_counter() - t0) * 1000)
    usage = resp["usage"]
    pin, pout = PRICE[req.role if req.role in PRICE else "main"]
    cost = usage["input_tokens"] / 1e6 * pin + usage["output_tokens"] / 1e6 * pout
    with connect("core") as c:
        c.execute("""insert into llm.llm_calls (company_id, run_key, seq, claim_ref, agent, role, model, provider,
                     input_tokens, output_tokens, cost_usd, latency_ms, request_hash) values
                     (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  [S.company_id, req.run_key, req.seq, req.claim_ref, req.agent, req.role, model_for(req.role), prov,
                   usage["input_tokens"], usage["output_tokens"], cost, latency, rhash])
        if m != "replay":
            c.execute("""insert into agent.agent_replays (company_id, run_key, seq, agent, request_hash, response, latency_ms)
                         values (%s,%s,%s,%s,%s,%s,%s) on conflict (run_key, seq) do update set response=excluded.response,
                         latency_ms=excluded.latency_ms, request_hash=excluded.request_hash, recorded_at=now()""",
                      [S.company_id, req.run_key, req.seq, req.agent, rhash, j(resp), latency])
    return {**resp, "model": model_for(req.role), "provider": prov, "mode": m, "cost_usd": cost}


@app.get("/llm/v1/usage")
def usage(claim_ref: str | None = None, _=Depends(require_service())):
    rows = fetchall("core", """select agent, role, count(*) as calls, sum(input_tokens) as input_tokens,
                               sum(output_tokens) as output_tokens, sum(cost_usd) as cost_usd from llm.llm_calls
                               where (%s::text is null or claim_ref=%s) group by agent, role order by agent""",
                    [claim_ref, claim_ref])
    return [{k: (float(v) if k == "cost_usd" else v) for k, v in r.items()} for r in rows]
