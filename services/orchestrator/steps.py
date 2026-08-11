"""One job = one claim step. Agents propose; the orchestrator decides transitions and enforces guardrails:
no submit with a failing rule, mandatory escalations (GOODWILL, > AED threshold, R01/R02/R09/R11/R12),
resubmission limit → escalate."""
from __future__ import annotations

import uuid

from wc_agents.definitions import AGENTS
from wc_agents.runner import AgentFailed, run_agent
from wc_agents.tools import ToolContext, t_build_payload, t_resubmit, t_run_rules
from wc_core.canonical import CanonicalClaim, compute_totals
from wc_core.http import ServiceClient
from wc_core.settings import get_settings
from wc_core.vocab import MANDATORY_ESCALATION_CODES
from wc_db.claims import ClaimRepository
from wc_db.clock import db_clock
from wc_db.db import connect, fetchall, fetchone, j
from wc_rules.packs import load_pack

from .transitions import log, reload, transition

repo = ClaimRepository()
clock = db_clock()
S = get_settings()
HIGH, NORMAL = 10, 100


def prio(claim: dict) -> int:
    return HIGH if claim.get("priority", NORMAL) <= HIGH else NORMAL


def attempt(claim_id: str, agent: str) -> int:
    return fetchone("core", "select count(*) as n from agent.agent_runs where claim_id=%s and agent=%s",
                    [claim_id, agent])["n"] + 1


def agent(name: str, claim: dict, task: dict) -> tuple[dict, ToolContext]:
    tctx = ToolContext(claim=claim, clock=clock, emit_step=lambda k, p: None)
    out = run_agent(AGENTS[name], {"claim_ref": claim["claim_ref"], "wip_no": claim["wip_no"],
                                   "programme": claim["programme"], "company_id": S.company_id, **task},
                    tctx, attempt(claim["id"], name))
    return out, tctx


def mandatory_escalation(claim: dict, codes: list[str] | None = None) -> str | None:
    c = repo.canonical(claim["id"])
    if c and c.claim_type == "GOODWILL" and not claim.get("touched_by_human"):
        return "GOODWILL"
    if c and float(c.totals.total) > S.escalation_amount_aed and not claim.get("touched_by_human"):
        return "AMOUNT_OVER_THRESHOLD"
    hit = [x for x in (codes or []) if x in MANDATORY_ESCALATION_CODES]
    return hit[0] if hit else None


def escalate(claim: dict, reason: str, esc: dict, actor: str = "orchestrator", updates: dict | None = None) -> dict:
    flags = sorted(set((claim.get("flags") or []) + [esc.get("reason_code") or "REVIEW"]))
    return transition(claim, "escalated", reason, sim_at=clock.now(), actor=actor,
                      updates={"flags": flags, **(updates or {})}, escalation=esc)


# ---------------------------------------------------------------------------- handlers
def h_triage(claim: dict, payload: dict) -> None:
    if claim["status"] == "wip_closed":
        claim = transition(claim, "triage", "run started", sim_at=clock.now())
    out, _ = agent("triage", claim, {"sim_now": clock.now().isoformat(),
                                     "window_days": load_pack(claim["programme"]).window_days})
    upd = {"triage": out, "priority": HIGH if out["priority"] == "high" else NORMAL}
    if out["action"] == "withdraw":
        transition(claim, "withdrawn", "; ".join(out["reasons"]), sim_at=clock.now(), updates=upd)
    elif out["action"] == "escalate":
        code = (out.get("reason_codes") or ["R01"])[0]
        escalate(claim, "; ".join(out["reasons"]), {
            "kind": "goodwill" if code == "R01" else "review", "reason_code": code, "source_agent": "triage",
            "recommendation": out.get("recommendation"), "draft_message": out.get("draft_message"),
            "confidence": out.get("confidence"), "tried": ["triage: coverage, campaigns, prior claims, modifications"]},
            updates=upd)
    else:
        upd.update(claim_type=out.get("claim_type"), contract_type=out.get("contract_type"))
        transition(claim, "building", "; ".join(out["reasons"]) or "claimable", sim_at=clock.now(), updates=upd,
                   job=("claim.build", {}, upd["priority"]))


def h_build(claim: dict, payload: dict) -> None:
    out, _ = agent("builder", claim, {"triage": claim.get("triage") or {}, "retype": payload.get("retype")})
    if out["version"] <= 0:
        escalate(claim, out["summary"], {"kind": "review", "reason_code": "AGENT_OUTPUT_INVALID",
                                         "recommendation": "Builder could not produce a valid claim — build manually.",
                                         "draft_message": out["summary"], "source_agent": "builder"})
        return
    flags = sorted(set((claim.get("flags") or []) + out.get("flags", [])))
    transition(reload(claim["id"]), "checking", out["summary"], sim_at=clock.now(), updates={"flags": flags},
               job=("claim.check", {}, prio(claim)))


