"""Simulated OEM connectors → oem-gateway-sim over HTTP. Same interface a real connector implements; only the
transport (+auth) differs. Adapter (shape) and transport are separate so a real connector reuses the adapter."""
from __future__ import annotations

from wc_core.canonical import CanonicalClaim
from wc_core.http import ServiceClient, ServiceError
from wc_core.ports import NotConfigured
from wc_core.settings import get_settings

from ..adapters.registry import adapter_for
from ..credentials import API_KEY_SETTING, SPECS

BRANDS = {"FORD": ["Ford", "Lincoln"], "FORD_TRUCKS": ["Ford Trucks"], "JLR": ["Jaguar", "Land Rover"],
          "FERRARI": ["Ferrari"], "MASERATI": ["Maserati"], "VINFAST": ["VinFast"]}


class SimConnector:
    simulated = True

    def __init__(self, programme: str, dealer_code: str):
        self.programme = programme
        self.dealer_code = dealer_code
        s = get_settings()
        self.adapter = adapter_for(programme)
        key = getattr(s, API_KEY_SETTING[programme])
        self._http = ServiceClient(s.oem_sim_url, "oem-hub", headers={"X-Api-Key": key})

    def capabilities(self) -> dict:
        return {"programme": self.programme, "brands": BRANDS[self.programme], "submission_mode": "api",
                "supports_pre_auth": True, "supports_status_poll": True, "supports_status_webhook": False,
                "supports_resubmit": True, "supports_attachments": True, "rate_limit_per_min": 120,
                "simulated": True, "layout": self.adapter.layout}

    def credential_spec(self) -> dict:
        return SPECS[self.programme].model_dump()

    def payload_schema(self) -> dict:
        return self.adapter.json_schema()

    def find_campaigns(self, vin: str) -> list[dict]:
        return self._http.get(f"/sim/{self.programme}/campaigns", params={"vin": vin})

    def build_payload(self, claim: dict) -> dict:
        return self.adapter.build(CanonicalClaim.model_validate(claim), self.dealer_code)

    def validate_payload(self, payload: dict) -> list[dict]:
        return self.adapter.validate(payload)

    def request_pre_authorisation(self, req: dict) -> dict:
        return self._http.post(f"/sim/{self.programme}/preauth", req)

    def get_pre_authorisation(self, ticket: str) -> dict:
        return self._http.get(f"/sim/{self.programme}/preauth/{ticket}")

    def decide_pre_authorisation(self, ticket: str, approved: bool, actor: str) -> dict:
        return self._http.post(f"/sim/{self.programme}/preauth/{ticket}/decision", {"approved": approved, "actor": actor})

    def _send(self, path: str, payload: dict, idem_key: str) -> dict:
        try:
            return self._http.post(path, payload, headers={"Idempotency-Key": idem_key}, retries=3)
        except ServiceError as e:
            if e.status in (400, 409, 422):
                return {"accepted_for_processing": False, "status": e.status, "errors": e.body}
            raise

    def submit_claim(self, payload: dict, idem_key: str) -> dict:
        return self._send(f"/sim/{self.programme}/claims", payload, idem_key)

    def get_claim_status(self, oem_claim_id: str) -> dict:
        return self._http.get(f"/sim/{self.programme}/claims/{oem_claim_id}")

    def resubmit_claim(self, oem_claim_id: str, payload: dict, idem_key: str) -> dict:
        return self._send(f"/sim/{self.programme}/claims/{oem_claim_id}/resubmit", payload, idem_key)

    def list_credit_notes(self, since: str | None) -> list[dict]:
        return self._http.get(f"/sim/{self.programme}/credit-notes", params={"since": since} if since else None)

    def map_rejection(self, raw: dict) -> list[str]:
        return list(raw.get("rejection_codes") or [])


class RealConnectorStub:
    """Documented stub for a real OEM channel (portal API / DMS-mediated / batch / manual pack)."""
    simulated = False

    def __init__(self, programme: str, dealer_code: str):
        self.programme = programme

    def __getattr__(self, name):
        def _nc(*a, **k):
            raise NotConfigured(f"real {self.programme} connector is not configured (Stage 1/2 roadmap)")
        return _nc


def ford(dealer_code: str): return SimConnector("FORD", dealer_code)
def ford_trucks(dealer_code: str): return SimConnector("FORD_TRUCKS", dealer_code)
def jlr(dealer_code: str): return SimConnector("JLR", dealer_code)
def ferrari(dealer_code: str): return SimConnector("FERRARI", dealer_code)
def maserati(dealer_code: str): return SimConnector("MASERATI", dealer_code)
def vinfast(dealer_code: str): return SimConnector("VINFAST", dealer_code)
