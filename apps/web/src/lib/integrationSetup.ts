// Connection setup (docs/integrations-setup.md, docs/tools-and-integrations.md#connection-setup):
// which owner-facing integrations this server has, what is missing and where each one is
// connected. Pure, so `pnpm --filter @anum/web test:unit` covers the wording without a browser
// (the page's grouping, filters and search are in integrationsPage.ts). The API never returns a
// secret: only statuses, setting names and callback URLs, the last two only for workspace owners
// and organization administrators.
import { CATALOGS, t, type MessageKey } from './i18n.ts';

export type SetupStatus = 'connected' | 'ready' | 'not_configured' | 'error';

export interface SetupItem {
  id: string;
  group: string;
  status: SetupStatus;
  connect_view: string | null;
  connections: number | null;
  failing: number;
  missing: string[];
  redirect_urls: string[];
  doc: string;
  checkable: boolean;
}

export interface IntegrationSetup {
  items: SetupItem[];
  can_view_setup: boolean;
  can_check: boolean;
}

export interface SetupCheck {
  id: string;
  reachable: boolean;
  latency_ms: number | null;
  detail: string;
  checked_at: string;
}

/** Where the owner runbook lives; each item links to its own numbered section. */
export const RUNBOOK_URL = 'https://github.com/ws123na-afk/Anum_Mine/blob/main/docs/integrations-setup.md';
export const GROUP_ORDER = ['ai', 'email', 'messaging', 'meetings', 'notifications', 'sign_in', 'storage', 'automation', 'marketing', 'commerce', 'operations', 'monitoring', 'developer'] as const;

export const docUrl = (item: Pick<SetupItem, 'doc'>): string => `${RUNBOOK_URL}#${encodeURIComponent(item.doc)}`;

// Every item and group this client knows has a catalog entry; an unknown one (a newer API)
// falls back to its id rather than showing a missing-key marker.
const known = (key: string): key is MessageKey => Object.hasOwn(CATALOGS.en, key);

export function itemName(id: string): string {
  const key = `setup.item.${id}.name`;
  return known(key) ? t(key) : id;
}

export function itemText(id: string): string {
  const key = `setup.item.${id}.text`;
  return known(key) ? t(key) : '';
}

export function itemCost(id: string): string {
  const key = `setup.item.${id}.cost`;
  return known(key) ? t(key) : '';
}

export function groupName(group: string): string {
  const key = `setup.group.${group}`;
  return known(key) ? t(key) : group;
}

export function statusBadge(status: SetupStatus): { label: string; tone: 'success' | 'warning' | 'neutral' | 'dangerTone' } {
  switch (status) {
    case 'connected': return { label: t('setup.status.connected'), tone: 'success' };
    case 'ready': return { label: t('setup.status.ready'), tone: 'neutral' };
    case 'error': return { label: t('setup.status.error'), tone: 'dangerTone' };
    default: return { label: t('setup.status.not_configured'), tone: 'warning' };
  }
}

/** One plain sentence: what the state means and what to do next. */
export function statusSentence(item: SetupItem, canViewSetup: boolean): string {
  if (item.status === 'connected') {
    if (item.connections === null) return t('setup.say.connected');
    return t('setup.say.connectedCount', { count: item.connections });
  }
  if (item.status === 'ready') return item.connect_view ? t('setup.say.readyConnect') : t('setup.say.ready');
  if (item.status === 'error') {
    if (item.failing > 0) return t('setup.say.failing', { count: item.failing });
    return canViewSetup && item.missing.length ? t('setup.say.errorFix') : t('setup.say.error');
  }
  return canViewSetup ? t('setup.say.notConfigured') : t('setup.say.notConfiguredMember');
}

export function checkSentence(check: SetupCheck, name: string): string {
  return check.reachable
    ? t('setup.check.ok', { name, ms: check.latency_ms ?? 0 })
    : t('setup.check.failed', { name, detail: check.detail });
}
