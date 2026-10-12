// Desktop notification for a new commerce alert (docs/commerce.md#alerts). Shown only when this
// person keeps "Store alerts" and "Desktop notifications" on for the workspace and the browser
// (or the desktop shell) already allows notifications; ANUM never prompts for permission here.
import type { DomainEvent } from '@anum/contracts';
import { getNotificationPreferences } from './api';
import { notificationFor, type AlertPreferences } from './commerce';

let preferences: Promise<AlertPreferences | null> | null = null;

export async function notifyCommerceAlert(event: DomainEvent): Promise<void> {
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
  preferences ??= getNotificationPreferences().catch(() => null);
  const message = notificationFor(event, await preferences);
  if (!message) return;
  new Notification(message.title, { body: message.body, tag: event.subject });
}

/** Forget the cached preferences (after they change in Settings). */
export function resetCommerceNotify(): void { preferences = null; }
