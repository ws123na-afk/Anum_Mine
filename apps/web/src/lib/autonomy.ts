// Autonomy policy (docs/autonomy.md): wire mapping, form drafts and the words the Activity and
// Autonomy views show. Pure, so `pnpm --filter @anum/web test:unit` exercises it without a DOM.
import type {
  ActionKind, AutonomousAction, AutonomousActionPage, AutonomyLevel, AutonomyOverview, AutonomyProposal, AutonomyRule,
  OrganizationGuardrails, QuietHours, ToolCategory, WorkspaceAutonomySettings,
} from '@anum/contracts';
import { CATALOGS, formatDateTime, t, type MessageKey } from './i18n.ts';

export const CATEGORIES: readonly ToolCategory[] = ['email', 'whatsapp', 'commerce', 'developer', 'files', 'tasks', 'general'];
export const LEVELS: readonly AutonomyLevel[] = ['ask_always', 'act_and_notify', 'act_silently'];
const RANK: Record<AutonomyLevel, number> = { ask_always: 0, act_and_notify: 1, act_silently: 2 };

// ---- Wire shapes (snake_case, as the API sends them) ----

interface ApiQuietHours { start: string; end: string; timezone: string }
export interface ApiGuardrails {
  spending_limit: number | null; max_auto_amount: number | null; max_auto_recipients: number | null; max_auto_price_change_percent: number | null;
  category_ceilings: Partial<Record<ToolCategory, AutonomyLevel>>; daily_caps: Record<string, number>; quiet_hours: ApiQuietHours | null;
  blocked_recipients: string[]; blocked_domains: string[]; updated_by?: string | null; updated_at?: string | null;
}
export interface ApiSettings {
  category_levels: Partial<Record<ToolCategory, AutonomyLevel>>; amount_threshold: number; recipients_threshold: number;
  price_change_percent_threshold: number; daily_caps: Record<string, number>; quiet_hours: ApiQuietHours | null;
  blocked_recipients: string[]; blocked_domains: string[]; approved_templates: string[]; proposal_after: number;
  updated_by?: string | null; updated_at?: string | null;
}
export interface ApiOverview {
  settings: ApiSettings; guardrails: ApiGuardrails;
  categories: { category: ToolCategory; level: AutonomyLevel; ceiling: AutonomyLevel | null; effective: AutonomyLevel }[];
  recommendations: { kind: ActionKind; recommended: AutonomyLevel; protected: boolean; note: string }[];
  pending_proposals: number; can_manage_workspace: boolean; can_manage_guardrails: boolean; can_tighten: boolean;
}
export interface ApiRule { id: string; pattern_key: string; tool: string; template: string | null; category: ToolCategory; action_kind: ActionKind; level: AutonomyLevel; source: AutonomyRule['source']; created_by: string; created_at: string }
export interface ApiProposal { id: string; pattern_key: string; tool: string; template: string | null; category: ToolCategory; action_kind: ActionKind; proposed_level: AutonomyLevel; approvals: number; status: AutonomyProposal['status']; created_at: string }
export interface ApiAction {
  id: string; task_id: string; tool: string; category: ToolCategory; action_kind: ActionKind; level: AutonomyLevel; pattern_key: string;
  template: string | null; summary: string; target: string | null; status: AutonomousAction['status']; requested_by: string; created_at: string;
  reversible: boolean; undo_available: boolean; undo_expires_at: string | null; undo_token: string | null; can_undo: boolean;
  undone_by: string | null; undone_at: string | null; undo_error: string | null; pattern_level: AutonomyLevel | null;
}
export interface ApiActionPage { items: ApiAction[]; next_cursor: string | null }

const quiet = (value: ApiQuietHours | null): QuietHours | null => value ? { start: value.start, end: value.end, timezone: value.timezone } : null;

