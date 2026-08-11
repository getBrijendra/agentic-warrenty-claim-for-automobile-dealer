"""Technician persona (fast role) for ask_workshop. Grounded ONLY in the WIP record + that WIP's hidden facts.
If the fact is not there, the answer is "not recorded" — the persona never invents evidence."""
from __future__ import annotations

from ..policy import Ctx, final
from ..text import pick

NOT_RECORDED = ["Not recorded on the job card — I don't have that.", "I don't have that; it wasn't recorded.",
                "Sorry, nothing recorded for that on this job."]


def _topic(q: str, about: str | None) -> str:
    if about:
        return about
    ql = q.lower()
    if "session" in ql:
        return "diagnostic_session_id"
    if "soh" in ql or "state of health" in ql:
        return "battery_soh_report"
    if "extra time" in ql or "labour" in ql or "hours" in ql:
        return "labour"
    if "failed first" in ql or "causal" in ql or "collateral" in ql:
        return "causal_part"
    if "photo" in ql or "picture" in ql:
        return "photo"
    if "cause" in ql or "test" in ql:
        return "narrative"
    return "other"


def run(ctx: Ctx):
    req = ctx.task
    tech = (req.get("technician") or {}).get("name", "Technician")
    first = tech.split()[0]
    topic = _topic(req.get("question", ""), req.get("about"))
    facts = [i.get("hidden_facts") or {} for i in req.get("hidden_facts", [])]
    issues = req.get("hidden_facts", [])
    out = {"answer": f"{first}: " + pick(ctx.rng, NOT_RECORDED), "provides": {}, "document": None, "found": False}

    def doc(kind: str, title: str, caption: str):
        out["document"] = {"kind": kind, "title": title, "caption": caption}

    if topic == "diagnostic_session_id":
        f = next((f for f in facts if f.get("evidence_kind") == "diagnostic_session_id"), None)
        if f:
            out.update(found=True, answer=f"{first}: Session was {f['value']} — I ran it on the diagnostic laptop but forgot "
                                         f"to copy the ID to the job card. Added it now.",
                       provides={"diagnostic_session_id": f["value"]})
    elif topic in ("photo", "battery_soh_report", "diag_log", "odometer_photo", "dtc_snapshot"):
        f = next((f for f in facts if f.get("evidence_kind") == topic and f.get("exists")), None)
        if f is None and topic == "photo":
            lab = next((f for f in facts if f.get("photo_exists")), None)
            if lab:
                f = {"title": lab["photo_title"], "caption": lab.get("photo_caption", "")}
        if f:
            out.update(found=True, answer=f"{first}: Yes, I have it — uploading \"{f['title']}\" to the job card now.")
            doc(topic, f["title"], f.get("caption", ""))
    elif topic == "labour":
        f = next((f for f in facts if f.get("justification")), None)
        if f:
            out.update(found=True, answer=f"{first}: {f['justification']}" +
                       (" Photo uploaded." if f.get("photo_exists") else ""),
                       provides={"justification": f["justification"]})
            if f.get("photo_exists"):
                doc("photo", f["photo_title"], f.get("photo_caption", ""))
    elif topic == "causal_part":
        f = next((f for f in facts if f.get("true_causal_part_no")), None)
        if f:
            out.update(found=True, answer=f"{first}: {f['explanation']} Causal part is {f['true_causal_part_no']}.",
                       provides={"causal_part_no": f["true_causal_part_no"]})
    elif topic == "narrative":
        f = next((f for f in facts if f.get("cause")), None)
        if f:
            out.update(found=True, answer=f"{first}: {f['cause']} {f.get('correction_test', '')}".strip(),
                       provides={"cause": f["cause"], "correction_test": f.get("correction_test", "")})
    elif topic == "hv_tech_id":
        hv = (req.get("technician") or {}).get("hv_certified_id")
        if hv:
            out.update(found=True, answer=f"{first}: I did the job — my HV certification ID is {hv}.",
                       provides={"hv_tech_id": hv})
    out["issue_types"] = [i.get("type") for i in issues]
    return final(out)
