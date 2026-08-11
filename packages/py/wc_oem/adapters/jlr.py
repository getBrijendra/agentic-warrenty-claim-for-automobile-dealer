"""JLR (Jaguar, Land Rover): grouped repair sections with symptom + fault codes and SRO lines (illustrative shape)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from wc_core.canonical import CanonicalClaim

from .base import Adapter, Attachment, Narrative, PayTotals, Strict, attachments, narrative, sline, subject, totals

PROG = {"BASE": "WTY", "EXTENDED": "EXT", "CAMPAIGN": "PRG", "GOODWILL": "GDW", "COMPONENT": "WTY",
        "MAINTENANCE_PROGRAMME": "SVC", "PARTS": "PTS"}
RPROG = {"WTY": "BASE", "EXT": "EXTENDED", "PRG": "CAMPAIGN", "GDW": "GOODWILL", "SVC": "MAINTENANCE_PROGRAMME",
         "PTS": "PARTS"}


class JlrSro(Strict):
    sro: str = Field(pattern=r"^JL-[A-Z]{2,3}-\d{4}$")
    description: str
    time_hrs: float = Field(gt=0, le=40)
    rate: float = Field(gt=0)


class JlrPart(Strict):
    part_no: str = Field(pattern=r"^JL-P-[A-Z]{3}-\d{4}$")
    description: str
    qty: float = Field(gt=0)
    price: float = Field(ge=0)
    causal: bool = False


class JlrSection(Strict):
    section: int
    symptom_code: str = Field(pattern=r"^JLS-[A-Z]{2,3}-\d{2}$")
    fault_codes: list[str] = []
    sro_lines: list[JlrSro] = Field(min_length=1)
    parts: list[JlrPart] = []
    related_damage: bool = False
    technician_note: str | None = None


class JlrPayload(Strict):
    retailer_code: str = Field(pattern=r"^JL\d{4}$")
    claim_ref: str
    brand: Literal["JAGUAR", "LAND_ROVER"]
    programme_code: Literal["WTY", "EXT", "PRG", "GDW", "SVC", "PTS"]
    campaign_code: str | None = None
    vin: str = Field(min_length=17, max_length=17)
    repair_order: str
    repair_date: date
    closed_at: datetime
    mileage_km: int = Field(ge=0)
    repair_sections: list[JlrSection] = Field(min_length=1)
    narrative: Narrative
    diagnostic_session_id: str | None = None
    ta_case_ref: str | None = None
    prior_authority_ref: str | None = None
    attachments: list[Attachment] = []
    totals: PayTotals


def build(c: CanonicalClaim, dealer_code: str) -> dict:
    return {
        "retailer_code": dealer_code, "claim_ref": c.claim_ref,
        "brand": "JAGUAR" if c.brand.lower() == "jaguar" else "LAND_ROVER", "programme_code": PROG[c.claim_type],
        "campaign_code": c.campaign_code, "vin": c.vin, "repair_order": c.wip_no,
        "repair_date": c.repair_date.isoformat(), "closed_at": c.wip_closed_at.isoformat(), "mileage_km": c.odometer_km,
        "repair_sections": [{
            "section": i + 1, "symptom_code": c.symptom_code, "fault_codes": c.dtc_codes if i == 0 else [],
            "sro_lines": [{"sro": ln.labour_op, "description": ln.description, "time_hrs": float(ln.hours_claimed),
                           "rate": float(ln.labour_rate)}],
            "parts": [{"part_no": p.part_no, "description": p.description, "qty": float(p.qty),
                       "price": float(p.unit_price), "causal": p.part_no == c.causal_part.part_no} for p in ln.parts],
            "related_damage": ln.is_related_damage,
            "technician_note": c.labour_justification if i == 0 else None,
        } for i, ln in enumerate(c.lines)],
        "narrative": narrative(c), "diagnostic_session_id": c.diagnostic_session_id, "ta_case_ref": c.tech_support_ref,
        "prior_authority_ref": c.pre_auth_ref, "attachments": attachments(c), "totals": totals(c),
    }


def to_subject(p: dict, catalog: dict):
    lines = []
    for s in p["repair_sections"]:
        for i, sro in enumerate(s["sro_lines"]):
            lines.append(sline(sro["sro"], sro["description"], sro["time_hrs"], sro["rate"],
                               s["parts"] if i == 0 else [], s["related_damage"]))
    causal = next((x["part_no"] for s in p["repair_sections"] for x in s["parts"] if x.get("causal")), "")
    note = next((s.get("technician_note") for s in p["repair_sections"] if s.get("technician_note")), None)
    return subject("JLR", claim_ref=p["claim_ref"], claim_type=RPROG[p["programme_code"]], contract_type=None,
                   vin=p["vin"], odometer_km=p["mileage_km"], repair_date=p["repair_date"], closed_at=p["closed_at"],
                   lines=lines, narrative_=p["narrative"], attachments_=p["attachments"], totals_=p["totals"],
                   catalog=catalog, causal_part_no=causal,
                   dtc_codes=[f for s in p["repair_sections"] for f in s["fault_codes"]],
                   diagnostic_session_id=p.get("diagnostic_session_id"), pre_auth_ref=p.get("prior_authority_ref"),
                   tech_support_ref=p.get("ta_case_ref"), campaign_code=p.get("campaign_code"),
                   labour_justification=note)


LAYOUT = {"style": "repair_sections", "title": "Warranty claim — repair sections",
          "header": [["retailer_code", "Retailer code"], ["brand", "Brand"], ["programme_code", "Programme"],
                     ["campaign_code", "Programme ref"], ["repair_order", "Repair order"], ["vin", "VIN"],
                     ["mileage_km", "Mileage (km)"], ["diagnostic_session_id", "Diagnostic session"]],
          "sections_key": "repair_sections",
          "extra": [["ta_case_ref", "TA case"], ["prior_authority_ref", "Prior authority"]]}

ADAPTER = Adapter("JLR", JlrPayload, build, to_subject, LAYOUT, lambda p: p["claim_ref"])
