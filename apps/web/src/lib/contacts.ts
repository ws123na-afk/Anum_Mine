// The Contacts directory (docs/contacts.md#web): wire types, the hash of each tab and open person,
// the person form's checks and plain words. Pure, so `pnpm --filter @anum/web test:unit` covers it.
import { t, type MessageKey } from './i18n.ts';

export type LinkKind = 'call_contact' | 'business_contact' | 'whatsapp_contact';
export type PersonSource = 'manual' | 'calls' | 'business' | 'whatsapp' | 'merge';
export type Channel = 'phone' | 'email' | 'whatsapp' | 'all';
export type ContactChannel = 'phone' | 'email' | 'whatsapp';
export type DncFilter = '' | 'any' | 'phone' | 'email' | 'whatsapp' | 'none';
export type SortKey = 'name' | 'updated' | 'created';

export const CONTACT_TABS = ['people', 'duplicates', 'import'] as const;
export type ContactTab = (typeof CONTACT_TABS)[number];
export const SOURCES: readonly PersonSource[] = ['manual', 'calls', 'business', 'whatsapp', 'merge'];
export const CHANNELS: readonly Channel[] = ['phone', 'email', 'whatsapp', 'all'];
export const DNC_FILTERS: readonly DncFilter[] = ['', 'any', 'phone', 'email', 'whatsapp', 'none'];
export const SORTS: readonly SortKey[] = ['name', 'updated', 'created'];
export const PAGE_SIZE = 25;

export interface IdentifierView { kind: 'phone' | 'email'; value: string; label: string }
export interface DoNotContactView { channel: Channel; reason: string; set_by: string; set_at: string }
export interface PersonView {
  id: string; name: string; organisation: string; notes: string; source: PersonSource;
  phones: IdentifierView[]; emails: IdentifierView[]; sources: LinkKind[]; do_not_contact: DoNotContactView[];
  open_proposals: number; created_by: string; created_at: string; updated_at: string;
}
export interface LinkView { id: string; kind: LinkKind; source_id: string; found: boolean; title: string; detail: string; href: string; facts: Record<string, unknown> }
export type FactSource = 'calls' | 'business' | 'whatsapp' | 'consent_records' | 'suppression' | 'directory';
export type FactState = 'granted' | 'withdrawn' | 'unknown' | 'none' | 'blocked' | 'customer_wrote' | 'no_message_yet';
export interface ConsentFact { source: FactSource; state: FactState; detail: string; at: string | null }
export interface ChannelSummary { channel: ContactChannel; identifiers: string[]; do_not_contact: DoNotContactView | null; facts: ConsentFact[]; can_contact: boolean }
export interface MailContactView { id: string; email: string; name: string; href: string }
export interface ProposalView {
  id: string; reason: 'same_phone' | 'same_email'; matched_value: string; status: 'open' | 'dismissed' | 'merged';
  a: PersonView | null; b: PersonView | null; created_at: string; decided_by: string | null; decided_at: string | null;
}
export interface PersonDetail { person: PersonView; links: LinkView[]; channels: ChannelSummary[]; proposals: ProposalView[]; mail_contacts: MailContactView[]; can_write: boolean; can_manage: boolean }
export interface Counts { people: number; do_not_contact: number; open_proposals: number; by_source: Record<string, number> }
export interface PeoplePage { items: PersonView[]; total: number; counts: Counts; can_write: boolean; can_manage: boolean; can_export: boolean }
export interface ProposalPage { items: ProposalView[]; total: number; can_write: boolean }
export interface BuildReport { scanned: number; linked: number; people_created: number; proposals_created: number; complete: boolean }

export interface PeopleFilters { q: string; source: PersonSource | ''; dnc: DncFilter; sort: SortKey }
export const NO_FILTERS: PeopleFilters = { q: '', source: '', dnc: '', sort: 'name' };
export const filtered = (f: PeopleFilters): boolean => Boolean(f.q.trim() || f.source || f.dnc);

// ---------------------------------------------------------------- hashes

