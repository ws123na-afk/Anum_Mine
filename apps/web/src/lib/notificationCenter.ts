// The Notifications page's model (docs/team-work.md#notifications-page): what each kind is called,
// where opening one goes, and the cursor trail Previous and Next walk. Pure, so test:unit covers it.
import type { UserNotification } from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

/** Kinds the API sends today (notifications.py create_notification callers); any other reads as "Other". */
export const KNOWN_KINDS = [
  'work_assigned', 'work_comment', 'work_completed', 'work_blocked', 'work_follow_up', 'work_overdue',
  'calendar_reminder', 'approval_reminder', 'budget_alert', 'agent_controls', 'skill_signing', 'document_expiry',
  'vehicle_reminder', 'purchase_reminder', 'attendance_missed_check', 'visitor_arrived', 'missed_call',
  'finance_daily_report', 'finance_weekly_advice', 'finance_loan_due', 'finance_suggestions', 'finance_exceptions', 'finance_sms',
] as const;

export const NOTIFICATIONS_PAGE = 20;

export function kindLabel(kind: string): string {
  return (KNOWN_KINDS as readonly string[]).includes(kind) ? t(`ntf.kind.${kind}` as MessageKey) : t('ntf.kind.other');
}

/**
 * Where opening a notification goes when it is not about a team work item (those open the work
 * item itself): the module the notice is about, by URL hash. Null when there is nowhere better
 * than the notification itself.
 */
export function notificationHash(n: Pick<UserNotification, 'kind' | 'work_item_id'>): string | null {
  if (n.work_item_id) return null;
  if (n.kind === 'finance_daily_report') return '#finance-intel-reports';
  if (n.kind === 'finance_weekly_advice') return '#finance-advisor';
  if (n.kind.startsWith('finance_')) return '#finance';
  const byKind: Record<string, string> = {
    calendar_reminder: '#calendar', approval_reminder: '#approvals', budget_alert: '#budgets', agent_controls: '#agentControls',
    skill_signing: '#skills', document_expiry: '#vault', vehicle_reminder: '#vehicles', purchase_reminder: '#purchases',
    attendance_missed_check: '#attendance', visitor_arrived: '#office', missed_call: '#calls',
  };
  return byKind[n.kind] ?? null;
}

/**
 * Cursor paging with a way back: `trail[i]` is the cursor that loads page i (null for the first).
 * Next pushes the page's `next_cursor`; Previous drops the last one.
 */
export interface CursorTrail { trail: (string | null)[] }
export const firstPage = (): CursorTrail => ({ trail: [null] });
export const currentCursor = (state: CursorTrail): string | null => state.trail[state.trail.length - 1] ?? null;
export const nextPage = (state: CursorTrail, cursor: string): CursorTrail => ({ trail: [...state.trail, cursor] });
export const previousPage = (state: CursorTrail): CursorTrail => (state.trail.length > 1 ? { trail: state.trail.slice(0, -1) } : state);
/** "21 to 40": the first and last row number of the page shown. */
export function pageRows(state: CursorTrail, shown: number, size = NOTIFICATIONS_PAGE): { from: number; to: number } {
  const start = (state.trail.length - 1) * size;
  return { from: start + 1, to: start + shown };
}
