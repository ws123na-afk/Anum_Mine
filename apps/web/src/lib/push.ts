// Phone push (docs/notifications.md): whether the server can send it (GET /api/v1/push/status)
// and the caller's own registered phones (GET/DELETE /api/v1/me/devices). Phones register
// themselves from the mobile app; the web only shows and removes them. Pure, so the unit tests
// run without a browser.

import { t, type MessageKey } from './i18n.ts';

export const PUSH_STATUS_PATH = '/api/v1/push/status';
export const MY_DEVICES_PATH = '/api/v1/me/devices';

export function deviceDeletePath(id: string): string {
  return `${MY_DEVICES_PATH}/${encodeURIComponent(id)}`;
}

export interface PushStatus {
  available: boolean;
  provider: string;
  project_id: string | null;
  message: string;
}

export type PushPlatform = 'android' | 'ios';

/** A registered phone. The server never returns its push token, only the last four characters. */
export interface PushDevice {
  id: string;
  platform: PushPlatform;
  label: string | null;
  token_hint: string;
  created_at: string;
  updated_at: string;
  last_sent_at: string | null;
}

/** The workspace notification switches (PUT /api/v1/notification-preferences). */
export interface NotificationPreferences {
  task_completed: boolean;
  approval_required: boolean;
  run_failed: boolean;
  automation_failed: boolean;
  email_enabled: boolean;
  desktop_enabled: boolean;
  /** Older servers do not send it; it is on there. */
  push_enabled?: boolean;
}

export type NotificationKey = keyof Required<NotificationPreferences>;

export interface NotificationSwitch {
  key: NotificationKey;
  label: string;
  description: string;
}

/** The switches in the order Settings shows them: what to hear about, then where. */
export const NOTIFICATION_KEYS: NotificationKey[] = [
  'approval_required', 'task_completed', 'run_failed', 'automation_failed', 'push_enabled', 'desktop_enabled', 'email_enabled',
];

/** What each switch means, in the current language. */
export function notificationSwitches(): NotificationSwitch[] {
  return NOTIFICATION_KEYS.map((key) => ({
    key,
    label: t(`settings.notifications.${key}` as MessageKey),
    description: t(`settings.notifications.${key}.hint` as MessageKey),
  }));
}

export function withDefaults(value: NotificationPreferences): Required<NotificationPreferences> {
  return { ...value, push_enabled: value.push_enabled ?? true };
}

export interface PushSummary {
  badge: string;
  tone: 'success' | 'neutral' | 'warning';
  text: string;
}

/** The state of phone push for this user in this workspace, and what to do next. */
export function pushSummary(status: PushStatus, devices: PushDevice[], preferences?: NotificationPreferences | null): PushSummary {
  if (!status.available) {
    return { badge: t('push.badge.offServer'), tone: 'warning', text: t('push.text.offServer', { message: status.message }) };
  }
  if (preferences && preferences.push_enabled === false) {
    return { badge: t('push.badge.off'), tone: 'neutral', text: t('push.text.off') };
  }
  if (devices.length === 0) {
    return { badge: t('push.badge.none'), tone: 'neutral', text: t('push.text.none') };
  }
  return { badge: t('push.badge.on'), tone: 'success', text: t('push.text.on', { count: devices.length }) };
}

export function platformLabel(platform: PushPlatform | string): string {
  return platform === 'ios' ? t('push.platform.ios') : platform === 'android' ? t('push.platform.android') : t('push.platform.other');
}

/** "Pixel 8 (Android, ends in 1a2b)": what a person needs to tell their phones apart. */
export function deviceTitle(device: PushDevice): string {
  const platform = platformLabel(device.platform);
  const name = device.label?.trim() || t('push.device.defaultName', { platform });
  return t('push.device.title', { name, platform, hint: device.token_hint });
}

/** "Last notified 5 minutes ago" or "Not notified yet". */
export function lastSentText(device: Pick<PushDevice, 'last_sent_at'>, now: Date = new Date()): string {
  if (!device.last_sent_at) return t('push.device.notSent');
  const seconds = Math.max(0, Math.round((now.getTime() - new Date(device.last_sent_at).getTime()) / 1000));
  if (seconds < 60) return t('push.device.sentNow');
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return t('push.device.sentMinutes', { count: minutes });
  const hours = Math.round(minutes / 60);
  if (hours < 48) return t('push.device.sentHours', { count: hours });
  return t('push.device.sentDays', { count: Math.round(hours / 24) });
}

/** Newest registration first. */
export function sortDevices(devices: PushDevice[]): PushDevice[] {
  return [...devices].sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : a.id.localeCompare(b.id)));
}
