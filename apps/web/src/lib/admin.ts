// Members, invitations and model budgets: wire mapping and display logic.
// docs/identity.md (Invitations and Membership Management), docs/model-gateway.md (Monthly budgets).
// Pure functions, so `pnpm --filter @anum/web test:unit` can exercise them without a DOM.
import type {
  AcceptedInvitation,
  BudgetKind,
  CreatedInvitation,
  InvitationEmail,
  InvitationRequest,
  ModelBudget,
  ModelBudgetLimits,
  ModelBudgetOverview,
  ModelBudgetScopeView,
  WorkspaceInvitation,
  WorkspaceMember,
  WorkspaceRole,
} from '@anum/contracts';
import { getLocale, intlLocale, t } from './i18n.ts';

export const ROLES: readonly WorkspaceRole[] = ['owner', 'member', 'viewer'];
export const INVITATION_PREFIX = 'anum_inv_';
export const DEFAULT_TTL_HOURS = 168;
export const MAX_TTL_HOURS = 720;

// ------------------------------------------------------------------ wire shapes

export interface ApiMember { tenant_id: string; workspace_id: string; user_id: string; role: string; active: boolean; created_at: string; updated_at: string; email?: string | null }
export interface ApiInvitation {
  id: string; tenant_id: string; workspace_id: string; role: string;
  invitee_user_id: string | null; invitee_email: string | null; status: string;
  created_by_user_id: string; expires_at: string; accepted_by_user_id: string | null;
  accepted_at: string | null; revoked_at: string | null; created_at: string;
}
export interface ApiInvitationEmail { status: string; to?: string | null; attempts?: number; error?: string | null; link_included?: boolean }
export interface ApiInvitationCreated { invitation: ApiInvitation; token: string; email?: ApiInvitationEmail }
export interface ApiInvitationAccepted { invitation: ApiInvitation; membership: ApiMember }
interface ApiBudget { scope: string; monthly_cost_limit_usd: number | null; monthly_token_limit: number | null; updated_at: string; updated_by: string }
interface ApiScopeView {
  budget: ApiBudget | null;
  usage: { input_tokens: number; output_tokens: number; estimated_cost_usd: number; calls: number; unpriced_calls: number };
  total_tokens: number;
  exceeded: boolean;
}
export interface ApiBudgetOverview { period_start: string; resets_on: string; tenant: ApiScopeView; workspace: ApiScopeView; can_manage_tenant_budget?: boolean }

const role = (value: string): WorkspaceRole => (ROLES as readonly string[]).includes(value) ? value as WorkspaceRole : 'viewer';

export function mapMember(value: ApiMember): WorkspaceMember {
  return { tenantId: value.tenant_id, workspaceId: value.workspace_id, userId: value.user_id, role: role(value.role), active: value.active, createdAt: value.created_at, updatedAt: value.updated_at, email: value.email ?? null };
}

export function mapInvitation(value: ApiInvitation): WorkspaceInvitation {
  const status = ['pending', 'accepted', 'revoked', 'expired'].includes(value.status) ? value.status as WorkspaceInvitation['status'] : 'pending';
  return {
    id: value.id, tenantId: value.tenant_id, workspaceId: value.workspace_id, role: role(value.role),
    inviteeUserId: value.invitee_user_id, inviteeEmail: value.invitee_email, status,
    createdByUserId: value.created_by_user_id, expiresAt: value.expires_at,
    acceptedByUserId: value.accepted_by_user_id, acceptedAt: value.accepted_at, revokedAt: value.revoked_at, createdAt: value.created_at,
  };
}

export function mapInvitationEmail(value: ApiInvitationEmail | undefined): InvitationEmail {
  const status = (['sent', 'failed', 'not_configured', 'not_requested'] as const).find((known) => known === value?.status) ?? 'not_requested';
  return { status, to: value?.to ?? null, attempts: value?.attempts ?? 0, error: value?.error ?? null, linkIncluded: value?.link_included ?? false };
}

