// Pure helpers for the Integrations, Skills and marketplace views. Every shape here mirrors an
// API response in services/api (anum_api/integrations.py, skills_api.py, phase5.py);
// docs/tools-and-integrations.md, docs/skills.md, docs/governance-and-scale.md.

import { joinList, t } from './i18n.ts';
import { installable, type SkillVerification } from './skillSigning.ts';

export type Result<T> = { ok: true; value: T } | { ok: false; error: string };

// --------------------------------------------------------------------------- integrations

export interface WorkspaceCredential { integration_id: string; configured: boolean; hint: string | null; updated_by: string | null; updated_at: string | null }

export interface IntegrationState {
  id: string;
  name: string;
  kind: string;
  status: 'connected' | 'degraded' | 'configured' | 'disabled';
  endpoint: string;
  latency_ms: number | null;
  detail: string;
  credentials: { configured: boolean; source: string; scopes: string[]; expires_at: string | null };
  enabled?: boolean;
  available?: boolean;
  remediation?: string | null;
  environment_variable?: string | null;
  checked_at?: string | null;
  last_error?: string | null;
  last_error_at?: string | null;
  accepts_workspace_credential?: boolean;
  workspace_credential?: WorkspaceCredential | null;
}

/** Whether a workspace switch means anything: the operator configured it for this deployment. */
export function canSwitchIntegration(item: IntegrationState): boolean {
  return item.available !== false;
}

/** The "last error" line, shown only when it is not the failure the card already reports. */
export function lastErrorNote(item: IntegrationState, format: (iso: string) => string): string | null {
  if (!item.last_error || !item.last_error_at) return null;
  if (item.status === 'degraded' && item.detail === item.last_error) return t('integrations.lastErrorFailing', { time: format(item.last_error_at) });
  return t('integrations.lastError', { time: format(item.last_error_at), error: item.last_error });
}

/** Where a credential comes from, in words. */
export function credentialSource(item: IntegrationState): string {
  const stored = item.workspace_credential;
  if (stored?.configured) return stored.updated_by ? t('integrations.credWorkspace', { hint: stored.hint ?? '', user: stored.updated_by }) : t('integrations.credWorkspaceNoUser', { hint: stored.hint ?? '' });
  if (item.credentials.configured && item.credentials.source === 'environment') return t('integrations.credOperator');
  return t('integrations.credNone');
}

/** Mirrors the API: 12 to 4096 printable ASCII characters without spaces (a bearer token). */
export function credentialBody(secret: string): Result<{ secret: string }> {
  if (!secret) return { ok: false, error: t('integrations.tokenEmpty') };
  if (/\s/.test(secret)) return { ok: false, error: t('integrations.tokenSpaces') };
  if (!/^[\x21-\x7e]+$/.test(secret)) return { ok: false, error: t('integrations.tokenAscii') };
  if (secret.length < 12) return { ok: false, error: t('integrations.tokenShort') };
  if (secret.length > 4096) return { ok: false, error: t('integrations.tokenLong') };
  return { ok: true, value: { secret } };
}

// --------------------------------------------------------------------------- skills

export interface SkillVersionLike { id: string; skill_id: string; version: string; name: string; description: string; required_tools: string[]; risk_level: string; created_at: string; created_by?: string; verification?: SkillVerification | null; signed_by?: string | null; signed_at?: string | null }
export interface SkillInstallLike { id: string; skill_id: string; version: string; approved_tools: string[]; enabled: boolean; installed_at: string; installed_by?: string; verification?: SkillVerification | null }

export type SkillState = 'available' | 'installed' | 'disabled';

export interface SkillGroup {
  skillId: string;
  name: string;
  description: string;
  latest: SkillVersionLike;
  /** Newest first. */
  versions: SkillVersionLike[];
  install: SkillInstallLike | null;
  state: SkillState;
  /** A newer version than the installed one exists. */
  upgrade: SkillVersionLike | null;
}

