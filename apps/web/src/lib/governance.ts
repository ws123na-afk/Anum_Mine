// Governance, enterprise routing and integration configuration: wire types, request bodies
// and the plain-words state the web views show. Pure functions, so
// `pnpm --filter @anum/web test:unit` can exercise them without a DOM.
// docs/governance-and-scale.md, docs/tools-and-integrations.md.
import { t } from './i18n.ts';

export interface PolicyRule { action: string; effect: string; conditions?: Record<string, unknown> }
export interface PolicyPack { id: string; name: string; description: string; version: number; active: boolean; rules: PolicyRule[]; created_by: string; created_at: string }
export interface RoleTemplate { id: string; name: string; permissions: string[]; created_at: string }
export type { ApprovalRule } from './approvalRules.ts';
export interface GovernanceSummary { policy_packs: number; active_policy_packs: number; role_templates: number; approval_rules: number; memory_governance_configured: boolean; can_manage_organization_policy?: boolean }
export type RegionStatus = 'healthy' | 'degraded' | 'offline' | 'unverified';
export interface RoutingTarget { id: string; region: string; provider: string; model: string; status: RegionStatus; modalities: string[]; sensitivity: string[]; cost_per_1k_tokens: number; latency_ms: number }
export interface EnterpriseOperations { active_regions: number; healthy_targets: number; degraded_targets: number; installed_packages: number; failover_ready: boolean }
export interface IntegrationConfiguration { enabled: boolean; endpoint: string | null }
export interface IntegrationConfigurationView extends IntegrationConfiguration { id: string; tenant_id: string; workspace_id: string }

export type Result<T> = { ok: true; value: T } | { ok: false; error: string };

/** Comma or newline separated text to a trimmed, de-duplicated list. */
export function parseList(text: string): string[] {
  return [...new Set(text.split(/[,\n]/).map((x) => x.trim()).filter(Boolean))];
}

/**
 * Who may change the tenant-wide controls (policy packs, role templates, approval rules).
 * The API decides (`can_manage_organization_policy`, the organization administrator role);
 * the view never guesses from the workspace role, because owning one workspace is not enough.
 * Until the summary loads nothing is editable and nothing is explained yet.
 */
export function organizationPolicyAccess(summary: Pick<GovernanceSummary, 'can_manage_organization_policy'> | null): TenantWriteAccess {
  return tenantWriteAccess(summary ? summary.can_manage_organization_policy ?? false : null, t('gov.subject.policy'));
}

export interface TenantWriteAccess { canManage: boolean; notice: string | null }

/**
 * Who may make a tenant-wide write (organization budget, routing targets, the marketplace
 * catalog, policy): the API's capability flag for the caller, never a guess from the
 * workspace role. `null` means the flag has not loaded yet: nothing is editable and nothing
 * is explained yet. `subject` names what is read only, in plain words ("routing targets").
 */
export function tenantWriteAccess(canManage: boolean | null, subject: string): TenantWriteAccess {
  if (canManage === null) return { canManage: false, notice: null };
  if (canManage) return { canManage: true, notice: null };
  return {
    canManage: false,
    notice: t('gov.readOnly', { subject }, { isolate: false }),
  };
}

/** Routing targets and the marketplace catalog (Enterprise view), from the governance summary. */
export const enterpriseWriteAccess = (summary: Pick<GovernanceSummary, 'can_manage_organization_policy'> | null): TenantWriteAccess =>
  tenantWriteAccess(summary ? summary.can_manage_organization_policy ?? false : null, t('gov.subject.enterprise'));

/** The badge and next action for a pack: the API activates or archives a stored version. */
export function packState(pack: Pick<PolicyPack, 'active'>): { label: string; tone: 'success' | 'neutral'; action: 'activate' | 'archive' } {
  return pack.active ? { label: t('gov.packs.active'), tone: 'success', action: 'archive' } : { label: t('gov.packs.archived'), tone: 'neutral', action: 'activate' };
}

/** Packs grouped by name, newest version first, so every version of one pack sits together. */
export function sortPacks(packs: PolicyPack[]): PolicyPack[] {
  return [...packs].sort((a, b) => a.name.localeCompare(b.name) || b.version - a.version);
}

export const ROLE_TEMPLATE_PERMISSIONS = [
  'task:read', 'task:create', 'task:run', 'task:cancel', 'approval:read', 'approval:decide', 'event:read',
  'memory:read', 'memory:create', 'memory:delete', 'file:read', 'file:upload', 'file:manage', 'integration:read', 'marketplace:read', 'routing:read',
  'operations:read', 'organization:read', 'policy:read', 'automation:read', 'automation:manage',
  'commerce:read', 'commerce:operate', 'commerce:manage', 'commerce:pii', 'meeting:read', 'meeting:create',
] as const;

