"""KPIs: first-pass rejection, cycle time (median / P90 sim-days), recovered / unrecovered, auto-resolved vs
escalated — for the agent run and for the manual baseline (12 months of seeded historical claims)."""
from __future__ import annotations

import statistics
from datetime import datetime

from wc_db.db import fetchall


def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    k = (len(vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 1)


def agent_kpis(now: datetime) -> dict:
    claims = fetchall("core", "select * from warranty.claims")
    esc = {r["claim_id"] for r in fetchall("core", "select distinct claim_id from warranty.escalations")}
    decided = [c for c in claims if c["first_pass_result"]]
    rejected_first = [c for c in decided if c["first_pass_result"] == "rejected"]
    paid = [c for c in claims if c["status"] == "paid"]
    cycles = [(c["sim_paid_at"] - c["sim_closed_at"]).total_seconds() / 86400 for c in paid if c["sim_paid_at"]]
    recovered = sum(float(c["amount_paid"] or 0) for c in paid)
    written_off = [c for c in claims if c["status"] == "written_off"]
    missed = [c for c in claims if c["status"] not in ("submitted", "resubmitted", "accepted", "paid", "rejected",
                                                       "resolving", "withdrawn", "written_off")
              and c["window_due_at"] and c["window_due_at"] < now and not c["sim_submitted_at"]]
    partial_loss = sum(float(c["amount_claimed"] or 0) - float(c["amount_paid"] or 0) for c in paid
                       if c["amount_claimed"] and float(c["amount_paid"] or 0) < float(c["amount_claimed"]))
    unrecovered = sum(float(c["amount_claimed"] or 0) for c in written_off + missed) + partial_loss
    at_risk = sum(float(c["amount_claimed"] or 0) for c in claims if c["status"] == "escalated")
    in_flight = [c for c in claims if c["status"] not in ("paid", "written_off", "withdrawn", "escalated", "wip_closed")]
    claimed_total = sum(float(c["amount_claimed"] or 0) for c in claims if c["status"] != "withdrawn")
    return {
        "claims": len(claims), "submitted": len(decided), "first_pass_rejected": len(rejected_first),
        "first_pass_rejection_rate": round(len(rejected_first) / len(decided), 3) if decided else None,
        "cycle_median_days": round(statistics.median(cycles), 1) if cycles else None, "cycle_p90_days": _pct(cycles, 0.9),
        "recovered": round(recovered, 2), "unrecovered": round(unrecovered, 2), "at_risk_escalated": round(at_risk, 2),
        "unrecovered_rate": round(unrecovered / claimed_total, 3) if claimed_total else None,
        "paid": len(paid), "auto_resolved": len([c for c in paid if c["id"] not in esc and not c["touched_by_human"]]),
        "escalated": len(esc), "escalated_open": len([c for c in claims if c["status"] == "escalated"]),
        "withdrawn": len([c for c in claims if c["status"] == "withdrawn"]), "in_flight": len(in_flight),
        "reject_fix_accept": len([c for c in claims if c["first_pass_result"] == "rejected" and c["status"] in ("accepted", "paid")]),
    }


def baseline_kpis() -> dict:
    rows = [r for r in fetchall("core", "select * from warranty.baseline_claims") if r["final_status"] != "submitted"]
    sub = [r for r in rows if r["submitted_at"]]
    rej = [r for r in sub if r["first_pass_rejected"]]
    paid = [r for r in rows if r["final_status"] == "paid"]
    cycles = [(r["paid_at"] - r["wip_closed_at"]).total_seconds() / 86400 for r in paid if r["paid_at"]]
    claimed = sum(float(r["amount_claimed"] or 0) for r in rows)
    recovered = sum(float(r["amount_paid"] or 0) for r in paid)
    unrec = claimed - recovered
    return {"claims": len(rows), "submitted": len(sub), "first_pass_rejection_rate": round(len(rej) / len(sub), 3) if sub else None,
            "cycle_median_days": round(statistics.median(cycles), 1) if cycles else None, "cycle_p90_days": _pct(cycles, 0.9),
            "recovered": round(recovered, 2), "unrecovered": round(unrec, 2),
            "unrecovered_rate": round(unrec / claimed, 3) if claimed else None,
            "never_filed": len([r for r in rows if r["final_status"] == "never_filed"]),
            "window_missed": len([r for r in rows if "R02" in (r["rejection_codes"] or [])]),
            "period": "12 months of manual claims (seeded history)"}
