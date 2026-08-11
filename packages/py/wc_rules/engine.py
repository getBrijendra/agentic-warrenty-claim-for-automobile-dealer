"""Deterministic rules engine — pure functions, no I/O.

evaluate(subject, ctx, side) runs every dealer rule configured by the programme's rule pack. With
side="gateway" it applies the stricter OEM view and additionally runs the pack's gateway_only_checks.
"""
from __future__ import annotations

from typing import Any, Callable, Literal

from simpleeval import simple_eval

from wc_core.vocab import MANDATORY_ESCALATION_CODES

from .helpers import (as_date, conflicting_mods, contract_valid, contracts_for, find_duplicate, keyword,
                      matching_campaign, service_gaps, text_flags, window_remaining_days)
from .model import RuleContext, RuleResult, RuleSubject
from .packs import RulePack, load_pack

Side = Literal["dealer", "gateway"]
RuleFn = Callable[[RuleSubject, RuleContext, RulePack, dict, Side], list[RuleResult]]

EVIDENCE_LABELS = {
    "photo": "photo", "dtc_snapshot": "DTC snapshot", "battery_soh_report": "battery SOH report",
    "diag_log": "remote diagnostic log", "odometer_photo": "odometer / hour-meter photo",
    "diagnostic_session_id": "diagnostic session ID", "tech_support_ref": "technical support case ref",
    "hv_tech_id": "HV-certified technician ID", "invoice": "invoice",
}


def names(s: RuleSubject) -> dict[str, Any]:
    texts = [s.complaint, s.cause, s.correction] + [ln.description for ln in s.lines]
    return {"system": s.system, "claim_type": s.claim_type, "amount_ex_vat": s.amount_ex_vat,
            "programme": s.programme, "True": True, "False": False, **text_flags(s.system, texts)}


def cond(expr: str | None, s: RuleSubject) -> bool:
    if not expr:
        return False
    return bool(simple_eval(expr, names=names(s)))


def _src(cfg: dict) -> dict:
    return {"source": cfg.get("source", "illustrative"), "source_url": cfg.get("source_url")}


def _r(key: str, label: str, result: str, detail: str, side: Side, code: str | None = None, *, fixable: bool = False,
       fix: str | None = None, cfg: dict | None = None, data: dict | None = None, source: str | None = None,
       source_url: str | None = None) -> RuleResult:
    src = _src(cfg or {})
    return RuleResult(rule_key=key, label=label, result=result, code=code if result != "pass" else code, detail=detail,
                      source=source or src["source"], source_url=source_url or src["source_url"], fixable=fixable,
                      suggested_fix=fix, side=side, data=data or {})


# ---- dealer rules -----------------------------------------------------------------------

def r_coverage(s, ctx, pack, cfg, side):
    url = next((c.source_url for c in pack.coverage if c.contract_type == "BASE"), None)
    if s.claim_type in ("CAMPAIGN", "GOODWILL", "PARTS"):
        return [_r("coverage", "Coverage (time / km)", "pass", f"{s.claim_type} claim is not coverage-term dependent",
                   side, "R01", source="public", source_url=url)]
    on = as_date(s.repair_date)
    cands = contracts_for(s.claim_type, ctx.contracts, s.contract_type)
    if not cands:
        return [_r("coverage", "Coverage (time / km)", "fail", f"no {s.claim_type} contract on file for VIN", side, "R01",
                   source="public", source_url=url)]
    reasons = []
    for c in cands:
        ok, why = contract_valid(c, on, s.odometer_km, s.system, check_scope=c["contract_type"].startswith("COMPONENT"))
        if ok:
            return [_r("coverage", "Coverage (time / km)", "pass", why, side, "R01", source="public", source_url=url,
                       data={"contract_type": c["contract_type"]})]
        reasons.append(why)
    return [_r("coverage", "Coverage (time / km)", "fail", "; ".join(reasons), side, "R01", source="public",
               source_url=url, fix="escalate: out of coverage — consider goodwill request")]


