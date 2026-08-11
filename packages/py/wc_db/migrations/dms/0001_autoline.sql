-- pg-dms: fake Autoline (al_* tables), including its warranty-module mirror tables
create schema if not exists autoline;

create table if not exists autoline.al_companies (
  company_id uuid primary key, name text not null, dealer_codes jsonb not null default '{}',
  labour_rates jsonb not null default '{}', created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists autoline.al_staff (
  id text primary key, company_id uuid not null, name text not null, role text not null, branch text,
  hv_certified_id text, created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists autoline.al_customers (
  id text primary key, company_id uuid not null, name text not null, phone text, emirate text,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists autoline.al_vehicles (
  vin text primary key, company_id uuid not null, customer_id text, make text not null, model text not null,
  model_year int, plate text, oem_programme text not null, warranty_start_date date not null,
  market_spec text not null default 'GCC', aftermarket_mods text[] not null default '{}', colour text,
  is_fictional boolean not null default true, created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists autoline.al_service_history (
  id text primary key, company_id uuid not null, vin text not null, service_date date not null, odometer_km int not null,
  kind text not null, description text, wip_no text, created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists autoline.al_repair_orders (
  id text primary key, company_id uuid not null, wip_no text unique not null, vin text not null, customer_id text,
  advisor_id text, branch text, status text not null, opened_at timestamptz, closed_at timestamptz,
  pay_type_summary text, technician_story text, customer_complaint text, dtc_codes text[] not null default '{}',
  diagnostic_session_id text, pre_auth_ref text, tech_support_ref text, odometer_in int, odometer_out int,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists autoline.al_repair_order_lines (
  id text primary key, company_id uuid not null, repair_order_id text not null references autoline.al_repair_orders(id) on delete cascade,
  line_no int not null, line_type text not null, parent_line_no int, op_code text, part_no text, description text not null,
  qty numeric(10,2) not null default 1, unit_price numeric(12,2) not null default 0, labour_hours_actual numeric(6,2),
  pay_type text not null, is_causal boolean not null default false, technician_id text, part_serial text,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists autoline.al_attachments (
  id text primary key, company_id uuid not null, repair_order_id text not null, kind text not null, title text not null,
  url text not null, blob_key text, caption text, captured_at timestamptz, uploaded_by text,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists autoline.al_coverage_contracts (
  id text primary key, company_id uuid not null, vin text not null, programme text not null, contract_type text not null,
  start_date date not null, end_date date not null, km_limit int, component_scope text[] not null default '{}',
  conditions jsonb not null default '{}', created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists autoline.al_campaigns (
  id text primary key, company_id uuid not null, programme text not null, campaign_code text not null, title text not null,
  affected_vins text[] not null default '{}', labour_op text, status text not null,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists autoline.al_labour_ops (
  id text primary key, company_id uuid not null, programme text not null, op_code text not null, description text not null,
  system text not null, standard_hours numeric(6,2) not null, created_at timestamptz default now(), updated_at timestamptz default now(),
  unique (programme, op_code)
);
create table if not exists autoline.al_condition_codes (
  id text primary key, company_id uuid not null, programme text not null, code text not null, description text not null,
  system text not null, created_at timestamptz default now(), updated_at timestamptz default now()
);
-- warranty-module mirror (write-back target, as a real DMS would show it)
create table if not exists autoline.al_warranty_claims (
  claim_ref text primary key, company_id uuid not null, wip_no text not null, status text not null,
  amount numeric(12,2), created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists autoline.al_warranty_credit_notes (
  oem_credit_ref text primary key, company_id uuid not null, claim_ref text not null, amount numeric(12,2) not null,
  sim_paid_at timestamptz, created_at timestamptz default now(), updated_at timestamptz default now()
);
