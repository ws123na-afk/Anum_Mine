// The short time a message list shows beside each sender (EmailView, EmailHub, MailAllInboxes):
// the time for today, the day for this year, the full date before that. The reader and the
// row's title keep the full date and time. Pure, so the unit tests run it without a browser.
import { formatDateTime } from './i18n.ts';

export function listTime(value: string | number | Date, now: Date = new Date()): string {
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return '';
  const sameYear = at.getFullYear() === now.getFullYear();
  if (sameYear && at.getMonth() === now.getMonth() && at.getDate() === now.getDate()) return formatDateTime(at, { timeStyle: 'short' });
  return formatDateTime(at, sameYear ? { month: 'short', day: 'numeric' } : { dateStyle: 'medium' });
}
