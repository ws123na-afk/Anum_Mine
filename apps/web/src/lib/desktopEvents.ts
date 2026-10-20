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

/** What the client needs to know about an approval to apply the server's recipient rule. */
export interface ApprovalFacts {
  riskLevel: string;
  requestedBy?: string | null;
  taskId: string;
}

export interface DesktopEventContext {
  preferences: NotificationPreferences | null;
  /** The signed-in user's id. */
  userId: string | null;
  /** Only owners can decide approvals, so only they are told one is waiting. */
  isOwner: boolean;
  /** Who created the task: from the loaded list when it is there, else fetched. undefined when it cannot be found. */
  taskCreator(taskId: string): Promise<string | null | undefined>;
  /** The approval behind an `approval.requested` event (null when it cannot be read). */
  approval(approvalId: string): Promise<ApprovalFacts | null>;
  /** Whether the workspace's two-person rule is on (false when it cannot be read). */
  twoPersonRule(): Promise<boolean>;
}

/** Risk levels the two-person rule covers: the same set as the server (push.TWO_PERSON_RISK_LEVELS). */
const TWO_PERSON_RISK_LEVELS = new Set(['high']);

/**
 * The notice for an event, or null when the person's switches, role or the event's owner say no.
 * Approvals follow the server's push recipient rule: an owner who requested the approval or created
 * its task is not told when the two-person rule covers its risk, because they cannot decide it.
 * An approval or task the client cannot look up still notifies / stays quiet as described on each case.
 */
export async function desktopNoticeFor(event: Pick<DomainEvent, 'id' | 'type' | 'subject' | 'payload'>, context: DesktopEventContext): Promise<DesktopNotice | null> {
  const prefs = context.preferences;
  if (!prefs || !prefs.desktop_enabled) return null;
  const taskId = typeof event.payload?.task_id === 'string' ? event.payload.task_id : null;
  switch (event.type) {
    case 'approval.requested': {
      if (!prefs.approval_required || !context.isOwner) return null;
      const approval = await context.approval(event.subject);
      // Not readable: tell the owner; the server still decides who may act.
      if (approval && TWO_PERSON_RISK_LEVELS.has(approval.riskLevel) && await context.twoPersonRule()) {
        const creator = await context.taskCreator(approval.taskId);
        if (context.userId && (approval.requestedBy === context.userId || creator === context.userId)) return null;
      }
      return { title: t('desktop.approval.title'), body: t('desktop.approval.body'), tag: event.subject || event.id };
    }
    case 'agent_run.completed':
    case 'agent_run.failed': {
      const completed = event.type === 'agent_run.completed';
      if (!(completed ? prefs.task_completed : prefs.run_failed) || !taskId || !context.userId) return null;
      // Only the task's creator is told; an unknown task (not in the loaded list) is looked up, and one that cannot be found stays quiet.
      if ((await context.taskCreator(taskId)) !== context.userId) return null;
      return completed
        ? { title: t('desktop.completed.title'), body: t('desktop.completed.body'), tag: event.subject || event.id }
        : { title: t('desktop.failed.title'), body: t('desktop.failed.body'), tag: event.subject || event.id };
    }
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
