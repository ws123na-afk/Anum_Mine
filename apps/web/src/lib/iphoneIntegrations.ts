// Settings, Integrations, iPhone (docs/iphone-integrations.md): the web side of the one device
// enrollment. The API lists the integrations it has (with their switch, permission, address and
// limits); this registry adds what the page says about each one (name, purpose, the exact
// Shortcut steps and the JSON body). A module's PR adds its row to IPHONE_GUIDES (and its words to
// its own message catalog) without editing IphoneIntegrations.tsx. An integration the API lists
// with no row here still shows, with its address and fields.
import type { IphoneEventOutcome, IphoneIntegration, IphoneScope } from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

export interface IphoneGuide {
  key: string;
  title: MessageKey;
  purpose: MessageKey;
  /** The Shortcut automation, step by step; `url` is the full address. */
  steps: (url: string) => string[];
  /** The JSON body lines as `field = value`, shown left to right. */
  body: string[];
  /** Where the module's own settings live (a hash), if it has any. */
  settingsHash?: string;
}

export const IPHONE_GUIDES: IphoneGuide[] = [
  {
    key: 'finance.sms', title: 'iph.int.finance.sms', purpose: 'iph.int.finance.sms.purpose', settingsHash: '#finance-sms',
    steps: (url) => [t('fsms.step.1'), t('fsms.step.2'), t('fsms.step.3'), t('fsms.step.4', { url }), t('fsms.step.5'), t('fsms.step.6')],
    body: ['message = Shortcut Input, Content', 'sender = Shortcut Input, Sender'],
  },
  {
    key: 'finance.wallet', title: 'iph.int.finance.wallet', purpose: 'iph.int.finance.wallet.purpose', settingsHash: '#finance-sms',
    steps: (url) => [t('fsms.wstep.1'), t('fsms.wstep.2', { url }), t('fsms.wstep.3'), t('fsms.wstep.4')],
    body: ['source = apple_wallet', 'amount = Shortcut Input, Amount', 'merchant = Shortcut Input, Merchant', 'card = Shortcut Input, Card or Pass', 'name = Shortcut Input, Name'],
  },
  {
    key: 'attendance.location', title: 'iph.int.attendance.location', purpose: 'iph.int.attendance.location.purpose', settingsHash: '#attendance',
    steps: (url) => [t('iph.att.step.1'), t('iph.att.step.2'), t('iph.att.step.3', { url }), t('iph.att.step.4'), t('iph.att.step.5')],
    body: ['event = arrive (or leave)', 'site = the site name in ANUM', 'idempotency_key = Current Date (ISO 8601) + -arrive', 'occurred_at = Current Date (ISO 8601)'],
  },
  {
    key: 'vehicle.driving', title: 'iph.int.vehicle.driving', purpose: 'iph.int.vehicle.driving.purpose', settingsHash: '#vehicles-settings',
    steps: (url) => [t('iph.veh.step.1'), t('iph.veh.step.2'), t('iph.veh.step.3', { url }), t('iph.veh.step.4'), t('iph.veh.step.5'), t('iph.veh.step.6')],
    body: ['action = start (or end)', 'vehicle = the registration in ANUM (empty: the default vehicle)', 'idempotency_key = Current Date (ISO 8601) + -start',
      'occurred_at = Current Date (ISO 8601)', 'odometer_km = Provided Input (end only, what you typed)', 'fuel_litres, fuel_total, parking_total = optional, end only'],
  },
  {
    key: 'documents.scan', title: 'iph.int.documents.scan', purpose: 'iph.int.documents.scan.purpose', settingsHash: '#vault',
    steps: (url) => [t('iph.doc.step.1'), t('iph.doc.step.2', { url }), t('iph.doc.step.3'), t('iph.doc.step.4')],
    body: ['Request Body = File: the scan, photo or Shortcut Input', 'name = scan.jpg (in the address: ?name=scan.jpg&category=receipt)'],
  },
  {
    key: 'purchases.share', title: 'iph.int.purchases.share', purpose: 'iph.int.purchases.share.purpose', settingsHash: '#purchases-settings',
    steps: (url) => [t('iph.pur.step.1'), t('iph.pur.step.2', { url }), t('iph.pur.step.3'), t('iph.pur.step.4'), t('iph.pur.step.5')],
    body: ['text = Text (the shared order or delivery message)', 'idempotency_key = optional: the order number', 'ownership = personal (or business)',
      'action = save (test: checks the connection, saves nothing)'],
  },
];

export const SCOPES: IphoneScope[] = ['finance.sms', 'finance.wallet', 'vehicle', 'attendance', 'documents', 'purchases'];

export function guideFor(key: string): IphoneGuide | null {
  return IPHONE_GUIDES.find((guide) => guide.key === key) ?? null;
}

export function integrationTitle(integration: Pick<IphoneIntegration, 'key'>): string {
  const guide = guideFor(integration.key);
  return guide ? t(guide.title) : integration.key;
}

export function scopeLabel(scope: string): string {
  const key = `iph.scope.${scope}` as MessageKey;
  const text = t(key);
  return text === key ? scope : text;
}

/** Plain words for a refusal or failure code; an unknown code is shown as it is. */
export function codeLabel(code: string | null): string {
  if (!code) return '-';
  const key = `iph.code.${code}` as MessageKey;
  const text = t(key);
  return text === key ? code : text;
}

export function outcomeLabel(outcome: IphoneEventOutcome): string {
  return t(`iph.outcome.${outcome}` as MessageKey);
}

export function fullUrl(apiBase: string, path: string): string {
  return `${apiBase.replace(/\/+$/, '')}${path}`;
}

/** The JSON the "Test connection" Shortcut sends to the ping address. */
export function pingBody(key: string): string {
  return JSON.stringify({ integration: key });
}

/** Scopes a person may tick when issuing a token: the integrations the server has and their role may use. */
export function issuableScopes(integrations: IphoneIntegration[]): IphoneScope[] {
  const allowed = new Set(integrations.filter((i) => i.allowed).map((i) => i.scope));
  return SCOPES.filter((scope) => allowed.has(scope));
}

export type IntegrationState = 'off' | 'no_device' | 'never' | 'failing' | 'ok';

/** One word for an integration's health: what the owner should look at first. */
export function integrationState(integration: IphoneIntegration): IntegrationState {
  if (!integration.enabled) return 'off';
  if (integration.devices_with_scope === 0) return 'no_device';
  if (!integration.last_connection_at) return 'never';
  if (integration.failed_7d > 0 && (!integration.last_ok_at || integration.last_ok_at < integration.last_connection_at)) return 'failing';
  return 'ok';
}
