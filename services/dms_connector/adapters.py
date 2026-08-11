"""DmsPort adapters. AutolineFakeAdapter reads the fake Autoline schema on pg-dms; real adapters are stubs."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from wc_core.ids import uuid7
from wc_core.infra import LocalFsBlobStore
from wc_core.placeholder import placeholder_svg
from wc_core.ports import NotConfigured
from wc_core.settings import get_settings
from wc_db.db import connect, fetchall, fetchone, tx

FACT_FIELDS = {"diagnostic_session_id", "tech_support_ref", "pre_auth_ref"}


def _f(v: Any) -> Any:
    from decimal import Decimal
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def clean(row: dict | None) -> dict | None:
    return None if row is None else {k: _f(v) for k, v in row.items() if k not in ("company_id", "created_at", "updated_at")}


class AutolineFakeAdapter:
    """Autoline vocabulary (WIP, pay types C/W/I/CP/MP) mapped to the DMS-neutral model."""

    def __init__(self):
        self.cid = get_settings().company_id
        self.blobs = LocalFsBlobStore()

    def capabilities(self) -> dict:
        return {"adapter": "autoline_fake", "vendor": "Autoline (fake)", "read": True, "write_claim_status": True,
                "upload_attachments": True, "change_detection": "pull", "simulated": True}

    def list_closed_wips(self, since: datetime | None) -> list[dict]:
        rows = fetchall("dms", """select r.wip_no, r.id as repair_order_id, r.vin, r.closed_at, v.oem_programme as programme,
                                          v.make, v.model
                                   from autoline.al_repair_orders r join autoline.al_vehicles v on v.vin = r.vin
                                   where r.company_id = %s and r.status = 'closed' and (%s::timestamptz is null or r.closed_at > %s)
                                   order by r.wip_no""", [self.cid, since, since])
        return [clean(r) for r in rows]

    def _staff(self, sid: str | None) -> dict | None:
        return clean(fetchone("dms", "select id, name, role, branch, hv_certified_id from autoline.al_staff where id=%s", [sid]))

    def get_repair_order(self, wip_no: str) -> dict:
        ro = fetchone("dms", "select * from autoline.al_repair_orders where company_id=%s and wip_no=%s", [self.cid, wip_no])
        if not ro:
            raise KeyError(f"WIP {wip_no} not found")
        ro = clean(ro)
        veh = clean(fetchone("dms", "select * from autoline.al_vehicles where vin=%s", [ro["vin"]]))
        lines = [clean(r) for r in fetchall("dms", """
            select l.*, o.system as op_system, o.standard_hours as op_standard_hours, o.description as op_description
            from autoline.al_repair_order_lines l
            left join autoline.al_labour_ops o on o.op_code = l.op_code and o.programme = %s
            where l.repair_order_id = %s order by l.line_no""", [veh["oem_programme"], ro["id"]])]
        techs = sorted({ln["technician_id"] for ln in lines if ln.get("technician_id")})
        ro.update({"programme": veh["oem_programme"], "make": veh["make"], "model": veh["model"],
                   "model_year": veh["model_year"], "lines": lines,
                   "technicians": [self._staff(t) for t in techs],
                   "advisor": self._staff(ro.get("advisor_id"))})
        return ro

    def get_job_card(self, wip_no: str) -> dict:
        ro = self.get_repair_order(wip_no)
        ro["vehicle"] = self.get_vehicle(ro["vin"])
        ro["customer"] = clean(fetchone("dms", "select id, name, phone, emirate from autoline.al_customers where id=%s",
                                        [ro["customer_id"]]))
        ro["attachments"] = self.list_attachments(wip_no)
        ro["warranty_module"] = [clean(r) for r in fetchall("dms", "select * from autoline.al_warranty_claims where wip_no=%s", [wip_no])]
        return ro

    def get_vehicle(self, vin: str) -> dict:
        v = clean(fetchone("dms", "select * from autoline.al_vehicles where company_id=%s and vin=%s", [self.cid, vin]))
        if not v:
            raise KeyError(f"vehicle {vin} not found")
        return v

    def get_service_history(self, vin: str) -> list[dict]:
        return [clean(r) for r in fetchall("dms", "select * from autoline.al_service_history where vin=%s order by service_date", [vin])]

    def get_coverage_contracts(self, vin: str) -> list[dict]:
        return [clean(r) for r in fetchall("dms", "select * from autoline.al_coverage_contracts where vin=%s order by start_date", [vin])]

    def list_attachments(self, wip_no: str) -> list[dict]:
        return [clean(r) for r in fetchall("dms", """select a.* from autoline.al_attachments a
                 join autoline.al_repair_orders r on r.id = a.repair_order_id where r.wip_no=%s order by a.created_at, a.id""",
                                           [wip_no])]

    def upload_attachment(self, wip_no: str, kind: str, title: str, caption: str, captured_at: str,
                          uploaded_by: str = "workshop") -> dict:
        ro = fetchone("dms", "select id from autoline.al_repair_orders where wip_no=%s", [wip_no])
        n = fetchone("dms", "select count(*) as n from autoline.al_attachments where repair_order_id=%s", [ro["id"]])["n"]
        aid = f"ATT-{wip_no}-{n + 1}"  # deterministic so replay runs reproduce tool arguments
        key = f"wip/{wip_no}/{aid}.svg"
        url = self.blobs.put(key, placeholder_svg(kind, title, caption, wip_no, captured_at), "image/svg+xml")
        with tx("dms") as c:
            c.execute("""insert into autoline.al_attachments (id, company_id, repair_order_id, kind, title, url, blob_key,
                         caption, captured_at, uploaded_by) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      [aid, self.cid, ro["id"], kind, title, url, key, caption, captured_at, uploaded_by])
        return {"id": aid, "kind": kind, "title": title, "url": url, "caption": caption, "captured_at": captured_at}

    def record_fact(self, wip_no: str, field: str, value: str) -> dict:
        if field not in FACT_FIELDS:
            raise ValueError(f"field {field} cannot be recorded")
        with tx("dms") as c:
            c.execute(f"update autoline.al_repair_orders set {field}=%s, updated_at=now() where wip_no=%s", [value, wip_no])
        return {"wip_no": wip_no, field: value}

    def get_labour_ops(self, programme: str, query: str | None, codes: list[str] | None) -> list[dict]:
        rows = fetchall("dms", """select op_code, description, system, standard_hours from autoline.al_labour_ops
                                  where programme=%s and (%s::text is null or description ilike %s or system ilike %s)
                                  and (%s::text[] is null or op_code = any(%s)) order by op_code""",
                        [programme, query, f"%{query}%", f"%{query}%", codes, codes])
        return [clean(r) for r in rows]

    def get_condition_codes(self, programme: str, system: str | None) -> list[dict]:
        return [clean(r) for r in fetchall("dms", """select code, description, system from autoline.al_condition_codes
                 where programme=%s and (%s::text is null or system=%s) order by code""", [programme, system, system])]

    def get_parts(self, wip_no: str) -> list[dict]:
        return [ln for ln in self.get_repair_order(wip_no)["lines"] if ln["line_type"] == "part"]

    def get_staff(self, sid: str) -> dict | None:
        return self._staff(sid)

    def write_claim_status(self, wip_no: str, claim_ref: str, status: str, amount: float | None) -> None:
        with connect("dms") as c:
            c.execute("""insert into autoline.al_warranty_claims (claim_ref, company_id, wip_no, status, amount)
                         values (%s,%s,%s,%s,%s) on conflict (claim_ref) do update set status=excluded.status,
                         amount=coalesce(excluded.amount, autoline.al_warranty_claims.amount), updated_at=now()""",
                      [claim_ref, self.cid, wip_no, status, amount])

    def post_credit_note(self, claim_ref: str, amount: float, ref: str, paid_at: str) -> None:
        with connect("dms") as c:
            c.execute("""insert into autoline.al_warranty_credit_notes (oem_credit_ref, company_id, claim_ref, amount, sim_paid_at)
                         values (%s,%s,%s,%s,%s) on conflict (oem_credit_ref) do nothing""",
                      [ref, self.cid, claim_ref, amount, paid_at])

    def reset_writeback(self) -> None:
        with tx("dms") as c:
            c.execute("delete from autoline.al_warranty_credit_notes where company_id=%s", [self.cid])
            c.execute("delete from autoline.al_warranty_claims where company_id=%s", [self.cid])


class AutolineKeyloopAdapter:
    """Stub: real Autoline via Keyloop partner API / scheduled extract (Stage 1)."""

    def __getattr__(self, name):
        def _nc(*a, **k):
            raise NotConfigured("Autoline (Keyloop) adapter is not configured — requires partner programme access")
        return _nc


class StarBodAdapter(AutolineKeyloopAdapter):
    """Stub: generic STAR BOD (RepairOrder / WarrantyClaim) adapter (Stage 2)."""


def make_adapter(name: str):
    return {"autoline_fake": AutolineFakeAdapter, "autoline_keyloop": AutolineKeyloopAdapter,
            "star_bod": StarBodAdapter}[name]()
