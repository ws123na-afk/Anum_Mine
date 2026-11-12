"""Files bucket reconcile and the scheduled routing probe for every reachable target (expand only).

docs/deploy-oracle.md#reconciling-the-files-bucket, docs/governance-and-scale.md#routing-targets.

Files bucket reconcile (``python -m anum_api.files_reconcile``): ``anum_maintenance`` may read the
tenant and storage key columns of ``workspace_files``, ``workspace_file_versions`` and
``mail_attachments`` and the tenant and archive key of ``tenant_lifecycle_jobs``, only for rows of
the tenant named by ``anum.tenant_id`` (policies ``maintenance_storage_keys_<table>``). The
restrictive owner policy of ``mail_attachments`` admits the role next to the purger (the vault's
restrictive policy on ``workspace_files`` already does). The role reads keys, never content, and
never writes. Files not scanned yet were already visible to it without a tenant context (the
malware rescan's discovery, ``0075_file_scan_status``); their storage key is now readable too.

Scheduled routing probe (``anum_api.routing_probe``): ``anum_maintenance`` may read the
``provider`` name of enabled routing targets, also those never probed (policy
``maintenance_routing_targets``), and the tenant, workspace and ``provider`` name of the model
configurations of organisations with an enabled routing target (policy
``maintenance_routing_probe_configs``), so the pass finds a workspace configured for each target's
provider. Never a key, base URL or model.

Chained after ``0175_business_depth``.

Expand only: new grants and policies, one restrictive predicate widened for one NOLOGIN role. The
previous release runs against this schema unchanged. Rollback: ``alembic downgrade
0175_business_depth`` drops the policies, restores the owner policy and revokes the grants.
"""

from alembic import op

revision = "0176_files_audit_probe"
down_revision = "0175_business_depth"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('anum.tenant_id', true), '')"
OWNER = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"


def upgrade() -> None:
    # --- Files bucket reconcile: storage keys of the tenant in context ---------------------
    op.execute("grant select (tenant_id, storage_key) on workspace_files to anum_maintenance")
    op.execute("grant select (tenant_id, storage_key) on workspace_file_versions to anum_maintenance")
    op.execute("grant select (tenant_id, storage_key) on mail_attachments to anum_maintenance")
    op.execute("grant select (tenant_id, archive_key) on tenant_lifecycle_jobs to anum_maintenance")
    op.execute(
        f"""
        create policy maintenance_storage_keys_workspace_files on workspace_files
        for select to anum_maintenance
        using ({TENANT})
        """
    )
    op.execute(
        f"""
        create policy maintenance_storage_keys_workspace_file_versions on workspace_file_versions
        for select to anum_maintenance
        using ({TENANT})
        """
    )
    op.execute(
        f"""
        create policy maintenance_storage_keys_mail_attachments on mail_attachments
        for select to anum_maintenance
        using ({TENANT})
        """
    )
    op.execute(
        f"""
        create policy maintenance_storage_keys_tenant_lifecycle_jobs on tenant_lifecycle_jobs
        for select to anum_maintenance
        using ({TENANT})
        """
    )
    op.execute(
        f"""
        alter policy owner_only_mail_attachments on mail_attachments
        using ({OWNER} or current_user in ('anum_tenant_purger', 'anum_maintenance'))
        """
    )

    # --- Scheduled routing probe: every enabled target, and who can reach its provider ------
    op.execute("grant select (provider) on routing_targets to anum_maintenance")
    op.execute(
        """
        create policy maintenance_routing_targets on routing_targets
        for select to anum_maintenance
        using (enabled)
        """
    )
    op.execute("grant select (provider) on workspace_model_configs to anum_maintenance")
    op.execute(
        """
        create policy maintenance_routing_probe_configs on workspace_model_configs
        for select to anum_maintenance
        using (
          exists (
            select 1 from routing_targets t
            where t.tenant_id = workspace_model_configs.tenant_id and t.enabled
          )
        )
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_routing_probe_configs on workspace_model_configs")
    op.execute("drop policy if exists maintenance_routing_targets on routing_targets")
    op.execute(
        f"""
        alter policy owner_only_mail_attachments on mail_attachments
        using ({OWNER} or current_user = 'anum_tenant_purger')
        """
    )
    op.execute("drop policy if exists maintenance_storage_keys_tenant_lifecycle_jobs on tenant_lifecycle_jobs")
    op.execute("drop policy if exists maintenance_storage_keys_mail_attachments on mail_attachments")
    op.execute("drop policy if exists maintenance_storage_keys_workspace_file_versions on workspace_file_versions")
    op.execute("drop policy if exists maintenance_storage_keys_workspace_files on workspace_files")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select (provider) on workspace_model_configs from anum_maintenance;
            revoke select (provider) on routing_targets from anum_maintenance;
            revoke select (archive_key) on tenant_lifecycle_jobs from anum_maintenance;
            revoke select (tenant_id, storage_key) on mail_attachments from anum_maintenance;
            revoke select (tenant_id, storage_key) on workspace_file_versions from anum_maintenance;
            revoke select (storage_key) on workspace_files from anum_maintenance;
          end if;
        end
        $$
        """
    )
