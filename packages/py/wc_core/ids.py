"""Identifiers: UUIDv7 primary keys, deterministic claim refs, idempotency keys."""
from __future__ import annotations

import hashlib
import os
import time
import uuid


def uuid7() -> str:
    ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= ((rand >> 66) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)
    return str(uuid.UUID(int=value))


def idem_key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:40]


def stable_hash(*parts: object) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)
