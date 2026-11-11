// Governance, enterprise routing and integration configuration: wire types, request bodies
// and the plain-words state the web views show. Pure functions, so
// `pnpm --filter @anum/web test:unit` can exercise them without a DOM.
// docs/governance-and-scale.md, docs/tools-and-integrations.md.
import { formatDateTime, t } from './i18n.ts';

export interface PolicyRule { action: string; effect: string; conditions?: Record<string, unknown> }
export interface PolicyPack { id: string; name: string; description: string; version: number; active: boolean; rules: PolicyRule[]; created_by: string; created_at: string }
export interface RoleTemplate { id: string; name: string; permissions: string[]; created_at: string }
export type { ApprovalRule } from './approvalRules.ts';
import type { ApprovalRule } from './approvalRules.ts';
export interface GovernanceSummary { policy_packs: number; active_policy_packs: number; role_templates: number; approval_rules: number; memory_governance_configured: boolean; can_manage_organization_policy?: boolean }
export type RegionStatus = 'healthy' | 'degraded' | 'offline' | 'unverified';
export interface RoutingTarget {
  id: string; region: string; provider: string; model: string; status: RegionStatus; modalities: string[]; sensitivity: string[]; cost_per_1k_tokens: number; latency_ms: number;
  /** In routing (default true). Off: decisions and the figures skip it and it can be deleted. */
  enabled?: boolean;
  // Written by the health probe only (docs/governance-and-scale.md#routing-targets); all null: not yet probed.
  last_checked_at?: string | null; measured_latency_ms?: number | null; last_error?: string | null; probe_workspace_id?: string | null;
}
export interface MemoryGovernance { default_retention_days: number; allow_permanent_retention: boolean; require_provenance: boolean; allowed_source_types: string[]; updated_by?: string; updated_at?: string }
export interface EnterpriseOperations { active_regions: number; healthy_targets: number; degraded_targets: number; installed_packages: number; failover_ready: boolean; unprobed_targets?: number; disabled_targets?: number }
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

/**
 * `notice` is the one line that says what is read only and why; `howTo` is the longer way to get
 * the role (shown behind a "How to get access" disclosure), with the self-hosted shell command
 * apart so it can be shown as code.
 */
export interface TenantWriteAccess { canManage: boolean; notice: string | null; howTo: { text: string; server: string; command: string } | null }

/**
 * Who may make a tenant-wide write (organization budget, routing targets, the marketplace
 * catalog, policy): the API's capability flag for the caller, never a guess from the
 * workspace role. `null` means the flag has not loaded yet: nothing is editable and nothing
 * is explained yet. `subject` names what is read only, in plain words ("routing targets").
 */
