"""Rules-engine inputs/outputs. The engine evaluates a normalised RuleSubject so the same code adjudicates
a dealer-side CanonicalClaim and an OEM payload received by the simulated gateway."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from wc_core.canonical import CanonicalClaim


class SubjectPart(BaseModel):
    part_no: str
    description: str = ""
    qty: float = 1
    unit_price: float = 0
    serial: str | None = None


class SubjectLine(BaseModel):
    labour_op: str
    description: str = ""
    system: str | None = None
    hours_claimed: float
    hours_actual: float | None = None
    standard_hours: float | None = None
    labour_rate: float = 0
    parts: list[SubjectPart] = Field(default_factory=list)
    is_related_damage: bool = False


class RuleSubject(BaseModel):
    programme: str
    claim_ref: str
    claim_type: str
    contract_type: str | None = None
    vin: str
    odometer_km: int
    repair_date: date
    wip_closed_at: datetime
    system: str
    lines: list[SubjectLine]
    causal_part_no: str
    causal_part_desc: str = ""
    amount_ex_vat: float
    total: float
    complaint: str = ""
    cause: str = ""
    correction: str = ""
    evidence_kinds: list[str] = Field(default_factory=list)
    dtc_codes: list[str] = Field(default_factory=list)
    diagnostic_session_id: str | None = None
    pre_auth_ref: str | None = None
    tech_support_ref: str | None = None
    hv_tech_id: str | None = None
    campaign_code: str | None = None
    labour_justification: str | None = None

    @property
    def ops(self) -> list[str]:
        return [ln.labour_op for ln in self.lines]

    @staticmethod
    def from_canonical(c: CanonicalClaim) -> "RuleSubject":
        return RuleSubject(
            programme=c.programme, claim_ref=c.claim_ref, claim_type=c.claim_type, contract_type=c.contract_type,
            vin=c.vin, odometer_km=c.odometer_km, repair_date=c.repair_date, wip_closed_at=c.wip_closed_at,
            system=c.system,
            lines=[SubjectLine(labour_op=ln.labour_op, description=ln.description, system=ln.system,
                               hours_claimed=float(ln.hours_claimed), hours_actual=float(ln.hours_actual),
                               standard_hours=float(ln.standard_hours), labour_rate=float(ln.labour_rate),
                               is_related_damage=ln.is_related_damage,
                               parts=[SubjectPart(part_no=p.part_no, description=p.description, qty=float(p.qty),
                                                  unit_price=float(p.unit_price), serial=p.serial) for p in ln.parts])
                   for ln in c.lines],
            causal_part_no=c.causal_part.part_no, causal_part_desc=c.causal_part.description,
            amount_ex_vat=float(c.totals.subtotal), total=float(c.totals.total),
            complaint=c.complaint, cause=c.cause, correction=c.correction,
            evidence_kinds=sorted(c.evidence_kinds()), dtc_codes=c.dtc_codes,
            diagnostic_session_id=c.diagnostic_session_id, pre_auth_ref=c.pre_auth_ref,
            tech_support_ref=c.tech_support_ref, hv_tech_id=c.hv_tech_id, campaign_code=c.campaign_code,
            labour_justification=c.labour_justification,
        )


class RuleContext(BaseModel):
    now: datetime
    vehicle: dict[str, Any]
    contracts: list[dict[str, Any]] = Field(default_factory=list)
    service_history: list[dict[str, Any]] = Field(default_factory=list)
    campaigns: list[dict[str, Any]] = Field(default_factory=list)
    prior_claims: list[dict[str, Any]] = Field(default_factory=list)
    repair_history: list[dict[str, Any]] = Field(default_factory=list)
    catalog: dict[str, dict[str, Any]] = Field(default_factory=dict)
    wip_part_nos: list[str] | None = None
    valid_auth_refs: list[str] | None = None


class RuleResult(BaseModel):
    rule_key: str
    label: str
    result: Literal["pass", "warn", "fail"]
    code: str | None = None
    detail: str
    source: Literal["public", "illustrative"] = "illustrative"
    source_url: str | None = None
    fixable: bool = False
    suggested_fix: str | None = None
    side: Literal["dealer", "gateway"] = "dealer"
    data: dict[str, Any] = Field(default_factory=dict)