/** `#contacts`, `#contacts-duplicates`, `#contacts-import`, `#contacts?person=<id>`. */
export function parseContactsHash(hash: string): { tab: ContactTab; person: string | null } {
  const [id, search = ''] = hash.replace(/^#/, '').split('?');
  const tab = id === 'contacts-duplicates' ? 'duplicates' : id === 'contacts-import' ? 'import' : 'people';
  const person = new URLSearchParams(search).get('person');
  return { tab, person: person && person.trim() ? person : null };
}
export const tabFromHash = (hash: string): ContactTab => parseContactsHash(hash).tab;
export function contactsHash(tab: ContactTab, person: string | null = null): string {
  const base = tab === 'people' ? '#contacts' : `#contacts-${tab}`;
  return person ? `${base}?person=${encodeURIComponent(person)}` : base;
}

// ---------------------------------------------------------------- the person form

export interface PersonDraft { name: string; organisation: string; notes: string; phones: string; emails: string }
export const emptyPersonDraft = (): PersonDraft => ({ name: '', organisation: '', notes: '', phones: '', emails: '' });
export const draftOf = (p: PersonView): PersonDraft => ({
  name: p.name, organisation: p.organisation, notes: p.notes,
  phones: p.phones.map((i) => i.label || i.value).join('\n'), emails: p.emails.map((i) => i.label || i.value).join('\n'),
});
export interface PersonBody { name: string; organisation: string; notes: string; phones: string[]; emails: string[] }
export type Result<T> = { ok: true; value: T } | { ok: false; error: string };

/** One value per line or comma; blanks dropped. */
export const splitValues = (text: string): string[] => text.split(/[\n,;]+/).map((v) => v.trim()).filter(Boolean);

/** A phone number by the API's rule: 8 to 15 digits once the formatting is gone (`00` and UAE `05x` are read too). */
export function looksLikePhone(value: string): boolean {
  const digits = value.replace(/\D/g, '');
  return digits.length >= 8 && digits.length <= 15;
}
export const looksLikeEmail = (value: string): boolean => /^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$/.test(value.trim());

/** The body of `POST /contacts` or `PATCH /contacts/{id}`, or the first problem in plain words. */
export function personBody(draft: PersonDraft): Result<PersonBody> {
  const name = draft.name.trim().replace(/\s+/g, ' ');
  if (!name || name.length > 200) return { ok: false, error: t('ct.err.name') };
  const phones = splitValues(draft.phones), emails = splitValues(draft.emails);
  if (phones.length > 10 || emails.length > 10) return { ok: false, error: t('ct.err.tooMany') };
  const badPhone = phones.find((v) => !looksLikePhone(v));
  if (badPhone) return { ok: false, error: t('ct.err.phone', { value: badPhone }) };
  const badEmail = emails.find((v) => !looksLikeEmail(v));
  if (badEmail) return { ok: false, error: t('ct.err.email', { value: badEmail }) };
  return { ok: true, value: { name, organisation: draft.organisation.trim(), notes: draft.notes.trim(), phones, emails } };
}

/** A do-not-contact reason, as the API requires it (3 to 300 characters). */
export function reasonText(reason: string): Result<string> {
  const text = reason.trim();
  return text.length >= 3 && text.length <= 300 ? { ok: true, value: text } : { ok: false, error: t('ct.err.reason') };
}

// ---------------------------------------------------------------- words

const label = (key: string): string => t(key as MessageKey);
export const sourceLabel = (source: string): string => (SOURCES.includes(source as PersonSource) ? label(`ct.source.${source}`) : source);
export const linkKindLabel = (kind: string): string => (['call_contact', 'business_contact', 'whatsapp_contact'].includes(kind) ? label(`ct.link.${kind}`) : kind);
export const channelLabel = (channel: string): string => (CHANNELS.includes(channel as Channel) ? label(`ct.channel.${channel}`) : channel);
export const tabLabel = (tab: ContactTab): string => label(`ct.tab.${tab}`);
export const sortLabel = (sort: SortKey): string => label(`ct.sort.${sort}`);
export const dncFilterLabel = (value: DncFilter): string => label(`ct.dncFilter.${value || 'all'}`);
export const reasonLabel = (reason: ProposalView['reason']): string => label(`ct.proposal.${reason}`);

/** One consent fact in words ("Phone calls: consent to AI calls recorded"). */
export function factText(fact: ConsentFact): string {
  const key = `ct.fact.${fact.source}.${fact.state}` as MessageKey;
  const text = t(key, { detail: fact.detail });
  return text === key ? `${fact.source}: ${fact.state}` : text;
}

/** The channels a person is blocked on, for the list row ("Do not contact: phone, e-mail"). */
export function blockedChannels(marks: DoNotContactView[]): ContactChannel[] {
  const channels = new Set(marks.map((m) => m.channel));
  if (channels.has('all')) return ['phone', 'email', 'whatsapp'];
  return (['phone', 'email', 'whatsapp'] as const).filter((c) => channels.has(c));
}

/** The CSV file name from Content-Disposition, or the default. */
export function exportName(disposition: string | null): string {
  const name = disposition?.match(/filename="?([^";]+)"?/i)?.[1]?.trim();
  return name && /^[\w.@-]+$/.test(name) ? name : 'contacts.csv';
}

