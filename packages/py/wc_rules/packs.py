"""Rule-pack loader (rules as data, P4). Packs are versioned YAML, validated at load."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from wc_core.settings import ROOT

PACK_DIR = ROOT / "rule_packs"
FILES = {"FORD": "ford", "FORD_TRUCKS": "ford_trucks", "JLR": "jlr", "FERRARI": "ferrari", "MASERATI": "maserati",
         "VINFAST": "vinfast", "DEEPAL": "deepal", "FOTON": "foton"}


class CoverageFact(BaseModel):
    contract_type: str
    label: str
    years: int | None = None
    km_limit: int | None = None
    component_scope: list[str] = Field(default_factory=list)
    note: str | None = None
    source: Literal["public", "illustrative"]
    source_url: str | None = None
    last_verified: str | None = None


class Fact(BaseModel):
    label: str
    value: str
    source: Literal["public", "illustrative"]
    source_url: str | None = None
    last_verified: str | None = None


class RulePack(BaseModel):
    programme: str
    display_name: str
    brands: list[str]
    claim_prefix: str
    colour: str
    status: Literal["active", "pending"]
    pack_version: str
    claim_format: str
    adjudication_delay_days: list[int]
    payment_weekday: int
    coverage: list[CoverageFact] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    dealer_rules: dict[str, dict[str, Any]] = Field(default_factory=dict)
    gateway_only_checks: list[dict[str, Any]] = Field(default_factory=list)
    rejection_wording: dict[str, str] = Field(default_factory=dict)

    def rule(self, key: str) -> dict[str, Any]:
        return self.dealer_rules.get(key, {})

    @property
    def max_resubmissions(self) -> int:
        return int(self.rule("max_resubmissions").get("value", 2))

    @property
    def window_days(self) -> int:
        return int(self.rule("submission_window").get("days", 30))

    @property
    def labour_rate_cap(self) -> float:
        return float(self.rule("labour_rate_cap").get("aed", 10_000))

    @property
    def retention_days(self) -> int:
        return int(self.rule("parts_retention").get("days", 60))


@lru_cache
def load_pack(programme: str) -> RulePack:
    path = PACK_DIR / f"{FILES[programme]}.yaml"
    return RulePack.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def all_packs() -> list[RulePack]:
    return [load_pack(p) for p in FILES]


def pack_excerpt(programme: str) -> str:
    """Compact rule-pack text included in agent system prompts (cacheable per programme)."""
    return yaml.safe_dump(load_pack(programme).model_dump(exclude={"rejection_wording", "gateway_only_checks"}),
                          sort_keys=False)
