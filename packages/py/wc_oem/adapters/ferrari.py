"""FERRARI: single section hung off a Service Entry, plus technical-support ticket (illustrative shape)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from wc_core.canonical import CanonicalClaim

from .base import Adapter, Attachment, Narrative, PayTotals, Strict, attachments, narrative, sline, subject, totals

CAT = {"BASE": "WARRANTY", "EXTENDED": "EXTENSION", "MAINTENANCE_PROGRAMME": "MAINTENANCE", "CAMPAIGN": "CAMPAIGN",
       "GOODWILL": "GOODWILL", "COMPONENT": "WARRANTY", "PARTS": "PARTS"}
RCAT = {"WARRANTY": "BASE", "EXTENSION": "EXTENDED", "MAINTENANCE": "MAINTENANCE_PROGRAMME", "CAMPAIGN": "CAMPAIGN",
        "GOODWILL": "GOODWILL", "PARTS": "PARTS"}


class FeOp(Strict):
    op_code: str = Field(pattern=r"^FE-[A-Z]{2}-\d{4}$")
    description: str
    time: float = Field(gt=0, le=40)
    rate: float = Field(gt=0)
    related_damage: bool = False


class FePart(Strict):
    part_no: str = Field(pattern=r"^FE-P-[A-Z]{3}-\d{4}$")
    description: str
    qty: float = Field(gt=0)
    unit_price: float = Field(ge=0)
    causal: bool = False
    op_code: str


class FeSection(Strict):
    defect_code: str = Field(pattern=r"^FEC-[A-Z]{2}-\d{2}$")
    operations: list[FeOp] = Field(min_length=1)
    parts: list[FePart] = []


class FerrariPayload(Strict):
    dealer_code: str = Field(pattern=r"^FE\d{3}$")
    claim_ref: str
    service_entry_ref: str = Field(pattern=r"^SE-\d+$")
    claim_category: Literal["WARRANTY", "EXTENSION", "MAINTENANCE", "CAMPAIGN", "GOODWILL", "PARTS"]
    campaign_code: str | None = None
    vin: str = Field(min_length=17, max_length=17)
    odometer_km: int = Field(ge=0)
    repair_date: date
    closed_at: datetime
    section: FeSection
    narrative: Narrative
    support_ticket_ref: str | None = None
    authorisation_code: str | None = None
    labour_justification: str | None = None
    dtc_codes: list[str] = []
    attachments: list[Attachment] = []
    totals: PayTotals


def build(c: CanonicalClaim, dealer_code: str) -> dict:
    return {
        "dealer_code": dealer_code, "claim_ref": c.claim_ref, "service_entry_ref": f"SE-{c.wip_no}",
        "claim_category": CAT[c.claim_type], "campaign_code": c.campaign_code, "vin": c.vin,
        "odometer_km": c.odometer_km, "repair_date": c.repair_date.isoformat(), "closed_at": c.wip_closed_at.isoformat(),
        "section": {"defect_code": c.condition_code,
                    "operations": [{"op_code": ln.labour_op, "description": ln.description,
                                    "time": float(ln.hours_claimed), "rate": float(ln.labour_rate),
                                    "related_damage": ln.is_related_damage} for ln in c.lines],
                    "parts": [{"part_no": p.part_no, "description": p.description, "qty": float(p.qty),
                               "unit_price": float(p.unit_price), "causal": p.part_no == c.causal_part.part_no,
                               "op_code": ln.labour_op} for ln in c.lines for p in ln.parts]},
        "narrative": narrative(c), "support_ticket_ref": c.tech_support_ref, "authorisation_code": c.pre_auth_ref,
        "labour_justification": c.labour_justification, "dtc_codes": c.dtc_codes, "attachments": attachments(c),
        "totals": totals(c),
    }


def to_subject(p: dict, catalog: dict):
    sec = p["section"]
    lines = [sline(o["op_code"], o["description"], o["time"], o["rate"],
                   [x for x in sec["parts"] if x["op_code"] == o["op_code"]], o["related_damage"])
             for o in sec["operations"]]
    causal = next((x["part_no"] for x in sec["parts"] if x.get("causal")), "")
    return subject("FERRARI", claim_ref=p["claim_ref"], claim_type=RCAT[p["claim_category"]], contract_type=None,
                   vin=p["vin"], odometer_km=p["odometer_km"], repair_date=p["repair_date"], closed_at=p["closed_at"],
                   lines=lines, narrative_=p["narrative"], attachments_=p["attachments"], totals_=p["totals"],
                   catalog=catalog, causal_part_no=causal, dtc_codes=p["dtc_codes"],
                   pre_auth_ref=p.get("authorisation_code"), tech_support_ref=p.get("support_ticket_ref"),
                   campaign_code=p.get("campaign_code"), labour_justification=p.get("labour_justification"))


LAYOUT = {"style": "single_section", "title": "Service Entry claim — single section",
          "header": [["dealer_code", "Dealer code"], ["service_entry_ref", "Service Entry"],
                     ["claim_category", "Category"], ["vin", "VIN"], ["odometer_km", "Odometer (km)"],
                     ["support_ticket_ref", "Support ticket"], ["authorisation_code", "Authorisation code"]],
          "section_key": "section", "extra": [["labour_justification", "Labour justification"]]}

ADAPTER = Adapter("FERRARI", FerrariPayload, build, to_subject, LAYOUT, lambda p: p["claim_ref"])