def h_check(claim: dict, payload: dict) -> None:
    out, _ = agent("compliance", claim, {})
    claim = reload(claim["id"])
    if out["status"] == "ready":
        rr = repo.latest_rule_results(claim["id"])
        if not rr or any(r["result"] == "fail" for r in rr):  # guardrail: orchestrator checks, not the prompt
            escalate(claim, "compliance said ready but rules still fail", {
                "kind": "review", "reason_code": "GUARDRAIL", "source_agent": "compliance",
                "recommendation": "Rule engine still reports failures — review manually."})
            return
        must = mandatory_escalation(claim)
        if must:
            escalate(claim, f"mandatory escalation: {must}", {
                "kind": "review", "reason_code": must, "source_agent": "orchestrator",
                "recommendation": f"Mandatory human approval ({must}). Claim is otherwise ready to submit.",
                "draft_message": None})
            return
        warn_codes = [r["code"] for r in rr if r["result"] == "warn" and r.get("code")]
        transition(claim, "ready", "all rules pass" + (f" (warn {', '.join(warn_codes)})" if warn_codes else ""),
                   sim_at=clock.now(), updates={"flags": sorted(set((claim.get("flags") or []) + warn_codes))},
                   job=("claim.submit", {}, prio(claim)))
        return
    req = fetchone("core", "select kind from warranty.preauth_requests where claim_id=%s order by created_at desc limit 1",
                   [claim["id"]])
    kind = req["kind"] if (out.get("pre_auth_ticket") and req) else "review"
    codes = out.get("reason_codes") or []
    if out["status"] == "blocked":
        claim = transition(claim, "blocked", "; ".join(out.get("open_questions") or []) or "blocked", sim_at=clock.now(),
                           updates={"flags": sorted(set((claim.get("flags") or []) + codes))})
    escalate(claim, out.get("recommendation") or "compliance escalation", {
        "kind": kind, "reason_code": codes[0] if codes else "REVIEW", "source_agent": "compliance",
        "recommendation": out.get("recommendation"), "draft_message": out.get("draft_message"), "confidence": 0.8,
        "tried": out.get("fixes", []) + out.get("open_questions", [])})


def h_submit(claim: dict, payload: dict) -> None:
    rr = repo.latest_rule_results(claim["id"])
    if not rr or any(r["result"] == "fail" for r in rr):
        escalate(claim, "submit blocked by guardrail (failing rules)", {"kind": "review", "reason_code": "GUARDRAIL",
                                                                         "source_agent": "orchestrator"})
        return
    out, tctx = agent("submitter", claim, {})
    claim = reload(claim["id"])
    receipt = tctx.flags.get("submitted")  # trust the tool-observed OEM receipt, never IDs reported by the model
    if not out["submitted"] or not receipt:
        escalate(claim, "format errors: " + "; ".join(out.get("format_errors", []))[:300], {
            "kind": "review", "reason_code": "FORMAT", "source_agent": "submitter",
            "recommendation": "OEM payload format errors the agent could not fix.",
            "draft_message": "\n".join(out.get("format_errors", []))})
        return
    transition(claim, "submitted", f"submitted as {receipt['oem_claim_id']} (v{receipt['version']})", sim_at=clock.now(),
               updates={"oem_claim_id": receipt["oem_claim_id"], "sim_submitted_at": clock.now()})


