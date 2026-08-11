"""Clear claims, versions, gateway submissions and agent runs; restore WIPs as seeded (keeps replay recordings).

    uv run python scripts/warranty/reset.py        (the stack must be running: pnpm dev)
"""
import httpx

r = httpx.post("http://127.0.0.1:8000/api/v1/admin/reset", timeout=120)
r.raise_for_status()
print("reset ok — sim clock", r.json()["sim_now"])