// ---------------------------------------------------------------- import from a CSV file (docs/contacts.md#import-from-a-csv-file)

export type ImportAction = 'create' | 'update' | 'skip';
export type ImportStatus = 'new' | 'match' | 'possible_duplicate' | 'duplicate_in_file' | 'invalid';
export const IMPORT_STATUSES: readonly ImportStatus[] = ['new', 'match', 'possible_duplicate', 'duplicate_in_file', 'invalid'];
export const IMPORT_PAGE = 50;
/** The API reads at most this much text and this many rows (`contacts/importer.py`). */
export const IMPORT_MAX_CHARS = 2_000_000;
export const IMPORT_MAX_ROWS = 2000;

export interface ImportMapping { name: string[]; organisation: string | null; notes: string | null; phones: string[]; emails: string[]; do_not_contact: string | null }
export interface ImportMatch { id: string; name: string; shared: string[]; do_not_contact: Channel[] }
export type ImportIssueCode = 'control' | 'name_long' | 'organisation_long' | 'notes_long' | 'not_phone' | 'not_email' | 'too_many' | 'bad_dnc' | 'empty';
export interface ImportIssue { code: ImportIssueCode; value: string }
export interface ImportRow {
  row: number; name: string; organisation: string; phones: string[]; emails: string[]; do_not_contact: Channel[];
  status: ImportStatus; action: ImportAction; actions: ImportAction[]; target_id: string | null; matches: ImportMatch[];
  duplicate_of: number | null; problems: string[]; issues?: ImportIssue[];
}
export interface ImportCounts { rows: number; new: number; match: number; possible_duplicate: number; duplicate_in_file: number; invalid: number }
export interface ImportPreview { columns: string[]; mapping: ImportMapping; suggested: boolean; rows: ImportRow[]; counts: ImportCounts; fingerprint: string; delimiter: string }
export interface ImportResult { created: number; updated: number; skipped: number; marks_added: number; proposals_created: number; rows: number }
/** The owner's choice for a row, when it differs from the suggestion. */
export interface ImportChoice { action: ImportAction; target_id: string | null }
export interface ImportDecision { row: number; action: ImportAction; target_id: string | null }

export const emptyMapping = (): ImportMapping => ({ name: [], organisation: null, notes: null, phones: [], emails: [], do_not_contact: null });

/** The formats the API reads (`contacts/importer.py`, `contacts/import_files.py`). */
export type ImportFormat = 'csv' | 'xlsx' | 'vcf';
/** The API reads an Excel workbook of at most this many bytes. */
export const IMPORT_MAX_WORKBOOK_BYTES = 2_000_000;

