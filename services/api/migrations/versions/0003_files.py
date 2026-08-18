from alembic import op

revision = "0003_files"
down_revision = "0002_memory_retention"
branch_labels = None
depends_on = None


_UPGRADE_SQL = """
create table if not exists files (
  id varchar(80) primary key,
  tenant_id varchar(80) not null,
  workspace_id varchar(80) not null,
  task_id varchar(80),
  owner_user_id varchar(120) not null,
  bucket varchar(160) not null,
  key varchar(1024) not null,
  checksum_sha256 varchar(64) not null,
  size_bytes bigint not null,
  content_type varchar(160) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_files_workspace foreign key (tenant_id, workspace_id)
    references workspaces(tenant_id, id),
  constraint fk_files_task foreign key (tenant_id, workspace_id, task_id)
    references tasks(tenant_id, workspace_id, id),
  constraint uq_files_bucket_key unique (bucket, key)
);

create index ix_files_tenant_workspace on files (tenant_id, workspace_id);
create index ix_files_task on files (task_id);

-- Matches the RLS pattern established in 0001_foundation.sql: FORCE makes
-- the policy effective for non-superuser table owners too (e.g. the
-- anum_test_app role used by the Postgres integration tests).
alter table files enable row level security;
alter table files force row level security;

create policy tenant_isolation_files on files
  using (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
  )
  with check (
    tenant_id = nullif(current_setting('anum.tenant_id', true), '')
    and workspace_id = nullif(current_setting('anum.workspace_id', true), '')
  );
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    # Dropping the table also drops its policy; no separate policy cleanup
    # needed, matching the child-first-drop style of 0001_foundation.py.
    op.execute("drop table if exists files")
