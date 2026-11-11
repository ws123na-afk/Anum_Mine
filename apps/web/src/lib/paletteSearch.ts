// The search palette's record searches beyond tasks, files, email and people (docs/design/anum360.md#web-command-center):
// customers (Business accounts), invoices, documents (the vault), vehicles and meetings, each through
// its module's own list route with its own permission check, five results each. Loaded only when the
// palette first searches (Shell.tsx imports it dynamically), so the main bundle does not grow.
import * as business from './businessApi';
import * as invoicesApi from './businessInvoicesApi';
import * as vault from './vaultApi';
import * as vehicles from './vehiclesApi';
import * as meetings from './meetingsApi';
import { isPermissionDenied } from './errors';
import { invoiceStatusLabel, invoiceTitle } from './invoices';
import { categoryLabel, filterQuery } from './vault';
import { vehicleName } from './vehicles';
import { statusLabel } from './meetings';
import type { PaletteItem, PaletteRecordKind } from './shell';

export const PALETTE_RECORD_LIMIT = 5;

/** A kind the person cannot use answers 403 (or 404 where the module is off): the palette leaves it out instead of showing an error. */
export class KindUnavailable extends Error {}

async function guarded<T>(work: () => Promise<T>): Promise<T> {
  try { return await work(); } catch (error) {
    if (isPermissionDenied(error) || (error as { status?: number }).status === 404) throw new KindUnavailable();
    throw error;
  }
}

const join = (...parts: (string | null | undefined)[]) => parts.filter(Boolean).join(' · ');

export const RECORD_SEARCHES: Record<PaletteRecordKind, (q: string) => Promise<PaletteItem[]>> = {
  customer: (q) => guarded(async () => (await business.listAccounts({ q, limit: PALETTE_RECORD_LIMIT, offset: 0 })).items
    .map((a) => ({ kind: 'customer', id: a.id, label: a.name, detail: join(a.industry, [a.city, a.country].filter(Boolean).join(', ')) }))),
  invoice: (q) => guarded(async () => (await invoicesApi.listInvoices({ q, limit: PALETTE_RECORD_LIMIT, offset: 0 })).items
    .map((i) => ({ kind: 'invoice', id: i.id, label: invoiceTitle(i), detail: join(i.account_name, invoiceStatusLabel(i.status)) }))),
  document: (q) => guarded(async () => (await vault.listDocuments(filterQuery({ q, category: '', access: '', expiry: '' }, 'active', null, PALETTE_RECORD_LIMIT))).items
    .map((d) => ({ kind: 'document', id: d.id, label: d.title, detail: categoryLabel(d.category) }))),
  vehicle: (q) => guarded(async () => (await vehicles.listVehicles(q, false, 0, PALETTE_RECORD_LIMIT)).items
    .map((v) => ({ kind: 'vehicle', id: v.id, label: vehicleName(v), detail: v.registration }))),
  meeting: (q) => guarded(async () => (await meetings.listMeetings({ query: q, offset: 0, limit: PALETTE_RECORD_LIMIT })).items
    .map((m) => ({ kind: 'meeting', id: m.id, label: m.title, detail: statusLabel(m.status) }))),
};
