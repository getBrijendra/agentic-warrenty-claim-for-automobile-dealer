"""Architecture contracts (import-linter style): ground truth is only reachable from workshop-sim; domain code does
not import vendor LLM SDKs; ports are implemented by the default adapters."""
from __future__ import annotations

import ast
from pathlib import Path

from wc_core.settings import ROOT

PY = [p for base in ("packages/py", "services") for p in (ROOT / base).rglob("*.py")]


def imports(path: Path) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_groundtruth_only_imported_by_workshop_sim():
    offenders = [str(p.relative_to(ROOT)) for p in PY
                 if any(m.startswith("wc_groundtruth") for m in imports(p))
                 and "workshop_sim" not in p.parts and "wc_groundtruth" not in p.parts]
    assert offenders == []


def test_agent_tools_never_query_groundtruth_schema():
    for p in (ROOT / "packages/py/wc_agents").rglob("*.py"):
        assert "groundtruth" not in p.read_text(encoding="utf-8").replace("wc_groundtruth", ""), p


def test_domain_has_no_vendor_llm_sdk():
    for p in PY:
        bad = {m for m in imports(p) if m.split(".")[0] in ("anthropic", "openai", "boto3")}
        assert not bad, f"{p}: {bad}"


def test_no_rule_logic_in_agents_or_ui():
    # thresholds live in rule packs; agent code must not hardcode programme numbers like window days or caps
    for p in (ROOT / "packages/py/wc_agents").rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        for literal in ("7500", "submissionWindowDays", "420"):
            assert literal not in src, (p, literal)


def test_ports_implemented():
    from wc_core.ports import DmsPort, OemConnector
    from dms_connector.adapters import AutolineFakeAdapter
    from wc_oem.connectors.sim import SimConnector
    for proto, impl in ((DmsPort, AutolineFakeAdapter), (OemConnector, SimConnector)):
        missing = [m for m in proto.__protocol_attrs__ if m != "programme" and not hasattr(impl, m)]
        assert missing == [], (impl.__name__, missing)
