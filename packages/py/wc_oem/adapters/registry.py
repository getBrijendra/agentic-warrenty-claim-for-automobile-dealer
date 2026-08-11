"""Programme → adapter registry (canonical → OEM payload, pure and deterministic)."""
from __future__ import annotations

from .base import Adapter
from .ferrari import ADAPTER as FERRARI
from .ford import ADAPTER as FORD
from .ford_trucks import ADAPTER as FORD_TRUCKS
from .jlr import ADAPTER as JLR
from .maserati import ADAPTER as MASERATI
from .vinfast import ADAPTER as VINFAST

ADAPTERS: dict[str, Adapter] = {a.programme: a for a in (FORD, FORD_TRUCKS, JLR, FERRARI, MASERATI, VINFAST)}


def adapter_for(programme: str) -> Adapter:
    if programme not in ADAPTERS:
        raise KeyError(f"no payload adapter for {programme} (rules pending)")
    return ADAPTERS[programme]
