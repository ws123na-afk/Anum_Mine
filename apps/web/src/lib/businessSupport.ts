// Business support tickets (docs/business.md#support-tickets): labels, tones, the SLA badge, the
// list filters as a query and form validation. Pure, so the unit tests cover it without a browser.
// The API is the authority: it rechecks every rule and computes the SLA in working hours only.
import type {
  ReplyStatus, ResolutionCode, SlaClock, SupportPolicy, TicketEntitlement, TicketFields, TicketSeverity, TicketSla, TicketSource, TicketStatus,
} from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';
import type { Check, Tone } from './business.ts';

export const SEVERITIES: readonly TicketSeverity[] = ['critical', 'high', 'normal', 'low'];
export const TICKET_STATUSES: readonly TicketStatus[] = ['new', 'triaged', 'in_progress', 'waiting_on_customer', 'resolved', 'closed'];
export const OPEN_STATUSES: readonly TicketStatus[] = ['new', 'triaged', 'in_progress', 'waiting_on_customer'];
export const RESOLUTION_CODES: readonly ResolutionCode[] = ['fixed', 'workaround', 'answered', 'no_fault_found', 'duplicate', 'out_of_scope'];
export const ENTITLEMENTS: readonly TicketEntitlement[] = ['none', 'project', 'support_plan'];
/** Python weekdays as the API stores them, shown Monday first. */
export const WEEKDAYS: readonly number[] = [0, 1, 2, 3, 4, 5, 6];
export const MIN_RESOLUTION_SUMMARY = 10;

const label = (key: string) => t(key as MessageKey);
export const severityLabel = (value: TicketSeverity) => label(`biz.sup.severity.${value}`);
export const ticketStatusLabel = (value: TicketStatus) => label(`biz.sup.status.${value}`);
export const resolutionLabel = (value: ResolutionCode) => label(`biz.sup.resolution.${value}`);
export const entitlementLabel = (value: TicketEntitlement) => label(`biz.sup.entitlement.${value}`);
export const replyStatusLabel = (value: ReplyStatus) => label(`biz.sup.replyStatus.${value}`);
export const sourceLabel = (value: TicketSource) => label(`biz.sup.source.${value}`);
export const slaLabel = (value: SlaClock) => label(`biz.sup.sla.${value}`);
export const weekdayLabel = (value: number) => label(`biz.sup.day.${value}`);

export function severityTone(value: TicketSeverity): Tone {
  return value === 'critical' ? 'dangerTone' : value === 'high' ? 'warning' : 'neutral';
}

export function ticketStatusTone(value: TicketStatus): Tone {
  if (value === 'resolved' || value === 'closed') return 'success';
  if (value === 'new' || value === 'waiting_on_customer') return 'warning';
  return 'neutral';
}

export function replyStatusTone(value: ReplyStatus): Tone {
  return value === 'sent' ? 'success' : value === 'failed' ? 'dangerTone' : value === 'pending_approval' ? 'warning' : 'neutral';
}

export function slaTone(value: SlaClock): Tone {
  if (value === 'breached' || value === 'missed') return 'dangerTone';
  if (value === 'due_soon') return 'warning';
  if (value === 'met') return 'success';
  return 'neutral';
}

// Which clock the list badge shows: a live problem first, then a missed target, then the rest.
const RANK: Record<SlaClock, number> = { breached: 0, due_soon: 1, missed: 2, paused: 3, on_track: 4, met: 5, none: 6 };

/** The one SLA badge a ticket row shows: the more urgent of its response and resolution clocks. */
export function slaBadge(sla: TicketSla): { state: SlaClock; clock: 'response' | 'resolution'; tone: Tone; label: string } {
  const clock = RANK[sla.response] < RANK[sla.resolution] ? 'response' : 'resolution';
  const state = sla.breached && sla[clock] !== 'breached' ? 'breached' : sla[clock];
  return { state, clock, tone: slaTone(state), label: slaLabel(state) };
}

/** Where a ticket's source conversation lives in ANUM (a link, never the message text). */
export function sourceHref(source: TicketSource): string | null {
  return source === 'email' ? '#email' : source === 'whatsapp' ? '#whatsapp' : null;
}

// -- list filters -------------------------------------------------------------------------------

export type TicketSort = 'updated' | 'due' | 'created';
export interface TicketFilters { q: string; status: 'open' | 'all' | TicketStatus; severity: '' | TicketSeverity; who: 'all' | 'me' | 'unassigned'; escalated: boolean; sort: TicketSort }
export const defaultFilters = (): TicketFilters => ({ q: '', status: 'open', severity: '', who: 'all', escalated: false, sort: 'updated' });
export const filtersActive = (filters: TicketFilters) =>
  Boolean(filters.q) || filters.status !== 'open' || Boolean(filters.severity) || filters.who !== 'all' || filters.escalated;

/** List filters as the API reads them; `assignee: 'me'` is the caller. */
export interface TicketListQuery {
  q?: string; status?: readonly string[]; severity?: readonly string[]; open_only?: boolean; assignee?: string; unassigned?: boolean; escalated?: boolean;
  account_id?: string; project_id?: string; source?: string; sort?: TicketSort; limit: number; offset: number;
}

export function filtersToQuery(filters: TicketFilters, limit: number, offset: number): TicketListQuery {
  const query: TicketListQuery = { limit, offset, sort: filters.sort };
  if (filters.q.trim()) query.q = filters.q.trim();
  if (filters.status === 'open') query.open_only = true;
  else if (filters.status !== 'all') query.status = [filters.status];
  if (filters.severity) query.severity = [filters.severity];
  if (filters.who === 'me') query.assignee = 'me';
  if (filters.who === 'unassigned') query.unassigned = true;
  if (filters.escalated) query.escalated = true;
  return query;
}

