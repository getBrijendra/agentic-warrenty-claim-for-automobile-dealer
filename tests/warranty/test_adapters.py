"""Payload adapters: canonical → OEM payload validates against the OEM schema; payload → subject round-trips."""
from __future__ import annotations

import pytest

from wc_oem.adapters.registry import ADAPTERS, adapter_for
from wc_rules.model import RuleSubject

from .conftest import claim_for, seed

WIP = {"FORD": "48201", "JLR": "48214", "FERRARI": "48219", "MASERATI": "48206", "VINFAST": "48210", "FORD_TRUCKS": "48222"}
DEALER = {"FORD": "FD10421", "JLR": "JL2207", "FERRARI": "FE118", "MASERATI": "MS4410", "VINFAST": "VF052", "FORD_TRUCKS": "FT3307"}


@pytest.mark.parametrize("prog", list(WIP))
def test_payload_valid_and_round_trips(prog):
    c = claim_for(WIP[prog])
    a = adapter_for(prog)
    p = a.build(c, DEALER[prog])
    assert a.validate(p) == []
    cat = {x["op_code"]: x for x in seed()["oem_side"]["labour_catalog"]}
    s = a.to_subject(p, cat)
    ref = RuleSubject.from_canonical(c)
    for f in ("vin", "odometer_km", "claim_type", "causal_part_no", "system", "total", "pre_auth_ref"):
        assert getattr(s, f) == getattr(ref, f), f
    assert s.ops == ref.ops
    assert abs(s.amount_ex_vat - ref.amount_ex_vat) < 0.01


@pytest.mark.parametrize("prog", list(WIP))
def test_shape_errors_are_field_level(prog):
    a = adapter_for(prog)
    p = a.build(claim_for(WIP[prog]), DEALER[prog])
    p["vin"] = "SHORT"
    p["unexpected_field"] = 1
    errs = a.validate(p)
    fields = {e["field"] for e in errs}
    assert "vin" in fields and "unexpected_field" in fields


def test_formats_differ_per_oem():
    shapes = {p: set(adapter_for(p).build(claim_for(w), DEALER[p])) for p, w in WIP.items()}
    assert "repair_sections" in shapes["JLR"] and "lines" not in shapes["JLR"]
    assert "section" in shapes["FERRARI"] and "service_entry_ref" in shapes["FERRARI"]
    assert "battery_modules" in shapes["VINFAST"]
    assert "chassis_ref" in shapes["FORD_TRUCKS"]
    assert "cost_code" in shapes["MASERATI"]
    assert "claim_type_code" in shapes["FORD"]


def test_all_active_programmes_have_adapters():
    assert set(ADAPTERS) == set(WIP)
