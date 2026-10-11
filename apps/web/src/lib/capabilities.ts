// Pure helpers for the Memory, Skills and Agents views. Every path here maps to an endpoint
// that exists in services/api: GET/POST/DELETE /api/v1/memories, GET /api/v1/retrieval/status,
// POST /api/v1/retrieval/index and GET/POST/PATCH/DELETE /api/v1/skills/installations.

export type IndexState = 'indexed' | 'failed' | 'skipped' | 'pending' | 'not_indexed';

export interface RetrievalSourceView { source_type: 'memory' | 'file'; source_id: string; name: string | null; status: string; error: string | null; chunk_count: number; embedding_model: string | null; indexed_at: string }
export interface RetrievalIndexStatus { embedding_model: string; indexed: number; failed: number; skipped: number; stale: number; chunks: number; sources: RetrievalSourceView[] }
export interface ReindexResult { indexed: number; reused: number; failed: number; removed: number; remaining: number; status: RetrievalIndexStatus }

/** Path for GET /api/v1/memories. The API's `query` filter is a text match; blank means "all". */
export function memorySearchPath(query: string, taskId?: string): string {
  const params = new URLSearchParams();
  const q = query.trim();
  if (q) params.set('query', q);
  if (taskId) params.set('task_id', taskId);
  const text = params.toString();
  return text ? `/api/v1/memories?${text}` : '/api/v1/memories';
}

/** Index state of one memory, read from the retrieval status. */
export function memoryIndexState(status: RetrievalIndexStatus | null, memoryId: string): { state: IndexState; error: string | null } {
  const source = status?.sources.find((s) => s.source_type === 'memory' && s.source_id === memoryId);
  if (!source) return { state: 'not_indexed', error: null };
  const known: IndexState[] = ['indexed', 'failed', 'skipped', 'pending'];
  return { state: known.includes(source.status as IndexState) ? (source.status as IndexState) : 'pending', error: source.error };
}

export function indexSummary(status: RetrievalIndexStatus | null): string {
  if (!status) return 'Retrieval index status unavailable.';
  const parts = [`${status.indexed} indexed`, `${status.chunks} chunks`];
  if (status.failed) parts.push(`${status.failed} failed`);
  if (status.stale) parts.push(`${status.stale} stale`);
  return `${parts.join(' · ')} (${status.embedding_model})`;
}

export interface SkillLike { skill_id: string; version: string; name: string; description: string; required_tools: string[]; risk_level: string }
export interface InstallLike { id: string; skill_id: string; version: string; approved_tools: string[]; enabled: boolean; installed_at: string }

export interface InstallReview { skillId: string; version: string; name: string; risk: string; highRisk: boolean; tools: string[]; summary: string }

/** What a user approves when installing: the version's required tools, granted verbatim. */
export function installReview(skill: SkillLike): InstallReview {
  const tools = [...new Set(skill.required_tools)].sort();
  const risk = skill.risk_level || 'unknown';
  const highRisk = ['high', 'critical'].includes(risk.toLowerCase());
  const summary = tools.length
    ? `Installing grants ${tools.length} tool${tools.length === 1 ? '' : 's'} to agents in this workspace.`
    : 'This skill requests no tools.';
  return { skillId: skill.skill_id, version: skill.version, name: skill.name, risk, highRisk, tools, summary };
}

export type SkillInstallState = 'available' | 'installed' | 'disabled' | 'other_version';

export function skillInstallState(skill: SkillLike, installs: InstallLike[]): SkillInstallState {
  const install = installs.find((i) => i.skill_id === skill.skill_id);
  if (!install) return 'available';
  if (install.version !== skill.version) return 'other_version';
  return install.enabled ? 'installed' : 'disabled';
}

export interface AgentRow { installId: string; skillId: string; name: string; description: string; version: string; enabled: boolean; tools: string[]; installedAt: string; detailsAnchor: string }

export function skillAnchor(skillId: string): string {
  return `skill-${skillId.replace(/[^A-Za-z0-9_-]/g, '-')}`;
}

/** Installed skills as agent rows, joined to their published version; enabled first, then by name. */
export function agentRows(installs: InstallLike[], versions: SkillLike[]): AgentRow[] {
  return installs
    .map((i) => {
      const v = versions.find((x) => x.skill_id === i.skill_id && x.version === i.version) ?? versions.find((x) => x.skill_id === i.skill_id);
      return { installId: i.id, skillId: i.skill_id, name: v?.name ?? i.skill_id, description: v?.description ?? '', version: i.version, enabled: i.enabled, tools: [...i.approved_tools].sort(), installedAt: i.installed_at, detailsAnchor: skillAnchor(i.skill_id) };
    })
    .sort((a, b) => Number(b.enabled) - Number(a.enabled) || a.name.localeCompare(b.name));
}

/** Installing, enabling/disabling and uninstalling skills need `policy:manage`: active workspace owners only. */
export function isActiveOwner(membership: { role: string; active: boolean } | null | undefined): boolean {
  return membership?.role === 'owner' && membership.active === true;
}
