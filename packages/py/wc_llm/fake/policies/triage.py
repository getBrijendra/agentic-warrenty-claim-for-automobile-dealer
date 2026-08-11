"""Triage policy: is the WIP claimable, under which programme / claim type? Uses the same deterministic helpers
as the rules engine (coverage math, campaign match, duplicate window, modification conflicts)."""
from __future__ import annotations

from datetime import datetime

from wc_rules.helpers import (as_date, conflicting_mods, find_duplicate, matching_campaign, select_coverage,
                              window_remaining_days)

from ..policy import Ctx, call, final, note
from ..text import money, pick


def _primary(ro: dict) -> dict:
    return next(ln for ln in ro["lines"] if ln["line_type"] == "labour")


def goodwill_draft(ro: dict, veh: dict, contracts: list[dict], history: list[dict], reasons: list[str]) -> str:
    base = next((c for c in contracts if c["contract_type"] == "BASE"), None)
    parts = sum(ln["qty"] * ln["unit_price"] for ln in ro["lines"] if ln["line_type"] == "part")
    labour = sum((ln.get("labour_hours_actual") or 0) for ln in ro["lines"] if ln["line_type"] == "labour") * ro.get("labour_rate", 0)
    total = parts + labour
    over = ""
    if base and base.get("km_limit") and ro["odometer_out"] > base["km_limit"]:
        km_over = ro["odometer_out"] - base["km_limit"]
        over = f"{km_over:,} km ({km_over / base['km_limit'] * 100:.1f}%) beyond the {base['km_limit']:,} km base limit"
    age = (as_date(ro["closed_at"]) - as_date(veh["warranty_start_date"])).days / 365.25
    services = len([h for h in history if h.get("kind") == "periodic_service"])
    return (f"Goodwill request — {veh['make']} {veh['model']} ({veh['model_year']}), VIN {veh['vin']}, WIP {ro['wip_no']}.\n"
            f"Repair: {_primary(ro)['description']}. Odometer {ro['odometer_out']:,} km at {age:.1f} years; {over or '; '.join(reasons)}.\n"
            f"The vehicle has {services} periodic services on record with us and the failure is consistent with a component "
            f"defect, not wear or misuse. We request an OEM goodwill contribution of 50% of parts and labour "
            f"({money(total * 0.5)} of {money(total)} excl. VAT); the dealer will cover the balance as customer goodwill.")


