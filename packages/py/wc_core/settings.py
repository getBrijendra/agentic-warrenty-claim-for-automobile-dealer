"""Central configuration (pydantic-settings). Every adapter is chosen from here (P9: swap by config)."""
from __future__ import annotations

import uuid
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    # databases (PGlite today, Postgres later — only these URLs change)
    database_url_core: str = "postgresql://postgres@127.0.0.1:5432/postgres"
    database_url_dms: str = "postgresql://postgres@127.0.0.1:5433/postgres"
    database_url_sim: str = "postgresql://postgres@127.0.0.1:5434/postgres"
    db_pool_size: int = 8

    # service locations
    api_url: str = "http://127.0.0.1:8000"
    dms_url: str = "http://127.0.0.1:8100"
    oem_hub_url: str = "http://127.0.0.1:8200"
    llm_gateway_url: str = "http://127.0.0.1:8300"
    oem_sim_url: str = "http://127.0.0.1:8400"
    workshop_url: str = "http://127.0.0.1:8500"
    web_origin: str = "http://localhost:3000"

    service_token_secret: str = "dev-only-service-secret"

    # adapters
    dms_adapter: str = "autoline_fake"
    llm_provider: str = "fake"  # fake | replay | anthropic | openai | bedrock
    oem_connector_default: str = "sim"
    blob_store: str = "local"
    queue: str = "pg"
    event_bus: str = "pg_outbox"

    # agents
    warranty_agent_model: str = "claude-sonnet-5-5"
    warranty_fast_model: str = "claude-haiku-5-5"
    warranty_max_parallel: int = 4
    warranty_sim_speed: float = 8640.0
    warranty_mode: str = "live"  # live | replay
    warranty_seed: int = 42
    llm_max_tokens_per_claim: int = 150_000
    llm_max_tokens_per_batch: int = 3_000_000
    escalation_amount_aed: float = 25_000

    # tenant
    company_slug: str = "default"
    brand_display_name: str = "Demo Motors Group"
    currency: str = "AED"
    vat_rate: float = 0.05
    sim_start: str = "2026-10-14T08:00:00+04:00"

    # fake credentials (exercise the auth paths; never real)
    oem_ford_client_id: str = "fake-ford-client"
    oem_ford_client_secret: str = "fake-ford-secret"
    oem_jlr_api_key: str = "fake-jlr-key"
    oem_ferrari_api_key: str = "fake-ferrari-key"
    oem_maserati_api_key: str = "fake-maserati-key"
    oem_vinfast_api_key: str = "fake-vinfast-key"
    oem_ford_trucks_api_key: str = "fake-fordtrucks-key"
    autoline_api_key: str = "fake-autoline-key"
    anthropic_api_key: str = ""

    @property
    def blob_dir(self) -> Path:
        return ROOT / "var" / "blobs"

    @property
    def company_id(self) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{self.company_slug}.tenant.warranty-claims.local"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