def r_window(s, ctx, pack, cfg, side):
    days = int(cfg.get("days", 30))
    rem = window_remaining_days(s.wip_closed_at, ctx.now, days)
    if rem < 0:
        return [_r("submission_window", "Submission window", "fail",
                   f"{-rem:.1f} days past the {days}-day window", side, "R02", cfg=cfg,
                   fix="escalate: window-extension request")]
    if rem <= 3:
        return [_r("submission_window", "Submission window", "warn", f"{rem:.1f} of {days} days remaining — priority",
                   side, "R02", cfg=cfg, data={"remaining_days": rem})]
    return [_r("submission_window", "Submission window", "pass", f"{rem:.1f} of {days} days remaining", side, "R02",
               cfg=cfg, data={"remaining_days": rem})]


def r_rate_cap(s, ctx, pack, cfg, side):
    cap = float(cfg.get("aed", 10_000))
    over = [ln for ln in s.lines if ln.labour_rate > cap + 0.001]
    if over:
        return [_r("labour_rate_cap", "Labour rate cap", "fail",
                   f"rate {over[0].labour_rate:.0f} > cap {cap:.0f} AED/hr on {over[0].labour_op}", side, "R03",
                   fixable=True, fix=f"cap labour rate at {cap:.0f}", cfg=cfg)]
    return [_r("labour_rate_cap", "Labour rate cap", "pass", f"all lines ≤ AED {cap:.0f}/hr", side, "R03", cfg=cfg)]


def _std(ln, ctx) -> float | None:
    cat = ctx.catalog.get(ln.labour_op)
    if cat:
        return float(cat["standard_hours"])
    return ln.standard_hours


def over_tolerance_lines(s: RuleSubject, ctx: RuleContext, pct: float) -> list[tuple[Any, float, float]]:
    out = []
    for ln in s.lines:
        std = _std(ln, ctx)
        if std and ln.hours_claimed > std * (1 + pct / 100) + 1e-6:
            out.append((ln, std, (ln.hours_claimed / std - 1) * 100))
    return out


def r_labour(s, ctx, pack, cfg, side):
    pct = float(cfg.get("pct", 10))
    res: list[RuleResult] = []
    actual_over = [ln for ln in s.lines if ln.hours_actual is not None and ln.hours_claimed > ln.hours_actual + 1e-6]
    if actual_over:
        res.append(_r("labour_tolerance", "Labour vs standard time", "fail",
                      f"{actual_over[0].labour_op}: claimed {actual_over[0].hours_claimed} h > actual {actual_over[0].hours_actual} h",
                      side, "R03", fixable=True, fix="reduce hours to actual", cfg=cfg))
        return res
    over = over_tolerance_lines(s, ctx, pct)
    if not over:
        return [_r("labour_tolerance", "Labour vs standard time", "pass", f"all lines within {pct:.0f}% of standard",
                   side, "R03", cfg=cfg)]
    ln, std, by = over[0]
    detail = f"{ln.labour_op}: {ln.hours_claimed} h vs standard {std} h (+{by:.0f}%, tolerance {pct:.0f}%)"
    data = {"labour_op": ln.labour_op, "claimed": ln.hours_claimed, "standard": std, "over_pct": round(by, 1)}
    if s.labour_justification:
        return [_r("labour_tolerance", "Labour vs standard time", "pass", detail + " — justification on file", side,
                   "R03", cfg=cfg, data=data)]
    if side == "gateway" or cfg.get("requires_tech_note"):
        return [_r("labour_tolerance", "Labour vs standard time", "fail", detail + " — no justification", side, "R03",
                   fixable=True, fix="add technician justification for the extra time", cfg=cfg, data=data)]
    return [_r("labour_tolerance", "Labour vs standard time", "warn",
               detail + " — OEM may reject without justification", side, "R03", fixable=True,
               fix="add technician justification for the extra time", cfg=cfg, data=data)]


