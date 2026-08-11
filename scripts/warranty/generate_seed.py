"""LLM seed generator (CLAUDE.md §9.4) — NOT part of the MVP (agent.md §23: "Real LLM; LLM seed generator" are out of
scope). The demo runs from the hand-written spec: scripts/warranty/seed_spec.py → build_default_seed.py →
seed/generated/default.warranty.json. This entry point exists so the command is discoverable; it refuses to run
until a real LLM provider is configured (Stage 1)."""
import sys

sys.exit("generate-seed needs a real LLM provider (Stage 1). Use `pnpm warranty:seed:build` for the hand-written seed.")
