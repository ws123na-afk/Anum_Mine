/**
 * "Show history" from a task, meeting or approval screen in the headquarters (matrix row 10.4).
 * The screens only send this event; the headquarters listens and opens the History panel, which is a lazy
 * chunk loaded on that click. The panel then finds the stored audit records of the item and opens its
 * request (correlation) view. Nothing is guessed: an item without records says so.
 * Spec: docs/ANUM_MINE_HQ_HISTORY_PANEL.md#show-history-from-a-task-meeting-or-approval.
 */
export const HISTORY_LINK_EVENT = 'anum:hq-show-history';

export type HistoryRecordKind = 'task' | 'meeting' | 'approval';
export interface HistoryRecordRef { recordKind: HistoryRecordKind; id: string; label?: string }

const KINDS: ReadonlySet<string> = new Set(['task', 'meeting', 'approval']);
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;

/** The reference carried by the event, or null when it is not one the panel can look up. */
export function parseHistoryRef(value: unknown): HistoryRecordRef | null {
  if (!value || typeof value !== 'object') return null;
  const v = value as Record<string, unknown>;
  if (typeof v.recordKind !== 'string' || !KINDS.has(v.recordKind) || typeof v.id !== 'string' || !ID.test(v.id)) return null;
  const label = typeof v.label === 'string' && v.label.trim() ? v.label.trim().slice(0, 160) : undefined;
  return { recordKind: v.recordKind as HistoryRecordKind, id: v.id, ...(label ? { label } : {}) };
}

/** Asks the headquarters to open the History panel for this item. False when the reference is not valid. */
export function showRecordHistory(ref: HistoryRecordRef, target: Pick<Window, 'dispatchEvent'> = window): boolean {
  const parsed = parseHistoryRef(ref);
  if (!parsed) return false;
  target.dispatchEvent(new CustomEvent<HistoryRecordRef>(HISTORY_LINK_EVENT, { detail: parsed }));
  return true;
}