def r_evidence(s, ctx, pack, cfg, side):
    have = set(s.evidence_kinds)
    missing: list[str] = []
    needed: list[str] = []
    for item in cfg.get("items", []):
        if cond(item.get("when"), s):
            for k in item.get("kinds", []):
                needed.append(k)
                if k not in have:
                    missing.append(k)
    if not needed:
        return [_r("required_evidence", "Required evidence", "pass", "no programme evidence required for this job",
                   side, "R04", cfg=cfg)]
    if missing:
        return [_r("required_evidence", "Required evidence", "fail",
                   "missing: " + ", ".join(EVIDENCE_LABELS.get(k, k) for k in missing), side, "R04", fixable=True,
                   fix="ask the workshop for " + ", ".join(EVIDENCE_LABELS.get(k, k) for k in missing), cfg=cfg,
                   data={"missing": missing})]
    return [_r("required_evidence", "Required evidence", "pass",
               "attached: " + ", ".join(EVIDENCE_LABELS.get(k, k) for k in sorted(set(needed))), side, "R04", cfg=cfg)]


TEST_WORDS = ("TEST", "VERIFIED", "CONFIRMED", "CHECKED OK", "NO FAULT", "CLEARED", "OPERATES CORRECTLY")


def r_narrative(s, ctx, pack, cfg, side):
    mn = int(cfg.get("min_chars", 20))
    problems, missing = [], []
    for k in ("complaint", "cause", "correction"):
        if len(getattr(s, k).strip()) < mn:
            problems.append(f"{k} too short")
            missing.append(k)
    if cfg.get("cause_names_causal_part") and s.causal_part_desc:
        kw = keyword(s.causal_part_desc)
        if kw not in s.cause.lower() and s.causal_part_no.lower() not in s.cause.lower():
            problems.append(f"cause does not name the causal part ({s.causal_part_desc})")
            missing.append("cause_part")
    if cfg.get("require_test_result") and not any(w in s.correction.upper() for w in TEST_WORDS):
        problems.append("correction has no test result")
        missing.append("test_result")
    if cond(cfg.get("require_support_ref_when"), s) and not s.tech_support_ref:
        problems.append("technical support reference missing")
        missing.append("tech_support_ref")
    if cond(cfg.get("require_hv_tech_id_when"), s) and not s.hv_tech_id:
        problems.append("HV-certified technician ID missing")
        missing.append("hv_tech_id")
    if problems:
        return [_r("narrative_3c", "3C narrative", "fail", "; ".join(problems), side, "R05", fixable=True,
                   fix="rewrite 3C from workshop facts", cfg=cfg, data={"missing": missing})]
    return [_r("narrative_3c", "3C narrative", "pass", "complaint, cause and correction complete", side, "R05", cfg=cfg)]


def r_causal(s, ctx, pack, cfg, side):
    claim_parts = {p.part_no for ln in s.lines for p in ln.parts}
    if s.causal_part_no not in claim_parts:
        return [_r("causal_part", "Causal part", "fail", f"causal part {s.causal_part_no} not on any claim line", side,
                   "R06", fixable=True, fix="select causal part from WIP lines")]
    if ctx.wip_part_nos is not None and s.causal_part_no not in ctx.wip_part_nos:
        return [_r("causal_part", "Causal part", "fail", f"causal part {s.causal_part_no} is not on the WIP", side,
                   "R06", fixable=True, fix="select causal part from WIP lines")]
    line = next(ln for ln in s.lines if any(p.part_no == s.causal_part_no for p in ln.parts))
    if line.is_related_damage:
        return [_r("causal_part", "Causal part", "fail", "causal part sits on a related-damage line", side, "R06",
                   fixable=True, fix="move causal part to the primary repair line")]
    return [_r("causal_part", "Causal part", "pass", f"{s.causal_part_no} on {line.labour_op}", side, "R06")]


