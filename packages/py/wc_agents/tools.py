"""Agent tools — LangChain StructuredTools with Pydantic arg schemas, grouped by the service they call.
Tool outputs are DATA: they are wrapped as <tool_result untrusted="true"> before reaching the model.
Agent tools never import wc_groundtruth (ground truth is only reachable through workshop-sim)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from wc_core.canonical import CanonicalClaim, compute_totals
from wc_core.http import ServiceClient
from wc_core.ids import idem_key
from wc_core.infra import to_jsonable
from wc_core.settings import get_settings
from wc_db.claims import ClaimRepository
from wc_db.db import connect, fetchone, j
from wc_events.bus import emit
from wc_rules.engine import evaluate
from wc_rules.model import RuleContext, RuleSubject
from wc_rules.packs import load_pack

from .guardrails import GuardrailViolation, apply_patch, validate_against_dms


@dataclass
class ToolContext:
    claim: dict
    clock: Any
    emit_step: Callable[[str, dict], None]
    repo: ClaimRepository = field(default_factory=ClaimRepository)
    dms: ServiceClient = field(default_factory=lambda: ServiceClient(get_settings().dms_url, "orchestrator-worker"))
    oem: ServiceClient = field(default_factory=lambda: ServiceClient(get_settings().oem_hub_url, "orchestrator-worker"))
    workshop: ServiceClient = field(default_factory=lambda: ServiceClient(get_settings().workshop_url, "orchestrator-worker", timeout=90))
    flags: dict = field(default_factory=dict)

    @property
    def programme(self) -> str:
        return self.claim["programme"]

    def ro(self, wip_no: str | None = None) -> dict:
        ro = self.dms.get(f"/dms/v1/repair-orders/{wip_no or self.claim['wip_no']}")
        rates = (fetchone("core", "select settings from ops.tenants where company_id=%s", [get_settings().company_id]) or {})
        ro["labour_rate"] = float(((rates.get("settings") or {}).get("labour_rates") or {}).get(ro["programme"], 0)) or \
            _company_rate(self, ro["programme"])
        return ro


def _company_rate(ctx: ToolContext, programme: str) -> float:
    return {"FORD": 400, "FORD_TRUCKS": 360, "JLR": 460, "FERRARI": 620, "MASERATI": 540, "VINFAST": 380}.get(programme, 400)


# ------------------------------------------------------------------ arg schemas
class WipArgs(BaseModel):
    wip_no: str


class VinArgs(BaseModel):
    vin: str


class CampaignArgs(BaseModel):
    programme: str
    vin: str


class LabourOpArgs(BaseModel):
    programme: str
    codes: list[str] = Field(default_factory=list)
    query: str | None = None


class CondArgs(BaseModel):
    programme: str
    system: str | None = None


class AskArgs(BaseModel):
    wip_no: str
    question: str
    about: str | None = None


class DraftArgs(BaseModel):
    claim: dict


class ClaimRefArgs(BaseModel):
    claim_ref: str


class UpdateArgs(BaseModel):
    claim_ref: str
    patch: dict
    change_summary: str


class PreAuthArgs(BaseModel):
    claim_ref: str
    kind: str = "pre_auth"
    reason: str = ""


class BuildPayloadArgs(BaseModel):
    claim_ref: str
    normalise: bool = False


class EscalateArgs(BaseModel):
    claim_ref: str
    kind: str
    reason_code: str
    recommendation: str
    draft_message: str
    confidence: float = 0.7
    tried: list[str] = Field(default_factory=list)


class NoteArgs(BaseModel):
    text: str


# ------------------------------------------------------------------ implementations
def t_get_repair_order(ctx: ToolContext, wip_no: str) -> dict:
    return ctx.ro(wip_no)


def t_get_vehicle(ctx: ToolContext, vin: str) -> dict:
    return ctx.dms.get(f"/dms/v1/vehicles/{vin}")


def t_get_contracts(ctx: ToolContext, vin: str) -> dict:
    return {"items": ctx.dms.get(f"/dms/v1/vehicles/{vin}/contracts")}


def t_get_history(ctx: ToolContext, vin: str) -> dict:
    return {"items": ctx.dms.get(f"/dms/v1/vehicles/{vin}/service-history")}


def t_find_campaigns(ctx: ToolContext, programme: str, vin: str) -> dict:
    return {"items": ctx.oem.get(f"/oem/v1/{programme}/campaigns", params={"vin": vin})}


def t_prior_claims(ctx: ToolContext, vin: str) -> dict:
    return {"items": [p for p in ctx.repo.prior_claims(vin) if p["claim_ref"] != ctx.claim["claim_ref"]]}


def t_attachments(ctx: ToolContext, wip_no: str) -> dict:
    return {"items": ctx.dms.get(f"/dms/v1/repair-orders/{wip_no}/attachments")}


def t_parts(ctx: ToolContext, wip_no: str) -> dict:
    return {"items": ctx.dms.get(f"/dms/v1/repair-orders/{wip_no}/parts")}


def t_labour_ops(ctx: ToolContext, programme: str, codes: list[str] | None = None, query: str | None = None) -> dict:
    params: dict = {"programme": programme}
    if codes:
        params["codes"] = codes
    if query:
        params["q"] = query
    return {"items": ctx.dms.get("/dms/v1/labour-ops", params=params)}


def t_condition_codes(ctx: ToolContext, programme: str, system: str | None = None) -> dict:
    return {"items": ctx.dms.get("/dms/v1/condition-codes", params={"programme": programme, "system": system})}


def t_ask_workshop(ctx: ToolContext, wip_no: str, question: str, about: str | None = None) -> dict:
    out = ctx.workshop.post("/workshop/v1/ask", {"wip_no": wip_no, "question": question, "about": about,
                                                 "claim_ref": ctx.claim["claim_ref"]})
    ctx.emit_step("workshop_chat", {"question": question, "answer": out["answer"], "technician": out["technician"],
                                    "attachment": out.get("attachment")})
    return out


def _store_version(ctx: ToolContext, claim: CanonicalClaim, built_by: str, summary: str) -> dict:
    ro = ctx.ro()
    atts = ctx.dms.get(f"/dms/v1/repair-orders/{ctx.claim['wip_no']}/attachments")
    errs = validate_against_dms(claim, ro, atts, ctx.repo.granted_refs(ctx.claim["id"]))
    if errs:
        raise GuardrailViolation("; ".join(errs))
    ver = ctx.repo.add_version(ctx.claim["id"], claim, built_by, summary)
    return {"version": ver, "claim_ref": claim.claim_ref, "total": float(claim.totals.total),
            "total_display": f"AED {float(claim.totals.total):,.2f} incl. VAT"}


def t_draft(ctx: ToolContext, claim: dict) -> dict:
    s = get_settings()
    claim = {**claim, "company_id": s.company_id, "claim_ref": ctx.claim["claim_ref"]}
    from wc_core.canonical import ClaimLine
    lines = [ClaimLine.model_validate(ln) for ln in claim["lines"]]
    claim["totals"] = compute_totals(lines, s.vat_rate, s.currency).model_dump(mode="json")
    try:
        c = CanonicalClaim.model_validate(claim)
        return _store_version(ctx, c, "agent", "v1 drafted by Claim Builder")
    except (GuardrailViolation, ValueError) as e:
        return {"error": str(e)[:600]}


def t_update(ctx: ToolContext, claim_ref: str, patch: dict, change_summary: str) -> dict:
    s = get_settings()
    cur = ctx.repo.canonical(ctx.claim["id"])
    atts = ctx.dms.get(f"/dms/v1/repair-orders/{ctx.claim['wip_no']}/attachments")
    try:
        new = apply_patch(cur, patch, atts)
        new = new.model_copy(update={"totals": compute_totals(new.lines, s.vat_rate, s.currency)})
        return _store_version(ctx, new, "agent", change_summary)
    except (GuardrailViolation, ValueError) as e:
        ctx.flags["guardrail_violations"] = ctx.flags.get("guardrail_violations", 0) + 1
        return {"error": str(e)[:600]}


def rule_context(ctx: ToolContext, claim: CanonicalClaim) -> RuleContext:
    vin = claim.vin
    ro = ctx.ro()
    first_sub = fetchone("core", """select min(sim_submitted_at) as t from warranty.submissions where claim_id=%s""",
                         [ctx.claim["id"]])
    now = (first_sub or {}).get("t") or ctx.clock.now()
    cps = ctx.dms.get(f"/dms/v1/vehicles/{vin}")
    campaigns = [dict(c) for c in ctx.oem.get(f"/oem/v1/{claim.programme}/campaigns", params={"vin": vin})]
    for cp in campaigns:
        cp["affected_vins"] = [vin]
    return RuleContext(now=now, vehicle=cps, contracts=ctx.dms.get(f"/dms/v1/vehicles/{vin}/contracts"),
                       service_history=ctx.dms.get(f"/dms/v1/vehicles/{vin}/service-history"), campaigns=campaigns,
                       prior_claims=[p for p in ctx.repo.prior_claims(vin) if p["claim_ref"] != claim.claim_ref],
                       wip_part_nos=[ln["part_no"] for ln in ro["lines"] if ln["line_type"] == "part"])


def t_run_rules(ctx: ToolContext, claim_ref: str) -> dict:
    v = ctx.repo.latest_version(ctx.claim["id"])
    claim = CanonicalClaim.model_validate(v["canonical"])
    results = [r.model_dump() for r in evaluate(RuleSubject.from_canonical(claim), rule_context(ctx, claim), "dealer")]
    ctx.repo.save_rule_results(ctx.claim["id"], v["version"], results)
    fails = [r for r in results if r["result"] == "fail"]
    ctx.flags["last_rules"] = {"version": v["version"], "has_fail": bool(fails)}
    return {"version": v["version"], "results": results, "has_fail": bool(fails),
            "has_warn": any(r["result"] == "warn" for r in results), "fails": [r["code"] for r in fails],
            "causal_part_no": claim.causal_part.part_no,
            "claim_summary": {"cause": claim.cause, "causal_part": claim.causal_part.model_dump(mode="json"),
                              "lines": [{"labour_op": ln.labour_op, "hours_claimed": float(ln.hours_claimed),
                                         "standard_hours": float(ln.standard_hours)} for ln in claim.lines],
                              "parts": [p.model_dump(mode="json") for ln in claim.lines for p in ln.parts]}}


def t_preauth(ctx: ToolContext, claim_ref: str, kind: str = "pre_auth", reason: str = "") -> dict:
    import uuid
    out = ctx.oem.post(f"/oem/v1/{ctx.programme}/preauth", {"claim_ref": claim_ref, "vin": ctx.claim["vin"],
                                                            "kind": kind, "reason": reason})
    with connect("core") as c:
        c.execute("""insert into warranty.preauth_requests (id, company_id, claim_id, kind, ticket, status, ref, request)
                     values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                  [str(uuid.uuid4()), get_settings().company_id, ctx.claim["id"], kind, out["ticket"], out["status"],
                   out.get("ref"), j({"reason": reason})])
    return out


