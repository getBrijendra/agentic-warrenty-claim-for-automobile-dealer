"""ClaimRepository — authoritative claim record in pg-core `warranty` schema (tenant-scoped)."""
from __future__ import annotations

import uuid
from typing import Any

from wc_core.canonical import CanonicalClaim
from wc_core.infra import to_jsonable
from wc_core.settings import get_settings

from .db import connect, fetchall, fetchone, j, tx


class ClaimRepository:
    def __init__(self):
        self.cid = get_settings().company_id

    def by_ref(self, claim_ref: str) -> dict | None:
        return fetchone("core", "select * from warranty.claims where company_id=%s and claim_ref=%s", [self.cid, claim_ref])

    def by_id(self, claim_id: str) -> dict | None:
        return fetchone("core", "select * from warranty.claims where id=%s", [claim_id])

    def all(self) -> list[dict]:
        return fetchall("core", "select * from warranty.claims where company_id=%s order by claim_ref", [self.cid])

    def latest_version(self, claim_id: str) -> dict | None:
        return fetchone("core", """select * from warranty.claim_versions where claim_id=%s order by version desc limit 1""",
                        [claim_id])

    def version(self, claim_id: str, version: int) -> dict | None:
        return fetchone("core", "select * from warranty.claim_versions where claim_id=%s and version=%s", [claim_id, version])

    def versions(self, claim_id: str) -> list[dict]:
        return fetchall("core", "select * from warranty.claim_versions where claim_id=%s order by version", [claim_id])

    def canonical(self, claim_id: str) -> CanonicalClaim | None:
        v = self.latest_version(claim_id)
        return CanonicalClaim.model_validate(v["canonical"]) if v else None

    def add_version(self, claim_id: str, claim: CanonicalClaim, built_by: str, change_summary: str) -> int:
        with tx("core") as c:
            cur = c.execute("select coalesce(max(version),0) as v from warranty.claim_versions where claim_id=%s",
                            [claim_id]).fetchone()["v"]
            ver = cur + 1
            c.execute("""insert into warranty.claim_versions (id, company_id, claim_id, version, canonical, built_by, change_summary)
                         values (%s,%s,%s,%s,%s,%s,%s)""",
                      [str(uuid.uuid4()), self.cid, claim_id, ver, j(claim.model_dump(mode="json")), built_by, change_summary])
            c.execute("""update warranty.claims set current_version=%s, amount_claimed=%s, claim_type=%s, contract_type=%s,
                         updated_at=now() where id=%s""",
                      [ver, claim.totals.total, claim.claim_type, claim.contract_type, claim_id])
        return ver

    def set_payload(self, claim_id: str, version: int, payload: dict) -> None:
        with connect("core") as c:
            c.execute("update warranty.claim_versions set oem_payload=%s, updated_at=now() where claim_id=%s and version=%s",
                      [j(payload), claim_id, version])

    def save_rule_results(self, claim_id: str, version: int, results: list[dict]) -> int:
        v = self.version(claim_id, version)
        with tx("core") as c:
            seq = c.execute("select coalesce(max(run_seq),0)+1 as s from warranty.rule_results where claim_version_id=%s",
                            [v["id"]]).fetchone()["s"]
            for r in results:
                c.execute("""insert into warranty.rule_results (id, company_id, claim_version_id, run_seq, rule_key, label, result,
                             code, detail, source, source_url, fixable, suggested_fix) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                          [str(uuid.uuid4()), self.cid, v["id"], seq, r["rule_key"], r["label"], r["result"], r.get("code"),
                           r["detail"], r["source"], r.get("source_url"), r.get("fixable"), r.get("suggested_fix")])
        return seq

    def latest_rule_results(self, claim_id: str) -> list[dict]:
        v = self.latest_version(claim_id)
        if not v:
            return []
        return fetchall("core", """select * from warranty.rule_results where claim_version_id=%s and run_seq =
                                   (select max(run_seq) from warranty.rule_results where claim_version_id=%s) order by created_at""",
                        [v["id"], v["id"]])

    def granted_refs(self, claim_id: str) -> list[str]:
        return [r["ref"] for r in fetchall("core", """select ref from warranty.preauth_requests where claim_id=%s
                                                        and status='approved' and ref is not null""", [claim_id])]

    def prior_claims(self, vin: str) -> list[dict[str, Any]]:
        out = []
        for c in fetchall("core", "select * from warranty.claims where vin=%s", [vin]):
            v = self.latest_version(c["id"])
            ops = [ln["labour_op"] for ln in (v["canonical"]["lines"] if v else [])]
            out.append({"claim_ref": c["claim_ref"], "vin": vin, "labour_ops": ops, "closed_at": c["sim_closed_at"].isoformat(),
                        "status": c["status"], "system": v["canonical"]["system"] if v else None,
                        "repair_date": c["sim_closed_at"].date().isoformat(), "source": "agent"})
        for b in fetchall("core", "select * from warranty.baseline_claims where vin=%s", [vin]):
            out.append({"claim_ref": b["claim_ref"], "vin": vin, "labour_ops": [b["labour_op"]],
                        "closed_at": b["wip_closed_at"].isoformat(), "status": b["final_status"], "system": b["system"],
                        "repair_date": b["wip_closed_at"].date().isoformat(), "source": "history", "wip_no": b["wip_no"]})
        return to_jsonable(out)