export function mapGuardrails(v: ApiGuardrails): OrganizationGuardrails {
  return {
    spendingLimit: v.spending_limit ?? null, maxAutoAmount: v.max_auto_amount ?? null, maxAutoRecipients: v.max_auto_recipients ?? null,
    maxAutoPriceChangePercent: v.max_auto_price_change_percent ?? null, categoryCeilings: { ...(v.category_ceilings ?? {}) },
    dailyCaps: { ...(v.daily_caps ?? {}) }, quietHours: quiet(v.quiet_hours ?? null), blockedRecipients: [...(v.blocked_recipients ?? [])],
    blockedDomains: [...(v.blocked_domains ?? [])], updatedBy: v.updated_by ?? null, updatedAt: v.updated_at ?? null,
  };
}

export function mapSettings(v: ApiSettings): WorkspaceAutonomySettings {
  return {
    categoryLevels: { ...(v.category_levels ?? {}) }, amountThreshold: v.amount_threshold, recipientsThreshold: v.recipients_threshold,
    priceChangePercentThreshold: v.price_change_percent_threshold, dailyCaps: { ...(v.daily_caps ?? {}) }, quietHours: quiet(v.quiet_hours ?? null),
    blockedRecipients: [...(v.blocked_recipients ?? [])], blockedDomains: [...(v.blocked_domains ?? [])],
    approvedTemplates: [...(v.approved_templates ?? [])], proposalAfter: v.proposal_after, updatedBy: v.updated_by ?? null, updatedAt: v.updated_at ?? null,
  };
}

export function mapOverview(v: ApiOverview): AutonomyOverview {
  return {
    settings: mapSettings(v.settings), guardrails: mapGuardrails(v.guardrails), categories: v.categories.map((c) => ({ ...c })),
    recommendations: v.recommendations.map((r) => ({ ...r })), pendingProposals: v.pending_proposals,
    canManageWorkspace: v.can_manage_workspace, canManageGuardrails: v.can_manage_guardrails, canTighten: v.can_tighten,
  };
}

export const mapRule = (v: ApiRule): AutonomyRule => ({
  id: v.id, patternKey: v.pattern_key, tool: v.tool, template: v.template ?? null, category: v.category, actionKind: v.action_kind,
  level: v.level, source: v.source, createdBy: v.created_by, createdAt: v.created_at,
});

export const mapProposal = (v: ApiProposal): AutonomyProposal => ({
  id: v.id, patternKey: v.pattern_key, tool: v.tool, template: v.template ?? null, category: v.category, actionKind: v.action_kind,
  proposedLevel: v.proposed_level, approvals: v.approvals, status: v.status, createdAt: v.created_at,
});

export const mapAction = (v: ApiAction): AutonomousAction => ({
  id: v.id, taskId: v.task_id, tool: v.tool, category: v.category, actionKind: v.action_kind, level: v.level, patternKey: v.pattern_key,
  template: v.template ?? null, summary: v.summary, target: v.target ?? null, status: v.status, requestedBy: v.requested_by, createdAt: v.created_at,
  reversible: v.reversible, undoAvailable: v.undo_available, undoExpiresAt: v.undo_expires_at ?? null, undoToken: v.undo_token ?? null,
  canUndo: v.can_undo, undoneBy: v.undone_by ?? null, undoneAt: v.undone_at ?? null, undoError: v.undo_error ?? null, patternLevel: v.pattern_level ?? null,
});

export const mapActionPage = (v: ApiActionPage): AutonomousActionPage => ({ items: v.items.map(mapAction), nextCursor: v.next_cursor ?? null });

// ---- Labels ----

function label(prefix: string, value: string): string {
  const key = `${prefix}.${value}`;
  return key in CATALOGS.en ? t(key as MessageKey) : value.replaceAll('_', ' ');
}
export const levelLabel = (value: AutonomyLevel) => label('autonomy.level', value);
export const categoryLabel = (value: ToolCategory) => label('autonomy.category', value);
export const kindLabel = (value: ActionKind) => label('autonomy.kind', value);

/** Levels a select offers under an organization ceiling: higher ones are shown disabled. */
export function levelChoices(ceiling: AutonomyLevel | null): { level: AutonomyLevel; allowed: boolean }[] {
  return LEVELS.map((level) => ({ level, allowed: ceiling === null || RANK[level] <= RANK[ceiling] }));
}

