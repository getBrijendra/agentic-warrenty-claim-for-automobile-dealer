"""Hidden ground truth (seed_issue_manifest). ONLY imported by workshop-sim (persona + scoring).
Agent tools must never import this module — enforced by tests/warranty/test_architecture.py."""
from __future__ import annotations

from wc_db.db import connect, fetchall, fetchone, j


def issues_for(repair_order_id: str) -> list[dict]:
    row = fetchone("sim", "select issues from groundtruth.seed_issue_manifest where repair_order_id=%s", [repair_order_id])
    return (row or {}).get("issues") or []


def manifest() -> list[dict]:
    return fetchall("sim", "select repair_order_id, wip_no, issues from groundtruth.seed_issue_manifest order by wip_no")


def save_score(company_id: str, result: dict) -> None:
    with connect("sim") as c:
        c.execute("insert into groundtruth.scoring_results (company_id, result) values (%s,%s)", [company_id, j(result)])


EXPECTED_CATCH = {
    "weak_narrative": "agent", "missing_evidence": "agent", "labour_over_standard": "gateway", "wrong_causal_part": "gateway",
    "open_campaign": "agent", "coverage_km_exceeded": "agent", "extended_scope": "gateway", "missed_service": "agent",
    "repeat_repair": "gateway", "preauth_missing": "agent", "window_near": "agent", "duplicate": "agent",
    "aftermarket_mod": "agent", "gateway_evidence": "gateway",
}


def score(claims: list[dict]) -> dict:
    """claims: [{wip_no, claim_ref, status, agent_codes[], gateway_codes[], final_ok}] → per-issue catch attribution."""
    by_wip = {c["wip_no"]: c for c in claims}
    rows, totals = [], {"caught_by_agent": 0, "caught_by_gateway": 0, "missed": 0, "showcase_ok": 0, "showcase_failed": 0}
    for m in manifest():
        c = by_wip.get(m["wip_no"])
        for issue in m["issues"] or [{"type": "clean", "code": None}]:
            code = issue.get("code")
            if c is None:
                verdict = "not_run"
            elif code is None:
                verdict = "showcase_ok" if c.get("final_ok") else "showcase_failed"
            elif code in (c.get("agent_codes") or []):
                verdict = "caught_by_agent"
            elif code in (c.get("gateway_codes") or []):
                verdict = "caught_by_gateway"
            else:
                verdict = "missed"
            if verdict in totals:
                totals[verdict] += 1
            rows.append({"wip_no": m["wip_no"], "claim_ref": c and c["claim_ref"], "issue": issue.get("type"), "code": code,
                         "detail": issue.get("detail"), "verdict": verdict,
                         "expected": EXPECTED_CATCH.get(issue.get("type"), "pass")})
    return {"totals": totals, "rows": rows}