export function roleTemplateBody(name: string, permissions: string[]): Result<{ name: string; permissions: string[] }> {
  const trimmed = name.trim();
  if (!trimmed) return { ok: false, error: t('gov.templates.err.name') };
  if (trimmed.length > 80) return { ok: false, error: t('gov.templates.err.nameLength') };
  const unique = [...new Set(permissions)];
  if (unique.length === 0) return { ok: false, error: t('gov.templates.err.permissions') };
  return { ok: true, value: { name: trimmed, permissions: unique } };
}

/**
 * What the regional metrics honestly say. With fewer than two serving regions there is no
 * failover to report, so the view says "single region" instead of "pending".
 */
export function regionSummary(ops: EnterpriseOperations | null, targets: RoutingTarget[]): { regions: string; failover: string; detail: string } {
  const serving = new Set(targets.filter((target) => target.status !== 'offline').map((target) => target.region));
  const count = ops?.active_regions ?? serving.size;
  if (targets.length === 0) return { regions: t('ent.region.none'), failover: t('ent.failover.na'), detail: t('ent.detail.noTargets') };
  if (count <= 1) {
    const only = [...serving][0];
    return { regions: only ? t('ent.region.single', { region: only }) : t('ent.region.noServing'), failover: t('ent.failover.na'), detail: t('ent.detail.needTwo') };
  }
  return { regions: t('ent.region.count', { count }), failover: ops?.failover_ready ? t('ent.failover.ready') : t('ent.failover.notReady'), detail: ops?.failover_ready ? t('ent.detail.ready') : t('ent.detail.notReady') };
}

export interface TargetDraft { id: string; region: string; provider: string; model: string; status: RegionStatus; modalities: string; sensitivity: string; cost: string; latency: string }

export const emptyTargetDraft = (): TargetDraft => ({ id: '', region: '', provider: '', model: '', status: 'unverified', modalities: 'text', sensitivity: 'standard', cost: '0', latency: '1000' });

export const targetDraft = (t: RoutingTarget): TargetDraft => ({
  id: t.id, region: t.region, provider: t.provider, model: t.model, status: t.status,
  modalities: t.modalities.join(', '), sensitivity: t.sensitivity.join(', '), cost: String(t.cost_per_1k_tokens), latency: String(t.latency_ms),
});

/** The `PUT /routing/targets/{id}` body; the id in the body must match the path. */
export function routingTargetBody(draft: TargetDraft): Result<RoutingTarget> {
  const id = draft.id.trim();
  if (!/^[A-Za-z0-9._-]{1,80}$/.test(id)) return { ok: false, error: t('ent.err.id') };
  for (const [field, value] of [['region', draft.region], ['provider', draft.provider], ['model', draft.model]] as const) {
    if (!value.trim()) return { ok: false, error: t(`ent.err.${field}`) };
  }
  const cost = Number(draft.cost);
  if (draft.cost.trim() === '' || !Number.isFinite(cost) || cost < 0) return { ok: false, error: t('ent.err.cost') };
  const latency = Number(draft.latency);
  if (!Number.isInteger(latency) || latency <= 0) return { ok: false, error: t('ent.err.latency') };
  const modalities = parseList(draft.modalities);
  const sensitivity = parseList(draft.sensitivity);
  if (!modalities.length) return { ok: false, error: t('ent.err.modalities') };
  if (!sensitivity.length) return { ok: false, error: t('ent.err.sensitivity') };
  return { ok: true, value: { id, region: draft.region.trim(), provider: draft.provider.trim(), model: draft.model.trim(), status: draft.status, modalities, sensitivity, cost_per_1k_tokens: cost, latency_ms: latency } };
}

/** The `PUT /integrations/{id}/configuration` body: an empty endpoint means "use the default". */
export function integrationConfigBody(enabled: boolean, endpoint: string): Result<IntegrationConfiguration> {
  const value = endpoint.trim();
  if (value.length > 500) return { ok: false, error: 'The endpoint is at most 500 characters.' };
  return { ok: true, value: { enabled, endpoint: value || null } };
}

/**
 * What an integration's health means for the agent tool it backs (only `search` backs
 * one today: SearXNG and the `web_search` tool). Null for infrastructure integrations.
 */
export function integrationToolNote(kind: string, status: string): { text: string; available: boolean } | null {
  if (kind !== 'search') return null;
  if (status === 'connected') return { text: t('integrations.tool.available'), available: true };
  if (status === 'degraded') return { text: t('integrations.tool.degraded'), available: false };
  return { text: t('integrations.tool.unavailable'), available: false };
}

/** Badge for an integration's health; a disabled integration is "Not used", not a failure. */
export function integrationBadge(status: string): { label: string; tone: 'success' | 'dangerTone' | 'neutral' } {
  if (status === 'disabled') return { label: t('integrations.badge.unused'), tone: 'neutral' };
  if (status === 'connected') return { label: t('integrations.badge.connected'), tone: 'success' };
  if (status === 'degraded') return { label: t('integrations.badge.degraded'), tone: 'dangerTone' };
  return { label: status, tone: 'neutral' };
}