export function compareVersions(a: string, b: string): number {
  const pa = a.split('.').map(Number), pb = b.split('.').map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i += 1) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d) return d;
  }
  return 0;
}

/** One entry per skill: its versions, this workspace's installation and what an owner can do. */
export function groupSkills(versions: SkillVersionLike[], installs: SkillInstallLike[]): SkillGroup[] {
  const bySkill = new Map<string, SkillVersionLike[]>();
  for (const v of versions) bySkill.set(v.skill_id, [...(bySkill.get(v.skill_id) ?? []), v]);
  return [...bySkill.entries()].map(([skillId, list]) => {
    const sorted = [...list].sort((a, b) => compareVersions(b.version, a.version));
    const install = installs.find((i) => i.skill_id === skillId) ?? null;
    const shown = (install && sorted.find((v) => v.version === install.version)) || sorted[0];
    const latest = sorted[0];
    return {
      skillId, name: shown.name, description: shown.description, latest, versions: sorted, install,
      state: !install ? 'available' : install.enabled ? 'installed' : 'disabled',
      upgrade: install && compareVersions(latest.version, install.version) > 0 ? latest : null,
    } satisfies SkillGroup;
  }).sort((a, b) => a.name.localeCompare(b.name));
}

export type SkillFilter = 'all' | 'installed' | 'available' | 'builtin';

export function filterSkills(groups: SkillGroup[], query: string, filter: SkillFilter): SkillGroup[] {
  const q = query.trim().toLowerCase();
  if (filter === 'builtin') return [];
  return groups.filter((g) => {
    if (filter === 'installed' && !g.install) return false;
    if (filter === 'available' && g.install) return false;
    if (!q) return true;
    return [g.skillId, g.name, g.description, ...g.latest.required_tools].some((x) => x.toLowerCase().includes(q));
  });
}

/** A skill built into the runtime (from the agent catalog): nothing to install, always available. */
export interface BuiltinSkillLike { id: string; name: string; description: string; source: string; required_tools: string[] }

