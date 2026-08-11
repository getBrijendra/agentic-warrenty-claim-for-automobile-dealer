"""RemoteChatModel — a LangChain BaseChatModel that calls the llm-gateway. Agents bind tools and invoke it like
any chat model; budgets, logging and record/replay live in the gateway."""
from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

from wc_core.http import ServiceClient, ServiceError
from wc_core.settings import get_settings


class BudgetExceeded(RuntimeError):
    pass


class RemoteChatModel(BaseChatModel):
    agent: str
    role: str
    run_key: str
    claim_ref: str | None = None
    programme: str | None = None
    _seq: int = PrivateAttr(default=0)
    _client: Any = PrivateAttr(default=None)
    _last_usage: dict = PrivateAttr(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "wc-llm-gateway"

    def _http(self) -> ServiceClient:
        if self._client is None:
            self._client = ServiceClient(get_settings().llm_gateway_url, "orchestrator-worker", timeout=120)
        return self._client

    def bind_tools(self, tools: list, **kwargs: Any):
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        body = {"agent": self.agent, "role": self.role, "run_key": self.run_key, "seq": self._seq,
                "claim_ref": self.claim_ref, "programme": self.programme,
                "messages": [message_to_dict(m) for m in messages], "tools": kwargs.get("tools", [])}
        try:
            resp = self._http().post("/llm/v1/chat", body)
        except ServiceError as e:
            if e.status == 429:
                raise BudgetExceeded(str(e.body))
            raise
        self._seq += 1
        msg = messages_from_dict([resp["message"]])[0]
        self._last_usage = resp.get("usage", {})
        msg.response_metadata = {"model": resp.get("model"), "provider": resp.get("provider"), "usage": resp.get("usage")}
        return ChatResult(generations=[ChatGeneration(message=msg)], llm_output={"usage": resp.get("usage")})

    @property
    def last_usage(self) -> dict:
        return self._last_usage