def r_duplicate(s, ctx, pack, cfg, side):
    dup = find_duplicate(ctx.prior_claims, s.vin, s.ops, s.wip_closed_at, s.claim_ref)
    if dup:
        return [_r("duplicate", "Duplicate check", "fail", f"same VIN + op as {dup['claim_ref']} within 7 days", side,
                   "R07", fix="withdraw", data={"duplicate_of": dup["claim_ref"]})]
    return [_r("duplicate", "Duplicate check", "pass", "no claim on same VIN + op within 7 days", side, "R07")]


def r_preauth(s, ctx, pack, cfg, side):
    if s.claim_type == "CAMPAIGN" or not cond(cfg.get("when"), s):
        return [_r("pre_authorisation", "Pre-authorisation", "pass", f"not required ({cfg.get('label', '')})", side,
                   "R08", cfg=cfg)]
    if not s.pre_auth_ref:
        return [_r("pre_authorisation", "Pre-authorisation", "fail", f"required ({cfg.get('label', '')}) — none on file",
                   side, "R08", fix="request pre-authorisation (human approval)", cfg=cfg)]
    if ctx.valid_auth_refs is not None and s.pre_auth_ref not in ctx.valid_auth_refs:
        return [_r("pre_authorisation", "Pre-authorisation", "fail", f"{s.pre_auth_ref} not found in OEM records", side,
                   "R08", cfg=cfg)]
    return [_r("pre_authorisation", "Pre-authorisation", "pass", f"{s.pre_auth_ref} on file", side, "R08", cfg=cfg)]


def _repeat(s, history: list[dict], days: int) -> dict | None:
    on = as_date(s.repair_date)
    for h in history:
        if h.get("vin", s.vin) != s.vin or h.get("claim_ref") == s.claim_ref:
            continue
        if h.get("system") != s.system:
            continue
        d = as_date(h.get("repair_date") or h.get("closed_at"))
        if 0 < (on - d).days <= days:
            return {**h, "days": (on - d).days}
    return None


def r_repeat(s, ctx, pack, cfg, side):
    days, needs = int(cfg.get("days", 30)), cfg.get("needs", "flag")
    hit = _repeat(s, ctx.prior_claims, days)
    if not hit:
        return [_r("repeat_repair", "Repeat repair", "pass", f"no {s.system} repair in prior {days} days", side, "R08",
                   cfg=cfg)]
    detail = f"previous {s.system} repair {hit['days']} days earlier ({hit.get('claim_ref', 'history')})"
    if needs == "ta_case" and not s.tech_support_ref:
        return [_r("repeat_repair", "Repeat repair", "fail", detail + " — TA case required", side, "R08", cfg=cfg,
                   fix="request Technical Assistance case")]
    if needs == "pre_auth" and not s.pre_auth_ref:
        return [_r("repeat_repair", "Repeat repair", "fail", detail + " — PA required", side, "R08", cfg=cfg,
                   fix="request pre-authorisation")]
    return [_r("repeat_repair", "Repeat repair", "warn", detail, side, "R08", cfg=cfg)]


def r_campaign(s, ctx, pack, cfg, side):
    cp = matching_campaign(ctx.campaigns, s.vin, s.ops)
    if cp and s.claim_type != "CAMPAIGN":
        return [_r("campaign", "Open campaign", "fail", f"VIN is in open campaign {cp['campaign_code']} ({cp['title']})",
                   side, "R10", fixable=True, fix=f"re-type claim as CAMPAIGN {cp['campaign_code']}",
                   data={"campaign_code": cp["campaign_code"]})]
    if s.claim_type == "CAMPAIGN" and (not cp or cp["campaign_code"] != s.campaign_code):
        return [_r("campaign", "Open campaign", "fail", f"campaign {s.campaign_code} does not apply to this VIN/op", side,
                   "R10")]
    if cp:
        return [_r("campaign", "Open campaign", "pass", f"filed under campaign {cp['campaign_code']}", side, "R10")]
    return [_r("campaign", "Open campaign", "pass", "no open campaign for VIN + op", side, "R10")]


