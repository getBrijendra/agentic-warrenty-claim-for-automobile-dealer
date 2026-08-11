"""Seed loader: JSON → the three databases (pg-dms, pg-sim, pg-core). One transaction per database; idempotent
per company (existing rows for the company are replaced). Used by scripts/warranty/seed.py and admin reset."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from wc_core.infra import LocalFsBlobStore
from wc_core.placeholder import placeholder_svg
from wc_core.settings import ROOT, get_settings
from .db import j, migrate, tx

DMS_TABLES = ["al_warranty_credit_notes", "al_warranty_claims", "al_attachments", "al_repair_order_lines",
              "al_repair_orders", "al_service_history", "al_coverage_contracts", "al_campaigns", "al_labour_ops",
              "al_condition_codes", "al_vehicles", "al_customers", "al_staff", "al_companies"]
SIM_TABLES = ["oem_sim.oem_credit_notes", "oem_sim.oem_adjudications", "oem_sim.oem_submissions", "oem_sim.oem_preauths",
              "oem_sim.oem_labour_catalog", "oem_sim.oem_campaigns", "oem_sim.oem_vehicle_registry",
              "groundtruth.seed_issue_manifest", "groundtruth.scoring_results"]


def insert(c, table: str, row: dict) -> None:
    cols = list(row)
    c.execute(f"insert into {table} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",
              [j(v) if isinstance(v, (dict,)) or (isinstance(v, list) and v and isinstance(v[0], dict)) else v
               for v in row.values()])


def load(slug: str) -> dict:
    return json.loads((ROOT / "seed" / "generated" / f"{slug}.warranty.json").read_text(encoding="utf-8"))


def migrate_all() -> None:
    for db in ("core", "dms", "sim"):
        migrate(db)


def seed_external(slug: str) -> dict:
    """(Re)load the fake Autoline DMS + simulated OEM side + ground truth. Keeps WIPs exactly as seeded."""
    s = get_settings()
    cid = s.company_id
    data = load(slug)
    migrate_all()
    blobs = LocalFsBlobStore()

    with tx("dms") as c:
        for t in DMS_TABLES:
            c.execute(f"delete from autoline.{t} where company_id = %s", [cid])
        insert(c, "autoline.al_companies", {"company_id": cid, "name": data["company"]["name"],
                                            "dealer_codes": data["company"]["dealer_codes"],
                                            "labour_rates": data["company"]["labour_rates"]})
        for st in data["staff"]:
            insert(c, "autoline.al_staff", {"company_id": cid, **{k: st.get(k) for k in ("id", "name", "role", "branch", "hv_certified_id")}})
        for cu in data["customers"]:
            insert(c, "autoline.al_customers", {"company_id": cid, **cu})
        for v in data["vehicles"]:
            insert(c, "autoline.al_vehicles", {"company_id": cid, **v})
        for h in data["service_history"]:
            insert(c, "autoline.al_service_history", {"company_id": cid, **h})
        for cc in data["coverage_contracts"]:
            insert(c, "autoline.al_coverage_contracts", {"company_id": cid, **cc})
        for cp in data["campaigns"]:
            insert(c, "autoline.al_campaigns", {"company_id": cid, **cp})
        for lo in data["labour_ops"]:
            insert(c, "autoline.al_labour_ops", {"company_id": cid, **lo})
        for cc in data["condition_codes"]:
            insert(c, "autoline.al_condition_codes", {"company_id": cid, **cc})
        for ro in data["repair_orders"]:
            insert(c, "autoline.al_repair_orders", {"company_id": cid, **ro})
        for ln in data["repair_order_lines"]:
            insert(c, "autoline.al_repair_order_lines", {"company_id": cid, **ln})
        wip_by_ro = {r["id"]: r["wip_no"] for r in data["repair_orders"]}
        for a in data["attachments"]:
            url = blobs.put(a["blob_key"], placeholder_svg(a["kind"], a["title"], a["caption"], wip_by_ro[a["repair_order_id"]],
                                                           a["captured_at"]), "image/svg+xml")
            insert(c, "autoline.al_attachments", {"company_id": cid, **a, "url": url})

    with tx("sim") as c:
        for t in SIM_TABLES:
            c.execute(f"delete from {t} where company_id = %s", [cid])
        oem = data["oem_side"]
        for r in oem["vehicle_registry"]:
            insert(c, "oem_sim.oem_vehicle_registry", {"company_id": cid, "vin": r["vin"], "programme": r["programme"],
                                                       "warranty_start_date": r["warranty_start_date"],
                                                       "contracts": j(r["contracts"]), "repair_history": j(r["repair_history"]),
                                                       "service_history": j(r["service_history"])})
        for cp in oem["campaigns"]:
            insert(c, "oem_sim.oem_campaigns", {"company_id": cid, **cp})
        for lc in oem["labour_catalog"]:
            insert(c, "oem_sim.oem_labour_catalog", {"company_id": cid, **lc})
        for pa in oem["preauths"]:
            insert(c, "oem_sim.oem_preauths", {"company_id": cid, **pa, "decided_by": "seed"})
        for m in data["issue_manifest"]:
            insert(c, "groundtruth.seed_issue_manifest", {"company_id": cid, "repair_order_id": m["repair_order_id"],
                                                          "wip_no": m["wip_no"], "issues": j(m["issues"])})

    return data


def seed(slug: str) -> None:
    s = get_settings()
    cid = s.company_id
    data = seed_external(slug)
    with tx("core") as c:
        c.execute("delete from warranty.baseline_claims where company_id = %s", [cid])
        for b in data["baseline_claims"]:
            insert(c, "warranty.baseline_claims", {"id": str(uuid.uuid4()), "company_id": cid, **b})
        c.execute("""insert into ops.tenants (company_id, slug, display_name, settings) values (%s,%s,%s,%s)
                     on conflict (company_id) do update set display_name = excluded.display_name, settings = excluded.settings""",
                  [cid, slug, s.brand_display_name, j({"currency": s.currency, "vat_rate": s.vat_rate,
                                                       "dealer_codes": data["company"]["dealer_codes"],
                                                       "labour_rates": data["company"]["labour_rates"],
                                                       "terminology": {"wip": "WIP", "job_card": "Job Card"},
                                                       "escalation_amount": s.escalation_amount_aed})])
        c.execute("""insert into ops.users (id, company_id, email, display_name, role, password_hash)
                     values (%s,%s,'admin@demo.local','Warranty Admin (demo)','warranty_admin','demo-only:plain:demo')
                     on conflict (email) do nothing""", [str(uuid.uuid4()), cid])
        c.execute("""insert into ops.sim_clock (id, company_id, base_sim, base_real, speed, paused) values (1,%s,%s,%s,%s,true)
                     on conflict (id) do nothing""",
                  [cid, data["sim_start"], datetime.now(timezone.utc), s.warranty_sim_speed])
    print(f"seeded '{slug}': {len(data['repair_orders'])} WIPs, {len(data['baseline_claims'])} baseline claims")