def h_resolve(claim: dict, payload: dict) -> None:
    if claim["status"] == "rejected":
        claim = transition(claim, "resolving", "resolver started", sim_at=clock.now())
    codes = payload.get("codes") or []
    pack = load_pack(claim["programme"])
    if claim["resubmissions"] >= pack.max_resubmissions:
        escalate(claim, "resubmission limit reached", {
            "kind": "review", "reason_code": codes[0] if codes else "LIMIT", "source_agent": "orchestrator",
            "recommendation": f"maxResubmissions ({pack.max_resubmissions}) reached — human decision required."})
        return
    out, tctx = agent("resolver", claim, {"codes": codes})
    claim = reload(claim["id"])
    must = [c for c in out.get("reason_codes", []) if c in MANDATORY_ESCALATION_CODES]
    if out["action"] == "resubmit" and not tctx.flags.get("resubmitted"):
        out = {**out, "action": "escalate", "summary": "resolver reported a resubmission the OEM did not receive"}
    if out["action"] == "resubmit" and not must:
        transition(claim, "resubmitted", out["summary"], sim_at=clock.now(),
                   updates={"resubmissions": claim["resubmissions"] + 1})
    elif out["action"] == "retype" and not must:
        with connect("core") as c:
            c.execute("update warranty.claims set triage = coalesce(triage,'{}'::jsonb) || %s where id=%s",
                      [j({"claim_type": out.get("new_claim_type")}), claim["id"]])
        transition(claim, "building", out["summary"], sim_at=clock.now(),
                   job=("claim.build", {"retype": out.get("new_claim_type")}, prio(claim)))
    elif out["action"] == "withdraw":
        transition(claim, "withdrawn", out["summary"], sim_at=clock.now())
    elif out["action"] == "write_off":
        transition(claim, "written_off", out["summary"], sim_at=clock.now())
    else:
        esc = tctx.flags.get("escalation") or {}
        escalate(claim, out["summary"], {
            "kind": esc.get("kind", "review"), "reason_code": esc.get("reason_code") or (codes[0] if codes else "REVIEW"),
            "recommendation": esc.get("recommendation") or out["summary"], "draft_message": esc.get("draft_message"),
            "confidence": esc.get("confidence", 0.7), "tried": esc.get("tried", []), "source_agent": "resolver"})


# ---------------------------------------------------------------------------- human resume (review queue)
oem = ServiceClient(S.oem_hub_url, "orchestrator-worker")


def _human_version(claim: dict, patch: dict, summary: str) -> int:
    from wc_agents.guardrails import apply_patch
    cur = repo.canonical(claim["id"])
    atts = ServiceClient(S.dms_url, "orchestrator-worker").get(f"/dms/v1/repair-orders/{claim['wip_no']}/attachments")
    new = apply_patch(cur, patch, atts)
    new = new.model_copy(update={"totals": compute_totals(new.lines, S.vat_rate, S.currency)})
    return repo.add_version(claim["id"], new, "human", summary)


def h_resume(claim: dict, payload: dict) -> None:
    esc = fetchone("core", "select * from warranty.escalations where id=%s", [payload["escalation_id"]])
    action, actor = payload["action"], payload.get("actor", "warranty_admin")
    with connect("core") as c:
        c.execute("update warranty.claims set touched_by_human=true where id=%s", [claim["id"]])
    claim = reload(claim["id"])
    if action == "write_off":
        transition(claim, "written_off", f"written off by {actor}", sim_at=clock.now(), actor=actor)
        return
    if action == "withdraw":
        transition(claim, "withdrawn", f"withdrawn by {actor}", sim_at=clock.now(), actor=actor)
        return
    kind = esc["kind"]
    if kind in ("pre_auth", "ta_case"):
        req = fetchone("core", """select * from warranty.preauth_requests where claim_id=%s and kind=%s
                                  order by created_at desc limit 1""", [claim["id"], kind])
        if req is None:  # resolver-escalated TA case: raise the request now (human approved sending it)
            r = oem.post(f"/oem/v1/{claim['programme']}/preauth", {"claim_ref": claim["claim_ref"], "vin": claim["vin"],
                                                                   "kind": kind, "reason": esc.get("draft_message") or ""})
            with connect("core") as c:
                c.execute("""insert into warranty.preauth_requests (id, company_id, claim_id, kind, ticket, status)
                             values (%s,%s,%s,%s,%s,'pending')""", [str(uuid.uuid4()), S.company_id, claim["id"], kind, r["ticket"]])
            req = fetchone("core", "select * from warranty.preauth_requests where claim_id=%s and kind=%s order by created_at desc limit 1",
                           [claim["id"], kind])
        d = oem.post(f"/oem/v1/{claim['programme']}/preauth/{req['ticket']}/decision",
                                                     {"approved": True, "actor": f"simulated-oem (sent by {actor})"})
        with connect("core") as c:
            c.execute("update warranty.preauth_requests set status=%s, ref=%s, updated_at=now() where id=%s",
                      [d["status"], d["ref"], req["id"]])
        field = "pre_auth_ref" if kind == "pre_auth" else "tech_support_ref"
        _human_version(claim, {field: d["ref"]}, f"{kind.replace('_', ' ')} {d['ref']} granted (approved by {actor})")
        if claim.get("oem_claim_id"):  # already at the OEM: re-check and resubmit (human-authorised)
            tctx = ToolContext(claim=claim, clock=clock, emit_step=lambda k, p: None)
            rr = t_run_rules(tctx, claim["claim_ref"])
            if rr["has_fail"]:
                escalate(claim, "rules still fail after authorisation", {"kind": "review", "reason_code": "GUARDRAIL",
                                                                          "source_agent": "orchestrator"})
                return
            res = t_resubmit(tctx, claim["claim_ref"])
            if res.get("errors"):
                escalate(reload(claim["id"]), f"resubmission refused: {res['errors']}", {"kind": "review", "reason_code": "LIMIT"})
                return
            transition(reload(claim["id"]), "resubmitted", f"resubmitted with {d['ref']} (human-approved)",
                       sim_at=clock.now(), actor=actor, updates={"resubmissions": claim["resubmissions"] + 1})
        else:
            transition(reload(claim["id"]), "checking", f"{d['ref']} granted — re-checking", sim_at=clock.now(),
                       actor=actor, job=("claim.check", {}, HIGH))
        return
    if kind == "goodwill" and action in ("approve", "edit"):
        if repo.latest_version(claim["id"]):
            _human_version(claim, {"claim_type": "GOODWILL", "contract_type": None},
                           f"re-typed as GOODWILL request (approved by {actor})")
            _human_submit(reload(claim["id"]), actor, override=False)
            return
        if True:
            claim = reload(claim["id"])
            transition(claim, "building", f"goodwill request approved by {actor}", sim_at=clock.now(), actor=actor,
                       job=("claim.build", {"retype": "GOODWILL"}, HIGH))
            with connect("core") as c:
                c.execute("update warranty.claims set triage = coalesce(triage,'{}'::jsonb) || %s where id=%s",
                          [j({"claim_type": "GOODWILL", "action": "build"}), claim["id"]])
            return
    if action == "override" or (kind == "review" and esc.get("reason_code") in ("AMOUNT_OVER_THRESHOLD", "GOODWILL")):
        # human takes responsibility: submit as-is (logged); agent guardrails do not bind a human override
        _human_submit(reload(claim["id"]), actor, override=(action == "override"))
        return
    # approve / edit on a non-claimable review → send the drafted note and close as unrecovered
    transition(claim, "written_off", f"{action} by {actor}: drafted message sent; not claimable", sim_at=clock.now(),
               actor=actor)


