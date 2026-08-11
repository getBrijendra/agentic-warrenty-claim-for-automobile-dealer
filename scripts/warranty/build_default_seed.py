"""Deterministically builds seed/generated/default.warranty.json from the hand-authored spec (no LLM, no DB).

    uv run python scripts/warranty/build_default_seed.py
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import seed_spec as S  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "seed" / "generated" / "default.warranty.json"
SIM = datetime.fromisoformat(S.SIM_START)

# ---------------------------------------------------------------- VIN helpers (fake, valid-format, NOT REAL)
TRANS = {**{str(i): i for i in range(10)}, "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
         "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9, "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7,
         "Y": 8, "Z": 9}
WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
YEAR = {2019: "K", 2020: "L", 2021: "M", 2022: "N", 2023: "P", 2024: "R", 2025: "S", 2026: "T"}
ALPHA = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"


def fake_vin(wmi: str, seed: str, year: int) -> str:
    h = hashlib.sha256(seed.encode()).digest()
    vds = "".join(ALPHA[b % len(ALPHA)] for b in h[:5])
    plant = ALPHA[h[6] % 23]
    serial = "".join(str(b % 10) for b in h[7:13])
    vin = wmi + vds + "0" + YEAR[year] + plant + serial
    total = sum(TRANS[c] * w for c, w in zip(vin, WEIGHTS))
    check = total % 11
    vin = vin[:8] + ("X" if check == 10 else str(check)) + vin[9:]
    assert len(vin) == 17 and not set("IOQ") & set(vin)
    return vin


def wmi_for(w: dict) -> str:
    if w["make"] == "Ford" and w["model"] in ("F-150", "Ranger"):
        return "MNC" if w["model"] == "Ranger" else "1FT"
    return S.WMI[w["make"]]


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def iso(d) -> str:
    return d.isoformat()


def build() -> dict:
    rng = random.Random(42)
    company = S.COMPANY
    vehicles, customers, ros, lines, atts, contracts, history = [], [], [], [], [], [], []
    manifest, oem_registry, baseline = [], [], []
    vin_by_key: dict[str, str] = {}

    for cid, (name, emirate) in S.CUSTOMERS.items():
        n = int(hashlib.md5(cid.encode()).hexdigest()[:4], 16) % 10000
        customers.append({"id": cid, "name": name, "phone": f"+971 50 000 {n:04d}", "emirate": emirate})

    for w in S.WIPS:
        vin = fake_vin(wmi_for(w), w["key"] + w["wip"], w["year"])
        vin_by_key[w["key"]] = vin
        start = date.fromisoformat(w["start"])
        closed = SIM - timedelta(days=w["days_ago"])
        opened = closed - timedelta(hours=30)
        vehicles.append({"vin": vin, "customer_id": w["cust"], "make": w["make"], "model": w["model"],
                         "model_year": w["year"], "plate": w["plate"], "oem_programme": w["programme"],
                         "warranty_start_date": iso(start), "market_spec": "GCC",
                         "aftermarket_mods": w.get("mods", []), "is_fictional": True})
        # coverage contracts (DMS view) + OEM registry view
        prev_end: date | None = None
        dms_contracts, oem_contracts = [], []
        for spec in w["contracts"]:
            ctype, years, km = spec[0], spec[1], spec[2]
            cond = spec[3] if len(spec) > 3 and spec[3] else {}
            scope = spec[4] if len(spec) > 4 else []
            c_start = (prev_end + timedelta(days=1)) if (ctype in ("EXTENDED", "LIMITED_EXTENDED") and prev_end) else start
            c_end = add_years(start, years) - timedelta(days=1)
            if ctype in ("BASE", "EXTENDED", "LIMITED_EXTENDED"):
                prev_end = c_end
            oem_scope = (w.get("oem_scope") or {}).get(ctype, scope)
            dms_scope = scope
            dms_cond = dict(cond)
            if ctype == "LIMITED_EXTENDED" and not scope:
                dms_cond["scope_note"] = "Drive-line cover per contract booklet (scope not keyed in DMS)"
            base = {"programme": w["programme"], "contract_type": ctype, "start_date": iso(c_start),
                    "end_date": iso(c_end), "km_limit": km}
            dms_contracts.append({**base, "id": f"CC-{w['wip']}-{len(dms_contracts) + 1}", "vin": vin,
                                  "component_scope": dms_scope, "conditions": dms_cond})
            oem_contracts.append({**base, "component_scope": oem_scope, "conditions": cond})
        contracts += dms_contracts
        # service history (periodic services), monotonic odometer
        interval_km, interval_m = S.SERVICE_INTERVALS[w["programme"]]
        days_total = (closed.date() - start).days
        km_per_day = w["odo"] / max(days_total, 1)
        d, km, n = start, 0, 0
        gap = w.get("service_gap")
        while True:
            by_km = interval_km / km_per_day
            step = min(by_km, interval_m * 30.4)
            d = d + timedelta(days=int(step))
            km = int(km + km_per_day * int(step))
            if d >= closed.date() - timedelta(days=5) or km >= w["odo"]:
                break
            n += 1
            if gap and gap[0] < km < gap[1]:
                continue
            history.append({"id": f"SH-{w['wip']}-{n}", "vin": vin, "service_date": iso(d), "odometer_km": km,
                            "kind": "periodic_service", "description": f"{interval_km // 1000}k periodic service",
                            "wip_no": None})
        if gap:
            history.append({"id": f"SH-{w['wip']}-gap", "vin": vin, "service_date": iso(start + timedelta(days=int(gap[0] / km_per_day))),
                            "odometer_km": gap[0], "kind": "periodic_service", "description": "periodic service",
                            "wip_no": None})
            history.append({"id": f"SH-{w['wip']}-gap2", "vin": vin, "service_date": iso(start + timedelta(days=int(gap[1] / km_per_day))),
                            "odometer_km": gap[1], "kind": "periodic_service", "description": "periodic service (overdue)",
                            "wip_no": None})
        # repair order
        ro_id = f"RO-{w['wip']}"
        ros.append({"id": ro_id, "wip_no": w["wip"], "vin": vin, "customer_id": w["cust"], "advisor_id": w["adv"],
                    "branch": next(s["branch"] for s in S.STAFF if s["id"] == w["tech"]), "status": "closed",
                    "opened_at": opened.isoformat(), "closed_at": closed.isoformat(), "pay_type_summary": "W",
                    "technician_story": w["story"], "customer_complaint": w["complaint"],
                    "dtc_codes": w.get("dtc", []), "diagnostic_session_id": w.get("diag_session"),
                    "pre_auth_ref": w.get("pre_auth"), "tech_support_ref": w.get("tech_support_ref"),
                    "odometer_in": w["odo"] - 3, "odometer_out": w["odo"]})
        parent = None
        for i, ln in enumerate(w["lines"], start=1):
            if ln[0] == "L":
                parent = i
                lines.append({"id": f"ROL-{w['wip']}-{i}", "repair_order_id": ro_id, "line_no": i, "line_type": "labour",
                              "parent_line_no": None, "op_code": ln[1], "part_no": None, "description": ln[2],
                              "qty": 1, "unit_price": 0, "labour_hours_actual": ln[3], "pay_type": "W",
                              "is_causal": False, "technician_id": w["tech"], "part_serial": None})
            else:
                lines.append({"id": f"ROL-{w['wip']}-{i}", "repair_order_id": ro_id, "line_no": i, "line_type": "part",
                              "parent_line_no": parent, "op_code": None, "part_no": ln[1], "description": ln[2],
                              "qty": ln[3], "unit_price": ln[4], "labour_hours_actual": None, "pay_type": "W",
                              "is_causal": ln[5], "technician_id": w["tech"], "part_serial": ln[6]})
        for i, (kind, title, caption) in enumerate(w["attachments"], start=1):
            aid = f"ATT-{w['wip']}-{i}"
            atts.append({"id": aid, "repair_order_id": ro_id, "kind": kind, "title": title, "caption": caption,
                         "captured_at": (closed - timedelta(hours=6)).isoformat(),
                         "blob_key": f"wip/{w['wip']}/{aid}.svg", "uploaded_by": w["tech"]})
        manifest.append({"repair_order_id": ro_id, "wip_no": w["wip"], "issues": w["issues"]})
        repair_hist = [{"repair_date": iso((closed - timedelta(days=h["days_before"])).date()), "system": h["system"],
                        "retailer": h["retailer"], "op": h["op"], "description": h["description"], "vin": vin}
                       for h in w.get("oem_repair_history", [])]
        oem_registry.append({"vin": vin, "programme": w["programme"], "warranty_start_date": iso(start),
                             "contracts": oem_contracts, "repair_history": repair_hist,
                             "service_history": [h for h in history if h["vin"] == vin]})
        if w.get("prior_claim"):
            pc = w["prior_claim"]
            pclosed = SIM - timedelta(days=pc["days_ago"])
            baseline.append({"claim_ref": pc["claim_ref"], "programme": w["programme"], "vin": vin,
                             "system": pc["system"], "wip_no": pc["wip"], "labour_op": pc["op"], "claim_type": "BASE",
                             "wip_closed_at": pclosed.isoformat(),
                             "submitted_at": (pclosed + timedelta(days=1)).isoformat(), "first_pass_rejected": None,
                             "rejection_codes": [], "final_status": "submitted", "amount_claimed": pc["amount"],
                             "amount_paid": None, "paid_at": None})

    # ---------------------------------------------------------------- baseline: 150 historical manual claims
    mix = ["FORD"] * 42 + ["JLR"] * 33 + ["FERRARI"] * 12 + ["MASERATI"] * 21 + ["VINFAST"] * 18 + ["FORD_TRUCKS"] * 24
    rng.shuffle(mix)
    seq = {p: 1 for p in S.COND_PREFIX}
    for i, prog in enumerate(mix):
        op = rng.choice(S.LABOUR_OPS[prog])
        closed = SIM - timedelta(days=rng.uniform(40, 395))
        amount = round(rng.uniform(900, 16000), 2)
        roll = rng.random()
        status, rejected, codes, sub_delay = "paid", False, [], rng.uniform(4, 16)
        if roll < 0.045:
            status, codes = "never_filed", []
        elif roll < 0.085:
            status, rejected, codes, sub_delay = "written_off", True, ["R02"], rng.uniform(31, 50)
        elif roll < 0.15:
            status, rejected, codes = "written_off", True, [rng.choice(["R01", "R09", "R11", "R12", "R04"])]
        elif roll < 0.30:
            status, rejected, codes = "paid", True, [rng.choice(["R03", "R04", "R05", "R06", "R10"])]
        submitted = None if status == "never_filed" else closed + timedelta(days=sub_delay)
        if status == "paid":
            cycle = rng.uniform(24, 46) if not rejected else rng.uniform(48, 95)
            paid_at = closed + timedelta(days=cycle)
            paid = amount if rng.random() > 0.1 else round(amount * rng.uniform(0.7, 0.95), 2)
        else:
            paid_at, paid = None, 0.0
        prefix = S.COND_PREFIX[prog][:2]
        baseline.append({"claim_ref": f"HC-{prefix}-2025-{seq[prog]:05d}", "programme": prog, "vin": None,
                         "system": op[2], "wip_no": None, "labour_op": op[0], "claim_type": "BASE",
                         "wip_closed_at": closed.isoformat(), "submitted_at": submitted.isoformat() if submitted else None,
                         "first_pass_rejected": None if submitted is None else rejected, "rejection_codes": codes,
                         "final_status": status, "amount_claimed": amount, "amount_paid": paid,
                         "paid_at": paid_at.isoformat() if paid_at else None})
        seq[prog] += 1

    labour_ops, cond_codes, oem_catalog = [], [], []
    for prog, ops in S.LABOUR_OPS.items():
        systems = sorted({o[2] for o in ops})
        for op in ops:
            labour_ops.append({"id": f"LOP-{op[0]}", "programme": prog, "op_code": op[0], "description": op[1],
                               "system": op[2], "standard_hours": op[3]})
            oem_catalog.append({"programme": prog, "op_code": op[0], "system": op[2], "standard_hours": op[3],
                                "causal_families": op[4]})
        for k, sysname in enumerate(systems, start=1):
            code = f"{S.COND_PREFIX[prog]}-{S.SYS2[sysname]}-{10 + k:02d}"
            cond_codes.append({"id": f"CND-{code}", "programme": prog, "code": code, "system": sysname,
                               "description": S.CONDITION_TEXT[sysname]})

    campaigns = []
    for cp in S.CAMPAIGNS:
        vins = [vin_by_key[k] for k in cp["affected_keys"]]
        wmi = {"JLR": "SAL", "FORD": "1FM", "VINFAST": "RLL"}[cp["programme"]]
        vins += [fake_vin(wmi, f"{cp['id']}-{n}", 2024) for n in range(cp["extra_vins"])]
        campaigns.append({k: cp[k] for k in ("id", "programme", "campaign_code", "title", "labour_op", "status")}
                         | {"affected_vins": vins})
    preauths = [{"ticket": p["ticket"], "ref": p["ref"], "programme": p["programme"], "vin": vin_by_key[p["key"]],
                 "kind": p["kind"], "status": "approved"} for p in S.OEM_PREAUTHS]

    return {
        "_note": "ALL PEOPLE, VINs, PLATES AND PHONE NUMBERS ARE FICTIONAL. Codes use invented prefixes.",
        "sim_start": S.SIM_START,
        "company": company, "staff": S.STAFF, "customers": customers, "vehicles": vehicles,
        "service_history": history, "coverage_contracts": contracts, "campaigns": campaigns,
        "labour_ops": labour_ops, "condition_codes": cond_codes, "repair_orders": ros, "repair_order_lines": lines,
        "attachments": atts, "baseline_claims": baseline,
        "oem_side": {"vehicle_registry": oem_registry, "campaigns": campaigns, "labour_catalog": oem_catalog,
                     "preauths": preauths},
        "issue_manifest": manifest,
    }


if __name__ == "__main__":
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT} — {len(data['repair_orders'])} WIPs, {len(data['baseline_claims'])} baseline claims")
