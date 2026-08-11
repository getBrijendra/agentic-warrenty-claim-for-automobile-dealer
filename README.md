# Warranty Claims Agent Platform — MVP

Agents turn closed Autoline WIPs (job cards) into OEM warranty claims: triage → build → compliance → submit →
track → resolve rejections → escalate to a human. Product spec: [CLAUDE.md](CLAUDE.md). System design: [agent.md](agent.md).
This is the MVP defined in agent.md §23: **one desk, six rulebooks, end-to-end on fake everything.**

| Part | Status in this MVP |
|---|---|
| Agents (Triage, Builder, Compliance, Submitter, Resolver) | LangGraph tool loops calling a **FAKE LLM** through `llm-gateway` (deterministic policies; no real AI reasoning). Swap: `LLM_PROVIDER=anthropic` (stub today). |
| Rules engine | Real, deterministic, driven by `rule_packs/*.yaml` with `public` / `illustrative` source badges. |
| Autoline DMS | **Fake** — PGlite tables shaped like Autoline (`al_*`), behind `DmsPort`. |
| OEM portals | **Simulated OEM Gateway** — per-OEM payload schemas, deterministic adjudication + gateway-only checks, payment runs. No live OEM connection. |
| Technician replies | Simulated persona answering only from the WIP + hidden ground truth (never invents evidence). |

## Run it