export const mapCreatedInvitation = (value: ApiInvitationCreated): CreatedInvitation => ({ invitation: mapInvitation(value.invitation), token: value.token, email: mapInvitationEmail(value.email) });

/** One sentence for the invitation email's outcome, with the tone of its notice. Always says what the owner does next. */
export function invitationEmailNotice(email: InvitationEmail): { tone: 'success' | 'warning' | 'neutral'; text: string } {
  switch (email.status) {
    case 'sent':
      return { tone: 'success', text: t(email.linkIncluded ? 'invite.email.sent' : 'invite.email.sentToken', { to: email.to ?? '' }) };
    case 'failed':
      return { tone: 'warning', text: t('invite.email.failed', { to: email.to ?? '', attempts: email.attempts, error: email.error ?? t('invite.email.unknownError') }) };
    case 'not_configured':
      return { tone: 'warning', text: t('invite.email.notConfigured', { to: email.to ?? '' }) };
    default:
      return { tone: 'neutral', text: t(email.to ? 'invite.email.notSent' : 'invite.email.noAddress') };
  }
}
export const mapAcceptedInvitation = (value: ApiInvitationAccepted): AcceptedInvitation => ({ invitation: mapInvitation(value.invitation), membership: mapMember(value.membership) });

function mapBudget(value: ApiBudget | null): ModelBudget | null {
  if (!value) return null;
  return { scope: value.scope === 'tenant' ? 'tenant' : 'workspace', monthlyCostLimitUsd: value.monthly_cost_limit_usd, monthlyTokenLimit: value.monthly_token_limit, updatedAt: value.updated_at, updatedBy: value.updated_by };
}

function mapScope(value: ApiScopeView): ModelBudgetScopeView {
  return {
    budget: mapBudget(value.budget),
    usage: { inputTokens: value.usage.input_tokens, outputTokens: value.usage.output_tokens, estimatedCostUsd: value.usage.estimated_cost_usd, calls: value.usage.calls, unpricedCalls: value.usage.unpriced_calls },
    totalTokens: value.total_tokens,
    exceeded: value.exceeded,
  };
}

export function mapBudgetOverview(value: ApiBudgetOverview): ModelBudgetOverview {
  // An API without the flag predates org_admin budgets: treat the organization budget as read only.
  return { periodStart: value.period_start, resetsOn: value.resets_on, tenant: mapScope(value.tenant), workspace: mapScope(value.workspace), canManageTenantBudget: value.can_manage_tenant_budget === true };
}

export function limitsBody(limits: ModelBudgetLimits): { monthly_cost_limit_usd: number | null; monthly_token_limit: number | null } {
  return { monthly_cost_limit_usd: limits.monthlyCostLimitUsd, monthly_token_limit: limits.monthlyTokenLimit };
}

// ------------------------------------------------------------------ invitations

export type InvitationDraft = { role: WorkspaceRole; userId: string; email: string; ttlHours: string };
export type DraftResult<T> = { ok: true; value: T } | { ok: false; error: string };

/** Validate the invite form the way the API does, so mistakes show before the request. */
export function invitationRequest(draft: InvitationDraft): DraftResult<InvitationRequest> {
  const userId = draft.userId.trim();
  const email = draft.email.trim();
  if (!userId && !email) return { ok: false, error: t('invite.err.noInvitee') };
  if (userId.length > 120) return { ok: false, error: t('invite.err.userIdLength') };
  if (email) {
    const [local, domain = ''] = email.split(/@(.*)/s);
    if (!email.includes('@') || !local || !domain.includes('.') || /\s/.test(email) || email.length > 320) {
      return { ok: false, error: t('invite.err.email') };
    }
  }
  const ttlText = draft.ttlHours.trim();
  const ttl = ttlText ? Number(ttlText) : DEFAULT_TTL_HOURS;
  if (!Number.isInteger(ttl) || ttl < 1 || ttl > MAX_TTL_HOURS) return { ok: false, error: t('invite.err.ttl', { max: MAX_TTL_HOURS }) };
  return { ok: true, value: { role: draft.role, ...(userId ? { inviteeUserId: userId } : {}), ...(email ? { inviteeEmail: email } : {}), ttlHours: ttl } };
}

