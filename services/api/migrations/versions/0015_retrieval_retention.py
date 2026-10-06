"""Retention for the retrieval index (docs/memory.md#retention).

* ``anum_maintenance`` may discover retrieval sources of *expired* memories: the
  ``tenant_id``, ``workspace_id``, ``source_type``, ``source_id`` and
  ``source_expires_at`` columns of ``retrieval_sources`` rows whose memory's retention
  has passed, and nothing else (no chunk text, no other row, no write). The retention
  purge then deletes those rows as the application role inside each workspace's RLS
  context (``anum_api/retrieval_retention.py``), never with ``BYPASSRLS``.
* Deleting a memory or a workspace file deletes its retrieval source (and, by the
  existing cascade, its chunks) in the same transaction: ``AFTER DELETE`` row triggers
  whose functions run as the invoking role (``SECURITY INVOKER``, the default), so the
  delete is checked by the same tenant-isolation policies as the statement that fired
  it.

Grant ``anum_maintenance`` to the API login ``WITH INHERIT FALSE, SET TRUE`` (as
``infra/helm/bootstrap-database.sql`` does): the new policy admits rows of every tenant,
so an inheriting grant would widen the application role's own queries.

Expand only: the previous release keeps working (its delete hooks find the rows already
gone, and it never reads the new policy).
"""

from alembic import op

revision = "0015_retrieval_retention"
down_revision = "0014_retrieval_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "grant select (tenant_id, workspace_id, source_type, source_id, source_expires_at) "
        "on retrieval_sources to anum_maintenance"
    )
    # Permissive policies are OR-ed per role, so this widens access for this role only.
    op.execute(
        """
        create policy maintenance_expired_retrieval_sources on retrieval_sources
        for select to anum_maintenance
        using (
          source_type = 'memory'
          and source_expires_at is not null
          and source_expires_at <= now()
        )
        """
    )
    op.execute(
        """
        create or replace function anum_forget_deleted_memory() returns trigger
        language plpgsql security invoker as $$
        begin
          delete from retrieval_sources
          where tenant_id = old.tenant_id
            and workspace_id = old.workspace_id
            and source_type = 'memory'
            and source_id = old.id;
          return null;
        end
        $$
        """
    )
    op.execute(
        """
        create or replace function anum_forget_deleted_file() returns trigger
        language plpgsql security invoker as $$
        begin
          delete from retrieval_sources
          where tenant_id = old.tenant_id
            and workspace_id = old.workspace_id
            and source_type = 'file'
            and source_id = old.id;
          return null;
        end
        $$
        """
    )
    op.execute(
        "create trigger trg_memories_forget_retrieval after delete on memories "
        "for each row execute function anum_forget_deleted_memory()"
    )
    op.execute(
        "create trigger trg_workspace_files_forget_retrieval after delete on workspace_files "
        "for each row execute function anum_forget_deleted_file()"
    )


def downgrade() -> None:
    op.execute("drop trigger if exists trg_workspace_files_forget_retrieval on workspace_files")
    op.execute("drop trigger if exists trg_memories_forget_retrieval on memories")
    op.execute("drop function if exists anum_forget_deleted_file()")
    op.execute("drop function if exists anum_forget_deleted_memory()")
    op.execute("drop policy if exists maintenance_expired_retrieval_sources on retrieval_sources")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on retrieval_sources from anum_maintenance;
          end if;
        end
        $$
        """
    )
