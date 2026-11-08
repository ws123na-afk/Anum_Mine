// The Integrations hub (docs/integrations-setup.md#the-integrations-page): the five tabs (Connected
// apps, Data sync, API and webhooks, Permissions, Logs), the KPI figures and the rows each tab
// shows. Everything is derived from what the API returns (GET /api/v1/integrations, GET
// /api/v1/integrations/setup and the audit log); nothing here invents a figure. Pure, so
// `pnpm --filter @anum/web test:unit` covers it.
import type { IntegrationState } from './catalog.ts';
import { itemName, type SetupItem } from './integrationSetup.ts';
import { CATALOGS, t, type MessageKey } from './i18n.ts';

export const INTEGRATION_TABS = ['apps', 'sync', 'webhooks', 'permissions', 'logs'] as const;
export type IntegrationTab = (typeof INTEGRATION_TABS)[number];

/** `#integrations` is Connected apps; `#integrations-<tab>` opens another tab; `#connections` is the old link. */
export function tabFromHash(hash: string): IntegrationTab {
  const id = hash.replace(/^#/, '');
  if (!id.startsWith('integrations-')) return 'apps';
  const tab = id.slice('integrations-'.length) as IntegrationTab;
  return INTEGRATION_TABS.includes(tab) ? tab : 'apps';
}
export const hashOf = (tab: IntegrationTab): string => (tab === 'apps' ? '#integrations' : `#integrations-${tab}`);

export interface HubFigures {
  /** Integrations that work now: connected setup items and connected deployment services. */
  connected: number;
  /** Everything the page lists that is in use or could be (services switched off are left out). */
  inUse: number;
  /** Setup items in error and degraded services. */
  issues: number;
  /** Setup items still to set up. */
  toSetUp: number;
  /** Share of checked integrations answering, 0 to 100, or null when nothing has been checked. */
  healthPercent: number | null;
  /** The newest health check of a deployment service, or null. */
  lastChecked: string | null;
}

export function hubFigures(items: readonly SetupItem[], services: readonly IntegrationState[]): HubFigures {
  const setupConnected = items.filter((i) => i.status === 'connected').length;
  const setupErrors = items.filter((i) => i.status === 'error').length;
  const toSetUp = items.filter((i) => i.status !== 'connected' && i.status !== 'error').length;
  const live = services.filter((s) => s.status === 'connected' || s.status === 'degraded');
  const serviceUp = live.filter((s) => s.status === 'connected').length;
  const serviceDown = live.length - serviceUp;
  const checked = setupConnected + setupErrors + live.length;
  const up = setupConnected + serviceUp;
  const stamps = services.map((s) => s.checked_at).filter((v): v is string => Boolean(v)).sort();
  return {
    connected: up,
    inUse: items.length + live.length,
    issues: setupErrors + serviceDown,
    toSetUp,
    healthPercent: checked ? Math.round((up / checked) * 1000) / 10 : null,
    lastChecked: stamps.length ? stamps[stamps.length - 1] : null,
  };
}

export type SyncState = 'ok' | 'failing' | 'none';
export interface SyncRow { id: string; name: string; connections: number; failing: number; state: SyncState; connectView: string | null }

/**
 * Data sync: every setup item that keeps its own connected accounts (mailboxes, calendars, chat
 * numbers and so on), with how many there are and how many failed their last sync.
 */
export function syncRows(items: readonly SetupItem[]): SyncRow[] {
  return items.filter((i) => i.connections !== null).map((i) => {
    const connections = i.connections ?? 0;
    const state: SyncState = i.failing > 0 ? 'failing' : connections > 0 ? 'ok' : 'none';
    return { id: i.id, name: itemName(i.id), connections, failing: i.failing, state, connectView: i.connect_view };
  }).sort((a, b) => order(a.state) - order(b.state) || b.connections - a.connections || a.name.localeCompare(b.name));
}
const order = (state: SyncState) => (state === 'failing' ? 0 : state === 'ok' ? 1 : 2);

export interface CallbackRow { id: string; name: string; url: string }

/** API and webhooks: the callback and webhook addresses each provider must be given, one row per address. */
export function callbackRows(items: readonly SetupItem[]): CallbackRow[] {
  return items.flatMap((i) => i.redirect_urls.map((url) => ({ id: i.id, name: itemName(i.id), url })));
}

/** The deployment services that receive or send events (webhooks, the event bus, workflows). */
export function eventServices(services: readonly IntegrationState[]): IntegrationState[] {
  return services.filter((s) => s.kind === 'tool' || s.kind === 'event_bus' || s.kind === 'workflow' || s.id === 'external-webhook');
}

export type ExpiryState = 'none' | 'ok' | 'soon' | 'expired';
export interface PermissionRow {
  id: string; name: string; configured: boolean; source: string; scopes: string[]; expiresAt: string | null; expiry: ExpiryState;
  workspaceToken: boolean | null; status: IntegrationState['status'];
}

/** Permissions: what each service is allowed to do, with which credential, and until when. */
export function permissionRows(services: readonly IntegrationState[], now = Date.now()): PermissionRow[] {
  return services.map((s) => ({
    id: s.id, name: s.name, configured: s.credentials.configured, source: s.credentials.source, scopes: s.credentials.scopes,
    expiresAt: s.credentials.expires_at, expiry: expiryState(s.credentials.expires_at, now),
    workspaceToken: s.accepts_workspace_credential ? Boolean(s.workspace_credential?.configured) : null, status: s.status,
  }));
}

const DAY = 86_400_000;
export function expiryState(expiresAt: string | null, now = Date.now()): ExpiryState {
  if (!expiresAt) return 'none';
  const at = Date.parse(expiresAt);
  if (Number.isNaN(at)) return 'none';
  if (at <= now) return 'expired';
  return at - now <= 30 * DAY ? 'soon' : 'ok';
}

const known = (key: string): key is MessageKey => Object.hasOwn(CATALOGS.en, key);

/** "integration.tested" reads "Connection tested"; an action this client does not know shows as recorded. */
export function logActionLabel(action: string): string {
  const key = `intHub.log.action.${action}`;
  return known(key) ? t(key) : action;
}

/** "success" and "failed" in words; any other outcome as recorded. */
export function logOutcomeLabel(outcome: string): string {
  const key = `intHub.log.outcome.${outcome}`;
  return known(key) ? t(key) : outcome || '-';
}

/** The integration an audit record is about: its setup or service name when this page knows it. */
export function logTargetName(target: string, items: readonly SetupItem[], services: readonly IntegrationState[]): string {
  const service = services.find((s) => s.id === target);
  if (service) return service.name;
  if (items.some((i) => i.id === target)) return itemName(target);
  return target || '-';
}

/** The credential source in words (environment setting, workspace token, none). */
export function sourceLabel(source: string, configured: boolean): string {
  if (!configured) return t('intHub.perm.source.none');
  const key = `intHub.perm.source.${source}`;
  return known(key) ? t(key) : source;
}
