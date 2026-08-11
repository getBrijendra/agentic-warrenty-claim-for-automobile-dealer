"""Rules engine: one test per rule per programme (pure functions, no I/O)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from wc_rules.engine import evaluate, failing_codes
from wc_rules.model import RuleSubject

from .conftest import claim_for, ctx_for, ro_for

# one representative clean WIP per programme
CLEAN = {"FORD": "48205", "JLR": "48217", "FERRARI": "48218", "MASERATI": "48207", "VINFAST": "48212", "FORD_TRUCKS": "48223"}


def run(wip, side="dealer", ctx_over=None, **claim_over):
    c = claim_for(wip, **claim_over)
    if wip == "48205":  # weak narrative on purpose; give it a full 3C for "clean" tests
        c = c.model_copy(update={"cause": "Auxiliary cooling fan assembly motor bearing worn. Causal part: Auxiliary cooling fan assembly (FD-P-FAN-2290).",
                                 "correction": "Replaced fan. Road test 10 km OK."})
    return {r.rule_key: r for r in evaluate(RuleSubject.from_canonical(c), ctx_for(wip, side=side, **(ctx_over or {})), side)}


def by(results, key):
    return results[key].result


@pytest.mark.parametrize("prog,wip", CLEAN.items())
def test_clean_claim_has_no_fail(prog, wip):
    r = run(wip)
    assert [k for k, v in r.items() if v.result == "fail"] == [], prog


@pytest.mark.parametrize("prog,wip", CLEAN.items())
def test_submission_window(prog, wip):
    ro = ro_for(wip)
    from datetime import datetime
    closed = datetime.fromisoformat(ro["closed_at"])
    from wc_rules.packs import load_pack
    days = load_pack(prog).window_days
    assert by(run(wip, ctx_over={"now": closed + timedelta(days=1)}), "submission_window") == "pass"
    assert by(run(wip, ctx_over={"now": closed + timedelta(days=days - 1)}), "submission_window") == "warn"
    late = run(wip, ctx_over={"now": closed + timedelta(days=days + 1)})["submission_window"]
    assert late.result == "fail" and late.code == "R02"


@pytest.mark.parametrize("prog,wip", CLEAN.items())
def test_preauth_threshold(prog, wip):
    if prog == "VINFAST":  # VinFast PA is by system (all HV battery), not amount
        wip = "48211"
    c = claim_for(wip).model_copy(update={"pre_auth_ref": None})
    big = c.model_copy(update={"totals": c.totals.model_copy(update={"subtotal": c.totals.subtotal + 20000})})
    res = {r.rule_key: r for r in evaluate(RuleSubject.from_canonical(big), ctx_for(wip), "dealer")}
    assert res["pre_authorisation"].result == "fail" and res["pre_authorisation"].code == "R08"


@pytest.mark.parametrize("prog,wip", CLEAN.items())
def test_labour_tolerance(prog, wip):
    from wc_rules.packs import load_pack
    c = claim_for(wip)
    ln = c.lines[0]
    pct = load_pack(prog).rule("labour_tolerance")["pct"]
    from decimal import Decimal
    over = Decimal(str(round(float(ln.standard_hours) * (1 + pct / 100 + 0.3), 2)))
    c2 = c.model_copy(update={"lines": [ln.model_copy(update={"hours_claimed": over, "hours_actual": over})] + c.lines[1:]})
    res = {r.rule_key: r for r in evaluate(RuleSubject.from_canonical(c2), ctx_for(wip), "dealer")}
    expected = "fail" if load_pack(prog).rule("labour_tolerance").get("requires_tech_note") else "warn"
    assert res["labour_tolerance"].result == expected
    gw = {r.rule_key: r for r in evaluate(RuleSubject.from_canonical(c2), ctx_for(wip, side="gateway"), "gateway")}
    assert gw["labour_tolerance"].result == "fail"
    c3 = c2.model_copy(update={"labour_justification": "seized bolts"})
    res3 = {r.rule_key: r for r in evaluate(RuleSubject.from_canonical(c3), ctx_for(wip), "dealer")}
    assert res3["labour_tolerance"].result == "pass"


@pytest.mark.parametrize("prog,wip", CLEAN.items())
def test_labour_rate_cap(prog, wip):
    c = claim_for(wip)
    c2 = c.model_copy(update={"lines": [c.lines[0].model_copy(update={"labour_rate": 5000})] + c.lines[1:]})
    res = {r.rule_key: r for r in evaluate(RuleSubject.from_canonical(c2), ctx_for(wip), "dealer")}
    assert res["labour_rate_cap"].result == "fail"


@pytest.mark.parametrize("prog,wip", CLEAN.items())
def test_narrative(prog, wip):
    c = claim_for(wip).model_copy(update={"cause": "ok"})
    res = {r.rule_key: r for r in evaluate(RuleSubject.from_canonical(c), ctx_for(wip), "dealer")}
    assert res["narrative_3c"].result == "fail" and res["narrative_3c"].code == "R05"


@pytest.mark.parametrize("prog,wip", CLEAN.items())
def test_causal_part_must_be_on_wip(prog, wip):
    c = claim_for(wip)
    res = {r.rule_key: r for r in evaluate(RuleSubject.from_canonical(c), ctx_for(wip, wip_part_nos=["X"]), "dealer")}
    assert res["causal_part"].result == "fail" and res["causal_part"].code == "R06"


@pytest.mark.parametrize("prog,wip", CLEAN.items())
def test_aftermarket_mod(prog, wip):
    c = claim_for(wip)
    veh = {**ctx_for(wip).vehicle, "aftermarket_mods": ["lift_kit", "engine_remap", "aftermarket_head_unit"]}
    res = {r.rule_key: r for r in evaluate(RuleSubject.from_canonical(c), ctx_for(wip, vehicle=veh), "dealer")}
    sys_ = c.system
    affected = sys_ in {"SUSPENSION", "DRIVELINE", "STEERING", "ENGINE", "TURBO", "TRANSMISSION", "INFOTAINMENT", "ELECTRICAL"}
    assert res["aftermarket"].result == ("fail" if affected else "pass")


@pytest.mark.parametrize("prog,wip", CLEAN.items())
def test_duplicate(prog, wip):
    c = claim_for(wip)
    dup = [{"claim_ref": "X1", "vin": c.vin, "labour_ops": [c.lines[0].labour_op], "closed_at": c.wip_closed_at.isoformat(),
            "status": "submitted", "system": c.system}]
    res = {r.rule_key: r for r in evaluate(RuleSubject.from_canonical(c), ctx_for(wip, prior_claims=dup), "dealer")}
    assert res["duplicate"].result == "fail" and res["duplicate"].code == "R07"


@pytest.mark.parametrize("prog,wip", CLEAN.items())
def test_campaign(prog, wip):
    c = claim_for(wip)
    cp = [{"campaign_code": "X-CMP-1", "title": "t", "affected_vins": [c.vin], "labour_op": c.lines[0].labour_op, "status": "open"}]
    res = {r.rule_key: r for r in evaluate(RuleSubject.from_canonical(c), ctx_for(wip, campaigns=cp), "dealer")}
    assert res["campaign"].result == "fail" and res["campaign"].fixable


# ---- issue-specific showcases ------------------------------------------------------
def test_ford_km_exceeded_r01():
    assert failing_codes(evaluate(RuleSubject.from_canonical(claim_for("48202", claim_type="BASE")), ctx_for("48202"))) [0] == "R01"


def test_ferrari_unlimited_km_passes():
    assert run("48218")["coverage"].result == "pass"


def test_vinfast_battery_component_term():
    c = claim_for("48210")
    assert c.claim_type == "COMPONENT"
    assert run("48210")["coverage"].result == "pass"
    base = run("48210", claim_type="BASE")["coverage"]
    assert base.result == "fail" and "200,000" in base.detail


def test_premiumcare_missed_service_r11():
    r = run("48204")
    assert r["maintenance"].result == "fail" and r["maintenance"].code == "R11"


def test_jlr_missing_diag_session_r04():
    r = run("48214")
    assert r["required_evidence"].result == "fail" and "diagnostic_session_id" in r["required_evidence"].data["missing"]


def test_maserati_scope_is_gateway_only():
    assert run("48206")["coverage"].result == "pass"
    gw = run("48206", side="gateway")
    assert gw["contract_scope"].result == "fail" and gw["contract_scope"].code == "R09"


def test_jlr_repeat_repair_gateway_only():
    assert run("48215")["repeat_repair"].result == "pass"
    assert run("48215", side="gateway")["oem_repeat_repair"].result == "fail"


def test_ford_causal_family_gateway_only():
    assert run("48203")["causal_part"].result == "pass"
    assert run("48203", side="gateway")["causal_part_family"].result == "fail"


def test_ford_trucks_leak_photo_gateway_only():
    assert run("48222")["required_evidence"].result == "pass"
    assert run("48222", side="gateway")["leak_photo"].result == "fail"


def test_ford_trucks_labour_within_20pct():
    assert run("48224")["labour_tolerance"].result == "pass"


def test_ferrari_powertrain_needs_preauth():
    assert run("48219")["pre_authorisation"].result == "fail"


def test_vinfast_hv_needs_soh_report():
    r = run("48211")
    assert r["required_evidence"].result == "fail" and "battery_soh_report" in r["required_evidence"].data["missing"]


def test_defender_lift_kit_r12():
    assert run("48216")["aftermarket"].code == "R12" and run("48216")["aftermarket"].result == "fail"


def test_defender_campaign_r10():
    r = run("48213")
    assert r["campaign"].result == "fail"
    assert run("48213", claim_type="CAMPAIGN", campaign_code="JL-CMP-26-014")["campaign"].result == "pass"


def test_every_rule_has_source_badge():
    for r in run("48201").values():
        assert r.source in ("public", "illustrative")