def _payload(ctx: ToolContext, normalise: bool) -> dict:
    v = ctx.repo.latest_version(ctx.claim["id"])
    out = ctx.oem.post(f"/oem/v1/{ctx.programme}/payload", {"claim": v["canonical"], "normalise": normalise})
    ctx.repo.set_payload(ctx.claim["id"], v["version"], out["payload"])
    return {"version": v["version"], **out}


def t_build_payload(ctx: ToolContext, claim_ref: str, normalise: bool = False) -> dict:
    out = _payload(ctx, normalise)
    return {"version": out["version"], "errors": out["errors"], "payload_preview": {k: out["payload"][k] for k in list(out["payload"])[:6]}}


def _guard_submit(ctx: ToolContext) -> int:
    """Precondition (also enforced by the orchestrator): latest rule run on the latest version has no fail."""
    v = ctx.repo.latest_version(ctx.claim["id"])
    rr = ctx.repo.latest_rule_results(ctx.claim["id"])
    if not rr or any(r["result"] == "fail" for r in rr):
        raise GuardrailViolation("cannot submit: latest run_rules on the current version has a fail (or was not run)")
    return v["version"]


def _record_submission(ctx: ToolContext, version: int, kind: str, key: str, out: dict) -> None:
    import uuid
    with connect("core") as c:
        c.execute("""insert into warranty.submissions (id, company_id, claim_id, version, kind, oem_claim_id, idem_key, status,
                     response, sim_submitted_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (idem_key) do nothing""",
                  [str(uuid.uuid4()), get_settings().company_id, ctx.claim["id"], version, kind, out.get("oem_claim_id"), key,
                   "received" if out.get("oem_claim_id") else "format_error", j(out), ctx.clock.now()])