export function invitationBody(request: InvitationRequest): Record<string, unknown> {
  return {
    role: request.role,
    ...(request.inviteeUserId ? { invitee_user_id: request.inviteeUserId } : {}),
    ...(request.inviteeEmail ? { invitee_email: request.inviteeEmail } : {}),
    ...(request.ttlHours ? { ttl_hours: request.ttlHours } : {}),
    ...(request.sendEmail === false ? { send_email: false } : {}),
    ...(request.appUrl ? { app_url: request.appUrl } : {}),
  };
}

/**
 * A link that opens the web app on the accept screen. The token travels in the URL fragment,
 * which browsers never send to a server, and the workspace tells the client which
 * `x-workspace-id` to accept it in.
 */
export function invitationLink(appUrl: string, token: string, workspaceId: string): string {
  const base = appUrl.split('#')[0];
  return `${base}#invitation=${encodeURIComponent(token)}&workspace=${encodeURIComponent(workspaceId)}`;
}

/** Read a pasted token or invitation link. Returns null when it holds no ANUM invitation token. */
export function parseInvitationInput(text: string): { token: string; workspaceId: string | null; tenantId?: string | null } | null {
  const value = text.trim();
  if (!value) return null;
  if (value.startsWith(INVITATION_PREFIX) && !/[\s&#?]/.test(value)) return { token: value, workspaceId: null };
  const params = new URLSearchParams(value.includes('#') ? value.slice(value.indexOf('#') + 1) : value.includes('?') ? value.slice(value.indexOf('?') + 1) : '');
  const token = params.get('invitation');
  if (!token || !token.startsWith(INVITATION_PREFIX)) return null;
  const workspace = params.get('workspace');
  // A customer organisation's first-admin link also names its organisation (docs/customer-organisations.md).
  const tenant = params.get('tenant');
  return {
    token, workspaceId: workspace && workspace.trim() ? workspace.trim() : null,
    ...(tenant && /^[A-Za-z0-9_-]{3,80}$/.test(tenant.trim()) ? { tenantId: tenant.trim() } : {}),
  };
}

export function inviteeLabel(invitation: WorkspaceInvitation): string {
  return [invitation.inviteeUserId, invitation.inviteeEmail].filter(Boolean).join(' · ') || t('invite.anyone');
}

/** "Expires in 3 d", "Expires in 5 h", "Expired 2 h ago". */
export function expiryLabel(expiresAt: string, now: Date = new Date()): string {
  const ms = new Date(expiresAt).getTime() - now.getTime();
  if (Number.isNaN(ms)) return t('invite.expiry.unknown');
  const abs = Math.abs(ms);
  const [unit, count] = abs >= 86_400_000 ? ['Days', Math.floor(abs / 86_400_000)] as const
    : abs >= 3_600_000 ? ['Hours', Math.floor(abs / 3_600_000)] as const
    : ['Minutes', Math.max(1, Math.floor(abs / 60_000))] as const;
  return t(`invite.expiry.${ms > 0 ? 'in' : 'ago'}${unit}`, { count });
}

export function activeOwnerCount(members: WorkspaceMember[]): number {
  return members.filter((member) => member.active && member.role === 'owner').length;
}

/** The only active owner: the API refuses to demote or deactivate them (409). */
export function isLastActiveOwner(members: WorkspaceMember[], member: WorkspaceMember): boolean {
  return member.active && member.role === 'owner' && activeOwnerCount(members) === 1;
}

/** Active members first, then owners before members before viewers, then by user id. */
export function sortMembers(members: WorkspaceMember[]): WorkspaceMember[] {
  return [...members].sort((a, b) => Number(b.active) - Number(a.active) || ROLES.indexOf(a.role) - ROLES.indexOf(b.role) || a.userId.localeCompare(b.userId));
}

// ------------------------------------------------------------------ budgets

export type MeterTone = 'ok' | 'warn' | 'stop' | 'none';
export interface BudgetMeter {
  kind: BudgetKind;
  label: string;
  used: number;
  limit: number | null;
  /** Share of the limit used, 0 to 100 (capped for the bar); null without a limit. */
  percent: number | null;
  /** Uncapped share, for the text. */
  rawPercent: number | null;
  tone: MeterTone;
  usedText: string;
  limitText: string;
}

/** Used share of a limit in percent. A zero limit blocks everything, so it reads as 100%. */
export function usagePercent(used: number, limit: number | null): number | null {
  if (limit === null) return null;
  if (limit <= 0) return 100;
  return (used / limit) * 100;
}

/** Thresholds match the API's: 80% logs a warning, 100% refuses calls. */
export function meterTone(percent: number | null): MeterTone {
  if (percent === null) return 'none';
  if (percent >= 100) return 'stop';
  if (percent >= 80) return 'warn';
  return 'ok';
}

export function formatUsd(value: number): string {
  if (value > 0 && value < 0.01) return '< $0.01';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: value >= 1000 ? 0 : 2 }).format(value);
}

