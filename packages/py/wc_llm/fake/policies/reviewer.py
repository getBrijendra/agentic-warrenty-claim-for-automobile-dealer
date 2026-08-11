"""OEM reviewer comment (fast role). Written from the deterministic reason codes ONLY; cannot change the decision."""
from __future__ import annotations

from ..policy import Ctx, final
from ..text import pick

ACCEPT = ["Claim approved for payment.", "Approved. Credit to follow on the next payment run.",
          "Accepted as submitted."]
CLOSE = ["Resubmit with corrections.", "Correct and resubmit.", "Amend and resubmit within the allowed limit.",
         "Refer to programme guidelines."]
FINAL = ["No further action on this claim.", "Claim closed.", "Not payable under the programme."]


def run(ctx: Ctx):
    req = ctx.task
    if req["decision"] == "accepted":
        return final({"comment": pick(ctx.rng, ACCEPT)})
    parts = [f"{c}: {req['wording'].get(c, c)}" for c in req["codes"]]
    fixable = req.get("fixable", True)
    return final({"comment": " ".join(parts) + " " + pick(ctx.rng, CLOSE if fixable else FINAL)})
