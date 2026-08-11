-- pg-sim: the simulated external world (OEM gateways) + hidden ground truth. Agents never reach this DB.
create schema if not exists oem_sim;
create schema if not exists groundtruth;

create table if not exists oem_sim.oem_vehicle_registry (
  vin text primary key, company_id uuid not null, programme text not null, warranty_start_date date not null,
  contracts jsonb not null default '[]', repair_history jsonb not null default '[]', service_history jsonb not null default '[]',
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists oem_sim.oem_campaigns (
  id text primary key, company_id uuid not null, programme text not null, campaign_code text not null, title text not null,
  affected_vins text[] not null default '{}', labour_op text, status text not null,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists oem_sim.oem_labour_catalog (
  programme text not null, op_code text not null, company_id uuid not null, system text not null,
  standard_hours numeric(6,2) not null, causal_families text[] not null default '{}',
  created_at timestamptz default now(), updated_at timestamptz default now(), primary key (programme, op_code)
);
create table if not exists oem_sim.oem_submissions (
  id text primary key, company_id uuid not null, programme text not null, claim_ref text not null, oem_claim_id text not null,
  version int not null, payload jsonb not null, idem_key text unique, received_at_sim timestamptz not null,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists oem_sim.oem_adjudications (
  id text primary key, company_id uuid not null, submission_id text not null references oem_sim.oem_submissions(id) on delete cascade,
  decision text not null, rejection_codes text[] not null default '{}', rule_trace jsonb, reviewer_comment text,
  approved_amount numeric(12,2), decided_at_sim timestamptz not null,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists oem_sim.oem_preauths (
  ticket text primary key, company_id uuid not null, programme text not null, vin text, claim_ref text, kind text not null,
  status text not null, ref text, request jsonb, decided_by text,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists oem_sim.oem_credit_notes (
  oem_credit_ref text primary key, company_id uuid not null, programme text not null, claim_ref text not null,
  oem_claim_id text not null, amount numeric(12,2) not null, sim_paid_at timestamptz not null,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists groundtruth.seed_issue_manifest (
  repair_order_id text primary key, company_id uuid not null, wip_no text not null, issues jsonb not null default '[]',
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists groundtruth.scoring_results (
  id bigserial primary key, company_id uuid not null, result jsonb not null,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
