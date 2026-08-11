"""Record replays: reset + full LIVE run. Every model call through the llm-gateway is recorded automatically into
agent.agent_replays; afterwards `warranty:run --mode replay` (or the control-room toggle) plays them back.

    uv run python scripts/warranty/record_replays.py
"""
import subprocess
import sys
from pathlib import Path

import httpx

rc = subprocess.call([sys.executable, str(Path(__file__).with_name("run.py")), "--mode", "live"])
n = httpx.get("http://127.0.0.1:8000/api/v1/admin/cost", timeout=60).json()
print(f"\nrecorded run · {sum(r['calls'] for r in n['rows'])} model calls captured for replay")
sys.exit(rc)
