"""Drafted human-facing messages (escalation recommendations, OEM requests, customer notes)."""
from __future__ import annotations

from wc_core.vocab import REJECTION_CODES

RECOMMEND = {
    "R01": "Out of coverage — consider a goodwill request; human approval required.",
    "R02": "Outside the submission window — request a window extension from the OEM with the reason for delay.",
    "R07": "Duplicate — withdraw this claim.",
    "R08": "Authorisation missing — request retro-authorisation / TA case from the OEM; human must approve the request.",
    "R09": "Component outside the active contract's scope — not claimable; offer customer-pay or goodwill.",
    "R11": "Maintenance non-compliance — contract condition not met; review service records and decide on goodwill.",
    "R12": "Misuse / modification — not claimable under warranty; customer-pay recommended.",
    "R13": "Failed part not retained — confirm part location before any appeal.",
}


def recommendation(codes: list[str]) -> str:
    return " ".join(RECOMMEND.get(c, REJECTION_CODES.get(c, (c,))[0]) for c in codes) or "Review required."


def draft_for(code: str, claim_ref: str, wip_no: str, detail: str, programme: str, extra: dict | None = None) -> str:
    extra = extra or {}
    if code == "R08" and extra.get("kind") == "ta_case":
        return (f"Technical Assistance case request — {programme}, claim {claim_ref} (WIP {wip_no}).\n"
                f"The OEM rejected the claim as a repeat repair: {detail}. The current failure is a separate component "
                f"(see 3C and photos). Please open a TA case so the repair can be assessed and the claim resubmitted "
                f"with the TA reference.")
    if code == "R08":
        return (f"Pre-authorisation request — {programme}, claim {claim_ref} (WIP {wip_no}).\n"
                f"{detail}. The diagnosis and technical-support reference are attached. We request retrospective "
                f"authorisation for the repair as performed.")
    if code == "R09":
        return (f"Customer note — WIP {wip_no}: the repair is outside the active extended contract's scope ({detail}). "
                f"It cannot be claimed under warranty. Options: customer-pay invoice, or a goodwill request to the OEM "
                f"citing the vehicle's service history.")
    if code == "R11":
        return (f"Internal review — claim {claim_ref} (WIP {wip_no}): {detail}. The contract requires all periodic "
                f"services; the claim will be rejected as filed. Options: (1) obtain missing service evidence from the "
                f"customer, (2) goodwill request, (3) customer-pay.")
    if code == "R02":
        return (f"Window-extension request — claim {claim_ref} (WIP {wip_no}): {detail}. Please accept late submission.")
    return f"Review claim {claim_ref} (WIP {wip_no}): {detail}."