/** "Undo until 14:05", "Undone by sara", or null when there is nothing to say. */
export function undoStatus(action: AutonomousAction, now: number = Date.now()): { text: string; tone: 'success' | 'warning' | 'neutral' } | null {
  if (action.status === 'undone') return { text: t('activity.undoneBy', { who: action.undoneBy ?? '?' }), tone: 'success' };
  if (!action.reversible) return null;
  if (action.undoExpiresAt && new Date(action.undoExpiresAt).getTime() > now) {
    return { text: t('activity.undoUntil', { time: formatDateTime(action.undoExpiresAt, { hour: '2-digit', minute: '2-digit' }) }), tone: 'warning' };
  }
  return { text: t('activity.undoClosed'), tone: 'neutral' };
}

export const canUndoNow = (action: AutonomousAction, now: number = Date.now()) =>
  action.status === 'done' && action.canUndo && Boolean(action.undoToken) && Boolean(action.undoExpiresAt) && new Date(action.undoExpiresAt as string).getTime() > now;

/** The pattern in words: "whatsapp.send_template with template order_status". */
export function patternLabel(tool: string, template: string | null): string {
  return template ? t('autonomy.patternWithTemplate', { tool, template }) : tool;
}

export function proposalSentence(p: AutonomyProposal): string {
  return t('activity.proposal', { pattern: patternLabel(p.tool, p.template), level: levelLabel(p.proposedLevel), count: p.approvals });
}

// ---- Feed query ----

export interface FeedFilters { category: ToolCategory | ''; level: AutonomyLevel | ''; status: 'done' | 'undone' | ''; q: string }
export const EMPTY_FILTERS: FeedFilters = { category: '', level: '', status: '', q: '' };
export const filtersActive = (f: FeedFilters) => Boolean(f.category || f.level || f.status || f.q.trim());

export function feedQuery(filters: FeedFilters, cursor: string | null = null, limit = 25): string {
  const params = new URLSearchParams();
  if (filters.category) params.set('category', filters.category);
  if (filters.level) params.set('level', filters.level);
  if (filters.status) params.set('status', filters.status);
  if (filters.q.trim()) params.set('q', filters.q.trim().slice(0, 200));
  params.set('limit', String(limit));
  if (cursor) params.set('cursor', cursor);
  return params.toString();
}

// ---- Notifications from the event stream ----

export interface ActionNotice { actionId: string; summary: string; tool: string; undoUntil: string | null }

/** An `autonomy.action_taken` event as a notification, or null for anything else. */
export function noticeFromEvent(event: { type: string; payload?: Record<string, unknown> }): ActionNotice | null {
  if (event.type !== 'autonomy.action_taken' || !event.payload) return null;
  const p = event.payload;
  const id = typeof p.action_id === 'string' ? p.action_id : null;
  if (!id) return null;
  return {
    actionId: id,
    summary: typeof p.summary === 'string' ? p.summary : '',
    tool: typeof p.tool === 'string' ? p.tool : '',
    undoUntil: typeof p.undo_until === 'string' ? p.undo_until : null,
  };
}

// ---- Settings form drafts ----

export interface QuietDraft { enabled: boolean; start: string; end: string; timezone: string }
export interface SettingsDraft {
  categoryLevels: Record<ToolCategory, AutonomyLevel>;
  amountThreshold: string; recipientsThreshold: string; priceChangePercentThreshold: string; proposalAfter: string;
  dailyCaps: string; quiet: QuietDraft; blockedRecipients: string; blockedDomains: string; approvedTemplates: string;
}
export interface GuardrailsDraft {
  categoryCeilings: Record<ToolCategory, AutonomyLevel | ''>;
  spendingLimit: string; maxAutoAmount: string; maxAutoRecipients: string; maxAutoPriceChangePercent: string;
  dailyCaps: string; quiet: QuietDraft; blockedRecipients: string; blockedDomains: string;
}