def r_maintenance(s, ctx, pack, cfg, side):
    on = as_date(s.repair_date)
    applicable = [c for c in contracts_for(s.claim_type, ctx.contracts, s.contract_type)
                  if contract_valid(c, on, s.odometer_km)[0]]
    for c in applicable:
        cond_ = c.get("conditions") or {}
        if cond_.get("requires_periodic_service"):
            gaps = service_gaps(ctx.service_history, as_date(ctx.vehicle["warranty_start_date"]), on,
                                int(cond_.get("service_interval_km", 10000)), int(cond_.get("service_interval_months", 6)))
            if gaps:
                return [_r("maintenance", "Maintenance compliance", "fail",
                           f"{c['contract_type']} requires all periodic services: " + "; ".join(gaps), side, "R11",
                           source="public", fix="escalate: maintenance non-compliance")]
            return [_r("maintenance", "Maintenance compliance", "pass",
                       f"{c['contract_type']} service conditions met", side, "R11", source="public")]
    return [_r("maintenance", "Maintenance compliance", "pass", "no service-history condition on this contract", side,
               "R11")]


def r_mods(s, ctx, pack, cfg, side):
    bad = conflicting_mods(ctx.vehicle.get("aftermarket_mods") or [], s.system)
    if bad:
        return [_r("aftermarket", "Aftermarket modifications", "fail",
                   f"{', '.join(bad)} fitted — affects {s.system}", side, "R12", fix="escalate: misuse / modification")]
    return [_r("aftermarket", "Aftermarket modifications", "pass", "no modification affecting this system", side, "R12")]


def r_retention(s, ctx, pack, cfg, side):
    from datetime import timedelta
    until = as_date(s.repair_date) + timedelta(days=int(cfg.get("days", 60)))
    return [_r("parts_retention", "Parts retention", "pass",
               f"retain {s.causal_part_no} until {until.isoformat()} ({cfg.get('days', 60)} days)", side, "R13", cfg=cfg)]


# ---- gateway-only checks -----------------------------------------------------------------

def g_causal_family(s, ctx, pack, cfg, side):
    line = next((ln for ln in s.lines if any(p.part_no == s.causal_part_no for p in ln.parts)), None)
    fams = (ctx.catalog.get(line.labour_op, {}) if line else {}).get("causal_families") or []
    if line and fams and not any(f"-{f}-" in s.causal_part_no for f in fams):
        return [_r("causal_part_family", cfg["label"], "fail",
                   f"{line.labour_op} expects causal part family {'/'.join(fams)}; claimed {s.causal_part_no}", side,
                   cfg["code"], fixable=True)]
    return [_r("causal_part_family", cfg["label"], "pass", "causal part family consistent", side, cfg["code"])]


def g_labour_evidence(s, ctx, pack, cfg, side):
    pct = float(pack.rule("labour_tolerance").get("pct", 10))
    over = over_tolerance_lines(s, ctx, pct)
    if over and (not s.labour_justification or "photo" not in s.evidence_kinds):
        ln, std, by = over[0]
        return [_r("labour_evidence", cfg["label"], "fail",
                   f"{ln.labour_op} +{by:.0f}% over standard without justification + photo", side, cfg["code"],
                   fixable=True, data={"labour_op": ln.labour_op, "standard": std, "claimed": ln.hours_claimed})]
    return [_r("labour_evidence", cfg["label"], "pass", "labour supported", side, cfg["code"])]


def g_repeat(s, ctx, pack, cfg, side):
    hit = _repeat(s, ctx.repair_history, int(cfg.get("days", 30)))
    if hit and not (s.tech_support_ref and (ctx.valid_auth_refs is None or s.tech_support_ref in ctx.valid_auth_refs)):
        return [_r("oem_repeat_repair", cfg["label"], "fail",
                   f"{s.system} repaired {hit['days']} days earlier at {hit.get('retailer', 'another retailer')}", side,
                   cfg["code"], data={"previous": hit})]
    return [_r("oem_repeat_repair", cfg["label"], "pass", "no repeat repair in OEM history", side, cfg["code"])]