export function tenantWriteAccess(canManage: boolean | null, subject: string): TenantWriteAccess {
  if (canManage === null) return { canManage: false, notice: null, howTo: null };
  if (canManage) return { canManage: true, notice: null, howTo: null };
  return {
    canManage: false,
    notice: t('gov.readOnly', { subject }, { isolate: false }),
    howTo: { text: t('gov.readOnlyHow'), server: t('gov.readOnlyHowServer'), command: t('gov.readOnlyHowCommand') },
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
  const serving = new Set(targets.filter((target) => (target.enabled ?? true) && target.status !== 'offline').map((target) => target.region));
  const count = ops?.active_regions ?? serving.size;
  if (targets.length === 0) return { regions: t('ent.region.none'), failover: t('ent.failover.na'), detail: t('ent.detail.noTargets') };
  if (ops && ops.healthy_targets === 0 && (ops.unprobed_targets ?? 0) > 0) {
    return { regions: t('ent.region.count', { count }), failover: t('ent.failover.unknown'), detail: t('ent.detail.unprobed', { count: ops.unprobed_targets ?? 0 }) };
  }
  if (count <= 1) {
    const only = [...serving][0];
    return { regions: only ? t('ent.region.single', { region: only }) : t('ent.region.noServing'), failover: t('ent.failover.na'), detail: t('ent.detail.needTwo') };
  }
  return { regions: t('ent.region.count', { count }), failover: ops?.failover_ready ? t('ent.failover.ready') : t('ent.failover.notReady'), detail: ops?.failover_ready ? t('ent.detail.ready') : t('ent.detail.notReady') };
}

export interface TargetDraft { id: string; region: string; provider: string; model: string; status: RegionStatus; modalities: string; sensitivity: string; cost: string; latency: string; enabled: boolean }

export const emptyTargetDraft = (): TargetDraft => ({ id: '', region: '', provider: '', model: '', status: 'unverified', modalities: 'text', sensitivity: 'standard', cost: '0', latency: '1000', enabled: true });

export const targetDraft = (t: RoutingTarget): TargetDraft => ({
  id: t.id, region: t.region, provider: t.provider, model: t.model, status: t.status,
  modalities: t.modalities.join(', '), sensitivity: t.sensitivity.join(', '), cost: String(t.cost_per_1k_tokens), latency: String(t.latency_ms), enabled: t.enabled ?? true,
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
  return { ok: true, value: { id, region: draft.region.trim(), provider: draft.provider.trim(), model: draft.model.trim(), status: draft.status, modalities, sensitivity, cost_per_1k_tokens: cost, latency_ms: latency, enabled: draft.enabled } };
}

/** The `PUT /integrations/{id}/configuration` body: an empty endpoint means "use the default". */
export function integrationConfigBody(enabled: boolean, endpoint: string): Result<IntegrationConfiguration> {
  const value = endpoint.trim();
  if (value.length > 500) return { ok: false, error: 'The endpoint is at most 500 characters.' };
  return { ok: true, value: { enabled, endpoint: value || null } };
}

/**
 * What an integration's health means for the agent tool it backs: SearXNG (`search`) backs
 * `web_search`, and the browser service (`browser-research`) backs `web.browse_page`.
 * Null for infrastructure integrations.
 */
export function integrationToolNote(kind: string, status: string, id = ''): { text: string; available: boolean } | null {
  if (id === 'browser-research') {
    if (status === 'connected') return { text: t('integrations.browse.available'), available: true };
    if (status === 'degraded') return { text: t('integrations.browse.degraded'), available: false };
    return { text: t('integrations.browse.unavailable'), available: false };
  }
  if (id === 'action-browser') {
    if (status === 'connected') return { text: t('integrations.act.available'), available: true };
    if (status === 'degraded') return { text: t('integrations.act.degraded'), available: false };
    return { text: t('integrations.act.unavailable'), available: false };
  }
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


// ------------------------------------------------------------------ routing target health

/** The health a row shows: what the probe measured, or "not yet probed"; never a typed status. */
export function targetHealth(target: RoutingTarget): { label: string; tone: 'success' | 'warning' | 'neutral' | 'danger'; detail: string } {
  if (!(target.enabled ?? true)) return { label: t('ent.health.disabled'), tone: 'neutral', detail: t('ent.health.disabledDetail') };
  if (!target.last_checked_at) return { label: t('ent.health.unprobed'), tone: 'neutral', detail: t('ent.health.unprobedDetail') };
  const when = formatDateTime(target.last_checked_at);
  if (target.status === 'healthy') return { label: t('ent.status.healthy'), tone: 'success', detail: t('ent.health.measured', { latency: target.measured_latency_ms ?? 0, when }) };
  if (target.status === 'degraded') return { label: t('ent.status.degraded'), tone: 'warning', detail: target.last_error ? t('ent.health.failedAt', { when, error: target.last_error }) : when };
  if (target.status === 'offline') return { label: t('ent.status.offline'), tone: 'danger', detail: target.last_error ? t('ent.health.failedAt', { when, error: target.last_error }) : when };
  return { label: t('ent.health.unprobed'), tone: 'neutral', detail: target.last_error ?? when };
}

/** Why a target cannot be deleted yet, or null when it can (mirrors the API's 409). */
export function deleteBlocker(target: RoutingTarget): string | null {
  if ((target.enabled ?? true) && target.last_checked_at && (target.status === 'healthy' || target.status === 'degraded')) return t('ent.delete.inUse');
  return null;
}

// ------------------------------------------------------------------ policy pack editor

export const POLICY_EFFECTS = ['allow', 'require_approval', 'deny'] as const;
export type PolicyEffect = typeof POLICY_EFFECTS[number];
export interface RuleDraft { action: string; effect: PolicyEffect; conditions: string }
export interface PackDraft { name: string; description: string; rules: RuleDraft[] }

export const emptyRuleDraft = (): RuleDraft => ({ action: '', effect: 'require_approval', conditions: '' });
export const emptyPackDraft = (): PackDraft => ({ name: '', description: '', rules: [emptyRuleDraft()] });

/** The organization baseline, a starting point in the editor: approval-first defaults, in the page language. */
export function baselinePackDraft(): PackDraft {
  return {
    name: t('gov.baseline.name'), description: t('gov.baseline.description'),
    rules: [
      { action: 'integration.*.write', effect: 'require_approval', conditions: '{"risk": "high"}' },
      { action: 'payment.*', effect: 'require_approval', conditions: '' },
      { action: 'memory.delete', effect: 'deny', conditions: '' },
    ],
  };
}

/** Edit an existing version: its rules as drafts, so publishing creates the next version of the same pack. */
export function packDraftFrom(pack: PolicyPack): PackDraft {
  return {
    name: pack.name, description: pack.description,
    rules: pack.rules.map((rule) => ({ action: rule.action, effect: (POLICY_EFFECTS as readonly string[]).includes(rule.effect) ? rule.effect as PolicyEffect : 'require_approval', conditions: rule.conditions && Object.keys(rule.conditions).length ? JSON.stringify(rule.conditions) : '' })),
  };
}

/** The `POST /policy-packs` body; conditions are a JSON object or empty. */
export function packBody(draft: PackDraft): Result<{ name: string; description: string; rules: PolicyRule[] }> {
  const name = draft.name.trim();
  if (!name) return { ok: false, error: t('gov.editor.err.name') };
  if (name.length > 160) return { ok: false, error: t('gov.editor.err.nameLength') };
  if (draft.description.length > 1000) return { ok: false, error: t('gov.editor.err.description') };
  if (draft.rules.length === 0) return { ok: false, error: t('gov.editor.err.noRules') };
  if (draft.rules.length > 100) return { ok: false, error: t('gov.editor.err.tooManyRules') };
  const rules: PolicyRule[] = [];
  for (const [index, rule] of draft.rules.entries()) {
    const action = rule.action.trim();
    if (!action || action.length > 160) return { ok: false, error: t('gov.editor.err.action', { row: index + 1 }) };
    let conditions: Record<string, unknown> = {};
    if (rule.conditions.trim()) {
      try {
        const parsed: unknown = JSON.parse(rule.conditions);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('not an object');
        conditions = parsed as Record<string, unknown>;
      } catch { return { ok: false, error: t('gov.editor.err.conditions', { row: index + 1 }) }; }
    }
    rules.push({ action, effect: rule.effect, conditions });
  }
  return { ok: true, value: { name, description: draft.description.trim(), rules } };
}

/** Move a rule one place up or down; out of range leaves the list as it is. */
export function moveRule<T>(rules: T[], index: number, direction: -1 | 1): T[] {
  const target = index + direction;
  if (index < 0 || index >= rules.length || target < 0 || target >= rules.length) return rules;
  const next = [...rules];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export interface PackGroup { name: string; versions: PolicyPack[]; active: PolicyPack | null }

/** One group per pack name (case-insensitive), newest version first; groups by name. */
export function packGroups(packs: PolicyPack[]): PackGroup[] {
  const byName = new Map<string, PolicyPack[]>();
  for (const pack of packs) {
    const key = pack.name.trim().toLocaleLowerCase();
    byName.set(key, [...(byName.get(key) ?? []), pack]);
  }
  return [...byName.values()]
    .map((versions) => {
      const sorted = [...versions].sort((a, b) => b.version - a.version);
      return { name: sorted[0].name, versions: sorted, active: sorted.find((pack) => pack.active) ?? null };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** Case-insensitive match on the pack's name, description or a rule's action or effect (the API's `q`). */
export function filterPacks(packs: PolicyPack[], query: string): PolicyPack[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return packs;
  return packs.filter((pack) => pack.name.toLocaleLowerCase().includes(needle) || pack.description.toLocaleLowerCase().includes(needle)
    || pack.rules.some((rule) => rule.action.toLocaleLowerCase().includes(needle) || rule.effect.toLocaleLowerCase().includes(needle)));
}

export function filterTemplates(templates: RoleTemplate[], query: string): RoleTemplate[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return templates;
  return templates.filter((item) => item.name.toLocaleLowerCase().includes(needle) || item.permissions.some((permission) => permission.toLocaleLowerCase().includes(needle)));
}

/** Case-insensitive match on an approval rule's name or action pattern. */
export function filterRules(rules: ApprovalRule[], query: string): ApprovalRule[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return rules;
  return rules.filter((rule) => rule.name.toLocaleLowerCase().includes(needle) || rule.action_pattern.toLocaleLowerCase().includes(needle));
}

export interface RuleChange { action: string; kind: 'added' | 'removed' | 'changed' | 'same'; before: PolicyRule | null; after: PolicyRule | null }

const sameRule = (a: PolicyRule, b: PolicyRule) => a.effect === b.effect && JSON.stringify(a.conditions ?? {}) === JSON.stringify(b.conditions ?? {});

/** What changed between two versions, rule by rule (rules are matched on their action). */
export function diffRules(before: PolicyRule[], after: PolicyRule[]): RuleChange[] {
  const changes: RuleChange[] = [];
  const seen = new Set<string>();
  for (const rule of after) {
    const previous = before.find((item) => item.action === rule.action && !seen.has(item.action));
    seen.add(rule.action);
    if (!previous) changes.push({ action: rule.action, kind: 'added', before: null, after: rule });
    else changes.push({ action: rule.action, kind: sameRule(previous, rule) ? 'same' : 'changed', before: previous, after: rule });
  }
  for (const rule of before) if (!after.some((item) => item.action === rule.action)) changes.push({ action: rule.action, kind: 'removed', before: rule, after: null });
  return changes;
}

/** `action: effect` plus the conditions, for a rule chip. */
export function ruleLabel(rule: PolicyRule): string {
  const conditions = rule.conditions && Object.keys(rule.conditions).length ? ` ${JSON.stringify(rule.conditions)}` : '';
  return `${rule.action}: ${rule.effect}${conditions}`;
}

// ------------------------------------------------------------------ memory governance

export interface MemoryGovernanceDraft { retentionDays: string; allowPermanent: boolean; requireProvenance: boolean; sourceTypes: string }

/** The defaults a tenant has before anyone saved the setting (the API's model defaults). */
export const DEFAULT_MEMORY_GOVERNANCE: MemoryGovernance = { default_retention_days: 365, allow_permanent_retention: false, require_provenance: true, allowed_source_types: [] };

export const memoryGovernanceDraft = (value: MemoryGovernance): MemoryGovernanceDraft => ({
  retentionDays: String(value.default_retention_days), allowPermanent: value.allow_permanent_retention, requireProvenance: value.require_provenance, sourceTypes: value.allowed_source_types.join(', '),
});

/** The `PUT /organization/memory-governance` body: 1 to 3650 days, at most 50 source types. */
export function memoryGovernanceBody(draft: MemoryGovernanceDraft): Result<MemoryGovernance> {
  const days = Number(draft.retentionDays);
  if (!Number.isInteger(days) || days < 1 || days > 3650) return { ok: false, error: t('gov.memory.err.days') };
  const sources = [...new Set(parseList(draft.sourceTypes).map((value) => value.toLowerCase()))];
  if (sources.length > 50) return { ok: false, error: t('gov.memory.err.sources') };
  if (sources.some((value) => value.length > 80)) return { ok: false, error: t('gov.memory.err.sourceLength') };
  return { ok: true, value: { default_retention_days: days, allow_permanent_retention: draft.allowPermanent, require_provenance: draft.requireProvenance, allowed_source_types: sources } };
}

// ------------------------------------------------------------------ role templates on members

/**
 * What a member's template does, in plain words: how many permissions apply and how many of the
 * template's are outside the role and therefore do not (effective = role's permissions, intersected).
 */
export function templateEffect(templatePermissions: string[], effective: string[] | null | undefined): { applies: number; outsideRole: string[] } {
  const applied = new Set(effective ?? []);
  return { applies: applied.size, outsideRole: templatePermissions.filter((permission) => !applied.has(permission)) };
}
