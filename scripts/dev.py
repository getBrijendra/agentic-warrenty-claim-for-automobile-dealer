"""Local process manager (honcho-equivalent, works on Windows/macOS/Linux): starts the 3 PGlite servers, migrates
+ seeds on first run, then all Python services and the Next.js web app. Ctrl+C stops everything.

    uv run python scripts/dev.py            # everything incl. web
    uv run python scripts/dev.py --no-web   # backend only
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
COLORS = ["\033[36m", "\033[33m", "\033[35m", "\033[32m", "\033[34m", "\033[31m", "\033[96m", "\033[93m", "\033[95m",
          "\033[92m", "\033[94m", "\033[91m", "\033[37m"]
RESET = "\033[0m"
STOP_FILE = ROOT / "var" / "pglite" / "STOP"  # creating this file makes PGlite flush + close cleanly (also used by stop.ps1)


def procs(web: bool) -> list[tuple[str, list[str], Path]]:
    node = "node"
    pg = ROOT / "infra" / "pglite" / "serve.mjs"
    var = ROOT / "var" / "pglite"
    uv = lambda mod, port: [PY, "-m", "uvicorn", f"{mod}:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    out = [
        # pg-core / pg-dms / pg-sim: three separate PGlite databases on three ports, hosted in one Node process to
        # keep memory low; --no-wasm-tier-up avoids the memory-hungry optimising WASM compile.
        ("pglite", [node, "--no-wasm-tier-up", str(pg), "--instance", f"5432={var / 'core'}", "--instance", f"5433={var / 'dms'}",
                    "--instance", f"5434={var / 'sim'}", "--max-connections", "96", "--stop-file", str(STOP_FILE)], ROOT),
        ("api", uv("api.main", 8000), ROOT),
        ("dms-connector", uv("dms_connector.main", 8100), ROOT),
        ("oem-hub", uv("oem_hub.main", 8200), ROOT),
        ("llm-gateway", uv("llm_gateway.main", 8300), ROOT),
        ("oem-sim", uv("oem_gateway_sim.main", 8400), ROOT),
        ("workshop-sim", uv("workshop_sim.main", 8500), ROOT),
        ("worker", [PY, "-m", "orchestrator.worker"], ROOT),
        ("scheduler", [PY, "-m", "scheduler.main"], ROOT),
        ("relay", [PY, "-m", "outbox_relay.main"], ROOT),
    ]
    if web:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        built = (ROOT / "apps" / "web" / ".next" / "BUILD_ID").exists()
        # a production build (pnpm web:build) uses far less memory than `next dev`
        out.append(("web", [npm, "run", "start" if built and not os.environ.get("WEB_DEV") else "dev"], ROOT / "apps" / "web"))
    return out


def wait_port(port: int, timeout: float = 60) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    raise SystemExit(f"port {port} did not open")


def pump(name: str, color: str, p: subprocess.Popen) -> None:
    for line in iter(p.stdout.readline, b""):
        sys.stdout.buffer.write(f"{color}{name:>13} |{RESET} {line.decode(errors='replace')}".encode("utf-8", "replace"))
        sys.stdout.flush()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-web", action="store_true")
    args = ap.parse_args()
    os.system("")  # enable ANSI on Windows
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    running: list[subprocess.Popen] = []
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0

    def start(i: int, name: str, cmd: list[str], cwd: Path) -> None:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
        running.append(p)
        threading.Thread(target=pump, args=(name, COLORS[i % len(COLORS)], p), daemon=True).start()

    plist = procs(not args.no_web)
    try:
        start(0, *plist[0])
        for port in (5432, 5433, 5434):
            wait_port(port, timeout=120)
        # migrate; seed on first run (idempotent)
        # migrate; seed when either the DMS or core database is empty (first run, or a database was recovered)
        check = ("from wc_db.db import migrate, fetchone\n"
                 "import sys\n"
                 "[migrate(d) for d in ('core','dms','sim')]\n"
                 "dms = fetchone('dms','select count(*) as n from autoline.al_repair_orders')['n']\n"
                 "core = fetchone('core','select count(*) as n from ops.tenants')['n']\n"
                 "sys.exit(0 if dms and core else 3)")
        if subprocess.run([PY, "-c", check], cwd=ROOT, env=env).returncode == 3:
            print("empty database detected — seeding from seed/generated/default.warranty.json")
            subprocess.run([PY, "scripts/warranty/seed.py"], cwd=ROOT, env=env, check=True)
        for i, (name, cmd, cwd) in enumerate(plist[1:], start=1):
            start(i, name, cmd, cwd)
        print(f"\n  Warranty Desk starting… UI http://localhost:3000/demo/warranty-claims · API http://127.0.0.1:8000/api/v1/meta\n")
        while all(p.poll() is None for p in running):
            time.sleep(0.5)
        print("a process exited — stopping all")
    except KeyboardInterrupt:
        pass
    finally:
        # stop services first (they hold DB connections), then let PGlite flush and close — never hard-kill it first:
        # a hard kill mid-write can corrupt the data directory
        for p in reversed(running[1:]):
            kill(p)
        if running:
            stop_pglite(running[0])


def kill(p: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            p.send_signal(signal.SIGTERM)
    except Exception:
        pass


def stop_pglite(p: subprocess.Popen, timeout: float = 30) -> None:
    if p.poll() is not None:
        return
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STOP_FILE.write_text("stop")
    try:
        p.wait(timeout=timeout)
        print("pglite closed cleanly")
    except subprocess.TimeoutExpired:
        print("pglite did not stop in time — killing (data may need recovery on next start)")
        kill(p)


if __name__ == "__main__":
    main()
