"""Hard guardrails in code (not prompts). Every claim version is diffed against the DMS snapshot:
labour ≤ actual hours, parts ⊆ WIP parts, odometer / dates / VIN immutable, evidence must exist in the DMS,
reference numbers must be on record. Agents never fabricate evidence, hours, parts, odometer or dates."""
from __future__ import annotations

import re
from decimal import Decimal

from wc_core.canonical import CanonicalClaim


class GuardrailViolation(ValueError):
    pass


def validate_against_dms(c: CanonicalClaim, ro: dict, attachments: list[dict], granted_refs: list[str]) -> list[str]:
    errs: list[str] = []
    if c.vin != ro["vin"]:
        errs.append("VIN differs from the WIP")
    if c.odometer_km != int(ro["odometer_out"]):
        errs.append(f"odometer {c.odometer_km} differs from WIP odometer-out {ro['odometer_out']}")
    if c.repair_date.isoformat() != ro["closed_at"][:10]:
        errs.append("repair date differs from WIP close date")
    if c.wip_closed_at.isoformat()[:19] != ro["closed_at"][:19]:
        errs.append("WIP close time differs from the DMS")
    lines = {ln["line_no"]: ln for ln in ro["lines"]}
    for ln in c.lines:
        w = lines.get(ln.wip_line_no)
        if not w or w["line_type"] != "labour" or w["op_code"] != ln.labour_op:
            errs.append(f"labour line {ln.labour_op} is not on the WIP")
            continue
        if ln.hours_claimed > Decimal(str(w["labour_hours_actual"])) + Decimal("0.001"):
            errs.append(f"{ln.labour_op}: hours claimed {ln.hours_claimed} > actual {w['labour_hours_actual']}")
        if ln.hours_actual != Decimal(str(w["labour_hours_actual"])):
            errs.append(f"{ln.labour_op}: actual hours changed")
        for p in ln.parts:
            wp = lines.get(p.wip_line_no)
            if not wp or wp["line_type"] != "part" or wp["part_no"] != p.part_no:
                errs.append(f"part {p.part_no} is not on the WIP")
                continue
            if p.qty > Decimal(str(wp["qty"])) or p.unit_price != Decimal(str(wp["unit_price"])).quantize(p.unit_price):
                errs.append(f"part {p.part_no}: qty/price differ from the WIP")
            if (p.serial or None) != (wp.get("part_serial") or None):
                errs.append(f"part {p.part_no}: serial differs from the WIP")
    claim_parts = {p.part_no for ln in c.lines for p in ln.parts}
    if c.causal_part.part_no not in claim_parts:
        errs.append("causal part is not on a claim line")
    att = {a["id"]: a for a in attachments}
    for e in c.evidence:
        if e.attachment_id not in att or att[e.attachment_id]["kind"] != e.kind:
            errs.append(f"evidence {e.attachment_id} ({e.kind}) does not exist on the WIP")
    if c.diagnostic_session_id and c.diagnostic_session_id != ro.get("diagnostic_session_id"):
        errs.append("diagnostic session ID is not recorded in the DMS")
    if c.pre_auth_ref and c.pre_auth_ref not in {ro.get("pre_auth_ref"), *granted_refs}:
        errs.append(f"pre-authorisation {c.pre_auth_ref} is not on record")
    if c.tech_support_ref:
        story_refs = set(re.findall(r"\b[A-Z]{2}-(?:ROL|TA)-\d+\b", ro.get("technician_story") or ""))
        if c.tech_support_ref not in {ro.get("tech_support_ref"), *granted_refs, *story_refs}:
            errs.append(f"support reference {c.tech_support_ref} is not on record")
    if c.hv_tech_id and c.hv_tech_id not in {t.get("hv_certified_id") for t in ro.get("technicians") or [] if t}:
        errs.append("HV technician ID does not match the technician on the WIP")
    return errs


PATCHABLE = {"evidence_add", "diagnostic_session_id", "labour_justification", "cause", "complaint", "correction",
             "correction_append", "claim_type", "campaign_code", "pre_auth_ref", "tech_support_ref", "hv_tech_id",
             "causal_part_no", "hours", "condition_code", "symptom_code", "contract_type"}


def apply_patch(c: CanonicalClaim, patch: dict, attachments: list[dict]) -> CanonicalClaim:
    bad = set(patch) - PATCHABLE
    if bad:
        raise GuardrailViolation(f"fields not patchable by agents: {', '.join(sorted(bad))}")
    d = c.model_dump(mode="json")
    att = {a["id"]: a for a in attachments}
    for aid in patch.get("evidence_add", []):
        if aid not in att:
            raise GuardrailViolation(f"attachment {aid} does not exist on the WIP")
        if aid not in {e["attachment_id"] for e in d["evidence"]}:
            d["evidence"].append({"attachment_id": aid, "kind": att[aid]["kind"], "title": att[aid]["title"]})
    for k in ("diagnostic_session_id", "labour_justification", "cause", "complaint", "correction", "claim_type",
              "campaign_code", "pre_auth_ref", "tech_support_ref", "hv_tech_id", "condition_code", "symptom_code",
              "contract_type"):
        if k in patch:
            d[k] = patch[k]
    if patch.get("correction_append"):
        d["correction"] = (d["correction"].rstrip() + " " + patch["correction_append"]).strip()
    if patch.get("causal_part_no"):
        part = next((p for ln in d["lines"] for p in ln["parts"] if p["part_no"] == patch["causal_part_no"]), None)
        if part is None:
            raise GuardrailViolation(f"causal part {patch['causal_part_no']} is not on a claim line")
        d["causal_part"] = part
    for line_no, hours in (patch.get("hours") or {}).items():
        for ln in d["lines"]:
            if str(ln["line_no"]) == str(line_no):
                ln["hours_claimed"] = hours
    return CanonicalClaim.model_validate(d)