/** Built-in skills for the Skills page: shown under All and Built in, matching the same search. */
export function filterBuiltins<T extends BuiltinSkillLike>(skills: T[], query: string, filter: SkillFilter): T[] {
  if (filter !== 'all' && filter !== 'builtin') return [];
  const q = query.trim().toLowerCase();
  return skills.filter((s) => s.source === 'builtin')
    .filter((s) => !q || [s.id, s.name, s.description, ...s.required_tools].some((x) => x.toLowerCase().includes(q)))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** "Loaded by A, B and 2 more": the first names and how many are left. */
export function usageNames(names: string[], max = 2): { shown: string[]; more: number } {
  return { shown: names.slice(0, max), more: Math.max(0, names.length - max) };
}

/** The first `pageSize * pages` items and whether more remain ("Show more" paging). */
export function pageOf<T>(items: T[], pages: number, pageSize: number): { shown: T[]; remaining: number } {
  const shown = items.slice(0, Math.max(1, pages) * pageSize);
  return { shown, remaining: items.length - shown.length };
}

/** Versions an organization administrator may roll the installation back to: older than installed and signed. */
export function rollbackTargets(versions: { version: string; verification?: SkillVerification | null }[], installed: string | null | undefined): string[] {
  if (!installed) return [];
  return versions.filter((v) => installable(v)).map((v) => v.version).filter((v) => compareVersions(v, installed) < 0).sort((a, b) => compareVersions(b, a));
}

export interface SkillToolView { name: string; registered: boolean; description: string | null; risk_level: string | null; approval: string; approval_text: string; target: string | null; granted: boolean }
export interface SkillHistoryEntry { id: string; action: string; actor: string; outcome: string; created_at: string; metadata: Record<string, unknown> }
export interface SkillDetail {
  skill_id: string; name: string; description: string; latest_version: string;
  versions: (SkillVersionLike & { instructions?: string; created_by: string })[];
  installation: SkillInstallLike | null; tools_version: string; tools: SkillToolView[];
  history: SkillHistoryEntry[]; can_manage: boolean; can_rollback: boolean;
}

const HISTORY_KEYS = {
  'skill.published': 'skills.history.published', 'skill.installed': 'skills.history.installed', 'skill.enabled': 'skills.history.enabled',
  'skill.disabled': 'skills.history.disabled', 'skill.uninstalled': 'skills.history.uninstalled',
  'skill.signed': 'signing.history.signed', 'skill.install_refused': 'signing.history.installRefused',
  'skill.signature_refused': 'signing.history.signatureRefused', 'skill.installation_flagged': 'signing.history.flagged',
} as const;

/** A history row in words: "Rolled back 2.0.0 → 1.0.0", "Installed 1.2.0", "Published 1.0.0". */
export function historyLabel(entry: SkillHistoryEntry): string {
  const m = entry.metadata ?? {};
  if (entry.action === 'skill.rollback_refused') return t('signing.history.rollbackRefused', { to: String(m.to_version ?? '?') });
  if (entry.action === 'skill.rolled_back') return t('skills.history.rolledBack', { from: String(m.from_version ?? '?'), to: String(m.to_version ?? '?') });
  if (entry.action === 'skill.installed' && m.previous_version) return t('skills.history.installedFrom', { version: String(m.version), previous: String(m.previous_version) });
  const key = HISTORY_KEYS[entry.action as keyof typeof HISTORY_KEYS];
  return key ? t(key, { version: m.version ? String(m.version) : '' }).trim() : entry.action;
}

/** Badge tone for a tool's approval need. */
export function approvalTone(approval: string): 'dangerTone' | 'warning' | 'success' | 'neutral' {
  if (approval === 'always' || approval === 'blocked') return 'dangerTone';
  if (approval === 'policy') return 'warning';
  if (approval === 'rule') return 'success';
  return 'neutral';
}

export interface PublishDraft { skillId: string; version: string; name: string; description: string; instructions: string; tools: string; risk: string }
export const emptyPublishDraft = (): PublishDraft => ({ skillId: '', version: '1.0.0', name: '', description: '', instructions: '', tools: '', risk: 'low' });

/** The `POST /skills/versions` body, validated as the API does. */
export function publishBody(d: PublishDraft): Result<{ skill_id: string; version: string; name: string; description: string; instructions: string; required_tools: string[]; risk_level: string }> {
  const skillId = d.skillId.trim();
  if (!/^[a-z0-9][a-z0-9_.-]{2,127}$/.test(skillId)) return { ok: false, error: t('skills.err.id') };
  const version = d.version.trim();
  if (!/^\d+\.\d+\.\d+$/.test(version)) return { ok: false, error: t('skills.err.version') };
  const name = d.name.trim(), description = d.description.trim(), instructions = d.instructions.trim();
  if (!name || name.length > 160) return { ok: false, error: t('skills.err.name') };
  if (!description || description.length > 2000) return { ok: false, error: t('skills.err.description') };
  if (!instructions || instructions.length > 50000) return { ok: false, error: t('skills.err.instructions') };
  const tools = [...new Set(d.tools.split(/[,\n]/).map((x) => x.trim()).filter(Boolean))];
  if (tools.length > 100) return { ok: false, error: t('skills.err.tools') };
  if (!['low', 'medium', 'high', 'blocked'].includes(d.risk)) return { ok: false, error: t('skills.err.risk') };
  return { ok: true, value: { skill_id: skillId, version, name, description, instructions, required_tools: tools, risk_level: d.risk } };
}

// --------------------------------------------------------------------------- marketplace

export interface MarketPackage { id: string; name: string; kind: 'skill' | 'integration'; version: string; publisher: string; verified: boolean; permissions: string[]; regions: string[] }
export interface PackageInstall { package_id: string; version: string; enabled: boolean; installed_by: string; installed_at: string }

export interface MarketFilter { query: string; kind: 'all' | 'skill' | 'integration'; verifiedOnly: boolean; installedOnly: boolean }
export const emptyMarketFilter = (): MarketFilter => ({ query: '', kind: 'all', verifiedOnly: false, installedOnly: false });

export function filterPackages(items: MarketPackage[], installs: PackageInstall[], f: MarketFilter): MarketPackage[] {
  const q = f.query.trim().toLowerCase();
  const installed = new Set(installs.map((i) => i.package_id));
  return items
    .filter((p) => (f.kind === 'all' || p.kind === f.kind) && (!f.verifiedOnly || p.verified) && (!f.installedOnly || installed.has(p.id)))
    .filter((p) => !q || [p.id, p.name, p.publisher, ...p.permissions].some((x) => x.toLowerCase().includes(q)))
    .sort((a, b) => Number(installed.has(b.id)) - Number(installed.has(a.id)) || a.name.localeCompare(b.name));
}

/** What an owner reads before installing: who published it, whether it is verified, what it may do. */
export function installSummary(p: MarketPackage): { lines: string[]; warning: string | null } {
  const lines = [
    t(p.kind === 'skill' ? 'market.summary.skill' : 'market.summary.integration', { name: p.name, version: p.version, publisher: p.publisher }),
    t('market.summary.permissions', { count: p.permissions.length }),
    p.regions.length ? t('market.summary.regions', { regions: joinList(p.regions) }) : t('market.summary.noRegions'),
  ];
  const warning = p.verified ? null : t('market.summary.unverified');
  return { lines, warning };
}

export interface PackageDraft { id: string; name: string; kind: 'skill' | 'integration'; version: string; publisher: string; verified: boolean; permissions: string; regions: string }
export const emptyPackageDraft = (): PackageDraft => ({ id: '', name: '', kind: 'integration', version: '1.0.0', publisher: '', verified: false, permissions: '', regions: '' });
export const packageDraft = (p: MarketPackage): PackageDraft => ({ ...p, permissions: p.permissions.join(', '), regions: p.regions.join(', ') });

const list = (value: string) => [...new Set(value.split(/[,\n]/).map((x) => x.trim()).filter(Boolean))];

/** The `PUT /marketplace/packages/{id}` body, validated as the API does. */
export function packageBody(d: PackageDraft): Result<MarketPackage> {
  const id = d.id.trim();
  if (!/^[a-z0-9][a-z0-9_.-]{1,79}$/.test(id)) return { ok: false, error: t('market.err.id') };
  if (!/^\d+\.\d+\.\d+$/.test(d.version.trim())) return { ok: false, error: t('market.err.version') };
  const name = d.name.trim(), publisher = d.publisher.trim();
  if (!name || name.length > 160) return { ok: false, error: t('market.err.name') };
  if (!publisher || publisher.length > 160) return { ok: false, error: t('market.err.publisher') };
  const permissions = list(d.permissions), regions = list(d.regions);
  if (permissions.length > 50) return { ok: false, error: t('market.err.permissions') };
  if (regions.length > 20) return { ok: false, error: t('market.err.regions') };
  return { ok: true, value: { id, name, kind: d.kind, version: d.version.trim(), publisher, verified: d.verified, permissions, regions } };
}

/** The ANUM Store's band: skills in the catalogue (plus built-in ones), installed, switched off, upgradable and not installed. */
export function skillCounts(groups: readonly Pick<SkillGroup, 'state' | 'upgrade'>[], builtins: number) {
  return {
    total: groups.length + builtins, builtin: builtins,
    installed: groups.filter((g) => g.state === 'installed').length, disabled: groups.filter((g) => g.state === 'disabled').length,
    available: groups.filter((g) => g.state === 'available').length, upgrades: groups.filter((g) => g.upgrade !== null).length,
  };
}
