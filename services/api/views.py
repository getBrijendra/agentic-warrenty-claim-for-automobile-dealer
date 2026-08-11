"""Read models for the UI: pipeline board, claim workspace aggregate, version diff, rules matrix, review queue."""
from __future__ import annotations

from typing import Any

from wc_core.infra import to_jsonable
from wc_db.claims import ClaimRepository
from wc_db.db import fetchall, fetchone
from wc_rules.packs import all_packs

repo = ClaimRepository()

COLUMNS = [
    ("closed", "Closed WIPs", ["wip_closed"]), ("triage", "Triage", ["triage"]), ("building", "Building", ["building"]),
    ("compliance", "Compliance", ["checking", "blocked", "ready"]), ("submitted", "Submitted", ["submitted", "resubmitted", "info_requested"]),
    ("accepted", "Accepted / Paid", ["accepted", "paid"]), ("fixing", "Rejected → Fixing", ["rejected", "resolving"]),
    ("escalated", "Escalated / Closed", ["escalated", "written_off", "withdrawn"]),
]
STATUS_COLUMN = {s: key for key, _, ss in COLUMNS for s in ss}


def card(c: dict) -> dict:
    return to_jsonable({
        "claim_ref": c["claim_ref"], "wip_no": c["wip_no"], "programme": c["programme"], "model": c["model"],
        "status": c["status"], "column": STATUS_COLUMN.get(c["status"]), "claim_type": c["claim_type"],
        "amount": c["amount_claimed"], "amount_paid": c["amount_paid"], "priority": c["priority"],
        "window_due_at": c["window_due_at"], "flags": c["flags"], "resubmissions": c["resubmissions"],
        "first_pass_result": c["first_pass_result"], "oem_claim_id": c["oem_claim_id"], "sim_closed_at": c["sim_closed_at"],
        "touched_by_human": c["touched_by_human"]})


def board() -> dict:
    return {"columns": [{"key": k, "label": label} for k, label, _ in COLUMNS], "cards": [card(c) for c in repo.all()]}


def workspace(claim_ref: str, dms, oem) -> dict | None:
    c = repo.by_ref(claim_ref)
    if not c:
        return None
    versions = repo.versions(c["id"])
    rule_runs = fetchall("core", """select r.*, v.version from warranty.rule_results r join warranty.claim_versions v
                                    on v.id = r.claim_version_id where v.claim_id=%s order by v.version, r.run_seq, r.created_at""",
                         [c["id"]])
    latest_rules: list[dict] = []
    if rule_runs:
        lv = max(r["version"] for r in rule_runs)
        ls = max(r["run_seq"] for r in rule_runs if r["version"] == lv)
        latest_rules = [r for r in rule_runs if r["version"] == lv and r["run_seq"] == ls]
    runs = fetchall("core", "select * from agent.agent_runs where claim_id=%s order by started_at", [c["id"]])
    steps = fetchall("core", """select s.* from agent.agent_steps s join agent.agent_runs r on r.id=s.run_id
                                where r.claim_id=%s order by s.created_at, s.seq""", [c["id"]])
    by_run: dict[str, list] = {}
    for s in steps:
        by_run.setdefault(str(s["run_id"]), []).append(s)
    try:
        job_card = dms.get(f"/dms/v1/repair-orders/{c['wip_no']}/job-card")
    except Exception as e:
        job_card = {"error": str(e)}
    try:
        layout = oem.get(f"/oem/v1/{c['programme']}/schema")["layout"]
    except Exception:
        layout = None
    return to_jsonable({
        "claim": c, "card": card(c), "job_card": job_card, "layout": layout,
        "versions": [{"version": v["version"], "built_by": v["built_by"], "change_summary": v["change_summary"],
                      "created_at": v["created_at"], "canonical": v["canonical"], "oem_payload": v["oem_payload"]} for v in versions],
        "rules": latest_rules, "rule_history": rule_runs,
        "agent_runs": [{**r, "steps": by_run.get(str(r["id"]), [])} for r in runs],
        "submissions": fetchall("core", "select * from warranty.submissions where claim_id=%s order by created_at", [c["id"]]),
        "adjudications": fetchall("core", "select * from warranty.adjudication_snapshots where claim_id=%s order by version", [c["id"]]),
        "credit_notes": fetchall("core", "select * from warranty.credit_notes where claim_id=%s", [c["id"]]),
        "escalations": fetchall("core", "select * from warranty.escalations where claim_id=%s order by created_at", [c["id"]]),
        "events": fetchall("core", "select * from warranty.claim_events where claim_id=%s order by id", [c["id"]]),
        "preauths": fetchall("core", "select * from warranty.preauth_requests where claim_id=%s order by created_at", [c["id"]]),
    })