def t_submit(ctx: ToolContext, claim_ref: str) -> dict:
    try:
        version = _guard_submit(ctx)
    except GuardrailViolation as e:
        return {"errors": [str(e)]}
    v = ctx.repo.latest_version(ctx.claim["id"])
    key = idem_key(claim_ref, version, "submit")
    out = ctx.oem.post(f"/oem/v1/{ctx.programme}/claims", {"payload": v["oem_payload"], "idem_key": key})
    _record_submission(ctx, version, "submit", key, out)
    if out.get("oem_claim_id"):
        ctx.flags["submitted"] = {"oem_claim_id": out["oem_claim_id"], "version": version}
        return {"oem_claim_id": out["oem_claim_id"], "version": version, "status": out.get("status")}
    return {"errors": _errs(out)}


def t_get_adjudication(ctx: ToolContext, claim_ref: str) -> dict:
    st = ctx.oem.get(f"/oem/v1/{ctx.programme}/claims/{ctx.claim['oem_claim_id']}")
    return {k: st.get(k) for k in ("status", "decision", "rejection_codes", "reviewer_comment", "version",
                                   "resubmissions", "max_resubmissions")}


def t_resubmit(ctx: ToolContext, claim_ref: str) -> dict:
    try:
        version = _guard_submit(ctx)
    except GuardrailViolation as e:
        return {"errors": [str(e)]}
    out_p = _payload(ctx, False)
    if out_p["errors"]:
        return {"errors": out_p["errors"]}
    key = idem_key(claim_ref, version, "resubmit")
    out = ctx.oem.post(f"/oem/v1/{ctx.programme}/claims/{ctx.claim['oem_claim_id']}/resubmit",
                       {"payload": out_p["payload"], "idem_key": key})
    _record_submission(ctx, version, "resubmit", key, out)
    if out.get("oem_claim_id"):
        ctx.flags["resubmitted"] = {"version": version}
        return {"oem_claim_id": out["oem_claim_id"], "version": version, "status": out.get("status")}
    return {"errors": _errs(out)}


