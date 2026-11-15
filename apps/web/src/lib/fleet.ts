// Running a client fleet on the web (docs/vehicles.md#fleet): fleet costs, the monthly report and the import. Pure helpers
// (the API calls are in fleetApi.ts). Money is never added across currencies; each row of a currency is its own figure.
import type { FleetImportKind, FleetImportRow, FleetRowAction, FleetRowStatus, FleetTotal, FleetVehicleRow } from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

export const FLEET_KINDS: readonly FleetImportKind[] = ['vehicles', 'fuel', 'service', 'odometer'];
export const FLEET_STATUSES: readonly FleetRowStatus[] = ['new', 'exists', 'duplicate_in_file', 'imported', 'invalid'];
export const IMPORT_MAX_ROWS = 5000;
export const IMPORT_MAX_WORKBOOK_BYTES = 5_000_000;
export const IMPORT_MAX_TEXT_BYTES = 6_000_000;
export const IMPORT_PAGE = 50;
export const ROWS_PAGE = 50;
export const NOTES_MAX = 4000;

/** The currency most of the fleet's money is in this month (the KPI row shows it; the table shows every currency). */
export function mainTotal(totals: FleetTotal[]): FleetTotal | null {
  if (!totals.length) return null;
  return [...totals].sort((a, b) => Number(b.total) - Number(a.total) || a.currency.localeCompare(b.currency))[0];
}

export type FleetSort = 'registration' | 'total' | 'per_km';
/** The vehicle rows matching the search (registration, nickname, make, model), sorted. A vehicle's first currency is its sort figure. */
export function fleetRows(rows: FleetVehicleRow[], query: string, sort: FleetSort): FleetVehicleRow[] {
  const needle = query.trim().toLocaleLowerCase();
  const shown = needle ? rows.filter((r) => [r.registration, r.nickname, r.make, r.model].some((v) => (v ?? '').toLocaleLowerCase().includes(needle))) : rows;
  const figure = (r: FleetVehicleRow) => {
    const cost = r.costs.find((c) => c.currency === r.currency) ?? r.costs[0];
    if (!cost) return -1;
    return sort === 'total' ? Number(cost.total) : cost.cost_per_km === null ? -1 : Number(cost.cost_per_km);
  };
  return [...shown].sort((a, b) => sort === 'registration' ? a.registration.localeCompare(b.registration) : figure(b) - figure(a) || a.registration.localeCompare(b.registration));
}

/** The change from the month before, in percent (null when there was nothing before). */
export function percentChange(now: string, before: string): number | null {
  const a = Number(now), b = Number(before);
  if (!Number.isFinite(a) || !Number.isFinite(b) || b === 0) return null;
  return Math.round(((a - b) / b) * 1000) / 10;
}

/** The share of a month's cost that came from record figures rather than linked payments, 0 to 100. */
export function recordedShare(total: FleetTotal): number {
  const all = Number(total.total);
  return all > 0 ? Math.round((Number(total.recorded) / all) * 100) : 0;
}

// ---- import ------------------------------------------------------------------------------------------------------------

export const fieldLabel = (field: string) => { const key = `veh.fleet.field.${field}` as MessageKey; const text = t(key); return text === key ? field : text; };
export const kindLabel = (kind: FleetImportKind) => t(`veh.fleet.kind.${kind}` as MessageKey);
export const statusLabel = (status: FleetRowStatus) => t(`veh.fleet.status.${status}` as MessageKey);
export const actionLabel = (action: FleetRowAction) => t(`veh.fleet.action.${action}` as MessageKey);

/** A row's problems in the reader's language (the server's English sentence for anything it words itself). */
export function rowIssues(row: FleetImportRow): string[] {
  return row.issues.map((issue) => {
    if (issue.code === 'other') return issue.text;
    const key = `veh.fleet.issue.${issue.code}` as MessageKey;
    const text = t(key, { field: issue.field ? fieldLabel(issue.field) : '', value: issue.value, registration: row.registration });
    return text === key ? issue.text : text;
  });
}

export type ImportFormat = 'csv' | 'xlsx';
export function importFormatOf(file: { name: string; type: string }): ImportFormat | null {
  const extension = /\.([^./\\]+)$/.exec(file.name)?.[1]?.toLowerCase();
  if (extension) return extension === 'csv' || extension === 'txt' ? 'csv' : extension === 'xlsx' ? 'xlsx' : null;
  if (/spreadsheetml\.sheet/i.test(file.type)) return 'xlsx';
  return /csv|text\/plain/i.test(file.type) ? 'csv' : null;
}

/** Why a chosen file cannot be sent, in words, or null. */
export function importFileProblem(file: { name: string; size: number; type: string }): string | null {
  const format = importFormatOf(file);
  if (!format) return t('veh.fleet.err.type');
  if (file.size === 0) return t('veh.fleet.err.empty');
  if (format === 'xlsx' ? file.size > IMPORT_MAX_WORKBOOK_BYTES : file.size > IMPORT_MAX_TEXT_BYTES) return t(format === 'xlsx' ? 'veh.fleet.err.workbookSize' : 'veh.fleet.err.size');
  return null;
}

/** Every row's action as the owner left it (the suggestion unless they chose another allowed one). */
export function importDecisions(rows: FleetImportRow[], choices: Record<number, FleetRowAction>): { row: number; action: FleetRowAction }[] {
  return rows.map((row) => ({ row: row.row, action: choices[row.row] && row.actions.includes(choices[row.row]) ? choices[row.row] : row.action }));
}

export function importTotals(decisions: { action: FleetRowAction }[]): Record<FleetRowAction, number> {
  const totals: Record<FleetRowAction, number> = { create: 0, update: 0, skip: 0 };
  for (const d of decisions) totals[d.action] += 1;
  return totals;
}

export function importRowsShown(rows: FleetImportRow[], status: FleetRowStatus | '', query: string): FleetImportRow[] {
  const needle = query.trim().toLocaleLowerCase();
  return rows.filter((row) => (!status || row.status === status)
    && (!needle || row.registration.toLocaleLowerCase().includes(needle) || Object.values(row.summary).some((v) => (v ?? '').toLocaleLowerCase().includes(needle))));
}

/** The header row of a template for each kind (English column names the server recognises), for a CSV the client fills in. */
export const TEMPLATE_HEADERS: Record<FleetImportKind, string[]> = {
  vehicles: ['Registration', 'Country', 'Fleet number', 'Make', 'Model', 'Year', 'VIN', 'Vehicle type', 'Fuel type', 'Tank litres', 'Odometer', 'Odometer date',
    'Insurance expiry', 'Registration expiry', 'Inspection expiry', 'Notes'],
  fuel: ['Registration', 'Date', 'Litres', 'Total', 'Price per litre', 'Odometer', 'Station', 'Fuel type', 'Full tank', 'Currency', 'Notes'],
  service: ['Registration', 'Date', 'Service type', 'Total', 'Odometer', 'Workshop', 'Description', 'Reference', 'Next due date', 'Next due km', 'Currency'],
  odometer: ['Registration', 'Date', 'Odometer'],
};

export function templateCsv(kind: FleetImportKind): string {
  return `${TEMPLATE_HEADERS[kind].join(',')}\r\n`;
}
