// Commerce (docs/commerce.md): pure helpers for the Commerce view. No browser or Vite APIs, so
// `pnpm test:unit` runs them directly.
import type {
  CommerceAlertSeverity, CommerceChannel, CommerceConnectorInput, CommerceDashboard, CommerceOrderStatus, ConnectorHealth, OwnStoreMode,
} from '@anum/contracts';
import { getLocale, intlLocale, t, type MessageKey } from './i18n.ts';

export const CHANNELS: CommerceChannel[] = ['amazon', 'noon', 'own_store'];
export const AMAZON_MARKETPLACES = [
  { id: 'A17E79C6D8DWNP', name: 'Amazon.sa', currency: 'SAR' },
  { id: 'A2VIGQ35RCS4UG', name: 'Amazon.ae', currency: 'AED' },
] as const;
export const NOON_MARKETPLACES = [
  { id: 'SA', name: 'noon Saudi', currency: 'SAR' },
  { id: 'AE', name: 'noon UAE', currency: 'AED' },
] as const;
export const OWN_STORE_MODES: OwnStoreMode[] = ['anum360', 'rest', 'supabase', 'webhook'];

/** Series colours, fixed per channel (validated for the dark chart surface; never by rank). */
export const CHANNEL_COLORS: Record<CommerceChannel, string> = { amazon: '#0284c7', noon: '#7c3aed', own_store: '#db2777' };

export const channelLabel = (channel: CommerceChannel): string => t(`commerce.channel.${channel}` as MessageKey);
export const orderStatusLabel = (status: CommerceOrderStatus | 'open'): string => t(`commerce.orderStatus.${status}` as MessageKey);
export const healthLabel = (health: ConnectorHealth): string => t(`commerce.health.${health}` as MessageKey);

export type Tone = 'success' | 'warning' | 'dangerTone' | 'neutral';

export function healthTone(health: ConnectorHealth): Tone {
  if (health === 'ok') return 'success';
  if (health === 'auth_failed' || health === 'error') return 'dangerTone';
  if (health === 'disabled') return 'neutral';
  return 'warning';
}

export function orderTone(status: CommerceOrderStatus, late = false): Tone {
  if (late) return 'dangerTone';
  if (status === 'delivered' || status === 'shipped') return 'success';
  if (status === 'cancelled' || status === 'returned') return 'neutral';
  return 'warning';
}

export function severityTone(severity: CommerceAlertSeverity): Tone {
  return severity === 'critical' ? 'dangerTone' : severity === 'warning' ? 'warning' : 'neutral';
}

