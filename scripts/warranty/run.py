"""Headless batch run against a running stack (scripts/dev.py), with the CLAUDE.md §13 acceptance check.

    uv run python scripts/warranty/run.py [--no-reset] [--mode live|replay] [--timeout 360]
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

API = "http://127.0.0.1:8000/api/v1"
DONE = {"paid", "written_off", "withdrawn", "escalated"}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--mode", default=None)
    ap.add_argument("--timeout", type=float, default=360)
    a = ap.parse_args()
    c = httpx.Client(timeout=120)
    if not a.no_reset:
        c.post(f"{API}/admin/reset").raise_for_status()
        for _ in range(60):  # wait for ingestion
            if len([x for x in c.get(f"{API}/claims").json()["cards"] if x["status"] == "wip_closed"]) >= 24:
                break
            time.sleep(0.5)
    if a.mode:
        c.post(f"{API}/mode", json={"mode": a.mode}).raise_for_status()
    run = c.post(f"{API}/runs", json={"scope": "batch"}).json()
    print(f"run {run['run_id']} · {run['claims']} claims · mode {run['mode']}")
    t0, last = time.time(), ""
    while time.time() - t0 < a.timeout:
        cards = c.get(f"{API}/claims").json()["cards"]
        counts: dict[str, int] = {}
        for x in cards:
            counts[x["status"]] = counts.get(x["status"], 0) + 1
        line = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        if line != last:
            print(f"{time.time() - t0:6.1f}s  {line}")
            last = line
        if all(x["status"] in DONE for x in cards):
            break
        time.sleep(1)
    elapsed = time.time() - t0
    cards = c.get(f"{API}/claims").json()["cards"]
    k = c.get(f"{API}/kpis").json()
    ag, bl = k["agent"], k["baseline"]
    print("\nclaim               wip    programme    status       type        first-pass  flags")
    for x in cards:
        print(f"{x['claim_ref']:<19} {x['wip_no']:<6} {x['programme']:<12} {x['status']:<12} {str(x['claim_type']):<11} "
              f"{str(x['first_pass_result']):<11} {','.join(x['flags'] or [])}")
    print(f"\nKPIs (agent vs baseline): first-pass rejection {ag['first_pass_rejection_rate']} vs {bl['first_pass_rejection_rate']} · "
          f"median cycle {ag['cycle_median_days']}d vs {bl['cycle_median_days']}d · recovered AED {ag['recovered']:,.0f} · "
          f"unrecovered AED {ag['unrecovered']:,.0f} · auto-resolved {ag['auto_resolved']}/{ag['claims']} · escalated {ag['escalated']}")
    by = {x["wip_no"]: x for x in cards}
    checks = [
        ("all claims settled", all(x["status"] in DONE for x in cards)),
        (f"finished in < 6 min ({elapsed:.0f}s)", elapsed < 360),
        (f">= 3 reject → fix → accept ({ag['reject_fix_accept']})", ag["reject_fix_accept"] >= 3),
        (f">= 4 escalations ({ag['escalated']})", ag["escalated"] >= 4),
        ("VinFast battery-term claim accepted (WIP 48210)", by["48210"]["status"] == "paid" and by["48210"]["claim_type"] == "COMPONENT"),
        ("duplicate withdrawn (WIP 48221)", by["48221"]["status"] == "withdrawn"),
        ("Defender re-typed to CAMPAIGN (WIP 48213)", by["48213"]["claim_type"] == "CAMPAIGN"),
    ]
    print()
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
