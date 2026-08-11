"""Canonical claim model (aligned with the STAR WarrantyClaim noun where reasonable).

One internal claim shape; OEM formats are produced by adapters at the edge (P3).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from .vocab import ClaimType


def money(v: Decimal | float | int | str) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class PartRef(BaseModel):
    wip_line_no: int
    part_no: str
    description: str
    qty: Decimal = Decimal("1")
    unit_price: Decimal
    serial: str | None = None


class ClaimLine(BaseModel):
    line_no: int
    wip_line_no: int
    labour_op: str
    description: str
    system: str
    standard_hours: Decimal
    hours_actual: Decimal
    hours_claimed: Decimal
    labour_rate: Decimal
    parts: list[PartRef] = Field(default_factory=list)
    is_related_damage: bool = False
    pay_type: str = "W"
    technician_id: str | None = None


class EvidenceRef(BaseModel):
    attachment_id: str
    kind: str
    title: str


class Totals(BaseModel):
    labour: Decimal
    parts: Decimal
    sublet: Decimal = Decimal("0.00")
    subtotal: Decimal
    vat_rate: Decimal
    vat: Decimal
    total: Decimal
    currency: str = "AED"


class CanonicalClaim(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    claim_ref: str
    company_id: str
    programme: str
    claim_type: ClaimType
    contract_type: str | None = None
    brand: str
    model: str
    wip_no: str
    repair_order_id: str
    vin: str
    odometer_km: int
    repair_date: date
    wip_closed_at: datetime
    market_spec: str = "GCC"
    system: str
    complaint: str
    cause: str
    correction: str
    causal_part: PartRef
    lines: list[ClaimLine]
    condition_code: str | None = None
    symptom_code: str | None = None
    dtc_codes: list[str] = Field(default_factory=list)
    diagnostic_session_id: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    campaign_code: str | None = None
    pre_auth_ref: str | None = None
    tech_support_ref: str | None = None
    hv_tech_id: str | None = None
    labour_justification: str | None = None
    technician_ids: list[str] = Field(default_factory=list)
    totals: Totals
    prior_claim_ref: str | None = None

    def evidence_kinds(self) -> set[str]:
        kinds = {e.kind for e in self.evidence}
        if self.dtc_codes:
            kinds.add("dtc_codes")
        if self.diagnostic_session_id:
            kinds.add("diagnostic_session_id")
        if self.tech_support_ref:
            kinds.add("tech_support_ref")
        if self.hv_tech_id:
            kinds.add("hv_tech_id")
        return kinds


def compute_totals(lines: list[ClaimLine], vat_rate: float | Decimal, currency: str = "AED") -> Totals:
    labour = money(sum((ln.hours_claimed * ln.labour_rate for ln in lines), Decimal("0")))
    parts = money(sum((p.qty * p.unit_price for ln in lines for p in ln.parts), Decimal("0")))
    subtotal = money(labour + parts)
    rate = Decimal(str(vat_rate))
    vat = money(subtotal * rate)
    return Totals(labour=labour, parts=parts, subtotal=subtotal, vat_rate=rate, vat=vat,
                  total=money(subtotal + vat), currency=currency)


# ---- agent output schemas -------------------------------------------------------------

class TriageDecision(BaseModel):
    claimable: bool
    programme: str
    claim_type: ClaimType | None = None
    contract_type: str | None = None
    priority: Literal["normal", "high"] = "normal"
    window_remaining_days: float | None = None
    reasons: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    action: Literal["build", "escalate", "withdraw"]
    duplicate_of: str | None = None
    campaign_code: str | None = None
    recommendation: str | None = None
    draft_message: str | None = None
    confidence: float = 0.8


class BuildResult(BaseModel):
    version: int
    summary: str
    flags: list[str] = Field(default_factory=list)


class ComplianceOutcome(BaseModel):
    status: Literal["ready", "blocked", "escalate"]
    fixes: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    draft_message: str | None = None
    pre_auth_ticket: str | None = None


class SubmissionResult(BaseModel):
    submitted: bool
    oem_claim_id: str | None = None
    version: int | None = None
    format_errors: list[str] = Field(default_factory=list)


class Resolution(BaseModel):
    action: Literal["resubmit", "retype", "withdraw", "escalate", "write_off"]
    new_version: int | None = None
    new_claim_type: ClaimType | None = None
    reason_codes: list[str] = Field(default_factory=list)
    summary: str
    draft_message: str | None = None