def t_escalate(ctx: ToolContext, claim_ref: str, kind: str, reason_code: str, recommendation: str, draft_message: str,
               confidence: float = 0.7, tried: list[str] | None = None) -> dict:
    ctx.flags["escalation"] = {"kind": kind, "reason_code": reason_code, "recommendation": recommendation,
                               "draft_message": draft_message, "confidence": confidence, "tried": tried or []}
    return {"queued_for_human": True, "kind": kind}


def t_note(ctx: ToolContext, text: str) -> dict:
    ctx.emit_step("status_note", {"text": text})
    return {"ok": True}


TOOLS: dict[str, tuple[Callable, type[BaseModel], str]] = {
    "get_repair_order": (t_get_repair_order, WipArgs, "Read a WIP (job card) from Autoline incl. lines and technicians"),
    "get_vehicle": (t_get_vehicle, VinArgs, "Read vehicle master data from Autoline"),
    "get_coverage_contracts": (t_get_contracts, VinArgs, "List coverage contracts for a VIN"),
    "get_service_history": (t_get_history, VinArgs, "List service history for a VIN"),
    "find_open_campaigns": (t_find_campaigns, CampaignArgs, "Open OEM campaigns affecting a VIN"),
    "find_prior_claims": (t_prior_claims, VinArgs, "Prior warranty claims for a VIN (agent + historical)"),
    "get_attachments": (t_attachments, WipArgs, "Attachments on a WIP"),
    "get_parts": (t_parts, WipArgs, "Part lines on a WIP"),
    "search_labour_ops": (t_labour_ops, LabourOpArgs, "Search the programme labour-op catalog"),
    "search_condition_codes": (t_condition_codes, CondArgs, "Search programme condition / symptom codes"),
    "ask_workshop": (t_ask_workshop, AskArgs, "Ask the assigned technician a question about a WIP"),
    "draft_canonical_claim": (t_draft, DraftArgs, "Validate and store the canonical claim (v1)"),
    "update_canonical_claim": (t_update, UpdateArgs, "Patch the canonical claim; validated against the DMS"),
    "run_rules": (t_run_rules, ClaimRefArgs, "Run the deterministic OEM rule pack on the latest claim version"),
    "request_pre_authorisation": (t_preauth, PreAuthArgs, "Request OEM pre-authorisation or a TA case"),
    "build_oem_payload": (t_build_payload, BuildPayloadArgs, "Transform canonical → OEM payload (deterministic adapter)"),
    "submit_to_oem": (t_submit, ClaimRefArgs, "Submit the claim to the (simulated) OEM"),
    "get_adjudication": (t_get_adjudication, ClaimRefArgs, "Read the OEM adjudication for the claim"),
    "resubmit_to_oem": (t_resubmit, ClaimRefArgs, "Resubmit the corrected claim to the OEM"),
    "escalate_to_human": (t_escalate, EscalateArgs, "Escalate to the human review queue with a drafted note"),
    "emit_status_note": (t_note, NoteArgs, "Emit a short user-facing status sentence to the live feed"),
}


def lc_tools(names: list[str]) -> list[StructuredTool]:
    """LangChain tool objects (schemas only; execution happens in the runner with a ToolContext)."""
    out = []
    for n in names:
        fn, schema, desc = TOOLS[n]
        out.append(StructuredTool.from_function(func=lambda **kw: None, name=n, description=desc, args_schema=schema))
    return out


def execute(ctx: ToolContext, name: str, args: dict) -> dict:
    fn, schema, _ = TOOLS[name]
    parsed = schema.model_validate(args).model_dump()
    return to_jsonable(fn(ctx, **parsed))


def wrap(name: str, result: Any) -> str:
    return f'<tool_result name="{name}" untrusted="true">{json.dumps(to_jsonable(result), ensure_ascii=False)}</tool_result>'