def g_scope(s, ctx, pack, cfg, side):
    on = as_date(s.repair_date)
    for c in contracts_for(s.claim_type, ctx.contracts, s.contract_type):
        ok, _ = contract_valid(c, on, s.odometer_km)
        if ok:
            scope = c.get("component_scope") or []
            if scope and s.system not in scope:
                return [_r("contract_scope", cfg["label"], "fail",
                           f"{s.system} not in {c['contract_type']} scope ({', '.join(scope)})", side, cfg["code"])]
            return [_r("contract_scope", cfg["label"], "pass", f"{s.system} within {c['contract_type']} scope", side,
                       cfg["code"])]
    return [_r("contract_scope", cfg["label"], "pass", "scope not applicable", side, cfg["code"])]


def g_leak_photo(s, ctx, pack, cfg, side):
    if names(s)["is_leak"] and "photo" not in s.evidence_kinds:
        return [_r("leak_photo", cfg["label"], "fail", "leak/seal repair without a photo of the leak", side, cfg["code"],
                   fixable=True, data={"missing": ["photo"]})]
    return [_r("leak_photo", cfg["label"], "pass", "leak evidence present or not applicable", side, cfg["code"])]


def g_serials(s, ctx, pack, cfg, side):
    if s.system == "HV_BATTERY":
        bad = [p.part_no for ln in s.lines for p in ln.parts if "-MOD-" in p.part_no and not p.serial]
        if bad:
            return [_r("battery_module_serials", cfg["label"], "fail", f"no serial for {', '.join(bad)}", side,
                       cfg["code"], fixable=True)]
    return [_r("battery_module_serials", cfg["label"], "pass", "module serials present or not applicable", side,
               cfg["code"])]


DEALER_RULES: list[tuple[str, RuleFn]] = [
    ("coverage", r_coverage), ("submission_window", r_window), ("labour_rate_cap", r_rate_cap),
    ("labour_tolerance", r_labour), ("required_evidence", r_evidence), ("narrative_3c", r_narrative),
    ("causal_part", r_causal), ("duplicate", r_duplicate), ("pre_authorisation", r_preauth),
    ("repeat_repair", r_repeat), ("campaign", r_campaign), ("maintenance", r_maintenance), ("aftermarket", r_mods),
    ("parts_retention", r_retention),
]
GATEWAY_CHECKS: dict[str, RuleFn] = {
    "causal_part_family": g_causal_family, "labour_evidence": g_labour_evidence, "oem_repeat_repair": g_repeat,
    "contract_scope": g_scope, "leak_photo": g_leak_photo, "battery_module_serials": g_serials,
}


def evaluate(subject: RuleSubject, ctx: RuleContext, side: Side = "dealer") -> list[RuleResult]:
    pack = load_pack(subject.programme)
    if pack.status != "active":
        return [RuleResult(rule_key="pack", label="Rule pack", result="fail", detail="rules pending for this programme",
                           side=side)]
    results: list[RuleResult] = []
    for key, fn in DEALER_RULES:
        results.extend(fn(subject, ctx, pack, pack.rule(key), side))
    if side == "gateway":
        for chk in pack.gateway_only_checks:
            results.extend(GATEWAY_CHECKS[chk["rule"]](subject, ctx, pack, chk, side))
    return results


def failing_codes(results: list[RuleResult]) -> list[str]:
    seen: list[str] = []
    for r in results:
        if r.result == "fail" and r.code and r.code not in seen:
            seen.append(r.code)
    return seen


def needs_escalation(codes: list[str]) -> list[str]:
    return [c for c in codes if c in MANDATORY_ESCALATION_CODES]
