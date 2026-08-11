"""LLMProvider implementations. Requests/responses are LangChain message dicts so any vendor fits behind the
llm-gateway; agents only ever see a LangChain BaseChatModel (RemoteChatModel)."""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import messages_from_dict, message_to_dict

from wc_core.ports import NotConfigured
from wc_core.settings import get_settings

from .fake.policies import builder, compliance, persona, resolver, reviewer, submitter, triage
from .fake.policy import Ctx

POLICIES = {"triage": triage.run, "builder": builder.run, "compliance": compliance.run, "submitter": submitter.run,
            "resolver": resolver.run, "technician_persona": persona.run, "oem_reviewer": reviewer.run}

# Rough $/M-token estimates for the cost meter (configurable; shown as estimates — the fake provider makes no paid calls)
PRICE = {"main": (3.0, 15.0), "fast": (1.0, 5.0)}


def est_tokens(obj: Any) -> int:
    return max(1, len(json.dumps(obj, ensure_ascii=False)) // 4)


class FakeLLMProvider:
    name = "fake"

    def info(self) -> dict:
        s = get_settings()
        return {"provider": "fake", "is_fake": True, "models": {"main": s.warranty_agent_model, "fast": s.warranty_fast_model},
                "note": "Deterministic scripted-policy chat model; no network, no real AI reasoning."}

    def chat(self, request: dict) -> dict:
        msgs = messages_from_dict(request["messages"])
        tools = [t["function"]["name"] if "function" in t else t["name"] for t in request.get("tools", [])]
        policy = POLICIES[request["agent"]]
        ctx = Ctx.build(msgs, tools, request["agent"], request["run_key"], request["seq"], get_settings().warranty_seed)
        ai = policy(ctx)
        out = message_to_dict(ai)
        return {"message": out, "usage": {"input_tokens": est_tokens(request["messages"]) + est_tokens(request.get("tools", [])),
                                          "output_tokens": est_tokens(out)}}


class AnthropicProvider:
    """Stage 1: langchain-anthropic ChatAnthropic with prompt caching on system prompt + rule pack."""
    name = "anthropic"

    def __init__(self):
        if not get_settings().anthropic_api_key:
            raise NotConfigured("ANTHROPIC_API_KEY is empty — set LLM_PROVIDER=fake or configure the key")
        raise NotConfigured("AnthropicProvider is a documented stub in the MVP (no real LLM calls yet)")


class OpenAIProvider(AnthropicProvider):
    name = "openai"


def make_provider(name: str):
    if name in ("fake", "replay"):
        return FakeLLMProvider()  # replay is applied at the gateway on top of recordings
    return {"anthropic": AnthropicProvider, "openai": OpenAIProvider, "bedrock": OpenAIProvider}[name]()
