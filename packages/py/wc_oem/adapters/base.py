"""Shared adapter pieces. An adapter = OEM payload model (JSON Schema) + canonical→payload (pure) +
payload→RuleSubject (what the simulated gateway adjudicates on — the decision follows from the payload)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from wc_core.canonical import CanonicalClaim
from wc_rules.model import RuleSubject, SubjectLine, SubjectPart


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Attachment(Strict):
    id: str
    kind: str
    title: str


class Narrative(Strict):
    complaint: str = Field(min_length=1, max_length=1200)
    cause: str = Field(min_length=1, max_length=1200)
    correction: str = Field(min_length=1, max_length=1500)


class PayTotals(Strict):
    labour: float
    parts: float
    vat: float
    total: float
    currency: str = Field(pattern=r"^[A-Z]{3}$")


def totals(c: CanonicalClaim) -> dict:
    t = c.totals
    return {"labour": float(t.labour), "parts": float(t.parts), "vat": float(t.vat), "total": float(t.total),
            "currency": t.currency}


def attachments(c: CanonicalClaim) -> list[dict]:
    return [{"id": e.attachment_id, "kind": e.kind, "title": e.title} for e in c.evidence]


def narrative(c: CanonicalClaim) -> dict:
    return {"complaint": c.complaint, "cause": c.cause, "correction": c.correction}


def field_errors(model: type[BaseModel], payload: dict) -> list[dict]:
    try:
        model.model_validate(payload)
        return []
    except ValidationError as e:
        return [{"field": ".".join(str(p) for p in err["loc"]), "message": err["msg"]} for err in e.errors()]


def evidence_kinds(atts: list[dict], **fields: Any) -> list[str]:
    kinds = {a["kind"] for a in atts}
    for k, v in fields.items():
        if v:
            kinds.add(k)
    return sorted(kinds)


def subject(programme: str, *, claim_ref: str, claim_type: str, contract_type: str | None, vin: str, odometer_km: int,
            repair_date: Any, closed_at: Any, lines: list[SubjectLine], narrative_: dict, attachments_: list[dict],
            totals_: dict, catalog: dict[str, dict], causal_part_no: str, dtc_codes: list[str] | None = None,
            diagnostic_session_id: str | None = None, pre_auth_ref: str | None = None,
            tech_support_ref: str | None = None, hv_tech_id: str | None = None, campaign_code: str | None = None,
            labour_justification: str | None = None) -> RuleSubject:
    primary = next((ln for ln in lines if not ln.is_related_damage), lines[0])
    for ln in lines:
        ln.system = ln.system or catalog.get(ln.labour_op, {}).get("system")
        ln.standard_hours = ln.standard_hours or catalog.get(ln.labour_op, {}).get("standard_hours")
    return RuleSubject(
        programme=programme, claim_ref=claim_ref, claim_type=claim_type, contract_type=contract_type, vin=vin,
        odometer_km=odometer_km, repair_date=date.fromisoformat(str(repair_date)[:10]),
        wip_closed_at=datetime.fromisoformat(str(closed_at)), system=primary.system or "UNKNOWN", lines=lines,
        causal_part_no=causal_part_no,
        causal_part_desc=next((p.description for ln in lines for p in ln.parts if p.part_no == causal_part_no), ""),
        amount_ex_vat=round(totals_["labour"] + totals_["parts"], 2), total=totals_["total"],
        complaint=narrative_["complaint"], cause=narrative_["cause"], correction=narrative_["correction"],
        evidence_kinds=evidence_kinds(attachments_, diagnostic_session_id=diagnostic_session_id,
                                      tech_support_ref=tech_support_ref, hv_tech_id=hv_tech_id,
                                      dtc_codes=bool(dtc_codes)),
        dtc_codes=dtc_codes or [], diagnostic_session_id=diagnostic_session_id, pre_auth_ref=pre_auth_ref,
        tech_support_ref=tech_support_ref, hv_tech_id=hv_tech_id, campaign_code=campaign_code,
        labour_justification=labour_justification,
    )


class Adapter:
    """Per-programme adapter contract."""
    programme: str
    payload_model: type[BaseModel]
    layout: dict
    build: Callable[[CanonicalClaim, str], dict]
    to_subject: Callable[[dict, dict], RuleSubject]
    claim_ref_of: Callable[[dict], str]

    def __init__(self, programme, payload_model, build, to_subject, layout, claim_ref_of):
        self.programme, self.payload_model, self.build, self.to_subject = programme, payload_model, build, to_subject
        self.layout, self.claim_ref_of = layout, claim_ref_of

    def validate(self, payload: dict) -> list[dict]:
        return field_errors(self.payload_model, payload)

    def json_schema(self) -> dict:
        return self.payload_model.model_json_schema()


def sline(labour_op: str, description: str, hours: float, rate: float, parts: list[dict], related: bool = False,
          system: str | None = None) -> SubjectLine:
    return SubjectLine(labour_op=labour_op, description=description, hours_claimed=hours, labour_rate=rate,
                       is_related_damage=related, system=system,
                       parts=[SubjectPart(part_no=p["part_no"], description=p.get("description", ""),
                                          qty=p.get("qty", 1), unit_price=p.get("unit_price", p.get("price", 0)),
                                          serial=p.get("serial") or p.get("module_serial")) for p in parts])


def causal_of(parts_lists: list[list[dict]]) -> str:
    for parts in parts_lists:
        for p in parts:
            if p.get("causal"):
                return p["part_no"]
    return ""
