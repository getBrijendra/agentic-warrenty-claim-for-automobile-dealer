"""Claim Builder policy: 3C from messy technician notes, causal part, labour ops, condition codes, evidence →
canonical claim v1. Asks the workshop when a 3C slot cannot be filled from the job card."""
from __future__ import annotations

import re

from wc_core.vocab import HV_SYSTEMS
from wc_rules.helpers import keyword
from wc_rules.packs import load_pack

from ..policy import Ctx, call, final, note
from ..text import draft_3c, pick

REF_PATTERNS = [r"\bFE-ROL-\d+\b", r"\bJL-TA-\d+\b", r"\bFD-TA-\d+\b"]


def causal_sentence(part: dict) -> str:
    return f"Causal part: {part['description']} ({part['part_no']})."


def ensure_causal(cause: str, part: dict) -> str:
    cause = re.sub(r"\s*Causal part: [^.]*\([^)]*\)\.", "", cause).strip()
    if keyword(part["description"]) in cause.lower() and part["part_no"] in cause:
        return cause
    return (cause + " " + causal_sentence(part)).strip()


def build_claim(t: dict, ro: dict, atts: list[dict], ops: dict[str, dict], codes: list[dict], narrative: dict) -> dict:
    programme = ro["programme"]
    pack = load_pack(programme)
    rate = min(float(ro["labour_rate"]), pack.labour_rate_cap)
    lines, cur = [], None
    for ln in ro["lines"]:
        if ln["line_type"] == "labour":
            op = ops.get(ln["op_code"], {})
            cur = {"line_no": len(lines) + 1, "wip_line_no": ln["line_no"], "labour_op": ln["op_code"],
                   "description": op.get("description", ln["description"]), "system": op.get("system", ln.get("op_system")),
                   "standard_hours": op.get("standard_hours", ln.get("op_standard_hours")),
                   "hours_actual": ln["labour_hours_actual"], "hours_claimed": ln["labour_hours_actual"],
                   "labour_rate": rate, "parts": [], "is_related_damage": False, "pay_type": ln["pay_type"],
                   "technician_id": ln.get("technician_id")}
            lines.append(cur)
        elif cur is not None:
            cur["parts"].append({"wip_line_no": ln["line_no"], "part_no": ln["part_no"], "description": ln["description"],
                                 "qty": ln["qty"], "unit_price": ln["unit_price"], "serial": ln.get("part_serial")})
    part_lines = [ln for ln in ro["lines"] if ln["line_type"] == "part"]
    causal_ln = next((ln for ln in part_lines if ln["is_causal"]), part_lines[0] if part_lines else None)
    causal = {"wip_line_no": causal_ln["line_no"], "part_no": causal_ln["part_no"], "description": causal_ln["description"],
              "qty": causal_ln["qty"], "unit_price": causal_ln["unit_price"], "serial": causal_ln.get("part_serial")}
    system = lines[0]["system"]
    story = ro.get("technician_story") or ""
    support = ro.get("tech_support_ref") or next((m.group(0) for p in REF_PATTERNS for m in [re.search(p, story)] if m), None)
    techs = ro.get("technicians") or []
    hv_id = next((x.get("hv_certified_id") for x in techs if x and x.get("hv_certified_id")), None) if system in HV_SYSTEMS else None
    code = codes[0]["code"] if codes else None
    tri = t.get("triage") or {}
    return {
        "claim_ref": t["claim_ref"], "company_id": t["company_id"], "programme": programme,
        "claim_type": tri.get("claim_type") or "BASE", "contract_type": tri.get("contract_type"),
        "brand": ro["make"], "model": ro["model"], "wip_no": ro["wip_no"], "repair_order_id": ro["id"], "vin": ro["vin"],
        "odometer_km": ro["odometer_out"], "repair_date": ro["closed_at"][:10], "wip_closed_at": ro["closed_at"],
        "system": system, "complaint": narrative["complaint"], "cause": ensure_causal(narrative["cause"], causal),
        "correction": narrative["correction"], "causal_part": causal, "lines": lines,
        "condition_code": code, "symptom_code": code, "dtc_codes": ro.get("dtc_codes") or [],
        "diagnostic_session_id": ro.get("diagnostic_session_id"),
        "evidence": [{"attachment_id": a["id"], "kind": a["kind"], "title": a["title"]} for a in atts],
        "campaign_code": tri.get("campaign_code"), "pre_auth_ref": ro.get("pre_auth_ref"),
        "tech_support_ref": support, "hv_tech_id": hv_id, "labour_justification": None,
        "technician_ids": sorted({ln["technician_id"] for ln in lines if ln.get("technician_id")}),
    }


def run(ctx: Ctx):
    t = ctx.task
    ro = ctx.last("get_repair_order")
    if ro is None:
        return call(ctx, note(f"Building the {t['programme']} claim from WIP {t['wip_no']}"),
                    ("get_repair_order", {"wip_no": t["wip_no"]}), ("get_attachments", {"wip_no": t["wip_no"]}))
    labour = [ln for ln in ro["lines"] if ln["line_type"] == "labour"]
    if not ctx.has("search_labour_ops"):
        return call(ctx, ("search_labour_ops", {"programme": ro["programme"], "codes": [ln["op_code"] for ln in labour]}),
                    ("search_condition_codes", {"programme": ro["programme"], "system": labour[0].get("op_system")}))
    narrative = draft_3c(ro.get("technician_story") or "", ro.get("customer_complaint") or "")
    flags: list[str] = []
    if narrative["weak"]:
        ans = ctx.last("ask_workshop")
        if ans is None:
            q = (f"WIP {ro['wip_no']}: the job card only says \"{ro.get('technician_story')}\". What root cause did you find, "
                 f"and what test confirmed the repair?")
            return call(ctx, note("Technician notes are thin — asking the workshop for root cause and test result"),
                        ("ask_workshop", {"wip_no": ro["wip_no"], "question": q}))
        prov = ans.get("provides") or {}
        if prov.get("cause"):
            narrative["cause"] = prov["cause"]
            narrative["correction"] = (narrative["correction"] + " " + prov.get("correction_test", "")).strip()
            flags.append("R05")
        else:
            flags.append("R05_unresolved")
    atts = ctx.last("get_attachments")["items"]
    ops = {o["op_code"]: o for o in ctx.last("search_labour_ops")["items"]}
    codes = ctx.last("search_condition_codes")["items"]
    drafted = ctx.last("draft_canonical_claim")
    if drafted is None:
        claim = build_claim(t, ro, atts, ops, codes, narrative)
        lop = claim["lines"][0]
        return call(ctx, note(pick(ctx.rng, [
            f"Drafted 3C; causal part {claim['causal_part']['part_no']} on {lop['labour_op']} ({lop['hours_claimed']} h vs {lop['standard_hours']} h standard)",
            f"Mapped {lop['labour_op']} and condition {claim['condition_code']}; causal part {claim['causal_part']['description']}"])),
            ("draft_canonical_claim", {"claim": claim}))
    if drafted.get("error"):
        return final({"version": 0, "summary": f"draft rejected by validator: {drafted['error']}", "flags": flags + ["INVALID"]})
    return final({"version": drafted["version"], "flags": flags,
                  "summary": f"v{drafted['version']} drafted · {drafted.get('total_display', '')}".strip()})
