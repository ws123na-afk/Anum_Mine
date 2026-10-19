// Desktop notifications for the events that push and email also carry (docs/notifications.md#desktop):
// an approval waiting, a task finished or failed. The Desktop switch turns them all on or off, and
// each kind keeps its own switch, like phone push. The text is the push text: fixed, never a title.
import type { DomainEvent } from '@anum/contracts';
import { t } from './i18n.ts';
import type { NotificationPreferences } from './push.ts';

/** Preferences changed in Settings: open pages re-read nothing, they take the saved value from this event. */
export const PREFERENCES_EVENT = 'anum:notification-preferences';

export function announcePreferences(value: NotificationPreferences, scope: EventTarget | undefined = typeof window === 'undefined' ? undefined : window): void {
  scope?.dispatchEvent(new CustomEvent(PREFERENCES_EVENT, { detail: value }));
}

/** Calls `listener` with each saved value; returns the way to stop listening. */
export function onPreferences(listener: (value: NotificationPreferences) => void, scope: EventTarget | undefined = typeof window === 'undefined' ? undefined : window): () => void {
  if (!scope) return () => undefined;
  const handler = (event: Event) => listener((event as CustomEvent<NotificationPreferences>).detail);
  scope.addEventListener(PREFERENCES_EVENT, handler);
  return () => scope.removeEventListener(PREFERENCES_EVENT, handler);
}

export interface DesktopNotice {
  title: string;
  body: string;
  tag: string;
}

export interface DesktopEventContext {
  preferences: NotificationPreferences | null;
  /** The signed-in user's id. */
  userId: string | null;
  /** Only owners can decide approvals, so only they are told one is waiting. */
  isOwner: boolean;
  /** Who created the task, from the loaded task list (undefined when unknown). */
  taskCreator(taskId: string): string | null | undefined;
}

/** The notice for an event, or null when the person's switches, role or the event's owner say no. */
export function desktopNoticeFor(event: Pick<DomainEvent, 'id' | 'type' | 'subject' | 'payload'>, context: DesktopEventContext): DesktopNotice | null {
  const prefs = context.preferences;
  if (!prefs || !prefs.desktop_enabled) return null;
  const taskId = typeof event.payload?.task_id === 'string' ? event.payload.task_id : null;
  switch (event.type) {
    case 'approval.requested':
      if (!prefs.approval_required || !context.isOwner) return null;
      return { title: t('desktop.approval.title'), body: t('desktop.approval.body'), tag: event.subject || event.id };
    case 'agent_run.completed':
      if (!prefs.task_completed || !taskId || !context.userId || context.taskCreator(taskId) !== context.userId) return null;
      return { title: t('desktop.completed.title'), body: t('desktop.completed.body'), tag: event.subject || event.id };
    case 'agent_run.failed':
      if (!prefs.run_failed || !taskId || !context.userId || context.taskCreator(taskId) !== context.userId) return null;
      return { title: t('desktop.failed.title'), body: t('desktop.failed.body'), tag: event.subject || event.id };
    default:
      return null;
  }
}

/** Shows the notice when the browser or desktop app already allows it and the page is in the background. Never asks for permission. */
export function showDesktopNotice(notice: DesktopNotice, env: { hidden: boolean; permission: string } = { hidden: typeof document !== 'undefined' && document.hidden, permission: typeof Notification === 'undefined' ? 'unsupported' : Notification.permission }): boolean {
  if (!env.hidden || env.permission !== 'granted' || typeof Notification === 'undefined') return false;
  try {
    new Notification(notice.title, { body: notice.body, tag: notice.tag });
    return true;
  } catch {
    return false; // some browsers only allow it from a service worker; the in-app list still has it
  }
}
