// Business tax on the web (docs/business-tax.md): labels, the settings form check, the VAT return rows and the ZATCA
// readiness list. Pure, so the unit tests cover it without a browser. The API is the authority: it reads the return
// from the journal, never files anything and refuses every ZATCA submission until the business is onboarded.
import type { TaxBox, TaxBoxSection, TaxCode, TaxDeadlineSource, TaxJurisdiction, TaxSettings, TaxSettingsInput, TaxWarning } from '@anum/contracts';
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

/** The settings form: the filing country and the due day are typed text until saved. */
export type TaxForm = Omit<TaxSettingsInput, 'version' | 'country' | 'filing_due_days'> & { country: string; filing_due_days: string };

/** GCC countries with a built-in VAT return deadline when the jurisdiction is Other, and those without VAT (docs/business-tax.md#filing-deadlines). */
export const PRESET_COUNTRIES = ['BH', 'OM'] as const;
export const NO_VAT_COUNTRIES = ['KW', 'QA'] as const;
export const MAX_DUE_DAYS = 120;

/** Where the deadline would come from for this form, the same order as the API: the owner's due day, a preset, no VAT, none. */
export function deadlineSource(form: Pick<TaxForm, 'jurisdiction' | 'country' | 'filing_due_days'>): TaxDeadlineSource {
  if (form.filing_due_days.trim()) return 'owner';
  if (form.jurisdiction !== 'OTHER') return 'preset';
  const country = form.country.trim().toUpperCase();
  if ((PRESET_COUNTRIES as readonly string[]).includes(country)) return 'preset';
  if ((NO_VAT_COUNTRIES as readonly string[]).includes(country)) return 'no_vat';
  return 'none';
}

/** The built-in deadline in words, or null: KSA and Bahrain the end of the next month, the UAE 28 days, Oman 30 days. */
export function presetKey(form: Pick<TaxForm, 'jurisdiction' | 'country'>): MessageKey | null {
  const country = form.jurisdiction === 'OTHER' ? form.country.trim().toUpperCase() : form.jurisdiction;
  if (country === 'SA' || country === 'BH') return 'gaps.deadline.endOfNextMonth';
  if (country === 'AE') return 'gaps.deadline.days28';
  if (country === 'OM') return 'gaps.deadline.days30';
  return null;
}

export function formFrom(settings: TaxSettings | null, country: string | null): TaxForm {
  const jurisdiction: TaxJurisdiction = settings?.jurisdiction ?? (country === 'SA' || country === 'AE' ? country : 'OTHER');
  return {
    jurisdiction, registered: settings?.registered ?? false, registration_number: settings?.registration_number ?? '',
    registration_date: settings?.registration_date ?? null, filing_frequency: settings?.filing_frequency ?? 'quarterly',
    period_start_month: settings?.period_start_month ?? 1, country: settings ? settings.country ?? '' : (jurisdiction === 'OTHER' && country ? country.toUpperCase() : ''),
    filing_due_days: settings?.filing_due_days != null ? String(settings.filing_due_days) : '', zatca_enabled: settings?.zatca_enabled ?? false, seller_crn: settings?.seller_crn ?? '',
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
  const country = form.country.trim();
  if (form.jurisdiction === 'OTHER' && country && !/^[A-Za-z]{2}$/.test(country)) return 'gaps.problem.country';
  if (form.jurisdiction === 'OTHER' && ['SA', 'AE'].includes(country.toUpperCase())) return 'gaps.problem.countryJurisdiction';
  const due = form.filing_due_days.trim();
  if (due && (!/^\d{1,3}$/.test(due) || Number(due) < 1 || Number(due) > MAX_DUE_DAYS)) return 'gaps.problem.dueDays';
  return null;
}

export function settingsBody(form: TaxForm, version: number | null): TaxSettingsInput {
  const country = form.country.trim().toUpperCase();
  const due = form.filing_due_days.trim();
  return { ...form, registration_number: form.registration_number.replace(/\s+/g, ''), zatca_enabled: form.jurisdiction === 'SA' && form.zatca_enabled,
    country: form.jurisdiction === 'OTHER' && country ? country : null, filing_due_days: due ? Number(due) : null, version };
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
