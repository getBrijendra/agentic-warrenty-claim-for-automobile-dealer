"""Offline fixtures built from seed/generated/default.warranty.json (no database, no services)."""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache

import pytest

from wc_core.canonical import CanonicalClaim, ClaimLine, compute_totals
from wc_core.settings import ROOT
from wc_llm.fake.policies.builder import build_claim
from wc_llm.fake.text import draft_3c
from wc_rules.helpers import select_coverage
from wc_rules.model import RuleContext


@lru_cache
def seed() -> dict:
    return json.loads((ROOT / "seed" / "generated" / "default.warranty.json").read_text(encoding="utf-8"))


def ro_for(wip: str) -> dict:
    d = seed()
    ro = dict(next(r for r in d["repair_orders"] if r["wip_no"] == wip))
    veh = next(v for v in d["vehicles"] if v["vin"] == ro["vin"])
    ops = {o["op_code"]: o for o in d["labour_ops"] if o["programme"] == veh["oem_programme"]}
    lines = []
    for ln in d["repair_order_lines"]:
        if ln["repair_order_id"] == ro["id"]:
            op = ops.get(ln["op_code"] or "", {})
            lines.append({**ln, "op_system": op.get("system"), "op_standard_hours": op.get("standard_hours")})
    techs = {ln["technician_id"] for ln in lines}
    ro.update(programme=veh["oem_programme"], make=veh["make"], model=veh["model"], lines=lines,
              technicians=[s for s in d["staff"] if s["id"] in techs],
              labour_rate=d["company"]["labour_rates"][veh["oem_programme"]])
    return ro


def claim_for(wip: str, **over) -> CanonicalClaim:
    d = seed()
    ro = ro_for(wip)
    contracts = [c for c in d["coverage_contracts"] if c["vin"] == ro["vin"]]
    system = next(ln for ln in ro["lines"] if ln["line_type"] == "labour")["op_system"]
    contract, ctype, _ = select_coverage(contracts, datetime.fromisoformat(ro["closed_at"]).date(), ro["odometer_out"], system)
    atts = [a for a in d["attachments"] if a["repair_order_id"] == ro["id"]]
    ops = {o["op_code"]: o for o in d["labour_ops"]}
    codes = [c for c in d["condition_codes"] if c["programme"] == ro["programme"] and c["system"] == system]
    narrative = draft_3c(ro["technician_story"], ro["customer_complaint"])
    task = {"claim_ref": f"WC-TEST-{wip}", "company_id": "test",
            "triage": {"claim_type": over.pop("claim_type", ctype or "BASE"),
                       "contract_type": contract["contract_type"] if contract else None,
                       "campaign_code": over.pop("campaign_code", None)}}
    raw = build_claim(task, ro, atts, ops, codes, narrative)
    raw.update(over)
    lines = [ClaimLine.model_validate(ln) for ln in raw["lines"]]
    raw["totals"] = compute_totals(lines, 0.05).model_dump(mode="json")
    return CanonicalClaim.model_validate(raw)


def ctx_for(wip: str, now=None, side: str = "dealer", **over) -> RuleContext:
    d = seed()
    ro = ro_for(wip)
    vin = ro["vin"]
    veh = next(v for v in d["vehicles"] if v["vin"] == vin)
    reg = next(r for r in d["oem_side"]["vehicle_registry"] if r["vin"] == vin)
    closed = datetime.fromisoformat(ro["closed_at"])
    base = dict(
        now=(datetime.fromisoformat(now) if isinstance(now, str) else now) if now else closed,
        vehicle=veh,
        contracts=reg["contracts"] if side == "gateway" else [c for c in d["coverage_contracts"] if c["vin"] == vin],
        service_history=[h for h in d["service_history"] if h["vin"] == vin],
        campaigns=[c for c in d["campaigns"] if vin in c["affected_vins"]],
        prior_claims=[{"claim_ref": b["claim_ref"], "vin": b["vin"], "labour_ops": [b["labour_op"]], "closed_at": b["wip_closed_at"],
                       "status": b["final_status"], "system": b["system"]} for b in d["baseline_claims"] if b["vin"] == vin],
        repair_history=reg["repair_history"] if side == "gateway" else [],
        catalog={c["op_code"]: c for c in d["oem_side"]["labour_catalog"]} if side == "gateway" else {},
        wip_part_nos=[ln["part_no"] for ln in ro["lines"] if ln["line_type"] == "part"] if side == "dealer" else None,
        valid_auth_refs=[p["ref"] for p in d["oem_side"]["preauths"] if p["vin"] == vin] if side == "gateway" else None,
    )
    base.update(over)
    return RuleContext(**base)


@pytest.fixture
def seed_data() -> dict:
    return seed()
