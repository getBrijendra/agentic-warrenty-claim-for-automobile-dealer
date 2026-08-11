"""FORD (Ford, Lincoln): flat lines, LOP + condition code, numeric claim type + sub code (illustrative shape)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from wc_core.canonical import CanonicalClaim

from .base import Adapter, Attachment, Narrative, PayTotals, Strict, attachments, narrative, sline, subject, totals

CLAIM_TYPE_CODE = {"BASE": "01", "EXTENDED": "12", "CAMPAIGN": "31", "MAINTENANCE_PROGRAMME": "45", "GOODWILL": "60",
                   "COMPONENT": "14", "PARTS": "70"}
CODE_CLAIM_TYPE = {v: k for k, v in CLAIM_TYPE_CODE.items()}


class FordPart(Strict):
    part_no: str = Field(pattern=r"^FD-P-[A-Z]{3}-\d{4}$")
    description: str
    qty: float = Field(gt=0)
    unit_price: float = Field(ge=0)
    causal: bool = False


class FordLine(Strict):
    line_no: int
    lop: str = Field(pattern=r"^FD-[A-Z]{2}-\d{4}$")
    description: str
    hours: float = Field(gt=0, le=40)
    rate: float = Field(gt=0)
    related_damage: bool = False
    parts: list[FordPart] = []


class FordPayload(Strict):
    dealer_code: str = Field(pattern=r"^FD\d{5}$")
    claim_ref: str
    claim_type_code: Literal["01", "12", "14", "31", "45", "60", "70"]
    sub_code: str | None = None
    contract: str | None = None
    vin: str = Field(min_length=17, max_length=17)
    ro_number: str
    repair_date: date
    ro_closed_at: datetime
    odometer_km: int = Field(ge=0)
    customer_concern_code: str = Field(pattern=r"^[A-Z0-9]{3}$")
    condition_code: str = Field(pattern=r"^FDC-[A-Z]{2}-\d{2}$")
    lines: list[FordLine] = Field(min_length=1)
    narrative: Narrative
    labour_justification: str | None = None
    prior_approval_ref: str | None = None
    ta_case_ref: str | None = None
    dtc_codes: list[str] = []
    attachments: list[Attachment] = []
    totals: PayTotals


def build(c: CanonicalClaim, dealer_code: str) -> dict:
    return {
        "dealer_code": dealer_code, "claim_ref": c.claim_ref, "claim_type_code": CLAIM_TYPE_CODE[c.claim_type],
        "sub_code": c.campaign_code, "contract": c.contract_type, "vin": c.vin, "ro_number": c.wip_no,
        "repair_date": c.repair_date.isoformat(), "ro_closed_at": c.wip_closed_at.isoformat(),
        "odometer_km": c.odometer_km, "customer_concern_code": (c.system[:2] + "1").upper()[:3],
        "condition_code": c.condition_code,
        "lines": [{"line_no": ln.line_no, "lop": ln.labour_op, "description": ln.description,
                   "hours": float(ln.hours_claimed), "rate": float(ln.labour_rate),
                   "related_damage": ln.is_related_damage,
                   "parts": [{"part_no": p.part_no, "description": p.description, "qty": float(p.qty),
                              "unit_price": float(p.unit_price), "causal": p.part_no == c.causal_part.part_no}
                             for p in ln.parts]} for ln in c.lines],
        "narrative": narrative(c), "labour_justification": c.labour_justification,
        "prior_approval_ref": c.pre_auth_ref, "ta_case_ref": c.tech_support_ref, "dtc_codes": c.dtc_codes,
        "attachments": attachments(c), "totals": totals(c),
    }


def to_subject(p: dict, catalog: dict):
    lines = [sline(l["lop"], l["description"], l["hours"], l["rate"], l["parts"], l["related_damage"]) for l in p["lines"]]
    causal = next((x["part_no"] for l in p["lines"] for x in l["parts"] if x.get("causal")), "")
    return subject("FORD", claim_ref=p["claim_ref"], claim_type=CODE_CLAIM_TYPE[p["claim_type_code"]],
                   contract_type=p.get("contract"), vin=p["vin"], odometer_km=p["odometer_km"],
                   repair_date=p["repair_date"], closed_at=p["ro_closed_at"], lines=lines, narrative_=p["narrative"],
                   attachments_=p["attachments"], totals_=p["totals"], catalog=catalog, causal_part_no=causal,
                   dtc_codes=p["dtc_codes"], pre_auth_ref=p.get("prior_approval_ref"),
                   tech_support_ref=p.get("ta_case_ref"), campaign_code=p.get("sub_code"),
                   labour_justification=p.get("labour_justification"))


LAYOUT = {"style": "flat_lines", "title": "Warranty claim — flat lines",
          "header": [["dealer_code", "Dealer code"], ["claim_type_code", "Claim type"], ["sub_code", "FSA / sub code"],
                     ["ro_number", "RO number"], ["vin", "VIN"], ["odometer_km", "Odometer (km)"],
                     ["customer_concern_code", "CCC"], ["condition_code", "Condition code"]],
          "lines_key": "lines", "line_cols": [["lop", "LOP"], ["description", "Description"], ["hours", "Hours"],
                                              ["rate", "Rate"], ["related_damage", "Related dmg"]],
          "extra": [["labour_justification", "Labour justification"], ["prior_approval_ref", "Prior approval"],
                    ["ta_case_ref", "TA case"]]}

ADAPTER = Adapter("FORD", FordPayload, build, to_subject, LAYOUT, lambda p: p["claim_ref"])