def _flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = d
    return out


def diff(claim_ref: str, a: int, b: int) -> dict | None:
    c = repo.by_ref(claim_ref)
    va, vb = repo.version(c["id"], a), repo.version(c["id"], b)
    if not va or not vb:
        return None
    fa, fb = _flatten(va["canonical"]), _flatten(vb["canonical"])
    changes = []
    for k in sorted(set(fa) | set(fb)):
        if k.startswith("totals.") and k not in ("totals.total",):
            continue
        if fa.get(k) != fb.get(k):
            changes.append({"path": k, "before": fa.get(k), "after": fb.get(k),
                            "op": "added" if k not in fa else "removed" if k not in fb else "changed"})
    return {"claim_ref": claim_ref, "a": a, "b": b, "summary": vb["change_summary"], "built_by": vb["built_by"],
            "changes": changes}


def _cov(pack, ctype: str) -> dict | None:
    c = next((x for x in pack.coverage if x.contract_type == ctype), None)
    if not c:
        return None
    yrs = f"{c.years} yrs" if c.years else "—"
    km = "unlimited km" if c.km_limit is None else f"{c.km_limit:,} km"
    val = f"{yrs} / {km}" + (f" · {', '.join(c.component_scope)}" if c.component_scope else "")
    return {"value": val, "label": c.label, "note": c.note, "source": c.source, "source_url": c.source_url,
            "last_verified": c.last_verified}