def run(ctx: Ctx):
    t = ctx.task
    ro = ctx.last("get_repair_order")
    if ro is None:
        return call(ctx, note(f"Reading WIP {t['wip_no']} (Job Card) from Autoline"),
                    ("get_repair_order", {"wip_no": t["wip_no"]}))
    vin = ro["vin"]
    if not ctx.has("get_vehicle"):
        veh_name = f"{ro['make']} {ro['model']}"
        return call(ctx, note(pick(ctx.rng, [f"Checking coverage, campaigns and prior claims for the {veh_name}",
                                             f"{veh_name} at {ro['odometer_out']:,} km — checking contracts and history"])),
                    ("get_vehicle", {"vin": vin}), ("get_coverage_contracts", {"vin": vin}),
                    ("get_service_history", {"vin": vin}),
                    ("find_open_campaigns", {"programme": ro["programme"], "vin": vin}),
                    ("find_prior_claims", {"vin": vin}))
    veh = ctx.last("get_vehicle")
    contracts = ctx.last("get_coverage_contracts")["items"]
    history = ctx.last("get_service_history")["items"]
    campaigns = ctx.last("find_open_campaigns")["items"]
    prior = ctx.last("find_prior_claims")["items"]
    line = _primary(ro)
    system, ops = line.get("op_system") or "UNKNOWN", [ln["op_code"] for ln in ro["lines"] if ln["line_type"] == "labour"]
    closed = datetime.fromisoformat(ro["closed_at"])
    now = datetime.fromisoformat(t["sim_now"])
    remaining = window_remaining_days(closed, now, int(t["window_days"]))
    priority = "high" if remaining <= 3 else "normal"
    name = f"{veh['make']} {veh['model']}"

    decision: dict
    dup = find_duplicate(prior, vin, ops, closed, t["claim_ref"])
    mods = conflicting_mods(veh.get("aftermarket_mods") or [], system)
    cp = matching_campaign(campaigns, vin, ops)
    contract, claim_type, reasons = select_coverage(contracts, as_date(ro["closed_at"]), int(ro["odometer_out"]), system)
    if dup:
        decision = dict(claimable=False, action="withdraw", reason_codes=["R07"], duplicate_of=dup["claim_ref"],
                        reasons=[f"Same VIN and labour op {', '.join(ops)} as {dup['claim_ref']} closed within 7 days"],
                        recommendation="Withdraw — duplicate of an existing claim", confidence=0.95)
        msg = f"WIP {ro['wip_no']} duplicates {dup['claim_ref']} (same VIN + op within 7 days) — withdrawing"
    elif mods:
        decision = dict(claimable=False, action="escalate", reason_codes=["R12"],
                        reasons=[f"Aftermarket modification ({', '.join(mods)}) affects {system}"],
                        recommendation="Do not file: failure is in a system affected by an aftermarket modification. "
                                       "Offer the customer a customer-pay invoice; a human may request goodwill.",
                        draft_message=(f"Dear customer, the {system.lower()} repair on your {name} (WIP {ro['wip_no']}) is not "
                                       f"covered by the manufacturer warranty because a non-approved {mods[0].replace('_', ' ')} "
                                       "is fitted and affects this system. We can provide a customer-pay quotation."),
                        confidence=0.9)
        msg = f"{name} has a {mods[0].replace('_', ' ')} fitted and the claim is on {system.lower()} — escalating (R12)"
    elif cp:
        decision = dict(claimable=True, action="build", claim_type="CAMPAIGN", campaign_code=cp["campaign_code"],
                        reason_codes=["R10"], reasons=[f"VIN listed in open campaign {cp['campaign_code']} ({cp['title']})"],
                        confidence=0.95)
        msg = f"{name} is in campaign {cp['campaign_code']}, re-typing as CAMPAIGN"
    elif contract is None:
        decision = dict(claimable=False, action="escalate", reason_codes=["R01"], reasons=reasons,
                        recommendation="Out of coverage — submit a goodwill request (human approval required)",
                        draft_message=goodwill_draft(ro, veh, contracts, history, reasons), confidence=0.85)
        msg = f"{name} at {ro['odometer_out']:,} km is outside coverage ({reasons[0] if reasons else 'no contract'}) — drafting goodwill request"
    else:
        decision = dict(claimable=True, action="build", claim_type=claim_type, contract_type=contract["contract_type"],
                        reasons=reasons[-1:], confidence=0.9)
        if claim_type == "COMPONENT":
            base_note = next((r for r in reasons if r.startswith("BASE")), None)
            msg = (f"{name}: vehicle term {('expired' if base_note is None else 'checked')} but the separate battery "
                   f"term is valid — filing as COMPONENT")
            base = next((c for c in contracts if c["contract_type"] == "BASE"), None)
            if base and base.get("km_limit") and int(ro["odometer_out"]) > int(base["km_limit"]):
                msg = (f"{name} at {ro['odometer_out']:,} km is past the vehicle term, but the HV battery term "
                       f"(10 yrs / unlimited km) is valid — filing as COMPONENT")
        else:
            msg = f"Claimable under {contract['contract_type']} — {reasons[-1]}"
    decision.update(programme=ro["programme"], priority=priority, window_remaining_days=remaining)
    if priority == "high" and decision["action"] == "build":
        msg += f" · window closes in {remaining:.1f} days — priority"

    if not ctx.has("emit_status_note") or len(ctx.results("emit_status_note")) < 3:
        return call(ctx, note(msg))
    return final(decision)
