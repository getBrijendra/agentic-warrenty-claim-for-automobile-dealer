"""Policy framework for the Fake LLM. A policy inspects the conversation (task + tool results) and returns the
next AIMessage: tool calls when it needs data or must act, or a final JSON answer matching the agent's output
schema. Policies are stateless over the transcript, like a real model."""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

TOOL_RESULT = re.compile(r"^<tool_result[^>]*>(.*)</tool_result>$", re.S)


def parse_tool_content(content: str) -> Any:
    m = TOOL_RESULT.match(content.strip())
    raw = m.group(1) if m else content
    try:
        return json.loads(raw)
    except Exception:
        return {"_text": raw}


@dataclass
class Event:
    name: str
    args: dict
    result: Any
    index: int


@dataclass
class Ctx:
    messages: list[BaseMessage]
    tools: list[str]
    agent: str
    run_key: str
    seq: int
    task: dict = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    rng: random.Random = field(default_factory=random.Random)

    @staticmethod
    def build(messages: list[BaseMessage], tools: list[str], agent: str, run_key: str, seq: int, seed: int) -> "Ctx":
        ctx = Ctx(messages, tools, agent, run_key, seq, rng=random.Random(f"{seed}:{run_key}:{seq}"))
        for m in messages:
            if isinstance(m, HumanMessage) and not ctx.task:
                try:
                    ctx.task = json.loads(m.content if isinstance(m.content, str) else str(m.content))
                except Exception:
                    ctx.task = {"_text": m.content}
        calls: dict[str, tuple[str, dict]] = {}
        for i, m in enumerate(messages):
            if isinstance(m, AIMessage):
                for tc in m.tool_calls or []:
                    calls[tc["id"]] = (tc["name"], tc["args"])
            elif isinstance(m, ToolMessage):
                name, args = calls.get(m.tool_call_id, (m.name or "?", {}))
                ctx.events.append(Event(name, args, parse_tool_content(m.content), i))
        return ctx

    # -- helpers
    def results(self, name: str) -> list[Any]:
        return [e.result for e in self.events if e.name == name]

    def last(self, name: str) -> Any:
        r = self.results(name)
        return r[-1] if r else None

    def last_event(self, name: str) -> Event | None:
        ev = [e for e in self.events if e.name == name]
        return ev[-1] if ev else None

    def after(self, ev: Event | None) -> list[Event]:
        if ev is None:
            return list(self.events)
        return [e for e in self.events if e.index > ev.index]

    def has(self, name: str) -> bool:
        return any(e.name == name for e in self.events)

    def system_prompt(self) -> str:
        for m in self.messages:
            if isinstance(m, SystemMessage):
                return str(m.content)
        return ""


def _id(ctx: Ctx, name: str) -> str:
    from wc_core.ids import stable_hash
    return f"call_{ctx.agent[:4]}_{ctx.seq}_{name}_{stable_hash(ctx.run_key, ctx.seq, name) % 10_000:04d}"


def call(ctx: Ctx, *calls: tuple[str, dict]) -> AIMessage:
    tcs = []
    for i, (name, args) in enumerate(calls):
        if name not in ctx.tools:
            continue  # a model can only call tools it was given
        tcs.append({"id": f"{_id(ctx, name)}_{i}", "name": name, "args": args, "type": "tool_call"})
    return AIMessage(content="", tool_calls=tcs)


def note(text: str) -> tuple[str, dict]:
    return ("emit_status_note", {"text": text})


def final(obj: dict) -> AIMessage:
    return AIMessage(content=json.dumps(obj, ensure_ascii=False))