def rules_matrix() -> dict:
    packs = all_packs()
    exercised: dict[str, dict[str, list[str]]] = {}
    for r in fetchall("core", """select distinct r.rule_key, r.result, c.claim_ref, c.programme from warranty.rule_results r
                                 join warranty.claim_versions v on v.id=r.claim_version_id
                                 join warranty.claims c on c.id=v.claim_id where r.result in ('fail','warn')"""):
        exercised.setdefault(r["rule_key"], {}).setdefault(r["programme"], [])
        if r["claim_ref"] not in exercised[r["rule_key"]][r["programme"]]:
            exercised[r["rule_key"]][r["programme"]].append(r["claim_ref"])
    for a in fetchall("core", """select a.rejection_codes, c.claim_ref, c.programme from warranty.adjudication_snapshots a
                                 join warranty.claims c on c.id=a.claim_id where a.decision='rejected'"""):
        for code in a["rejection_codes"]:
            exercised.setdefault(f"gateway:{code}", {}).setdefault(a["programme"], []).append(a["claim_ref"])
    rows = []
    cov_types = ["BASE", "EXTENDED", "LIMITED_EXTENDED", "PREMIUMCARE", "COMPONENT_BATTERY", "MAINTENANCE_PROGRAMME"]
    labels = {"BASE": "Base coverage", "EXTENDED": "Extended contract", "LIMITED_EXTENDED": "Limited extended (yrs 6–7)",
              "PREMIUMCARE": "PremiumCare", "COMPONENT_BATTERY": "HV battery component term",
              "MAINTENANCE_PROGRAMME": "Maintenance programme"}
    for ct in cov_types:
        cells = {p.programme: _cov(p, ct) for p in packs}
        if any(cells.values()):
            rows.append({"key": f"coverage:{ct}", "group": "Coverage (public)", "label": labels[ct], "cells": cells,
                         "why": "Determines whether the repair is claimable and under which claim type.",
                         "exercised": exercised.get("coverage", {})})
    dealer_keys = [("submission_window", "Submission window"), ("pre_authorisation", "Pre-authorisation threshold"),
                   ("labour_tolerance", "Labour tolerance over standard"), ("required_evidence", "Required evidence"),
                   ("narrative_3c", "3C narrative rule"), ("parts_retention", "Parts retention"),
                   ("repeat_repair", "Repeat-repair rule"), ("max_resubmissions", "Max resubmissions"),
                   ("labour_rate_cap", "Labour rate cap"), ("claim_format", "Claim format")]
    for key, label in dealer_keys:
        cells = {}
        for p in packs:
            r = p.rule(key)
            cells[p.programme] = ({"value": r.get("label"), "source": r.get("source", "illustrative"),
                                   "source_url": r.get("source_url")} if r else None)
        rows.append({"key": key, "group": "Dealer-claim rules (illustrative)", "label": label, "cells": cells,
                     "why": WHY.get(key, ""), "exercised": exercised.get(key, {})})
    for p in packs:
        for chk in p.gateway_only_checks:
            rows.append({"key": f"gateway:{chk['rule']}:{p.programme}", "group": "Gateway-only checks (simulated OEM)",
                         "label": chk["label"],
                         "cells": {q.programme: ({"value": f"{chk['code']} · applied by the Simulated OEM Gateway",
                                                  "source": "illustrative"} if q.programme == p.programme else None)
                                   for q in packs},
                         "why": "The dealer side cannot see this check — this is why some claims pass compliance and "
                                "are still rejected, giving the resolver real work.",
                         "exercised": {p.programme: exercised.get(f"gateway:{chk['code']}", {}).get(p.programme, [])}})
    facts = []
    for p in packs:
        for f in p.facts:
            facts.append({"programme": p.programme, **f.model_dump()})
    return {"programmes": [{"programme": p.programme, "display_name": p.display_name, "colour": p.colour,
                            "status": p.status, "pack_version": p.pack_version, "brands": p.brands} for p in packs],
            "rows": rows, "facts": facts}


WHY = {
    "submission_window": "Late claims are refused outright (R02) — the orchestrator prioritises claims near the deadline.",
    "pre_authorisation": "Repairs above the threshold need OEM approval before work starts (R08).",
    "labour_tolerance": "Hours above standard time need justification and evidence (R03).",
    "required_evidence": "Missing photos / DTCs / session IDs / SOH reports cause R04 rejections.",
    "narrative_3c": "Thin Complaint/Cause/Correction narratives cause R05 rejections.",
    "parts_retention": "Failed parts must be kept for possible OEM return (R13).",
    "repeat_repair": "A second repair on the same system needs a TA case or PA.",
    "max_resubmissions": "After the limit the claim must be escalated.",
    "labour_rate_cap": "Labour is paid at most at the programme rate.",
    "claim_format": "Each OEM portal expects a different payload shape — adapters handle this.",
}


def review_queue() -> list[dict]:
    rows = fetchall("core", """select e.*, c.claim_ref, c.wip_no, c.programme, c.model, c.status as claim_status,
                               c.amount_claimed, c.claim_type from warranty.escalations e join warranty.claims c on c.id=e.claim_id
                               order by (e.status='open') desc, e.created_at""")
    acts = fetchall("core", "select * from warranty.review_actions order by created_at")
    by: dict[str, list] = {}
    for a in acts:
        by.setdefault(str(a["escalation_id"]), []).append(a)
    return to_jsonable([{**r, "actions": by.get(str(r["id"]), [])} for r in rows])
