"""MASERATI: flat lines (labour and part lines interleaved) with cost codes (illustrative shape)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from wc_core.canonical import CanonicalClaim

from .base import Adapter, Attachment, Narrative, PayTotals, Strict, attachments, narrative, sline, subject, totals


def cost_code(claim_type: str, contract: str | None) -> str:
    if claim_type == "EXTENDED":
        return "W7" if contract == "LIMITED_EXTENDED" else "W5"
    return {"BASE": "W1", "CAMPAIGN": "C1", "GOODWILL": "G1", "MAINTENANCE_PROGRAMME": "M1", "COMPONENT": "W1",
            "PARTS": "P1"}[claim_type]


RCOST = {"W1": ("BASE", "BASE"), "W5": ("EXTENDED", "EXTENDED"), "W7": ("EXTENDED", "LIMITED_EXTENDED"),
         "C1": ("CAMPAIGN", None), "G1": ("GOODWILL", None), "M1": ("MAINTENANCE_PROGRAMME", None), "P1": ("PARTS", None)}


class MsLine(Strict):
    line_no: int
    line_type: Literal["L", "P"]
    code: str = Field(pattern=r"^(MS-[A-Z]{2}-\d{4}|MS-P-[A-Z]{3}-\d{4})$")
    description: str
    qty: float = Field(gt=0)
    unit_amount: float = Field(ge=0)
    parent_line: int | None = None
    causal: bool = False
    related_damage: bool = False


class MaseratiPayload(Strict):
    dealer_code: str = Field(pattern=r"^MS\d{4}$")
    claim_ref: str
    cost_code: Literal["W1", "W5", "W7", "C1", "G1", "M1", "P1"]
    campaign_code: str | None = None
    vin: str = Field(min_length=17, max_length=17)
    ro_number: str
    repair_date: date
    closed_at: datetime
    odometer_km: int = Field(ge=0)
    condition_code: str = Field(pattern=r"^MSC-[A-Z]{2}-\d{2}$")
    lines: list[MsLine] = Field(min_length=1)
    narrative: Narrative
    labour_justification: str | None = None
    prior_approval_ref: str | None = None
    dtc_codes: list[str] = []
    attachments: list[Attachment] = []
    totals: PayTotals


def build(c: CanonicalClaim, dealer_code: str) -> dict:
    lines, n = [], 0
    for ln in c.lines:
        n += 1
        parent = n
        lines.append({"line_no": n, "line_type": "L", "code": ln.labour_op, "description": ln.description,
                      "qty": float(ln.hours_claimed), "unit_amount": float(ln.labour_rate), "parent_line": None,
                      "causal": False, "related_damage": ln.is_related_damage})
        for p in ln.parts:
            n += 1
            lines.append({"line_no": n, "line_type": "P", "code": p.part_no, "description": p.description,
                          "qty": float(p.qty), "unit_amount": float(p.unit_price), "parent_line": parent,
                          "causal": p.part_no == c.causal_part.part_no, "related_damage": ln.is_related_damage})
    return {"dealer_code": dealer_code, "claim_ref": c.claim_ref, "cost_code": cost_code(c.claim_type, c.contract_type),
            "campaign_code": c.campaign_code, "vin": c.vin, "ro_number": c.wip_no,
            "repair_date": c.repair_date.isoformat(), "closed_at": c.wip_closed_at.isoformat(),
            "odometer_km": c.odometer_km, "condition_code": c.condition_code, "lines": lines,
            "narrative": narrative(c), "labour_justification": c.labour_justification,
            "prior_approval_ref": c.pre_auth_ref, "dtc_codes": c.dtc_codes, "attachments": attachments(c),
            "totals": totals(c)}


def to_subject(p: dict, catalog: dict):
    labour = [l for l in p["lines"] if l["line_type"] == "L"]
    lines = []
    for l in labour:
        parts = [{"part_no": x["code"], "description": x["description"], "qty": x["qty"], "unit_price": x["unit_amount"]}
                 for x in p["lines"] if x["line_type"] == "P" and x["parent_line"] == l["line_no"]]
        lines.append(sline(l["code"], l["description"], l["qty"], l["unit_amount"], parts, l["related_damage"]))
    causal = next((x["code"] for x in p["lines"] if x["line_type"] == "P" and x["causal"]), "")
    ct, contract = RCOST[p["cost_code"]]
    return subject("MASERATI", claim_ref=p["claim_ref"], claim_type=ct, contract_type=contract, vin=p["vin"],
                   odometer_km=p["odometer_km"], repair_date=p["repair_date"], closed_at=p["closed_at"], lines=lines,
                   narrative_=p["narrative"], attachments_=p["attachments"], totals_=p["totals"], catalog=catalog,
                   causal_part_no=causal, dtc_codes=p["dtc_codes"], pre_auth_ref=p.get("prior_approval_ref"),
                   campaign_code=p.get("campaign_code"), labour_justification=p.get("labour_justification"))


LAYOUT = {"style": "flat_lines", "title": "Warranty claim — flat lines with cost code",
          "header": [["dealer_code", "Dealer code"], ["cost_code", "Cost code"], ["ro_number", "RO number"],
                     ["vin", "VIN"], ["odometer_km", "Odometer (km)"], ["condition_code", "Condition code"]],
          "lines_key": "lines", "line_cols": [["line_type", "Type"], ["code", "Code"], ["description", "Description"],
                                              ["qty", "Qty / hrs"], ["unit_amount", "Unit"], ["causal", "Causal"]],
          "extra": [["labour_justification", "Labour justification"], ["prior_approval_ref", "Prior approval"]]}

ADAPTER = Adapter("MASERATI", MaseratiPayload, build, to_subject, LAYOUT, lambda p: p["claim_ref"])
