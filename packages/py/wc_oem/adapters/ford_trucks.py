"""FORD_TRUCKS (F-MAX): lines + chassis/axle refs (illustrative shape)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from wc_core.canonical import CanonicalClaim

from .base import Adapter, Attachment, Narrative, PayTotals, Strict, attachments, narrative, sline, subject, totals

CT = {"BASE": "W", "EXTENDED": "X", "CAMPAIGN": "C", "GOODWILL": "G", "COMPONENT": "W", "MAINTENANCE_PROGRAMME": "M",
      "PARTS": "P"}
RCT = {"W": "BASE", "X": "EXTENDED", "C": "CAMPAIGN", "G": "GOODWILL", "M": "MAINTENANCE_PROGRAMME", "P": "PARTS"}


class FtPart(Strict):
    part_no: str = Field(pattern=r"^FT-P-[A-Z]{3}-\d{4}$")
    description: str
    qty: float = Field(gt=0)
    unit_price: float = Field(ge=0)
    causal: bool = False
    serial: str | None = None


class FtLine(Strict):
    lop: str = Field(pattern=r"^FT-[A-Z]{2}-\d{4}$")
    description: str
    hours: float = Field(gt=0, le=60)
    rate: float = Field(gt=0)
    axle_ref: str | None = None
    related_damage: bool = False
    parts: list[FtPart] = []


class FtPayload(Strict):
    dealer_code: str = Field(pattern=r"^FT\d{4}$")
    claim_ref: str
    claim_type: Literal["W", "X", "C", "G", "M", "P"]
    campaign_code: str | None = None
    vin: str = Field(min_length=17, max_length=17)
    chassis_ref: str = Field(min_length=8, max_length=8)
    ro_number: str
    repair_date: date
    closed_at: datetime
    odometer_km: int = Field(ge=0)
    condition_code: str = Field(pattern=r"^FTC-[A-Z]{2}-\d{2}$")
    lines: list[FtLine] = Field(min_length=1)
    narrative: Narrative
    labour_justification: str | None = None
    pre_approval_ref: str | None = None
    dtc_codes: list[str] = []
    attachments: list[Attachment] = []
    totals: PayTotals


def build(c: CanonicalClaim, dealer_code: str) -> dict:
    return {
        "dealer_code": dealer_code, "claim_ref": c.claim_ref, "claim_type": CT[c.claim_type],
        "campaign_code": c.campaign_code, "vin": c.vin, "chassis_ref": c.vin[-8:], "ro_number": c.wip_no,
        "repair_date": c.repair_date.isoformat(), "closed_at": c.wip_closed_at.isoformat(),
        "odometer_km": c.odometer_km, "condition_code": c.condition_code,
        "lines": [{"lop": ln.labour_op, "description": ln.description, "hours": float(ln.hours_claimed),
                   "rate": float(ln.labour_rate), "related_damage": ln.is_related_damage,
                   "axle_ref": next((p.serial for p in ln.parts if p.serial), None),
                   "parts": [{"part_no": p.part_no, "description": p.description, "qty": float(p.qty),
                              "unit_price": float(p.unit_price), "causal": p.part_no == c.causal_part.part_no,
                              "serial": p.serial} for p in ln.parts]} for ln in c.lines],
        "narrative": narrative(c), "labour_justification": c.labour_justification,
        "pre_approval_ref": c.pre_auth_ref, "dtc_codes": c.dtc_codes, "attachments": attachments(c),
        "totals": totals(c),
    }


def to_subject(p: dict, catalog: dict):
    lines = [sline(l["lop"], l["description"], l["hours"], l["rate"], l["parts"], l["related_damage"]) for l in p["lines"]]
    causal = next((x["part_no"] for l in p["lines"] for x in l["parts"] if x.get("causal")), "")
    return subject("FORD_TRUCKS", claim_ref=p["claim_ref"], claim_type=RCT[p["claim_type"]], contract_type=None,
                   vin=p["vin"], odometer_km=p["odometer_km"], repair_date=p["repair_date"], closed_at=p["closed_at"],
                   lines=lines, narrative_=p["narrative"], attachments_=p["attachments"], totals_=p["totals"],
                   catalog=catalog, causal_part_no=causal, dtc_codes=p["dtc_codes"],
                   pre_auth_ref=p.get("pre_approval_ref"), campaign_code=p.get("campaign_code"),
                   labour_justification=p.get("labour_justification"))


LAYOUT = {"style": "lines_with_chassis", "title": "Truck warranty claim — lines with chassis / axle refs",
          "header": [["dealer_code", "Dealer code"], ["claim_type", "Claim type"], ["ro_number", "RO number"],
                     ["vin", "VIN"], ["chassis_ref", "Chassis ref"], ["odometer_km", "Odometer (km)"],
                     ["condition_code", "Condition code"]],
          "lines_key": "lines", "line_cols": [["lop", "LOP"], ["description", "Description"], ["hours", "Hours"],
                                              ["rate", "Rate"], ["axle_ref", "Axle ref"]],
          "extra": [["labour_justification", "Labour justification"], ["pre_approval_ref", "Pre-approval"]]}

ADAPTER = Adapter("FORD_TRUCKS", FtPayload, build, to_subject, LAYOUT, lambda p: p["claim_ref"])
