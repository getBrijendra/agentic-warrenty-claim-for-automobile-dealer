"""VINFAST: lines + battery module serials, HV technician ID (illustrative shape)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from wc_core.canonical import CanonicalClaim

from .base import Adapter, Attachment, Narrative, PayTotals, Strict, attachments, narrative, sline, subject, totals

KIND = {"BASE": "VEHICLE", "COMPONENT": "BATTERY", "MAINTENANCE_PROGRAMME": "MAINTENANCE", "CAMPAIGN": "CAMPAIGN",
        "GOODWILL": "GOODWILL", "EXTENDED": "VEHICLE", "PARTS": "PARTS"}
RKIND = {"VEHICLE": ("BASE", "BASE"), "BATTERY": ("COMPONENT", "COMPONENT_BATTERY"),
         "MAINTENANCE": ("MAINTENANCE_PROGRAMME", None), "CAMPAIGN": ("CAMPAIGN", None), "GOODWILL": ("GOODWILL", None),
         "PARTS": ("PARTS", None)}


class VfPart(Strict):
    part_no: str = Field(pattern=r"^VF-P-[A-Z]{3}-\d{4}$")
    description: str
    qty: float = Field(gt=0)
    unit_price: float = Field(ge=0)
    causal: bool = False
    module_serial: str | None = None


class VfLine(Strict):
    op_code: str = Field(pattern=r"^VF-[A-Z]{2}-\d{4}$")
    description: str
    hours: float = Field(gt=0, le=40)
    rate: float = Field(gt=0)
    related_damage: bool = False
    parts: list[VfPart] = []


class VinfastPayload(Strict):
    distributor_code: str = Field(pattern=r"^VF\d{3}$")
    claim_ref: str
    claim_kind: Literal["VEHICLE", "BATTERY", "MAINTENANCE", "CAMPAIGN", "GOODWILL", "PARTS"]
    campaign_code: str | None = None
    vin: str = Field(min_length=17, max_length=17)
    ro_number: str
    odometer_km: int = Field(ge=0)
    repair_date: date
    closed_at: datetime
    condition_code: str = Field(pattern=r"^VFC-[A-Z]{2}-\d{2}$")
    lines: list[VfLine] = Field(min_length=1)
    battery_modules: list[str] = []
    hv_technician_id: str | None = None
    pre_approval_ref: str | None = None
    narrative: Narrative
    labour_justification: str | None = None
    dtc_codes: list[str] = []
    attachments: list[Attachment] = []
    totals: PayTotals


def build(c: CanonicalClaim, dealer_code: str) -> dict:
    return {
        "distributor_code": dealer_code, "claim_ref": c.claim_ref, "claim_kind": KIND[c.claim_type],
        "campaign_code": c.campaign_code, "vin": c.vin, "ro_number": c.wip_no, "odometer_km": c.odometer_km,
        "repair_date": c.repair_date.isoformat(), "closed_at": c.wip_closed_at.isoformat(),
        "condition_code": c.condition_code,
        "lines": [{"op_code": ln.labour_op, "description": ln.description, "hours": float(ln.hours_claimed),
                   "rate": float(ln.labour_rate), "related_damage": ln.is_related_damage,
                   "parts": [{"part_no": p.part_no, "description": p.description, "qty": float(p.qty),
                              "unit_price": float(p.unit_price), "causal": p.part_no == c.causal_part.part_no,
                              "module_serial": p.serial} for p in ln.parts]} for ln in c.lines],
        "battery_modules": [p.serial for ln in c.lines for p in ln.parts if p.serial],
        "hv_technician_id": c.hv_tech_id, "pre_approval_ref": c.pre_auth_ref, "narrative": narrative(c),
        "labour_justification": c.labour_justification, "dtc_codes": c.dtc_codes, "attachments": attachments(c),
        "totals": totals(c),
    }


def to_subject(p: dict, catalog: dict):
    lines = [sline(l["op_code"], l["description"], l["hours"], l["rate"], l["parts"], l["related_damage"])
             for l in p["lines"]]
    causal = next((x["part_no"] for l in p["lines"] for x in l["parts"] if x.get("causal")), "")
    ct, contract = RKIND[p["claim_kind"]]
    return subject("VINFAST", claim_ref=p["claim_ref"], claim_type=ct, contract_type=contract, vin=p["vin"],
                   odometer_km=p["odometer_km"], repair_date=p["repair_date"], closed_at=p["closed_at"], lines=lines,
                   narrative_=p["narrative"], attachments_=p["attachments"], totals_=p["totals"], catalog=catalog,
                   causal_part_no=causal, dtc_codes=p["dtc_codes"], pre_auth_ref=p.get("pre_approval_ref"),
                   hv_tech_id=p.get("hv_technician_id"), campaign_code=p.get("campaign_code"),
                   labour_justification=p.get("labour_justification"))


LAYOUT = {"style": "lines_with_modules", "title": "Warranty claim — lines with battery modules",
          "header": [["distributor_code", "Distributor code"], ["claim_kind", "Claim kind"], ["ro_number", "RO number"],
                     ["vin", "VIN"], ["odometer_km", "Odometer (km)"], ["condition_code", "Condition code"],
                     ["hv_technician_id", "HV technician"], ["pre_approval_ref", "Pre-approval"]],
          "lines_key": "lines", "line_cols": [["op_code", "Op code"], ["description", "Description"],
                                              ["hours", "Hours"], ["rate", "Rate"]],
          "list_fields": [["battery_modules", "Battery module serials"]],
          "extra": [["labour_justification", "Labour justification"]]}

ADAPTER = Adapter("VINFAST", VinfastPayload, build, to_subject, LAYOUT, lambda p: p["claim_ref"])
