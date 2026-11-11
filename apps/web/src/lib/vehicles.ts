// Vehicles, fuel and mileage on the web (docs/vehicles.md#web): tabs, labels and small pure helpers.
// Money is never added across currencies, and a cost counts only when its payment link is active.
import type {
  VehicleDueItem, VehicleFuelType, VehicleMoney, VehiclePayment, VehiclePaymentMethod, VehicleServiceKind, VehicleTripPurpose,
} from '@anum/contracts';
import { formatAmount } from './finance.ts';
import { t, type MessageKey } from './i18n.ts';

export const VEHICLE_TABS = ['overview', 'list', 'settings'] as const;
export type VehicleTab = (typeof VEHICLE_TABS)[number];
export const VEHICLE_DETAIL_TABS = ['fuel', 'trips', 'service', 'odometer', 'report', 'details'] as const;
export type VehicleDetailTab = (typeof VEHICLE_DETAIL_TABS)[number];
export const PAGE_SIZE = 20;

export function tabFromHash(hash: string): VehicleTab {
  const id = hash.replace(/^#/, '').split('?')[0];
  const tab = id.startsWith('vehicles-') ? id.slice('vehicles-'.length) : 'overview';
  return (VEHICLE_TABS as readonly string[]).includes(tab) ? (tab as VehicleTab) : 'overview';
}

/** The vehicle a link opens (`#vehicles?vehicle=<id>`, the search palette); null for any other hash. */
export function vehicleFromHash(hash: string): string | null {
  const [path, search] = hash.replace(/^#/, '').split('?');
  if (path !== 'vehicles' || !search) return null;
  const value = new URLSearchParams(search).get('vehicle');
  return value && /^[A-Za-z0-9_.:-]{1,120}$/.test(value) ? value : null;
}

export function hashForTab(tab: VehicleTab): string {
  return tab === 'overview' ? '#vehicles' : `#vehicles-${tab}`;
}

const label = (prefix: string, value: string) => {
  const key = `${prefix}.${value}` as MessageKey;
  const text = t(key);
  return text === key ? value : text;
};
export const fuelLabel = (value: VehicleFuelType | string) => label('veh.fuel', value);
export const serviceLabel = (value: VehicleServiceKind | string) => label('veh.service', value);
export const methodLabel = (value: VehiclePaymentMethod | string | null) => (value ? label('veh.method', value) : '-');
export const purposeLabel = (value: VehicleTripPurpose | string) => label('veh.purpose', value);
export const typeLabel = (value: string) => label('veh.type', value);
export const anomalyLabel = (value: string) => label('veh.anomaly', value);
export const dueLabel = (item: VehicleDueItem) => item.kind === 'service' ? serviceLabel(item.service_kind ?? 'maintenance') : label('veh.due', item.kind);

/** Kilometres with one decimal at most, grouped for the locale, never a dash-based range. */
export function km(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '-';
  const number = Number(value);
  if (!Number.isFinite(number)) return '-';
  return t('veh.km', { value: new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(number) });
}

export function litres(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '-';
  return t('veh.litres', { value: new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(Number(value)) });
}

/** One line per currency; never a sum across currencies. */
export function moneyList(rows: readonly VehicleMoney[] | null | undefined): string {
  if (!rows || rows.length === 0) return '-';
  return rows.map((row) => formatAmount(row.amount, row.currency)).join(' · ');
}

export type PaymentState = 'none' | 'active' | 'needs_review' | 'void';

export function paymentState(payment: VehiclePayment | null | undefined): PaymentState {
  return payment ? payment.status : 'none';
}

/** What the payment cell says: the linked amount, or why there is none. */
export function paymentText(payment: VehiclePayment | null | undefined): string {
  const state = paymentState(payment);
  if (state === 'none') return t('veh.pay.none');
  if (state === 'void') {
    if (payment?.status_reason === 'allocation_removed') return t('veh.pay.allocationRemoved');
    return payment?.status_reason === 'transaction_undone' ? t('veh.pay.undone') : t('veh.pay.removed');
  }
  const amount = formatAmount(payment!.amount, payment!.currency);
  if (payment!.source === 'allocation') return t('veh.pay.allocated', { amount });
  return state === 'needs_review' ? t('veh.pay.review', { amount }) : amount;
}

/** Days left in words: today, overdue or in N days. */
export function dueText(item: VehicleDueItem): string {
  if (item.days_left === null) return item.km_left !== null ? t('veh.due.km', { km: km(item.km_left) }) : '-';
  if (item.days_left < 0) return t('veh.due.overdue', { count: -item.days_left });
  if (item.days_left === 0) return t('veh.due.today');
  return t('veh.due.in', { count: item.days_left });
}

export function vehicleName(v: { nickname: string | null; make: string | null; model: string | null; year: number | null; registration: string }): string {
  const model = [v.make, v.model].filter(Boolean).join(' ');
  return v.nickname || model || v.registration;
}

/** A datetime-local value (local time, minutes) for an input, from an instant. */
export function localInput(date: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** An ISO instant from a datetime-local value; null when empty or not a date. */
export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export function today(): string {
  return localInput().slice(0, 10);
}

/** A positive decimal as typed (dot or Arabic digits and separators), or null. */
export function decimal(value: string, places = 2): string | null {
  const western = value.trim().replace(/[٠-٩]/g, (d) => String('٠١٢٣٤٥٦٧٨٩'.indexOf(d))).replace('٫', '.').replace(/,/g, '');
  if (!/^\d+(\.\d+)?$/.test(western)) return null;
  const [whole, fraction = ''] = western.split('.');
  if (fraction.length > places) return null;
  return fraction ? `${whole}.${fraction}` : whole;
}

/** A new idempotency key for one press of a save button. */
export function newKey(prefix = 'veh'): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}
