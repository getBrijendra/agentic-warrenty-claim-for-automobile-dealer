"""Compliance policy: run the rule pack, interpret failures, fix what is fixable (workshop queries, re-typing),
request pre-authorisation, and block / escalate hard fails."""
from __future__ import annotations

from ..drafts import draft_for, recommendation
from ..policy import Ctx, call, final, note
from .workshop import patch_from_answers, topics_for

HARD = {"R01", "R02", "R07", "R09", "R11", "R12"}


def run(ctx: Ctx):
    t = ctx.task
    rr_ev = ctx.last_event("run_rules")
    if rr_ev is None:
        return call(ctx, note(f"Running the {t['programme']} rule pack on {t['claim_ref']}"),
                    ("run_rules", {"claim_ref": t["claim_ref"]}))
    rr = rr_ev.result
    after = ctx.after(rr_ev)
    fails = [r for r in rr["results"] if r["result"] == "fail"]
    warns = [r for r in rr["results"] if r["result"] == "warn"]
    fixes = [e.args.get("change_summary", "") for e in ctx.events if e.name == "update_canonical_claim" and
             not (e.result or {}).get("error")]

    if not fails:
        if not any(e.name == "emit_status_note" for e in after):
            w = f" · {len(warns)} warning(s): " + "; ".join(x["detail"] for x in warns[:2]) if warns else ""
            return call(ctx, note(f"All {len(rr['results'])} rules pass on v{rr['version']}{w}"))
        return final({"status": "ready", "fixes": fixes, "reason_codes": [w["code"] for w in warns if w.get("code")]})

    hard = [f for f in fails if f.get("code") in HARD]
    if hard:
        codes = sorted({f["code"] for f in hard})
        detail = "; ".join(f["detail"] for f in hard)
        if not any(e.name == "emit_status_note" for e in after):
            return call(ctx, note(f"Hard fail {', '.join(codes)}: {hard[0]['detail']} — escalating to a human"))
        return final({"status": "escalate", "reason_codes": codes, "fixes": fixes, "recommendation": recommendation(codes),
                      "draft_message": draft_for(codes[0], t["claim_ref"], t["wip_no"], detail, t["programme"])})

    hint = {"wip_no": t["wip_no"], "causal_part_no": rr.get("causal_part_no")}
    fixable = [f for f in fails if f.get("fixable")]
    retype = next((f for f in fixable if f.get("code") == "R10"), None)
    updates_after = [e for e in after if e.name == "update_canonical_claim"]
    asks_after = [e for e in after if e.name == "ask_workshop"]
    asked_before = {e.args.get("about") for e in ctx.events if e.name == "ask_workshop" and e.index < rr_ev.index}

    if updates_after:
        if (updates_after[-1].result or {}).get("error"):
            return final({"status": "blocked", "fixes": fixes,
                          "open_questions": [f"validator rejected fix: {updates_after[-1].result['error']}"],
                          "reason_codes": sorted({f['code'] for f in fails if f.get('code')})})
        return call(ctx, ("run_rules", {"claim_ref": t["claim_ref"]}))

    if retype:
        return call(ctx, note(f"Re-typing as CAMPAIGN {retype['data'].get('campaign_code')}"),
                    ("update_canonical_claim", {"claim_ref": t["claim_ref"], "change_summary": "re-typed as CAMPAIGN",
                                                "patch": {"claim_type": "CAMPAIGN",
                                                          "campaign_code": retype["data"].get("campaign_code")}}))
    if fixable:
        if asks_after:
            patch, notes = patch_from_answers(asks_after)
            if not patch:
                return final({"status": "blocked", "fixes": fixes, "reason_codes": sorted({f["code"] for f in fixable}),
                              "open_questions": [e.args.get("question", "") for e in asks_after],
                              "recommendation": "Workshop could not supply the missing facts — human follow-up needed."})
            return call(ctx, note("Workshop answered: " + "; ".join(notes)),
                        ("update_canonical_claim", {"claim_ref": t["claim_ref"], "patch": patch,
                                                    "change_summary": "; ".join(notes)}))
        topics = [tp for f in fixable for tp in topics_for(f, hint)]
        new = [(a, q) for a, q in topics if a not in asked_before]
        if new:
            return call(ctx, note(f"{fixable[0]['label']}: {fixable[0]['detail']} — asking the workshop"),
                        *[("ask_workshop", {"wip_no": t["wip_no"], "question": q, "about": a}) for a, q in new])
        return final({"status": "blocked", "fixes": fixes, "reason_codes": sorted({f["code"] for f in fixable}),
                      "open_questions": [f["detail"] for f in fixable],
                      "recommendation": "Fix attempts exhausted — human review required."})

    # remaining fails are authorisation-type (R08): request PA / TA case — a human must approve
    auth = fails[0]
    kind = "ta_case" if auth["rule_key"] == "repeat_repair" and "TA" in auth["detail"] else "pre_auth"
    req = ctx.last("request_pre_authorisation")
    if req is None:
        return call(ctx, note(f"{auth['label']}: {auth['detail']} — requesting {kind.replace('_', ' ')} from the OEM"),
                    ("request_pre_authorisation", {"claim_ref": t["claim_ref"], "kind": kind, "reason": auth["detail"]}))
    return final({"status": "blocked", "fixes": fixes, "reason_codes": ["R08"], "pre_auth_ticket": req.get("ticket"),
                  "open_questions": [f"{kind} ticket {req.get('ticket')} awaiting OEM / human decision"],
                  "recommendation": recommendation(["R08"]),
                  "draft_message": draft_for("R08", t["claim_ref"], t["wip_no"], auth["detail"], t["programme"],
                                             {"kind": kind})})
