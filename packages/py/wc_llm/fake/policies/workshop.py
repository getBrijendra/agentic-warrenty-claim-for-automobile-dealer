"""Shared playbook: turning failing rules into workshop questions, and workshop answers into claim patches."""
from __future__ import annotations

from wc_rules.engine import EVIDENCE_LABELS

from ..policy import Event


def topics_for(fail: dict, claim_hint: dict) -> list[tuple[str, str]]:
    """Return [(about, question)] for one failing rule."""
    wip = claim_hint["wip_no"]
    key, data = fail["rule_key"], fail.get("data") or {}
    out = []
    if key in ("required_evidence", "leak_photo"):
        for k in data.get("missing", []):
            label = EVIDENCE_LABELS.get(k, k)
            if k == "diagnostic_session_id":
                out.append((k, f"What is the diagnostic session ID for WIP {wip}? The OEM requires it for electrical jobs."))
            else:
                out.append((k, f"Can you upload the {label} for WIP {wip}? The rule pack requires it for this job."))
    elif key == "narrative_3c":
        miss = data.get("missing", [])
        if "hv_tech_id" in miss:
            out.append(("hv_tech_id", f"Which HV-certified technician did the repair on WIP {wip}, and what is their ID?"))
        if any(m in miss for m in ("complaint", "cause", "correction", "test_result", "cause_part")):
            out.append(("narrative", f"For WIP {wip}: what root cause did you find, and what test confirmed the repair?"))
        if "tech_support_ref" in miss:
            out.append(("tech_support_ref", f"Is there a technical-support case reference for WIP {wip}?"))
    elif key in ("causal_part", "causal_part_family"):
        out.append(("causal_part", f"For WIP {wip}: which part failed first — is {claim_hint.get('causal_part_no')} really "
                                   f"the causal part, or was it replaced as collateral damage?"))
    elif key in ("labour_tolerance", "labour_evidence"):
        out.append(("labour", f"WIP {wip}: labour was {data.get('claimed')} h against a {data.get('standard')} h standard. "
                              f"What caused the extra time, and do you have a photo that shows it?"))
    return out


def patch_from_answers(answers: list[Event], ro_lines: list[dict] | None = None) -> tuple[dict, list[str]]:
    patch: dict = {}
    notes: list[str] = []
    for ev in answers:
        a = ev.result or {}
        prov = a.get("provides") or {}
        att = a.get("attachment")
        if att:
            patch.setdefault("evidence_add", []).append(att["id"])
            notes.append(f"attached {att['title']}")
        if prov.get("diagnostic_session_id"):
            patch["diagnostic_session_id"] = prov["diagnostic_session_id"]
            notes.append(f"diagnostic session {prov['diagnostic_session_id']}")
        if prov.get("justification"):
            patch["labour_justification"] = prov["justification"]
            notes.append("labour justification from technician")
        if prov.get("cause"):
            patch["cause"] = prov["cause"]
            if prov.get("correction_test"):
                patch["correction_append"] = prov["correction_test"]
            notes.append("3C rewritten from technician answer")
        if prov.get("causal_part_no"):
            patch["causal_part_no"] = prov["causal_part_no"]
            notes.append(f"causal part → {prov['causal_part_no']}")
        if prov.get("hv_tech_id"):
            patch["hv_tech_id"] = prov["hv_tech_id"]
        if prov.get("tech_support_ref"):
            patch["tech_support_ref"] = prov["tech_support_ref"]
    return patch, notes