const lines = (values: string[]) => values.join('\n');
const capsText = (caps: Record<string, number>) => Object.entries(caps).map(([key, value]) => `${key} = ${value}`).join('\n');
const quietDraft = (q: QuietHours | null): QuietDraft => q ? { enabled: true, ...q } : { enabled: false, start: '22:00', end: '07:00', timezone: 'Asia/Riyadh' };
const num = (value: number | null) => value === null || value === undefined ? '' : String(value);

export function settingsDraft(s: WorkspaceAutonomySettings): SettingsDraft {
  const levels = Object.fromEntries(CATEGORIES.map((c) => [c, s.categoryLevels[c] ?? 'act_silently'])) as Record<ToolCategory, AutonomyLevel>;
  return {
    categoryLevels: levels, amountThreshold: num(s.amountThreshold), recipientsThreshold: num(s.recipientsThreshold),
    priceChangePercentThreshold: num(s.priceChangePercentThreshold), proposalAfter: num(s.proposalAfter), dailyCaps: capsText(s.dailyCaps),
    quiet: quietDraft(s.quietHours), blockedRecipients: lines(s.blockedRecipients), blockedDomains: lines(s.blockedDomains),
    approvedTemplates: lines(s.approvedTemplates),
  };
}

export function guardrailsDraft(g: OrganizationGuardrails): GuardrailsDraft {
  const ceilings = Object.fromEntries(CATEGORIES.map((c) => [c, g.categoryCeilings[c] ?? ''])) as Record<ToolCategory, AutonomyLevel | ''>;
  return {
    categoryCeilings: ceilings, spendingLimit: num(g.spendingLimit), maxAutoAmount: num(g.maxAutoAmount), maxAutoRecipients: num(g.maxAutoRecipients),
    maxAutoPriceChangePercent: num(g.maxAutoPriceChangePercent), dailyCaps: capsText(g.dailyCaps), quiet: quietDraft(g.quietHours),
    blockedRecipients: lines(g.blockedRecipients), blockedDomains: lines(g.blockedDomains),
  };
}

export function splitLines(text: string): string[] {
  return [...new Set(text.split(/[\n,]+/).map((line) => line.trim()).filter(Boolean))];
}

type Parsed<T> = { ok: true; value: T } | { ok: false; error: string };

/** "email = 20" lines (category or tool name, then a whole number). */
export function parseCaps(text: string): Parsed<Record<string, number>> {
  const caps: Record<string, number> = {};
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (!line) continue;
    const match = /^([A-Za-z0-9_.-]{1,120})\s*[=:]\s*(\d{1,6})$/.exec(line);
    if (!match) return { ok: false, error: t('autonomy.error.cap', { line }) };
    caps[match[1].toLowerCase()] = Number(match[2]);
  }
  return { ok: true, value: caps };
}

function parseNumber(text: string, field: string, { optional = false, integer = false, max = Infinity } = {}): Parsed<number | null> {
  const value = text.trim();
  if (!value) return optional ? { ok: true, value: null } : { ok: false, error: t('autonomy.error.required', { field }) };
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0 || n > max || (integer && !Number.isInteger(n))) return { ok: false, error: t('autonomy.error.number', { field }) };
  return { ok: true, value: n };
}

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
function parseQuiet(q: QuietDraft): Parsed<ApiQuietHours | null> {
  if (!q.enabled) return { ok: true, value: null };
  if (!HHMM.test(q.start) || !HHMM.test(q.end)) return { ok: false, error: t('autonomy.error.time') };
  if (!q.timezone.trim()) return { ok: false, error: t('autonomy.error.timezone') };
  return { ok: true, value: { start: q.start, end: q.end, timezone: q.timezone.trim() } };
}