/** The query string for GET /support/tickets: false flags and empty values are left out. */
export function ticketQuery(q: TicketListQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(q)) {
    if (value === undefined || value === null || value === '' || value === false) continue;
    if (Array.isArray(value)) value.forEach((item) => params.append(key, String(item)));
    else params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : '';
}

// -- forms --------------------------------------------------------------------------------------

const EMAILISH = /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/;

export interface TicketDraft {
  title: string; description: string; severity: TicketSeverity; requesterName: string; requesterEmail: string; requesterPhone: string;
  accountId: string; contactId: string; projectId: string; entitlement: TicketEntitlement; offerId: string; entitlementNote: string; assigneeId: string;
}
export const emptyTicketDraft = (): TicketDraft => ({
  title: '', description: '', severity: 'normal', requesterName: '', requesterEmail: '', requesterPhone: '', accountId: '', contactId: '', projectId: '',
  entitlement: 'none', offerId: '', entitlementNote: '', assigneeId: '',
});

/** The POST /support/tickets body, or what to fix. */
export function ticketBody(draft: TicketDraft): Check<TicketFields & { title: string }> {
  const title = draft.title.replace(/\s+/g, ' ').trim();
  if (title.length < 3) return { ok: false, error: t('biz.sup.form.titleRequired') };
  const email = draft.requesterEmail.trim();
  if (email && !EMAILISH.test(email)) return { ok: false, error: t('biz.form.emailInvalid') };
  const phone = draft.requesterPhone.trim();
  if (phone && phone.replace(/\D/g, '').length < 8) return { ok: false, error: t('biz.form.phoneInvalid') };
  if (draft.entitlement === 'project' && !draft.projectId) return { ok: false, error: t('biz.sup.form.projectRequired') };
  if (draft.entitlement === 'support_plan' && !draft.offerId) return { ok: false, error: t('biz.sup.form.planRequired') };
  return {
    ok: true,
    value: {
      title, description: draft.description.trim(), severity: draft.severity, requester_name: draft.requesterName.trim(),
      requester_email: email || null, requester_phone: phone || null, account_id: draft.accountId || null, contact_id: draft.contactId || null,
      project_id: draft.projectId || null, entitlement: draft.entitlement, support_offer_id: draft.entitlement === 'support_plan' ? draft.offerId : null,
      entitlement_note: draft.entitlementNote.trim(), assignee_id: draft.assigneeId || null,
    },
  };
}

/** What a resolve needs: a resolution and a summary of at least a sentence. */
export function resolveProblem(code: ResolutionCode | '', summary: string): string | null {
  if (!code) return t('biz.sup.resolve.codeRequired');
  if (summary.trim().length < MIN_RESOLUTION_SUMMARY) return t('biz.sup.resolve.summaryShort', { min: MIN_RESOLUTION_SUMMARY });
  return null;
}

export interface PolicyDraft { timezone: string; workDays: number[]; startHour: string; endHour: string; targets: Record<TicketSeverity, { response: string; resolution: string }>; note: string }

export function policyDraft(policy: SupportPolicy): PolicyDraft {
  const targets = {} as PolicyDraft['targets'];
  for (const severity of SEVERITIES) {
    const target = policy.targets[severity];
    targets[severity] = { response: target ? String(target.response_hours) : '', resolution: target ? String(target.resolution_hours) : '' };
  }
  return { timezone: policy.timezone, workDays: [...policy.work_days], startHour: String(policy.start_hour), endHour: String(policy.end_hour), targets, note: policy.note };
}

const hours = (text: string, max: number) => {
  const value = Number(text.trim());
  return text.trim() !== '' && Number.isFinite(value) && value > 0 && value <= max && Math.abs(Math.round(value * 100) - value * 100) < 1e-6 ? value : null;
};

/** The PUT /support/policy body, or what to fix (the API checks the time zone name). */
export function policyBody(draft: PolicyDraft): Check<SupportPolicy> {
  const timezone = draft.timezone.trim();
  if (timezone.length < 3) return { ok: false, error: t('biz.sup.policy.timezoneRequired') };
  if (draft.workDays.length === 0) return { ok: false, error: t('biz.sup.policy.daysRequired') };
  const start = Number(draft.startHour), end = Number(draft.endHour);
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || start > 23 || end < 1 || end > 24 || end <= start) return { ok: false, error: t('biz.sup.policy.hoursInvalid') };
  const targets = {} as SupportPolicy['targets'];
  for (const severity of SEVERITIES) {
    const response = hours(draft.targets[severity].response, 1000), resolution = hours(draft.targets[severity].resolution, 5000);
    if (response === null || resolution === null) return { ok: false, error: t('biz.sup.policy.targetInvalid', { severity: severityLabel(severity) }) };
    if (resolution < response) return { ok: false, error: t('biz.sup.policy.targetOrder', { severity: severityLabel(severity) }) };
    targets[severity] = { response_hours: response, resolution_hours: resolution };
  }
  return { ok: true, value: { timezone, work_days: [...new Set(draft.workDays)].sort((a, b) => a - b), start_hour: start, end_hour: end, targets, note: draft.note.trim() } };
}

/** "Sunday to Thursday, 9:00 to 17:00 (Asia/Riyadh)" style summary of the working time. */
export function workingTimeText(policy: SupportPolicy): string {
  const days = WEEKDAYS.filter((day) => policy.work_days.includes(day)).map(weekdayLabel).join(', ');
  return t('biz.sup.policy.summary', { days, start: `${policy.start_hour}:00`, end: `${policy.end_hour}:00`, zone: policy.timezone });
}
