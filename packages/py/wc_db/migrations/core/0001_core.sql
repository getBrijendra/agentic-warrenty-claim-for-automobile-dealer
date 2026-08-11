-- pg-core: warranty, agent, ops, llm schemas
create schema if not exists warranty;
create schema if not exists agent;
create schema if not exists ops;
create schema if not exists llm;

create table if not exists ops.tenants (
  company_id uuid primary key, slug text unique not null, display_name text not null,
  settings jsonb not null default '{}', created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists ops.sim_clock (
  id int primary key default 1, company_id uuid, base_sim timestamptz not null, base_real timestamptz not null,
  speed double precision not null, paused boolean not null default true,
  created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists warranty.claims (
  id uuid primary key, company_id uuid not null, claim_ref text unique not null, wip_no text not null,
  repair_order_id text not null, vin text not null, model text, programme text not null, claim_type text,
  contract_type text, status text not null, priority int not null default 100,
  amount_claimed numeric(12,2), amount_paid numeric(12,2), currency text not null default 'AED',
  current_version int not null default 0, version_no int not null default 0, resubmissions int not null default 0,
  first_pass_result text, oem_claim_id text, triage jsonb, flags text[] not null default '{}',
  window_due_at timestamptz, sim_closed_at timestamptz not null, sim_submitted_at timestamptz,
  sim_accepted_at timestamptz, sim_paid_at timestamptz, escalated_by_human boolean not null default false,
  touched_by_human boolean not null default false,
  created_at timestamptz default now(), updated_at timestamptz default now(),
  unique (company_id, wip_no)
);

create table if not exists warranty.claim_versions (
  id uuid primary key, company_id uuid not null, claim_id uuid not null references warranty.claims(id) on delete cascade,
  version int not null, canonical jsonb not null, oem_payload jsonb, built_by text not null, change_summary text,
  created_at timestamptz default now(), updated_at timestamptz default now(), unique (claim_id, version)
);

create table if not exists warranty.rule_results (
  id uuid primary key, company_id uuid not null, claim_version_id uuid not null references warranty.claim_versions(id) on delete cascade,
  run_seq int not null default 1, rule_key text not null, label text, result text not null, code text, detail text,
  source text, source_url text, fixable boolean, suggested_fix text,
  created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists warranty.claim_events (
  id bigserial primary key, company_id uuid not null, claim_id uuid not null references warranty.claims(id) on delete cascade,
  from_status text, to_status text not null, reason text, actor text not null default 'orchestrator', sim_at timestamptz,
  created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists warranty.submissions (
  id uuid primary key, company_id uuid not null, claim_id uuid not null references warranty.claims(id) on delete cascade,
  version int not null, kind text not null, oem_claim_id text, idem_key text not null unique, status text not null,
  response jsonb, sim_submitted_at timestamptz, created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists warranty.adjudication_snapshots (
  id uuid primary key, company_id uuid not null, claim_id uuid not null references warranty.claims(id) on delete cascade,
  version int not null, decision text not null, rejection_codes text[] not null default '{}', reviewer_comment text,
  approved_amount numeric(12,2), decided_at_sim timestamptz, raw jsonb,
  created_at timestamptz default now(), updated_at timestamptz default now(), unique (claim_id, version)
);

create table if not exists warranty.credit_notes (
  id uuid primary key, company_id uuid not null, claim_id uuid not null references warranty.claims(id) on delete cascade,
  oem_credit_ref text unique not null, amount numeric(12,2) not null, sim_paid_at timestamptz,
  created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists warranty.preauth_requests (
  id uuid primary key, company_id uuid not null, claim_id uuid not null references warranty.claims(id) on delete cascade,
  kind text not null default 'pre_auth', ticket text not null, status text not null, ref text, request jsonb,
  created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists warranty.escalations (
  id uuid primary key, company_id uuid not null, claim_id uuid not null references warranty.claims(id) on delete cascade,
  kind text not null, reason_code text, agent_recommendation text, confidence double precision, tried jsonb,
  draft_message text, status text not null default 'open', resolved_by text, resolution jsonb, source_agent text,
  created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists warranty.review_actions (
  id uuid primary key, company_id uuid not null, escalation_id uuid not null references warranty.escalations(id) on delete cascade,
  action text not null, actor text not null, payload jsonb, created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists warranty.baseline_claims (
  id uuid primary key, company_id uuid not null, claim_ref text not null, programme text not null, vin text, system text, wip_no text,
  labour_op text, claim_type text, wip_closed_at timestamptz, submitted_at timestamptz, first_pass_rejected boolean,
  rejection_codes text[] not null default '{}', final_status text not null, amount_claimed numeric(12,2),
  amount_paid numeric(12,2), paid_at timestamptz, created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists agent.agent_runs (
  id uuid primary key, company_id uuid not null, run_key text not null, claim_id uuid, agent text not null, model text,
  role text, status text not null, started_at timestamptz default now(), ended_at timestamptz,
  input_tokens int not null default 0, output_tokens int not null default 0, cost_usd numeric(10,5) not null default 0,
  output jsonb, error text, turns int not null default 0, created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists agent.agent_steps (
  id bigserial primary key, company_id uuid not null, run_id uuid not null references agent.agent_runs(id) on delete cascade,
  seq int not null, kind text not null, tool_name text, payload jsonb, duration_ms int,
  created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists agent.agent_replays (
  id bigserial primary key, company_id uuid not null, run_key text not null, seq int not null, agent text,
  request_hash text, response jsonb not null, latency_ms int not null default 0, recorded_at timestamptz default now(),
  created_at timestamptz default now(), updated_at timestamptz default now(), unique (run_key, seq)
);

create table if not exists ops.jobs (
  id bigserial primary key, company_id uuid not null, kind text not null, claim_id uuid, payload jsonb not null default '{}',
  priority int not null default 100, run_at timestamptz not null default now(), status text not null default 'queued',
  attempts int not null default 0, lease_until timestamptz, idem_key text unique, last_error text,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create index if not exists jobs_ready_idx on ops.jobs (status, priority, run_at);

create table if not exists ops.outbox (
  id bigserial primary key, company_id uuid not null, topic text not null, payload jsonb not null,
  published_at timestamptz, created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists ops.event_log (
  id bigserial primary key, company_id uuid not null, outbox_id bigint unique, topic text not null, payload jsonb not null,
  created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists ops.sync_state (
  company_id uuid not null, key text not null, value text, created_at timestamptz default now(),
  updated_at timestamptz default now(), primary key (company_id, key)
);

create table if not exists ops.runs (
  id uuid primary key, company_id uuid not null, scope text not null, filter jsonb, claim_refs text[] not null default '{}',
  mode text, status text not null default 'running', started_at timestamptz default now(), ended_at timestamptz,
  created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists ops.idempotency_keys (
  key text primary key, company_id uuid not null, response jsonb not null,
  created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists ops.integration_log (
  id bigserial primary key, company_id uuid not null, service text not null, target text not null, method text,
  path text, request_hash text, status int, latency_ms int, created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists ops.audit_log (
  id bigserial primary key, company_id uuid not null, actor text not null, action text not null, subject text,
  detail jsonb, created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists ops.users (
  id uuid primary key, company_id uuid not null, email text unique not null, display_name text, role text not null,
  password_hash text not null, created_at timestamptz default now(), updated_at timestamptz default now()
);

create table if not exists llm.llm_calls (
  id bigserial primary key, company_id uuid not null, run_key text not null, seq int not null, claim_ref text,
  agent text, role text, model text, provider text, input_tokens int not null default 0, output_tokens int not null default 0,
  cost_usd numeric(10,5) not null default 0, latency_ms int, request_hash text,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create index if not exists llm_calls_claim_idx on llm.llm_calls (claim_ref);
