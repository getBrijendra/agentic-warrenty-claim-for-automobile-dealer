"""Orchestrator transition table + hard guardrails (pure parts, no DB)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from wc_agents.guardrails import GuardrailViolation, apply_patch, validate_against_dms
from wc_core.vocab import TERMINAL_STATUSES

from .conftest import claim_for, ro_for, seed


def transitions():
    import importlib
    return importlib.import_module("orchestrator.transitions").ALLOWED


def test_terminal_states_have_no_exits():
    allowed = transitions()
    for s in TERMINAL_STATUSES:
        assert allowed[s] == set()


@pytest.mark.parametrize("path", [
    ["wip_closed", "triage", "building", "checking", "ready", "submitted", "accepted", "paid"],
    ["wip_closed", "triage", "building", "checking", "ready", "submitted", "rejected", "resolving", "resubmitted", "accepted", "paid"],
    ["wip_closed", "triage", "escalated", "building", "checking", "ready", "submitted"],
    ["wip_closed", "triage", "withdrawn"],
    ["submitted", "rejected", "resolving", "building"],
    ["checking", "blocked", "escalated", "checking"],
    ["escalated", "written_off"],
])
def test_lifecycle_paths_allowed(path):
    allowed = transitions()
    for a, b in zip(path, path[1:]):
        assert b in allowed[a], f"{a} → {b}"


@pytest.mark.parametrize("a,b", [("wip_closed", "submitted"), ("checking", "submitted"), ("paid", "rejected"),
                                 ("rejected", "submitted"), ("building", "ready")])
def test_illegal_transitions(a, b):
    assert b not in transitions()[a]


def _atts(wip):
    ro = ro_for(wip)
    return [a for a in seed()["attachments"] if a["repair_order_id"] == ro["id"]]


def test_valid_claim_passes_guardrails():
    c = claim_for("48201")
    assert validate_against_dms(c, ro_for("48201"), _atts("48201"), []) == []


def test_cannot_raise_hours_above_actual():
    c = claim_for("48201")
    c2 = apply_patch(c, {"hours": {1: 9.5}}, _atts("48201"))
    errs = validate_against_dms(c2, ro_for("48201"), _atts("48201"), [])
    assert any("hours claimed" in e for e in errs)


def test_cannot_change_odometer_or_dates():
    with pytest.raises(GuardrailViolation):
        apply_patch(claim_for("48201"), {"odometer_km": 1000}, _atts("48201"))
    c = claim_for("48201").model_copy(update={"odometer_km": 1000})
    assert any("odometer" in e for e in validate_against_dms(c, ro_for("48201"), _atts("48201"), []))


def test_cannot_add_parts_not_on_wip():
    c = claim_for("48201")
    ln = c.lines[0]
    bogus = ln.parts[0].model_copy(update={"part_no": "FD-P-XXX-0001", "wip_line_no": 99})
    c2 = c.model_copy(update={"lines": [ln.model_copy(update={"parts": ln.parts + [bogus]})]})
    assert any("not on the WIP" in e for e in validate_against_dms(c2, ro_for("48201"), _atts("48201"), []))


def test_cannot_fabricate_evidence_or_refs():
    with pytest.raises(GuardrailViolation):
        apply_patch(claim_for("48201"), {"evidence_add": ["ATT-FAKE-1"]}, _atts("48201"))
    c = claim_for("48214").model_copy(update={"diagnostic_session_id": "JLR-DS-INVENTED"})
    assert any("session" in e for e in validate_against_dms(c, ro_for("48214"), _atts("48214"), []))
    c = claim_for("48219").model_copy(update={"pre_auth_ref": "FE-PA-99999"})
    assert any("pre-authorisation" in e for e in validate_against_dms(c, ro_for("48219"), _atts("48219"), []))
    ok = claim_for("48219").model_copy(update={"pre_auth_ref": "FE-PA-12345"})
    assert validate_against_dms(ok, ro_for("48219"), _atts("48219"), ["FE-PA-12345"]) == []


def test_causal_part_patch_must_exist_on_claim():
    c = apply_patch(claim_for("48203"), {"causal_part_no": "FD-P-ICL-7781"}, _atts("48203"))
    assert c.causal_part.part_no == "FD-P-ICL-7781"
    with pytest.raises(GuardrailViolation):
        apply_patch(claim_for("48203"), {"causal_part_no": "FD-P-NOP-0000"}, _atts("48203"))


def test_totals_include_vat():
    c = claim_for("48201")
    assert c.totals.vat == (c.totals.subtotal * Decimal("0.05")).quantize(Decimal("0.01"))