export function formatTokens(value: number): string {
  return new Intl.NumberFormat('en-US').format(Math.round(value));
}

export function budgetMeters(view: ModelBudgetScopeView): BudgetMeter[] {
  const make = (kind: BudgetKind, used: number, limit: number | null): BudgetMeter => {
    const raw = usagePercent(used, limit);
    const format = kind === 'cost' ? formatUsd : formatTokens;
    return {
      kind,
      label: kind === 'cost' ? t('budgets.meter.cost') : t('budgets.meter.tokens'),
      used,
      limit,
      rawPercent: raw,
      percent: raw === null ? null : Math.min(100, Math.max(0, raw)),
      tone: meterTone(raw),
      usedText: format(used),
      limitText: limit === null ? t('budgets.noLimit') : format(limit),
    };
  };
  return [
    make('cost', view.usage.estimatedCostUsd, view.budget?.monthlyCostLimitUsd ?? null),
    make('tokens', view.totalTokens, view.budget?.monthlyTokenLimit ?? null),
  ];
}

/**
 * "1 November 2026 (UTC)" for an ISO date, in the page language ("1 نوفمبر 2026 (UTC)" in Arabic);
 * the API's months are UTC calendar months. `withZone: false` leaves out the "(UTC)".
 */
export function formatResetDate(isoDate: string, withZone = true): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return isoDate;
  const locale = getLocale();
  const text = new Intl.DateTimeFormat(locale === 'en' ? 'en-GB' : intlLocale(locale), { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(date);
  return withZone ? t('budgets.utc', { date: text }, { isolate: false }) : text;
}

export type LimitDraft = { cost: string; tokens: string };

export function limitDraft(budget: ModelBudget | null): LimitDraft {
  return {
    cost: budget?.monthlyCostLimitUsd === null || budget?.monthlyCostLimitUsd === undefined ? '' : String(budget.monthlyCostLimitUsd),
    tokens: budget?.monthlyTokenLimit === null || budget?.monthlyTokenLimit === undefined ? '' : String(budget.monthlyTokenLimit),
  };
}

/** Empty means no limit. Cost is USD up to 1e9; tokens a whole number up to 1e15 (the API's bounds). */
export function parseLimits(draft: LimitDraft): DraftResult<ModelBudgetLimits> {
  const costText = draft.cost.trim().replace(/^\$/, '').replaceAll(',', '');
  const tokenText = draft.tokens.trim().replaceAll(',', '').replaceAll('_', '');
  let cost: number | null = null;
  let tokens: number | null = null;
  if (costText) {
    cost = Number(costText);
    if (!Number.isFinite(cost) || cost < 0 || cost > 1_000_000_000) return { ok: false, error: t('budgets.err.cost') };
  }
  if (tokenText) {
    tokens = Number(tokenText);
    if (!Number.isSafeInteger(tokens) || tokens < 0 || tokens > 10 ** 15) return { ok: false, error: t('budgets.err.tokens') };
  }
  return { ok: true, value: { monthlyCostLimitUsd: cost, monthlyTokenLimit: tokens } };
}
