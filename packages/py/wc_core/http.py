"""Service-to-service HTTP: signed short-lived service tokens + retrying client."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import time
from typing import Any

import httpx

from .settings import get_settings

TOKEN_HEADER = "X-Service-Token"


class ServiceError(Exception):
    def __init__(self, status: int, body: Any, url: str):
        super().__init__(f"{status} from {url}: {str(body)[:400]}")
        self.status = status
        self.body = body
        self.url = url


def make_service_token(service: str, ttl_s: int = 300) -> str:
    secret = get_settings().service_token_secret.encode()
    payload = base64.urlsafe_b64encode(json.dumps({"svc": service, "exp": int(time.time()) + ttl_s}).encode()).decode()
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_service_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    secret = get_settings().service_token_secret.encode()
    if not hmac.compare_digest(hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest(), sig):
        return None
    data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    if data.get("exp", 0) < time.time():
        return None
    return data.get("svc")


class ServiceClient:
    """Sync client with retries (transient: connect errors, 5xx) — exponential backoff with jitter, max 5."""

    def __init__(self, base_url: str, caller: str, timeout: float = 30.0, headers: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.caller = caller
        self._client = httpx.Client(timeout=timeout)
        self._headers = headers or {}
        self._token: tuple[str, float] | None = None

    def _auth(self) -> dict:
        if self._token is None or self._token[1] < time.time() + 30:
            self._token = (make_service_token(self.caller), time.time() + 300)
        return {TOKEN_HEADER: self._token[0], **self._headers}

    def request(self, method: str, path: str, *, json_body: Any = None, params: dict | None = None,
                headers: dict | None = None, retries: int = 5, raise_for_status: bool = True) -> httpx.Response:
        url = self.base_url + path
        attempt = 0
        while True:
            try:
                resp = self._client.request(method, url, json=json_body, params=params,
                                            headers={**self._auth(), **(headers or {})})
                if resp.status_code >= 500 and attempt < retries:
                    raise httpx.TransportError(f"server error {resp.status_code}")
                if raise_for_status and resp.status_code >= 400:
                    try:
                        body = resp.json()
                    except Exception:
                        body = resp.text
                    raise ServiceError(resp.status_code, body, url)
                return resp
            except httpx.TransportError:
                attempt += 1
                if attempt > retries:
                    raise
                time.sleep(min(4.0, 0.15 * 2 ** attempt) + random.random() * 0.1)

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw).json()

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("POST", path, json_body=body, **kw).json()

    def patch(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("PATCH", path, json_body=body, **kw).json()
