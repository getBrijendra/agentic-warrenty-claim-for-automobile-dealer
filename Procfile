# honcho start  (or: uv run python scripts/dev.py — same processes, Windows-friendly, seeds on first run)
pglite:        node --no-wasm-tier-up infra/pglite/serve.mjs --instance 5432=./var/pglite/core --instance 5433=./var/pglite/dms --instance 5434=./var/pglite/sim --max-connections 96
api:           uv run uvicorn api.main:app --port 8000
dms-connector: uv run uvicorn dms_connector.main:app --port 8100
oem-hub:       uv run uvicorn oem_hub.main:app --port 8200
llm-gateway:   uv run uvicorn llm_gateway.main:app --port 8300
oem-sim:       uv run uvicorn oem_gateway_sim.main:app --port 8400
workshop-sim:  uv run uvicorn workshop_sim.main:app --port 8500
worker:        uv run python -m orchestrator.worker
scheduler:     uv run python -m scheduler.main
relay:         uv run python -m outbox_relay.main
web:           npm --prefix apps/web run dev
