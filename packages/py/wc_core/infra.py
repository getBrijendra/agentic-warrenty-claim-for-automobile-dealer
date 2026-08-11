"""Small infrastructure adapters: EnvSecretStore, LocalFsBlobStore, JSON helpers, FastAPI service-auth."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .settings import get_settings


class EnvSecretStore:
    def get(self, ref: str) -> str:
        val = os.environ.get(ref.upper()) or getattr(get_settings(), ref.lower(), None)
        if val is None:
            raise KeyError(f"secret {ref} not configured")
        return str(val)


class LocalFsBlobStore:
    def __init__(self, root: Path | None = None, public_base: str | None = None):
        self.root = root or get_settings().blob_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_base = public_base or f"{get_settings().dms_url}/dms/v1/blobs"

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self.url(key)

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()

    def url(self, key: str) -> str:
        return f"{self.public_base}/{key}"


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return to_jsonable(obj.model_dump(mode="json"))
    return obj


def dumps(obj: Any) -> str:
    return json.dumps(to_jsonable(obj), ensure_ascii=False)


def require_service(allowed: set[str] | None = None):
    """FastAPI dependency: internal APIs require a valid signed service token."""
    from fastapi import Header, HTTPException

    from .http import verify_service_token

    def dep(x_service_token: str | None = Header(default=None)) -> str:
        svc = verify_service_token(x_service_token)
        if svc is None:
            raise HTTPException(401, "missing or invalid service token")
        if allowed is not None and svc not in allowed:
            raise HTTPException(403, f"service {svc} not allowed")
        return svc

    return dep
