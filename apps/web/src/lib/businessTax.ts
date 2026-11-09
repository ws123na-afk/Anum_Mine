// Business tax on the web (docs/business-tax.md): labels, the settings form check, the VAT return rows and the ZATCA
// readiness list. Pure, so the unit tests cover it without a browser. The API is the authority: it reads the return
// from the journal, never files anything and refuses every ZATCA submission until the business is onboarded.
import type { TaxBox, TaxBoxSection, TaxCode, TaxJurisdiction, TaxSettings, TaxSettingsInput, TaxWarning } from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

const label = (key: string) => t(key as MessageKey);

export const codeLabel = (code: TaxCode | 'unknown' | string | null | undefined) => label(`tax.code.${code ?? 'unknown'}`);
export const jurisdictionLabel = (value: TaxJurisdiction) => label(`tax.jurisdiction.${value}`);
export const missingLabel = (key: string) => label(`tax.zatca.missing.${key}`);
export const sectionTitle = (section: TaxBoxSection) => label(`tax.section.${section}`);

/** KSA: 15 digits starting and ending with 3. UAE TRN: 15 digits. Other: 3 to 30 plain characters. */
export function numberProblem(jurisdiction: TaxJurisdiction, value: string): MessageKey | null {
  const text = value.replace(/\s+/g, '');
  if (!text) return null;
  if (jurisdiction === 'SA' && !/^3\d{13}3$/.test(text)) return 'tax.problem.saNumber';
  if (jurisdiction === 'AE' && !/^\d{15}$/.test(text)) return 'tax.problem.aeNumber';
  if (jurisdiction === 'OTHER' && !/^[A-Za-z0-9 ./-]{3,30}$/.test(text)) return 'tax.problem.otherNumber';
  return null;
}

export type TaxForm = Omit<TaxSettingsInput, 'version'>;

export function formFrom(settings: TaxSettings | null, country: string | null): TaxForm {
  const jurisdiction: TaxJurisdiction = settings?.jurisdiction ?? (country === 'SA' || country === 'AE' ? country : 'OTHER');
  return {
    jurisdiction, registered: settings?.registered ?? false, registration_number: settings?.registration_number ?? '',
    registration_date: settings?.registration_date ?? null, filing_frequency: settings?.filing_frequency ?? 'quarterly',
    period_start_month: settings?.period_start_month ?? 1, zatca_enabled: settings?.zatca_enabled ?? false, seller_crn: settings?.seller_crn ?? '',
    seller_street: settings?.seller_street ?? '', seller_building: settings?.seller_building ?? '', seller_district: settings?.seller_district ?? '',
    seller_city: settings?.seller_city ?? '', seller_postal_code: settings?.seller_postal_code ?? '',
  };
}

/** Why the settings cannot be saved yet, or null. The API checks the same and more. */
export function settingsProblem(form: TaxForm, today: string): MessageKey | null {
  const wrong = numberProblem(form.jurisdiction, form.registration_number);
  if (wrong) return wrong;
  if (form.registered && !form.registration_number.trim()) return 'tax.problem.needNumber';
  if (form.registration_date && form.registration_date > today) return 'tax.problem.futureDate';
  if (form.zatca_enabled && form.jurisdiction !== 'SA') return 'tax.problem.zatcaSa';
  if (form.zatca_enabled && !form.registered) return 'tax.problem.zatcaRegistered';
  if (form.seller_crn && !/^\d+$/.test(form.seller_crn)) return 'tax.problem.crn';
  return null;
}

export function settingsBody(form: TaxForm, version: number | null): TaxSettingsInput {
  return { ...form, registration_number: form.registration_number.replace(/\s+/g, ''), zatca_enabled: form.jurisdiction === 'SA' && form.zatca_enabled, version };
}

/** Return boxes grouped by section, in the order the API sends them. */
export function boxGroups(boxes: TaxBox[]): { section: TaxBoxSection; rows: TaxBox[] }[] {
  const out: { section: TaxBoxSection; rows: TaxBox[] }[] = [];
  for (const box of boxes) {
    const group = box.section === 'sales_total' ? 'sales' : box.section === 'purchases_total' ? 'purchases' : box.section;
    const last = out[out.length - 1];
    if (last && last.section === group) last.rows.push(box);
    else out.push({ section: group, rows: [box] });
  }
  return out;
}

/** The box label in the reader's language (the API's English label stays the fallback for a layout the web does not know). */
export function boxLabel(jurisdiction: TaxJurisdiction, box: TaxBox): string {
  const key = `tax.box.${jurisdiction}.${box.box}`;
  const text = label(key);
  return text === key ? box.label : text;
}

export function warningText(warning: TaxWarning): string {
  const key = `tax.warning.${warning.code}`;
  const text = t(key as MessageKey, { count: warning.count ?? 0, currencies: (warning.currencies ?? []).join(', ') });
  return text === key ? warning.text : text;
}

/** The return's notes in the reader's language (the API's English notes stay the fallback). */
export function notesFor(jurisdiction: TaxJurisdiction, apiNotes: string[]): string[] {
  const out: string[] = [];
  for (let index = 1; index <= 5; index += 1) {
    const key = `tax.note.${jurisdiction}.${index}`;
    const text = label(key);
    if (text === key) break;
    out.push(text);
  }
  return out.length ? out : apiNotes;
}

export function infoLabel(key: string, fallback: string): string {
  const text = label(`tax.info.${key}`);
  return text === `tax.info.${key}` ? fallback : text;
}

export const hasAdjustments = (boxes: TaxBox[]) => boxes.some((box) => Number(box.adjustment_amount) !== 0 || Number(box.adjustment_vat) !== 0);

/** A short form of a base64 hash for tables (the full value is in the XML and the tooltip). */
export const shortHash = (value: string) => (value.length > 16 ? `${value.slice(0, 8)}...${value.slice(-6)}` : value || '-');
