"""Credential contracts per connector. Sim connectors declare the same spec as real ones and accept fake values."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CredentialField(BaseModel):
    name: str
    secret: bool = False
    description: str = ""


class CredentialSpec(BaseModel):
    fields: list[CredentialField]
    auth_flow: Literal["api_key", "oauth2_client_credentials", "basic", "mtls", "portal_session"]
    token_ttl_s: int | None = None


SPECS: dict[str, CredentialSpec] = {
    "FORD": CredentialSpec(fields=[CredentialField(name="dealer_code", description="Ford P&A / dealer code"),
                                   CredentialField(name="oem_ford_client_id"),
                                   CredentialField(name="oem_ford_client_secret", secret=True)],
                           auth_flow="oauth2_client_credentials", token_ttl_s=3600),
    "JLR": CredentialSpec(fields=[CredentialField(name="dealer_code", description="JLR retailer code"),
                                  CredentialField(name="oem_jlr_api_key", secret=True)], auth_flow="api_key"),
    "FERRARI": CredentialSpec(fields=[CredentialField(name="dealer_code"),
                                      CredentialField(name="oem_ferrari_api_key", secret=True)], auth_flow="api_key"),
    "MASERATI": CredentialSpec(fields=[CredentialField(name="dealer_code"),
                                       CredentialField(name="oem_maserati_api_key", secret=True)], auth_flow="api_key"),
    "VINFAST": CredentialSpec(fields=[CredentialField(name="dealer_code"),
                                      CredentialField(name="oem_vinfast_api_key", secret=True)], auth_flow="api_key"),
    "FORD_TRUCKS": CredentialSpec(fields=[CredentialField(name="dealer_code"),
                                          CredentialField(name="oem_ford_trucks_api_key", secret=True)],
                                  auth_flow="api_key"),
}

API_KEY_SETTING = {"FORD": "oem_ford_client_secret", "JLR": "oem_jlr_api_key", "FERRARI": "oem_ferrari_api_key",
                   "MASERATI": "oem_maserati_api_key", "VINFAST": "oem_vinfast_api_key",
                   "FORD_TRUCKS": "oem_ford_trucks_api_key"}
