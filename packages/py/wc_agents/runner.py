"""LangGraph agent loop: call_model → route → run_tools → guard → call_model … → validate_output → done.
Aborts on max_turns, token budget, repeated guardrail violations, or invalid output after 2 repairs."""
from __future__ import annotations

import json
import time
import uuid
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import ValidationError

from wc_core.infra import to_jsonable
from wc_core.settings import get_settings
from wc_db.db import connect, j
from wc_events.bus import emit
from wc_llm.remote import BudgetExceeded, RemoteChatModel

from .definitions import AgentDef
from .tools import ToolContext, execute, lc_tools, wrap


class AgentFailed(RuntimeError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    turn: int
    repairs: int
    output: dict | None
    abort: str | None
    tokens: int


def _truncate(obj: Any, n: int = 1500) -> Any:
    s = json.dumps(to_jsonable(obj), ensure_ascii=False)
    return obj if len(s) <= n else {"_truncated": True, "preview": s[:n]}


class Telemetry:
    def __init__(self, defn: AgentDef, claim: dict, run_key: str, model: str, clock):
        self.run_id = str(uuid.uuid4())
        self.seq = 0
        self.claim, self.defn, self.clock = claim, defn, clock
        with connect("core") as c:
            c.execute("""insert into agent.agent_runs (id, company_id, run_key, claim_id, agent, model, role, status)
                         values (%s,%s,%s,%s,%s,%s,%s,'running')""",
                      [self.run_id, get_settings().company_id, run_key, claim["id"], defn.name, model, defn.role])

    def step(self, kind: str, payload: dict, tool_name: str | None = None, duration_ms: int | None = None) -> None:
        self.seq += 1
        sim_at = self.clock.now().isoformat()  # NB: resolve before taking a pooled connection (no nested connections)
        with connect("core") as c:
            c.execute("""insert into agent.agent_steps (company_id, run_id, seq, kind, tool_name, payload, duration_ms)
                         values (%s,%s,%s,%s,%s,%s,%s)""",
                      [get_settings().company_id, self.run_id, self.seq, kind, tool_name, j(_truncate(payload)), duration_ms])
            if kind in ("status_note", "workshop_chat", "decision", "tool_call"):
                emit(c, "agent.step", {"claim_ref": self.claim["claim_ref"], "wip_no": self.claim["wip_no"],
                                       "programme": self.claim["programme"], "agent": self.defn.name, "kind": kind,
                                       "tool_name": tool_name, "sim_at": sim_at,
                                       "text": payload.get("text") or payload.get("answer") or payload.get("summary"),
                                       "question": payload.get("question")})

    def finish(self, status: str, output: dict | None, error: str | None, tokens_in: int, tokens_out: int, cost: float,
               turns: int) -> None:
        with connect("core") as c:
            c.execute("""update agent.agent_runs set status=%s, output=%s, error=%s, ended_at=now(), input_tokens=%s,
                         output_tokens=%s, cost_usd=%s, turns=%s, updated_at=now() where id=%s""",
                      [status, j(output) if output else None, error, tokens_in, tokens_out, cost, turns, self.run_id])


def run_agent(defn: AgentDef, task: dict, tctx: ToolContext, attempt: int = 1) -> dict:
    s = get_settings()
    claim = tctx.claim
    run_key = f"{claim['claim_ref']}:{defn.name}:{attempt}"
    model_id = s.warranty_agent_model if defn.role == "main" else s.warranty_fast_model
    tel = Telemetry(defn, claim, run_key, model_id, tctx.clock)
    tctx.emit_step = lambda kind, payload: tel.step(kind, payload)
    llm = RemoteChatModel(agent=defn.name, role=defn.role, run_key=run_key, claim_ref=claim["claim_ref"],
                          programme=claim["programme"])
    bound = llm.bind_tools(lc_tools(list(defn.tools)))
    usage = {"in": 0, "out": 0, "cost": 0.0}

    def call_model(state: AgentState) -> dict:
        if state["turn"] >= defn.max_turns:
            return {"abort": f"MAX_TURNS ({defn.max_turns})"}
        if state["tokens"] > defn.token_budget:
            return {"abort": f"TOKEN_BUDGET ({state['tokens']} > {defn.token_budget})"}
        try:
            ai: AIMessage = bound.invoke(state["messages"])
        except BudgetExceeded as e:
            return {"abort": f"BUDGET_EXCEEDED {e}"}
        u = (ai.response_metadata or {}).get("usage") or {}
        usage["in"] += u.get("input_tokens", 0)
        usage["out"] += u.get("output_tokens", 0)
        return {"messages": [ai], "turn": state["turn"] + 1,
                "tokens": state["tokens"] + u.get("input_tokens", 0) + u.get("output_tokens", 0)}

    def route(state: AgentState) -> str:
        if state.get("abort"):
            return "abort"
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else "final"

    def run_tools(state: AgentState) -> dict:
        out = []
        for tc in state["messages"][-1].tool_calls:
            name, args = tc["name"], tc["args"]
            if name not in defn.tools:  # permission matrix: rejected before execution
                tel.step("tool_result", {"error": "tool not permitted"}, name)
                out.append(ToolMessage(content=wrap(name, {"error": f"tool {name} not permitted for {defn.name}"}),
                                       tool_call_id=tc["id"], name=name))
                continue
            if name != "emit_status_note":
                tel.step("tool_call", {"args": args}, name)
            t0 = time.perf_counter()
            try:
                result = execute(tctx, name, args)
            except Exception as e:  # tool failure is reported to the model as data
                result = {"error": f"{type(e).__name__}: {str(e)[:400]}"}
            dur = int((time.perf_counter() - t0) * 1000)
            if name != "emit_status_note":
                tel.step("tool_result", result if isinstance(result, dict) else {"value": result}, name, dur)
            out.append(ToolMessage(content=wrap(name, result), tool_call_id=tc["id"], name=name))
        abort = None
        if tctx.flags.get("guardrail_violations", 0) >= 3:
            abort = "GUARDRAIL_VIOLATION (repeated)"
        return {"messages": out, "abort": abort}

    def guard(state: AgentState) -> str:
        return "abort" if state.get("abort") else "ok"

    def validate_output(state: AgentState) -> dict:
        last = state["messages"][-1]
        try:
            data = json.loads(last.content)
            out = defn.output.model_validate(data).model_dump(mode="json")
            tel.step("decision", {"summary": _summary(defn.name, out), "output": out})
            return {"output": out}
        except (ValueError, ValidationError) as e:
            if state["repairs"] >= 2:
                return {"abort": f"AGENT_OUTPUT_INVALID {str(e)[:200]}"}
            return {"repairs": state["repairs"] + 1,
                    "messages": [HumanMessage(content=f"Output did not match {defn.output.__name__}: {str(e)[:300]}. "
                                                      f"Reply with valid JSON only.")]}

    def after_validate(state: AgentState) -> str:
        if state.get("output"):
            return "done"
        return "abort" if state.get("abort") else "retry"

    g = StateGraph(AgentState)
    g.add_node("call_model", call_model)
    g.add_node("run_tools", run_tools)
    g.add_node("validate_output", validate_output)
    g.add_edge(START, "call_model")
    g.add_conditional_edges("call_model", route, {"tools": "run_tools", "final": "validate_output", "abort": END})
    g.add_conditional_edges("run_tools", guard, {"ok": "call_model", "abort": END})
    g.add_conditional_edges("validate_output", after_validate, {"done": END, "retry": "call_model", "abort": END})
    graph = g.compile()

    init: AgentState = {"messages": [SystemMessage(content=defn.system_prompt(claim["programme"])),
                                     HumanMessage(content=json.dumps(to_jsonable(task), ensure_ascii=False))],
                        "turn": 0, "repairs": 0, "output": None, "abort": None, "tokens": 0}
    from wc_llm.providers import PRICE
    pin, pout = PRICE[defn.role]
    try:
        final = graph.invoke(init, config={"recursion_limit": defn.max_turns * 3 + 10})
    except Exception as e:
        tel.finish("failed", None, str(e)[:1000], usage["in"], usage["out"], 0, 0)
        raise AgentFailed("AGENT_ERROR", str(e))
    cost = usage["in"] / 1e6 * pin + usage["out"] / 1e6 * pout
    if final.get("abort") or not final.get("output"):
        tel.finish("aborted", None, final.get("abort"), usage["in"], usage["out"], cost, final["turn"])
        raise AgentFailed(final.get("abort") or "NO_OUTPUT")
    tel.finish("succeeded", final["output"], None, usage["in"], usage["out"], cost, final["turn"])
    return final["output"]


def _summary(agent: str, out: dict) -> str:
    if agent == "triage":
        return f"{out['action']} · {out.get('claim_type') or ''} {' '.join(out.get('reason_codes') or [])}".strip()
    if agent == "builder":
        return out.get("summary", "")
    if agent == "compliance":
        return f"{out['status']} {' '.join(out.get('reason_codes') or [])}".strip()
    if agent == "submitter":
        return f"submitted {out.get('oem_claim_id')}" if out.get("submitted") else "format errors"
    if agent == "resolver":
        return f"{out['action']} — {out.get('summary', '')}"
    return ""