/** The format of a chosen file: the extension decides; a name without one falls back to the type the browser gives. */
export function importFormatOf(file: { name: string; type: string }): ImportFormat | null {
  const extension = /\.([^./\\]+)$/.exec(file.name)?.[1]?.toLowerCase();
  if (extension) {
    if (extension === 'csv' || extension === 'txt') return 'csv';
    if (extension === 'xlsx') return 'xlsx';
    if (extension === 'vcf' || extension === 'vcard') return 'vcf';
    return null;
  }
  if (/spreadsheetml\.sheet/i.test(file.type)) return 'xlsx';
  if (/vcard/i.test(file.type)) return 'vcf';
  if (/csv|text\/plain/i.test(file.type)) return 'csv';
  return null;
}

/** The reason a chosen file cannot be imported, before it is read, or null. */
export function importFileProblem(file: { name: string; size: number; type: string }): string | null {
  const format = importFormatOf(file);
  if (!format) return t('ct.import.err.type');
  if (file.size === 0) return t('ct.import.err.empty');
  if (format === 'xlsx' ? file.size > IMPORT_MAX_WORKBOOK_BYTES : file.size > IMPORT_MAX_CHARS * 2) return t(format === 'xlsx' ? 'ct.import.err.workbookSize' : 'ct.import.err.size');
  return null;
}

/** A file read for the import: its text (CSV, vCard) or the workbook's bytes as base64 (Excel). */
export interface ImportSource { name: string; format: ImportFormat; content: string }

/** The request body that carries the file: CSV as before (`csv`), vCard text in `csv` with its format, Excel in `file_base64`. */
export function importFileBody(source: ImportSource): Record<string, string> {
  if (source.format === 'xlsx') return { format: 'xlsx', file_base64: source.content, file_name: source.name };
  if (source.format === 'vcf') return { format: 'vcf', csv: source.content, file_name: source.name };
  return { csv: source.content, file_name: source.name };
}

/** Bytes as base64, in chunks so a large workbook never overflows the call stack. */
export function bytesToBase64(bytes: Uint8Array): string {
  let text = '';
  for (let start = 0; start < bytes.length; start += 0x8000) text += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  return btoa(text);
}

/** Every row's action as the owner left it (the suggestion unless they chose another), sent with the import. */
export function importDecisions(rows: ImportRow[], choices: Record<number, ImportChoice>): ImportDecision[] {
  return rows.map((row) => {
    const choice = choices[row.row];
    const action = choice && row.actions.includes(choice.action) ? choice.action : row.action;
    const target = action === 'update' ? (choice?.target_id ?? row.target_id ?? row.matches[0]?.id ?? null) : null;
    return { row: row.row, action, target_id: target };
  });
}

/** What the import would do, counted: people added, updated, skipped. */
export function importTotals(decisions: ImportDecision[]): { create: number; update: number; skip: number } {
  const totals = { create: 0, update: 0, skip: 0 };
  for (const d of decisions) totals[d.action] += 1;
  return totals;
}

/** Rows shown under a status filter and a search over name, numbers and addresses. */
export function importRowsShown(rows: ImportRow[], status: ImportStatus | '', query: string): ImportRow[] {
  const q = query.trim().toLowerCase();
  return rows.filter((row) => (!status || row.status === status)
    && (!q || row.name.toLowerCase().includes(q) || row.phones.some((v) => v.includes(q)) || row.emails.some((v) => v.includes(q)) || String(row.row) === q));
}

export const importStatusLabel = (status: ImportStatus): string => label(`ct.import.status.${status}`);
export const importActionLabel = (action: ImportAction): string => label(`ct.import.action.${action}`);
export const importFieldLabel = (field: string): string => label(`ct.import.field.${field}`);

/** Why a row cannot be imported, in the reader's language (the API's English when a code is unknown). */
export function importIssues(row: ImportRow): string[] {
  if (!row.issues?.length) return row.problems;
  return row.issues.map((issue, index) => {
    const key = `ct.import.issue.${issue.code}` as MessageKey;
    const text = t(key, { value: issue.value });
    return text === key ? (row.problems[index] ?? issue.code) : text;
  });
}
