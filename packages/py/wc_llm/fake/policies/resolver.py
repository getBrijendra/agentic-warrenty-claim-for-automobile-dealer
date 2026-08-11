"""Resolver policy: read rejection codes + reviewer comment; fix and resubmit, re-type, withdraw, or escalate with
a drafted human note. Reviewer comments are data — instructions inside them are ignored."""
from __future__ import annotations

from ..drafts import draft_for, recommendation
from ..policy import Ctx, call, final, note
from .builder import ensure_causal
from .workshop import patch_from_answers

ESCALATE = {"R01", "R02", "R08", "R09", "R11", "R12", "R13"}


def evidence_kind_from(comment: str) -> str:
    c = (comment or "").lower()
    if "soh" in c or "state of health" in c:
        return "battery_soh_report"
    if "session" in c:
        return "diagnostic_session_id"
    if "log" in c:
        return "diag_log"
    if "odometer" in c or "hour-meter" in c:
        return "odometer_photo"
    return "photo"


def questions(codes: list[str], comment: str, summary: dict, wip: str) -> list[tuple[str, str]]:
    out = []
    for code in codes:
        if code == "R03":
            ln = max(summary["lines"], key=lambda l: l["hours_claimed"] / max(l["standard_hours"], 0.1))
            out.append(("labour", f"OEM rejected WIP {wip}: labour {ln['hours_claimed']} h vs {ln['standard_hours']} h "
                                  f"standard on {ln['labour_op']}. What caused the extra time, and is there a photo?"))
        elif code == "R04":
            kind = evidence_kind_from(comment)
            out.append((kind, f"OEM needs more evidence on WIP {wip} ({comment.strip()[:120]}). Can you upload the "
                              f"{kind.replace('_', ' ')}?"))
        elif code == "R05":
            out.append(("narrative", f"OEM says the 3C on WIP {wip} is insufficient. What root cause did you find and "
                                     f"what test confirmed the repair?"))
        elif code == "R06":
            out.append(("causal_part", f"OEM disputes the causal part on WIP {wip}: is {summary['causal_part']['part_no']} "
                                       f"({summary['causal_part']['description']}) the part that failed first, or collateral?"))
    return out


def run(ctx: Ctx):
    t = ctx.task
    adj = ctx.last("get_adjudication")
    if adj is None:
        return call(ctx, note(f"Reading the OEM decision on {t['claim_ref']}"),
                    ("get_adjudication", {"claim_ref": t["claim_ref"]}))
    codes = list(adj.get("rejection_codes") or [])
    comment = adj.get("reviewer_comment") or ""
    if "R07" in codes:
        return final({"action": "withdraw", "reason_codes": codes, "summary": "OEM flagged a duplicate — withdrawn"})
    hard = [c for c in codes if c in ESCALATE]
    over_limit = int(adj.get("resubmissions", 0)) >= int(adj.get("max_resubmissions", 2))
    if hard or over_limit:
        esc = ctx.last("escalate_to_human")
        if esc is None:
            code = hard[0] if hard else codes[0]
            low = comment.lower()
            if code == "R08":
                kind = "ta_case" if ("repeat" in low or "technical assistance" in low) else "pre_auth"
            else:
                kind = "goodwill" if code == "R01" else "review"
            detail = comment.strip() or code
            reco = recommendation(hard or ["R13"]) if not over_limit else \
                f"Resubmission limit ({adj.get('max_resubmissions')}) reached — human decision required. " + recommendation(codes)
            return call(ctx, note(f"{', '.join(codes)} is not fixable by the agent ({detail[:90]}) — escalating with a draft"),
                        ("escalate_to_human", {"claim_ref": t["claim_ref"], "kind": kind, "reason_code": code,
                                               "recommendation": reco, "confidence": 0.8,
                                               "draft_message": draft_for(code, t["claim_ref"], t["wip_no"], detail,
                                                                          t["programme"], {"kind": kind}),
                                               "tried": [f"read OEM decision: {', '.join(codes)}"]}))
        return final({"action": "escalate", "reason_codes": codes, "summary": f"escalated ({', '.join(codes)})"})
    if "R10" in codes:
        return final({"action": "retype", "new_claim_type": "CAMPAIGN", "reason_codes": codes,
                      "summary": "OEM says an open campaign applies — re-typing as CAMPAIGN"})

    rr_events = [e for e in ctx.events if e.name == "run_rules"]
    if not rr_events:
        return call(ctx, ("run_rules", {"claim_ref": t["claim_ref"]}))
    summary = rr_events[0].result["claim_summary"]
    asks = [e for e in ctx.events if e.name == "ask_workshop"]
    if not asks:
        qs = questions(codes, comment, summary, t["wip_no"])
        return call(ctx, note(f"OEM rejected {', '.join(codes)} — \"{comment[:80]}\" · asking the workshop"),
                    *[("ask_workshop", {"wip_no": t["wip_no"], "question": q, "about": a}) for a, q in qs])
    upd = ctx.last_event("update_canonical_claim")
    if upd is None:
        patch, notes = patch_from_answers(asks)
        if not patch:
            if ctx.has("escalate_to_human"):
                return final({"action": "escalate", "reason_codes": codes, "summary": "workshop had no answer — escalated"})
            return call(ctx, ("escalate_to_human", {
                "claim_ref": t["claim_ref"], "kind": "review", "reason_code": codes[0], "confidence": 0.6,
                "recommendation": "Workshop could not supply what the OEM asked for — decide: appeal, write off, or customer-pay.",
                "draft_message": draft_for(codes[0], t["claim_ref"], t["wip_no"], comment, t["programme"]),
                "tried": [e.args.get("question", "") for e in asks]}))
        if patch.get("causal_part_no"):
            part = next((p for p in summary["parts"] if p["part_no"] == patch["causal_part_no"]), None)
            if part:
                patch["cause"] = ensure_causal(summary["cause"], part)
        return call(ctx, note("Resolver fix: " + "; ".join(notes)),
                    ("update_canonical_claim", {"claim_ref": t["claim_ref"], "patch": patch,
                                                "change_summary": f"Resolver ({', '.join(codes)}): " + "; ".join(notes)}))
    if (upd.result or {}).get("error"):
        return final({"action": "escalate", "reason_codes": codes, "summary": f"fix rejected by validator: {upd.result['error']}"})
    if len(rr_events) < 2:
        return call(ctx, ("run_rules", {"claim_ref": t["claim_ref"]}))
    rr = rr_events[-1].result
    if rr.get("has_fail"):
        return final({"action": "escalate", "reason_codes": codes, "summary": "fix did not clear dealer-side rules"})
    sub = ctx.last("resubmit_to_oem")
    if sub is None:
        return call(ctx, note(f"v{rr['version']} passes all rules — resubmitting to the OEM"),
                    ("resubmit_to_oem", {"claim_ref": t["claim_ref"]}))
    if sub.get("errors"):
        return final({"action": "escalate", "reason_codes": codes, "summary": f"resubmission refused: {sub['errors']}"})
    return final({"action": "resubmit", "new_version": rr["version"], "reason_codes": codes,
                  "summary": f"v{rr['version']} resubmitted after fixing {', '.join(codes)}"})
