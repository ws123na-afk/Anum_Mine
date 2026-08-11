create extension if not exists vector;

create table if not exists tenants (
  id text primary key,
  name text not null,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists workspaces (
  id text primary key,
  tenant_id text not null references tenants(id),
  name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, id)
);

create table if not exists tasks (
  id text primary key,
  tenant_id text not null,
  workspace_id text not null,
  created_by_user_id text not null,
  title text not null,
  prompt text not null,
  status text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (tenant_id, workspace_id) references workspaces(tenant_id, id)
);

create table if not exists agent_runs (
  id text primary key,
  tenant_id text not null,
  task_id text not null references tasks(id),
  status text not null,
  result text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists agent_run_steps (
  id text primary key,
  tenant_id text not null,
  run_id text not null references agent_runs(id),
  type text not null,
  summary text not null,
  step_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists approvals (
  id text primary key,
  tenant_id text not null,
  task_id text not null references tasks(id),
  action text not null,
  risk_level text not null,
  status text not null,
  reason text not null,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists domain_events (
  id text primary key,
  tenant_id text not null,
  workspace_id text,
  type text not null,
  version integer not null default 1,
  subject text not null,
  correlation_id text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists memories (
  id text primary key,
  tenant_id text not null,
  workspace_id text,
  source_task_id text references tasks(id),
  kind text not null,
  content text not null,
  embedding vector(1536),
  provenance jsonb not null default '{}'::jsonb,
  retention_policy text not null default 'default',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists ix_tasks_tenant_workspace_status on tasks (tenant_id, workspace_id, status);
create index if not exists ix_agent_runs_tenant_task on agent_runs (tenant_id, task_id);
create index if not exists ix_approvals_tenant_status on approvals (tenant_id, status);
create index if not exists ix_events_tenant_type_created on domain_events (tenant_id, type, created_at);
create index if not exists ix_memories_tenant_workspace on memories (tenant_id, workspace_id);

alter table workspaces enable row level security;
alter table tasks enable row level security;
alter table agent_runs enable row level security;
alter table agent_run_steps enable row level security;
alter table approvals enable row level security;
alter table domain_events enable row level security;
alter table memories enable row level security;

create policy tenant_isolation_workspaces on workspaces
  using (tenant_id = current_setting('anum.tenant_id', true))
  with check (tenant_id = current_setting('anum.tenant_id', true));

create policy tenant_isolation_tasks on tasks
  using (tenant_id = current_setting('anum.tenant_id', true))
  with check (tenant_id = current_setting('anum.tenant_id', true));

create policy tenant_isolation_agent_runs on agent_runs
  using (tenant_id = current_setting('anum.tenant_id', true))
  with check (tenant_id = current_setting('anum.tenant_id', true));

create policy tenant_isolation_agent_run_steps on agent_run_steps
  using (tenant_id = current_setting('anum.tenant_id', true))
  with check (tenant_id = current_setting('anum.tenant_id', true));

create policy tenant_isolation_approvals on approvals
  using (tenant_id = current_setting('anum.tenant_id', true))
  with check (tenant_id = current_setting('anum.tenant_id', true));

create policy tenant_isolation_domain_events on domain_events
  using (tenant_id = current_setting('anum.tenant_id', true))
  with check (tenant_id = current_setting('anum.tenant_id', true));

create policy tenant_isolation_memories on memories
  using (tenant_id = current_setting('anum.tenant_id', true))
  with check (tenant_id = current_setting('anum.tenant_id', true));
