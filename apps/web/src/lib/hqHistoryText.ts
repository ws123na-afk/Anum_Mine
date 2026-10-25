/** Wording for the office history panel: action, type, field and value labels. Unknown actions show their stored name. */
import type { OfficeHistoryCategory } from '@anum/contracts';
import { CATALOGS, formatDateTime, t, type MessageKey } from './i18n.ts';

export type NameMap = ReadonlyMap<string, string>;

const has = (key: string): key is MessageKey => key in CATALOGS.en;

const humanize = (action: string) => action.replace(/[._]+/g, ' ').trim();

export function actionLabel(action: string): string {
  const key = `hqHist.action.${action}`;
  return has(key) ? t(key) : humanize(action);
}
export function categoryLabel(category: OfficeHistoryCategory | string): string {
  const key = `hqHist.cat.${category}`;
  return has(key) ? t(key) : String(category);
}
export function fieldLabel(field: string): string {
  const key = `hqHist.field.${field}`;
  return has(key) ? t(key) : field;
}
export function outcomeLabel(outcome: string): string {
  const key = `hqHist.outcome.${outcome}`;
  return has(key) ? t(key) : outcome;
}
/** A person or agent id shown with its name when one is known. */
export function whoLabel(id: string, names: NameMap): string {
  const name = names.get(id);
  return name ? `${name}` : id;
}
export function whenLabel(iso: string): string {
  const stamp = Date.parse(iso);
  return Number.isNaN(stamp) ? iso : formatDateTime(stamp, { dateStyle: 'medium', timeStyle: 'short' });
}
export function timeOnly(iso: string): string {
  const stamp = Date.parse(iso);
  return Number.isNaN(stamp) ? iso : formatDateTime(stamp, { timeStyle: 'short' });
}

/** A recorded value as text. Agent and room ids use the names the open office knows. */
export function valueLabel(field: string, value: unknown, names: NameMap): string {
  if (value === null || value === undefined || value === '') return t('hqHist.detail.notSet');
  if ((field === 'agent_id' || field === 'room_id') && typeof value === 'string') return names.get(value) ?? value;
  if (Array.isArray(value)) return value.length === 0 ? t('hqHist.detail.notSet') : value.map((v) => (typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v))).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
