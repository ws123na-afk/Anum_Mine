// Connection setup (docs/integrations-setup.md, docs/tools-and-integrations.md#connection-setup):
// which owner-facing integrations this server has, what is missing and where each one is
// connected. Pure, so `pnpm --filter @anum/web test:unit` covers the grouping, filtering, search
// and wording without a browser. The API never returns a secret: only statuses, setting names
// and callback URLs, the last two only for workspace owners and organization administrators.
import { CATALOGS, t, type MessageKey } from './i18n.ts';

export type SetupStatus = 'connected' | 'ready' | 'not_configured' | 'error';
export type SetupFilter = 'all' | 'attention' | 'connected';

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
const STATUS_ORDER: Record<SetupStatus, number> = { error: 0, not_configured: 1, ready: 2, connected: 3 };

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

export const needsAttention = (item: SetupItem): boolean => item.status === 'error' || item.status === 'not_configured';

export function setupCounts(items: readonly SetupItem[]): { connected: number; ready: number; attention: number } {
  return {
    connected: items.filter((item) => item.status === 'connected').length,
    ready: items.filter((item) => item.status === 'ready').length,
    attention: items.filter(needsAttention).length,
  };
}

const fold = (text: string) => text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().trim();

/** Filter and search (name, description, group and setting names), then sort problems first. */
export function filterSetup(items: readonly SetupItem[], filter: SetupFilter, query: string): SetupItem[] {
  const words = fold(query).split(/\s+/).filter(Boolean);
  return items
    .filter((item) => filter === 'all' || (filter === 'attention' ? needsAttention(item) : item.status === 'connected'))
    .filter((item) => {
      if (!words.length) return true;
      const text = fold([item.id, itemName(item.id), itemText(item.id), groupName(item.group), ...item.missing].join(' '));
      return words.every((word) => text.includes(word));
    })
    .sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status]);
}

/** Items grouped in the fixed group order (unknown groups last), each group keeping its sort. */
export function groupSetup(items: readonly SetupItem[]): { group: string; items: SetupItem[] }[] {
  const order = (group: string) => {
    const index = (GROUP_ORDER as readonly string[]).indexOf(group);
    return index === -1 ? GROUP_ORDER.length : index;
  };
  const groups = new Map<string, SetupItem[]>();
  for (const item of items) groups.set(item.group, [...(groups.get(item.group) ?? []), item]);
  return [...groups.entries()].sort(([a], [b]) => order(a) - order(b) || a.localeCompare(b)).map(([group, list]) => ({ group, items: list }));
}

export function checkSentence(check: SetupCheck, name: string): string {
  return check.reachable
    ? t('setup.check.ok', { name, ms: check.latency_ms ?? 0 })
    : t('setup.check.failed', { name, detail: check.detail });
}