Prerequisites: Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
pnpm setup              # uv sync + npm installs (or run the three commands by hand)
pnpm web:build          # optional but recommended: production web build uses far less memory than `next dev`
pnpm dev                # PGlite ×3 DBs, 6 FastAPI services, worker, scheduler, relay, web — seeds on first run
# → http://localhost:3000/demo/warranty-claims
pnpm stop               # (Windows) kill everything dev.py started
```

Headless:

```bash
pnpm warranty:run                 # reset + batch run of 24 WIPs + acceptance check (needs `pnpm dev:backend`)
pnpm warranty:run --mode replay   # replays the recorded run
pnpm warranty:record-replays      # live run; every model call is recorded automatically
pnpm warranty:reset               # clear claims/gateway/agent runs, restore WIPs (keeps recordings)
pnpm warranty:seed:build          # regenerate seed/generated/default.warranty.json from scripts/warranty/seed_spec.py
pnpm test                         # 125 pytest tests (rules, adapters, guardrails, transitions, policies, architecture) + tsc
```

Acceptance (last verified on this machine): 24 claims settle in ~160 s at default sim speed; 3 reject → fix → accept
(Ford R03 labour, Ford R06 causal part, Ford Trucks R04 leak photo); 6 escalations with drafts (Explorer goodwill R01,
Navigator PremiumCare R11, Defender lift kit R12, Ferrari pre-auth, Maserati year-6 scope R09, Range Rover Sport repeat
repair / TA case); VinFast battery-term claim accepted as COMPONENT; duplicate withdrawn; Defender re-typed to CAMPAIGN;
replay reproduces the run claim-for-claim.

## Map

```
apps/web/                     Next.js 15 UI (control room, claim workspace, rules matrix, review queue, gateway inbox)
services/api                  :8000  BFF — REST + SSE (fed by the transactional outbox), runs, review actions, KPIs, admin
services/dms_connector        :8100  DmsPort over HTTP → AutolineFakeAdapter (Keyloop / STAR adapters are stubs)
services/oem_hub              :8200  OemConnector registry (entry points) + rate limit, circuit breaker, idempotency, log
services/llm_gateway          :8300  LLMProvider (fake) · budgets · usage/cost · record & replay
services/oem_gateway_sim      :8400  SIMULATED OEM portals: 400 field errors, adjudication, reviewer comment, credit notes
services/workshop_sim         :8500  technician persona + accuracy scoring — the only reader of ground truth
services/orchestrator         worker: deterministic state machine, one job = one step, guardrails, escalation policy
services/scheduler            ingestion, OEM status polling, payment runs, window watcher, sim clock ticks
services/outbox_relay         outbox → event log (SSE fan-out with Last-Event-ID resume)
packages/py/wc_core           settings, ports, canonical claim, vocab, clock, service tokens
packages/py/wc_rules          pure rules engine + rule-pack loader
packages/py/wc_oem            per-programme payload adapters + sim connectors + credential specs
packages/py/wc_agents         LangGraph runner, tools, guardrails, agent definitions
packages/py/wc_llm            fake model policies, remote chat model, providers
packages/py/wc_db / wc_events DB pools + SQL migrations + seeding / job queue + outbox
rule_packs/                   ford, ford_trucks, jlr, ferrari, maserati, vinfast (+ deepal, foton stubs)
infra/pglite/serve.mjs        PGlite over the Postgres wire protocol (safe multiplexer, see below)
```

## Demo storyline (~8 min, CLAUDE.md §14)

1. **OEM rules** — six rulebooks; unlimited-km Ferrari/Maserati vs km-capped Ford/JLR; VinFast battery term.
2. **Control room** — baseline KPIs from 12 months of manual claims. Press **Run agents**.
3. **Watch the board** — Defender re-typed to campaign JL-CMP-26-014; Range Rover blocked until the workshop supplies
   the diagnostic session ID; Expedition jumps the queue (window closes in 2.8 days).
4. **Open WC-FD-2026-000121** — rejected R03; resolver gets the justification (seized bolts, 44 °C) + photo from the
   workshop; v1→v2 diff; accepted; credit note.
5. **Review queue** — Explorer goodwill draft; Maserati year-6 infotainment outside drive-line scope. Approve a few:
   the claims resume (Ferrari PA granted → paid; JLR TA case → resubmitted → paid; goodwill → 50 % contribution).
6. **Close** — KPI bar vs baseline.

## Deviations and honest notes

- **Stack per agent.md** (Python/FastAPI/LangGraph/PGlite/fake LLM) with these MVP simplifications: plain-SQL
  migrations instead of Alembic; sync psycopg + threads (async psycopg cannot use Windows' default event loop);
  no LangGraph Postgres checkpointer — escalations pause at the orchestrator level and the review action enqueues a
  resume job; hand-written TS types instead of OpenAPI codegen; structlog only (no OTel/Jaeger); no Docker compose.
- **PGlite multiplexing:** `@electric-sql/pglite-socket`'s built-in multiplexer interleaves extended-protocol messages
  from different connections (wrong/missing results under load). `infra/pglite/serve.mjs` replaces it: per-connection
  units up to `Sync`, transaction affinity, rollback on disconnect. Code must never open a second connection to the
  same DB while holding one — `wc_db` raises `NestedConnectionError` if it happens.
- **PGlite durability:** always stop with `pnpm stop` or Ctrl+C in the `pnpm dev` window — both make PGlite flush and
  close (stop file `var/pglite/STOP`). A hard kill can corrupt a data directory; on the next start `serve.mjs`
  probes any directory without a `.clean_shutdown` marker, moves an unreadable one to `<dir>.corrupt-<time>` and the
  launcher reseeds it (claims and replay recordings in that DB are lost; re-run `pnpm warranty:record-replays`).
  The `.corrupt-*` folders are kept for inspection and can be deleted.
- **Memory:** the three PGlite DBs share one Node process with `--no-wasm-tier-up`. On machines with a small page
  file, `next dev` + a browser can exhaust commit memory — use `pnpm web:build` (dev.py then serves the build).
- **Sim speed** defaults to 8640 (1 sim day = 10 s) so the batch finishes in < 6 min; CLAUDE.md suggests 1440.
- **First-pass rejection** on this deliberately issue-heavy set (60 % seeded with problems, several caught only by
  gateway-only checks) is ~26 % vs a 25 % manual baseline; the agent's gains show in cycle time (≈21 d vs 38 d median)
  and unrecovered value. Accuracy attribution per seeded issue is in the control room's **Admin** drawer.
- **VinFast V1** is a 2023 VF 8 fleet car at 230,400 km (a 7-year-old VF 8 cannot exist yet); the showcase is the same:
  vehicle term exceeded on km, battery term (10 yrs / unlimited km) valid → COMPONENT claim accepted.
- **Public facts** carry source URLs but `last_verified` is unset — re-check every URL before a meeting. All
  dealer-claim values are illustrative; all people, VINs, plates and phone numbers are fictional; codes use invented
  prefixes; no OEM logos.
