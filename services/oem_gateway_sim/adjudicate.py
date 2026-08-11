"""Deterministic adjudication: same rule packs as the dealer side + gateway-only checks, evaluated on the OEM
payload against the OEM's own records (registry, catalog, repair history, authorisations)."""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from wc_core.settings import get_settings
from wc_db.db import fetchall, fetchone
from wc_oem.adapters.registry import adapter_for
from wc_rules.engine import evaluate, failing_codes
from wc_rules.helpers import next_weekday
from wc_rules.model import RuleContext
from wc_rules.packs import load_pack


def catalog(programme: str) -> dict[str, dict]:
    return {r["op_code"]: {"system": r["system"], "standard_hours": float(r["standard_hours"]),
                           "causal_families": r["causal_families"]}
            for r in fetchall("sim", "select * from oem_sim.oem_labour_catalog where programme=%s", [programme])}


def context(programme: str, vin: str, claim_ref: str, window_ref: datetime) -> RuleContext:
    reg = fetchone("sim", "select * from oem_sim.oem_vehicle_registry where vin=%s", [vin]) or {}
    camps = fetchall("sim", "select * from oem_sim.oem_campaigns where programme=%s", [programme])
    prior = fetchall("sim", """select s.claim_ref, s.payload, s.received_at_sim from oem_sim.oem_submissions s
                               where s.programme=%s and s.claim_ref <> %s""", [programme, claim_ref])
    cat = catalog(programme)
    adapter = adapter_for(programme)
    prior_claims = []
    for p in prior:
        subj = adapter.to_subject(p["payload"], cat)
        if subj.vin == vin:
            prior_claims.append({"claim_ref": p["claim_ref"], "vin": vin, "labour_ops": subj.ops,
                                 "closed_at": subj.wip_closed_at.isoformat(), "status": "submitted", "system": subj.system,
                                 "repair_date": subj.repair_date.isoformat()})
    auth = [r["ref"] for r in fetchall("sim", "select ref from oem_sim.oem_preauths where vin=%s and status='approved'", [vin])]
    return RuleContext(now=window_ref, vehicle={"vin": vin, "warranty_start_date": reg.get("warranty_start_date"),
                                                "aftermarket_mods": []},
                       contracts=reg.get("contracts") or [], service_history=reg.get("service_history") or [],
                       campaigns=[dict(c) for c in camps], prior_claims=prior_claims,
                       repair_history=reg.get("repair_history") or [], catalog=cat, valid_auth_refs=auth)


def adjudicate(programme: str, payload: dict, window_ref: datetime) -> dict:
    cat = catalog(programme)
    subj = adapter_for(programme).to_subject(payload, cat)
    ctx = context(programme, subj.vin, subj.claim_ref, window_ref)
    results = evaluate(subj, ctx, side="gateway")
    codes = failing_codes(results)
    decision, amount = ("rejected", 0.0) if codes else ("accepted", subj.total)
    if not codes and subj.claim_type == "GOODWILL":  # goodwill: simulated OEM contributes 50%
        decision, amount = "partial", round(subj.total * 0.5, 2)
    return {"decision": decision, "codes": codes, "trace": [r.model_dump(mode="json") for r in results], "amount": amount}


def delay_days(programme: str, claim_ref: str, version: int) -> float:
    lo, hi = load_pack(programme).adjudication_delay_days
    rng = random.Random(f"{get_settings().warranty_seed}:{claim_ref}:{version}")
    return rng.uniform(lo, hi)


def payment_date(programme: str, decided: datetime) -> datetime:
    return next_weekday(decided, load_pack(programme).payment_weekday)


def decided_at(programme: str, claim_ref: str, version: int, received: datetime) -> datetime:
    return received + timedelta(days=delay_days(programme, claim_ref, version))