def _human_submit(claim: dict, actor: str, override: bool) -> None:
    tctx = ToolContext(claim=claim, clock=clock, emit_step=lambda k, p: None)
    t_run_rules(tctx, claim["claim_ref"])
    b = t_build_payload(tctx, claim["claim_ref"])
    if b["errors"]:
        escalate(claim, "payload format errors", {"kind": "review", "reason_code": "FORMAT"})
        return
    from wc_agents.tools import _record_submission
    from wc_core.ids import idem_key
    v = repo.latest_version(claim["id"])
    key = idem_key(claim["claim_ref"], v["version"], "human-submit")
    if claim.get("oem_claim_id"):
        out = oem.post(f"/oem/v1/{claim['programme']}/claims/{claim['oem_claim_id']}/resubmit",
                       {"payload": v["oem_payload"], "idem_key": key})
    else:
        out = oem.post(f"/oem/v1/{claim['programme']}/claims", {"payload": v["oem_payload"], "idem_key": key})
    _record_submission(tctx, v["version"], "human", key, out)
    if not out.get("oem_claim_id"):
        escalate(claim, f"OEM refused submission: {out.get('errors')}", {"kind": "review", "reason_code": "FORMAT"})
        return
    to = "resubmitted" if claim.get("oem_claim_id") else "submitted"
    transition(claim, to, f"submitted by {actor}" + (" (override)" if override else ""), sim_at=clock.now(), actor=actor,
               updates={"oem_claim_id": out["oem_claim_id"], "sim_submitted_at": claim.get("sim_submitted_at") or clock.now(),
                        **({"resubmissions": claim["resubmissions"] + 1} if to == "resubmitted" else {})})


HANDLERS = {"claim.triage": h_triage, "claim.build": h_build, "claim.check": h_check, "claim.submit": h_submit,
            "claim.resolve": h_resolve, "claim.escalation_resumed": h_resume}


def handle(job: dict) -> None:
    claim = reload(str(job["claim_id"]))
    try:
        HANDLERS[job["kind"]](claim, job["payload"] or {})
    except AgentFailed as e:
        claim = reload(str(job["claim_id"]))
        log.warning("agent_failed", claim_ref=claim["claim_ref"], reason=e.reason, detail=e.detail[:200])
        if claim["status"] in ("escalated", "paid", "withdrawn", "written_off"):
            return
        escalate(claim, f"agent stopped: {e.reason}", {
            "kind": "review", "reason_code": e.reason.split()[0][:40], "source_agent": job["kind"].split(".")[1],
            "recommendation": f"Agent stopped ({e.reason}). Review the trace and complete manually.",
            "draft_message": e.detail[:500]})