/** The owner's `PUT /autonomy/settings` body, or the first problem in words. */
export function settingsBody(d: SettingsDraft): Parsed<Omit<ApiSettings, 'updated_by' | 'updated_at'>> {
  const amount = parseNumber(d.amountThreshold, t('autonomy.field.amountThreshold'));
  const recipients = parseNumber(d.recipientsThreshold, t('autonomy.field.recipientsThreshold'), { integer: true, max: 10000 });
  const price = parseNumber(d.priceChangePercentThreshold, t('autonomy.field.priceThreshold'), { max: 1000 });
  const after = parseNumber(d.proposalAfter, t('autonomy.field.proposalAfter'), { integer: true, max: 50 });
  const caps = parseCaps(d.dailyCaps);
  const quietHours = parseQuiet(d.quiet);
  for (const part of [amount, recipients, price, after, caps, quietHours]) if (!part.ok) return part;
  if ((after as { value: number }).value < 2) return { ok: false, error: t('autonomy.error.number', { field: t('autonomy.field.proposalAfter') }) };
  return {
    ok: true,
    value: {
      category_levels: { ...d.categoryLevels },
      amount_threshold: (amount as { value: number }).value, recipients_threshold: (recipients as { value: number }).value,
      price_change_percent_threshold: (price as { value: number }).value, proposal_after: (after as { value: number }).value,
      daily_caps: (caps as { value: Record<string, number> }).value, quiet_hours: (quietHours as { value: ApiQuietHours | null }).value,
      blocked_recipients: splitLines(d.blockedRecipients), blocked_domains: splitLines(d.blockedDomains), approved_templates: splitLines(d.approvedTemplates),
    },
  };
}

/** The organization administrator's `PUT /autonomy/guardrails` body, or the first problem. */
export function guardrailsBody(d: GuardrailsDraft): Parsed<Omit<ApiGuardrails, 'updated_by' | 'updated_at'>> {
  const spending = parseNumber(d.spendingLimit, t('autonomy.field.spendingLimit'), { optional: true });
  const amount = parseNumber(d.maxAutoAmount, t('autonomy.field.maxAutoAmount'), { optional: true });
  const recipients = parseNumber(d.maxAutoRecipients, t('autonomy.field.maxAutoRecipients'), { optional: true, integer: true, max: 10000 });
  const price = parseNumber(d.maxAutoPriceChangePercent, t('autonomy.field.maxAutoPrice'), { optional: true, max: 1000 });
  const caps = parseCaps(d.dailyCaps);
  const quietHours = parseQuiet(d.quiet);
  for (const part of [spending, amount, recipients, price, caps, quietHours]) if (!part.ok) return part;
  const ceilings: Partial<Record<ToolCategory, AutonomyLevel>> = {};
  for (const c of CATEGORIES) if (d.categoryCeilings[c]) ceilings[c] = d.categoryCeilings[c] as AutonomyLevel;
  const value = (p: Parsed<number | null>) => (p as { value: number | null }).value;
  return {
    ok: true,
    value: {
      spending_limit: value(spending), max_auto_amount: value(amount), max_auto_recipients: value(recipients), max_auto_price_change_percent: value(price),
      category_ceilings: ceilings, daily_caps: (caps as { value: Record<string, number> }).value, quiet_hours: (quietHours as { value: ApiQuietHours | null }).value,
      blocked_recipients: splitLines(d.blockedRecipients), blocked_domains: splitLines(d.blockedDomains),
    },
  };
}

/** Workspace values above the organization's floors, in words (the API refuses them with 422 too). */
export function floorWarnings(d: SettingsDraft, g: OrganizationGuardrails): string[] {
  const out: string[] = [];
  const over = (text: string, max: number | null, field: string) => {
    const n = Number(text);
    if (max !== null && text.trim() && Number.isFinite(n) && n > max) out.push(t('autonomy.floor', { field, max }));
  };
  over(d.amountThreshold, g.maxAutoAmount, t('autonomy.field.amountThreshold'));
  over(d.recipientsThreshold, g.maxAutoRecipients, t('autonomy.field.recipientsThreshold'));
  over(d.priceChangePercentThreshold, g.maxAutoPriceChangePercent, t('autonomy.field.priceThreshold'));
  for (const c of CATEGORIES) {
    const ceiling = g.categoryCeilings[c];
    if (ceiling && RANK[d.categoryLevels[c]] > RANK[ceiling]) out.push(t('autonomy.floorLevel', { category: categoryLabel(c), level: levelLabel(ceiling) }));
  }
  return out;
}
