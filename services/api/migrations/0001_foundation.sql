create extension if not exists vector;

create table if not exists tenants (
  id varchar(80) primary key,
  name varchar(160) not null,
  status varchar(40) not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists workspaces (
  id varchar(80) primary key,
  tenant_id varchar(80) not null,
  name varchar(160) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_workspaces_tenant foreign key (tenant_id) references tenants(id),
  constraint uq_workspaces_tenant_id unique (tenant_id, id)
);

create table if not exists tasks (
  id varchar(80) primary key,
  tenant_id varchar(80) not null,
  workspace_id varchar(80) not null,
  created_by_user_id varchar(120) not null,
  title varchar(160) not null,
  prompt text not null,
  status varchar(40) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_tasks_workspace foreign key (tenant_id, workspace_id)
    references workspaces(tenant_id, id),
  constraint uq_tasks_scope_id unique (tenant_id, workspace_id, id)
);

create table if not exists agent_runs (
  id varchar(80) primary key,
  tenant_id varchar(80) not null,
  workspace_id varchar(80) not null,
  task_id varchar(80) not null,
  status varchar(40) not null,
  result text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_agent_runs_task foreign key (tenant_id, workspace_id, task_id)
    references tasks(tenant_id, workspace_id, id),
  constraint uq_agent_runs_scope_id unique (tenant_id, workspace_id, id)
);

create table if not exists agent_run_steps (
  id varchar(80) primary key,
  tenant_id varchar(80) not null,
  workspace_id varchar(80) not null,
  run_id varchar(80) not null,
  type varchar(80) not null,
  summary text not null,
  step_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_agent_run_steps_run foreign key (tenant_id, workspace_id, run_id)
    references agent_runs(tenant_id, workspace_id, id)
);

create table if not exists approvals (
  id varchar(80) primary key,
  tenant_id varchar(80) not null,
  workspace_id varchar(80) not null,
  task_id varchar(80) not null,
  action varchar(160) not null,
  risk_level varchar(40) not null,
  status varchar(40) not null,
  reason text not null,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_approvals_task foreign key (tenant_id, workspace_id, task_id)
    references tasks(tenant_id, workspace_id, id)
);

create table if not exists domain_events (
  id varchar(80) primary key,
  tenant_id varchar(80) not null,
  workspace_id varchar(80),
  type varchar(160) not null,
  version integer not null default 1,
  subject varchar(160) not null,
  correlation_id varchar(120) not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_domain_events_workspace foreign key (tenant_id, workspace_id)
    references workspaces(tenant_id, id)
);

create table if not exists memories (
  id varchar(80) primary key,
  tenant_id varchar(80) not null,
  workspace_id varchar(80),
  source_task_id varchar(80),
  kind varchar(80) not null,
  content text not null,
  embedding vector(1536),
  provenance jsonb not null default '{}'::jsonb,
  retention_policy varchar(80) not null default 'default',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_memories_workspace foreign key (tenant_id, workspace_id)
    references workspaces(tenant_id, id),
  constraint fk_memories_source_task foreign key (tenant_id, workspace_id, source_task_id)
    references tasks(tenant_id, workspace_id, id),
  constraint ck_memories_source_task_workspace
    check (source_task_id is null or workspace_id is not null)
);

create index ix_tasks_tenant_workspace_status
  on tasks (tenant_id, workspace_id, status);
create index ix_agent_runs_tenant_workspace_task
  on agent_runs (tenant_id, workspace_id, task_id);
create index ix_agent_run_steps_tenant_workspace_run
  on agent_run_steps (tenant_id, workspace_id, run_id);
create index ix_approvals_tenant_workspace_status
  on approvals (tenant_id, workspace_id, status);
create index ix_events_tenant_workspace_type_created
  on domain_events (tenant_id, workspace_id, type, created_at);
create index ix_events_correlation on domain_events (correlation_id);
create index ix_memories_tenant_workspace on memories (tenant_id, workspace_id);

-- Application roles are deployment concerns. FORCE makes these policies effective for
-- non-superuser table owners too, so CI can test with a separately provisioned app role.
alter table workspaces enable row level security;
alter table workspaces force row level security;
alter table tasks enable row level security;
alter table tasks force row level security;
alter table agent_runs enable row level security;
alter table agent_runs force row level security;
alter table agent_run_steps enable row level security;
alter table agent_run_steps force row level security;
alter table approvals enable row level security;
alter table approvals force row level security;
alter table domain_events enable row level security;
alter table domain_events force row level security;
alter table memories enable row level security;
alter table memories force row level security;

create policy tenant_isolation_workspaces on workspaces
  using (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and id = nullif(current_setting('anum.workspace_id', true), '')
  )
  with check (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and id = nullif(current_setting('anum.workspace_id', true), '')
  );

create policy tenant_isolation_tasks on tasks
  using (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
  )
  with check (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
  );

create policy tenant_isolation_agent_runs on agent_runs
  using (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
  )
  with check (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
  );

create policy tenant_isolation_agent_run_steps on agent_run_steps
  using (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
  )
  with check (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
  );

create policy tenant_isolation_approvals on approvals
  using (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
  )
  with check (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
  );

create policy tenant_isolation_domain_events on domain_events
  using (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and (
      workspace_id is null
      or workspace_id = nullif(current_setting('anum.workspace_id', true), '')
    )
  )
  with check (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and (
      workspace_id is null
      or workspace_id = nullif(current_setting('anum.workspace_id', true), '')
    )
  );

create policy tenant_isolation_memories on memories
  using (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and (
      workspace_id is null
      or workspace_id = nullif(current_setting('anum.workspace_id', true), '')
    )
  )
  with check (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and (
      workspace_id is null
      or workspace_id = nullif(current_setting('anum.workspace_id', true), '')
    )
  );
