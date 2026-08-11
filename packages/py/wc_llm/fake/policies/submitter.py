"""Submitter policy: canonical → OEM payload via the deterministic adapter, fix format errors, submit."""
from __future__ import annotations

from ..policy import Ctx, call, final, note


def run(ctx: Ctx):
    t = ctx.task
    seq = [e for e in ctx.events if e.name in ("build_oem_payload", "submit_to_oem")]
    builds = [e for e in seq if e.name == "build_oem_payload"]
    if not builds:
        return call(ctx, note(f"Building the {t['programme']} payload for {t['claim_ref']}"),
                    ("build_oem_payload", {"claim_ref": t["claim_ref"], "normalise": False}))
    last = seq[-1]
    if last.name == "build_oem_payload":
        b = last.result
        if b.get("errors"):
            if len(builds) == 1:
                return call(ctx, note(f"Payload has {len(b['errors'])} format error(s) — normalising and rebuilding"),
                            ("build_oem_payload", {"claim_ref": t["claim_ref"], "normalise": True}))
            return final({"submitted": False, "format_errors": [f"{e['field']}: {e['message']}" for e in b["errors"]]})
        return call(ctx, note(f"Submitting {t['claim_ref']} v{b['version']} to the Simulated OEM Gateway"),
                    ("submit_to_oem", {"claim_ref": t["claim_ref"]}))
    sub = last.result
    if sub.get("errors") or not sub.get("oem_claim_id"):
        if len(builds) == 1:
            return call(ctx, note("Gateway returned a format error — normalising the payload"),
                        ("build_oem_payload", {"claim_ref": t["claim_ref"], "normalise": True}))
        errs = sub.get("errors") or ["no claim id returned"]
        return final({"submitted": False, "format_errors": [str(e) for e in (errs if isinstance(errs, list) else [errs])]})
    return final({"submitted": True, "oem_claim_id": sub["oem_claim_id"], "version": builds[-1].result["version"],
                  "format_errors": []})