/** "SAR 1,234.50" in the interface language (Western digits in Arabic too). */
export function formatMoney(amount: number | null | undefined, currency: string | null | undefined, locale = getLocale()): string {
  if (amount === null || amount === undefined || Number.isNaN(amount)) return '-';
  const code = currency && /^[A-Z]{3}$/.test(currency) ? currency : null;
  if (!code) return new Intl.NumberFormat(intlLocale(locale), { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount);
  return new Intl.NumberFormat(intlLocale(locale), { style: 'currency', currency: code, currencyDisplay: 'code', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount);
}

export function formatCount(value: number, locale = getLocale()): string {
  return new Intl.NumberFormat(intlLocale(locale)).format(value);
}

export type RangePreset = 'today' | '7d' | '30d' | '90d';
export const RANGE_PRESETS: RangePreset[] = ['today', '7d', '30d', '90d'];

function isoDay(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

/** The from/to dates (inclusive, local days) for a preset. */
export function presetRange(preset: RangePreset, today: Date = new Date()): { from: string; to: string } {
  const days = preset === 'today' ? 0 : preset === '7d' ? 6 : preset === '30d' ? 29 : 89;
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - days);
  return { from: isoDay(start), to: isoDay(today) };
}

/** Every day from `from` to `to` (YYYY-MM-DD), so empty days show as gaps, not as missing bars. */
export function daysBetween(from: string, to: string): string[] {
  const [fy, fm, fd] = from.split('-').map(Number);
  const [ty, tm, td] = to.split('-').map(Number);
  const out: string[] = [];
  const cursor = new Date(fy, fm - 1, fd);
  const end = new Date(ty, tm - 1, td);
  while (cursor <= end && out.length < 400) { out.push(isoDay(cursor)); cursor.setDate(cursor.getDate() + 1); }
  return out;
}

export interface ChartDay { date: string; total: number; parts: { channel: CommerceChannel; revenue: number; orders: number }[] }

/** Revenue per day stacked by channel, in the dashboard's currency. */
export function salesChart(dashboard: Pick<CommerceDashboard, 'by_day' | 'start' | 'end'>): { days: ChartDay[]; max: number; channels: CommerceChannel[] } {
  const byKey = new Map(dashboard.by_day.map((row) => [`${row.date}|${row.channel}`, row]));
  const present = CHANNELS.filter((channel) => dashboard.by_day.some((row) => row.channel === channel));
  const days = daysBetween(dashboard.start, dashboard.end).map((date) => {
    const parts = present.map((channel) => {
      const row = byKey.get(`${date}|${channel}`);
      return { channel, revenue: row?.revenue ?? 0, orders: row?.orders ?? 0 };
    });
    return { date, parts, total: parts.reduce((sum, part) => sum + part.revenue, 0) };
  });
  return { days, max: Math.max(0, ...days.map((day) => day.total)), channels: present };
}

/** A round axis maximum (1, 2, 2.5 or 5 times a power of ten) at or above `value`. */
export function niceMax(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) if (step * power >= value) return step * power;
  return 10 * power;
}

export type Result<T> = { ok: true; value: T } | { ok: false; error: string };

export interface ConnectorForm {
  channel: CommerceChannel;
  name: string;
  marketplaces: string[];
  sellerId: string;
  fetchBuyerDetails: boolean;
  lwaClientId: string;
  lwaClientSecret: string;
  refreshToken: string;
  apiKey: string;
  partnerId: string;
  mode: OwnStoreMode;
  baseUrl: string;
  email: string;
  password: string;
  currency: string;
}

export function emptyConnectorForm(channel: CommerceChannel = 'amazon'): ConnectorForm {
  return {
    channel, name: '', marketplaces: channel === 'amazon' ? AMAZON_MARKETPLACES.map((m) => m.id) : channel === 'noon' ? NOON_MARKETPLACES.map((m) => m.id) : [],
    sellerId: '', fetchBuyerDetails: false, lwaClientId: '', lwaClientSecret: '', refreshToken: '', apiKey: '', partnerId: '',
    mode: 'anum360', baseUrl: '', email: '', password: '', currency: 'AED',
  };
}

const need = (value: string) => value.trim().length > 0;

/** The POST /connectors body, or what to fix. `editing` keeps saved credentials when fields are left empty. */
export function connectorBody(form: ConnectorForm, editing = false): Result<CommerceConnectorInput> {
  const name = form.name.trim();
  if (name.length > 120) return { ok: false, error: t('commerce.form.error.name') };
  const body: CommerceConnectorInput = editing ? {} : { channel: form.channel };
  if (name) body.name = name;
  const credentials: Record<string, string> = {};
  const put = (key: string, value: string) => { if (need(value)) credentials[key] = value.trim(); };
  if (form.channel === 'amazon') {
    if (!form.marketplaces.length) return { ok: false, error: t('commerce.form.error.marketplace') };
    if (!/^[A-Za-z0-9]{8,20}$/.test(form.sellerId.trim())) return { ok: false, error: t('commerce.form.error.sellerId') };
    if (!editing && !(need(form.lwaClientId) && need(form.lwaClientSecret) && need(form.refreshToken))) return { ok: false, error: t('commerce.form.error.amazonCredentials') };
    body.marketplaces = form.marketplaces;
    body.settings = { seller_id: form.sellerId.trim().toUpperCase(), fetch_buyer_details: form.fetchBuyerDetails };
    put('lwa_client_id', form.lwaClientId); put('lwa_client_secret', form.lwaClientSecret); put('refresh_token', form.refreshToken);
  } else if (form.channel === 'noon') {
    if (!form.marketplaces.length) return { ok: false, error: t('commerce.form.error.marketplace') };
    if (!editing && !need(form.apiKey)) return { ok: false, error: t('commerce.form.error.apiKey') };
    body.marketplaces = form.marketplaces;
    body.settings = form.partnerId.trim() ? { partner_id: form.partnerId.trim() } : {};
    put('api_key', form.apiKey);
  } else {
    const settings: Record<string, unknown> = { mode: form.mode, currency: form.currency.trim().toUpperCase() || 'SAR' };
    if (!/^[A-Z]{3}$/.test(String(settings.currency))) return { ok: false, error: t('commerce.form.error.currency') };
    if (form.mode !== 'webhook') {
      if (!/^https?:\/\/\S+$/i.test(form.baseUrl.trim())) return { ok: false, error: t('commerce.form.error.baseUrl') };
      settings.base_url = form.baseUrl.trim();
    }
    if (form.mode === 'anum360') {
      if (!editing && !(need(form.email) && need(form.password))) return { ok: false, error: t('commerce.form.error.staffAccount') };
      put('email', form.email); put('password', form.password);
    } else if (form.mode !== 'webhook') {
      if (!editing && !need(form.apiKey)) return { ok: false, error: t('commerce.form.error.apiKey') };
      put('api_key', form.apiKey);
    }
    body.settings = settings;
  }
  if (Object.keys(credentials).length) body.credentials = credentials;
  return { ok: true, value: body };
}

/** A new price from the input box: above 0, at most two decimals. */
export function parsePrice(value: string): Result<number> {
  const normalized = value.trim().replace(',', '.');
  if (!/^\d{1,7}(\.\d{1,2})?$/.test(normalized) || Number(normalized) <= 0) return { ok: false, error: t('commerce.change.error.price') };
  return { ok: true, value: Number(normalized) };
}

/** A new stock quantity: a whole number from 0. */
export function parseQuantity(value: string): Result<number> {
  if (!/^\d{1,7}$/.test(value.trim())) return { ok: false, error: t('commerce.change.error.quantity') };
  return { ok: true, value: Number(value.trim()) };
}

/** The display title in the interface language: the Arabic title when there is one and the page is Arabic. */
export function displayTitle(item: { title: string; title_ar?: string | null }, locale = getLocale()): string {
  return (locale === 'ar' && item.title_ar) || item.title || item.title_ar || '';
}

export interface CommerceAlertEvent { type: string; payload?: Record<string, unknown> }
export interface AlertPreferences { commerce_alerts?: boolean; desktop_enabled?: boolean }

/** A `commerce.alert.raised` event becomes a desktop notification only when the person wants both. */
export function notificationFor(event: CommerceAlertEvent, prefs: AlertPreferences | null): { title: string; body: string } | null {
  if (event.type !== 'commerce.alert.raised' || !prefs || prefs.commerce_alerts === false || prefs.desktop_enabled === false) return null;
  const title = typeof event.payload?.title === 'string' && event.payload.title ? event.payload.title : t('commerce.notify.fallback');
  return { title: t('commerce.notify.title'), body: title };
}

/** Page window text: "21 to 40 of 132". */
export function pageLabel(offset: number, count: number, total: number): string {
  if (!total) return t('commerce.page.none');
  return t('commerce.page.window', { first: offset + 1, last: offset + count, total });
}
