// The Document vault page's model (docs/documents.md#the-web-page): tabs and their links, labels for
// categories, expiry and warnings, the edit form's diff and the review form's checks. Pure, so
// `node --test` covers it without a browser.
import type {
  DocumentExpiryState, DocumentExtraction, VaultCategory, VaultDocument, VaultDocumentPatch, VaultMeta,
} from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

export type {
  DocumentExtraction, VaultCategory, VaultDevice, VaultDeviceCreated, VaultDocument, VaultDocumentDetail, VaultDocumentPage, VaultMeta,
  VaultReviewItem, VaultSettings, VaultVersionPage, DocumentLink, DocumentLinkTarget, DocumentAccessGrant,
} from '@anum/contracts';

export const VAULT_TABS = ['overview', 'documents', 'review', 'expiring', 'trash', 'settings'] as const;
export type VaultTab = (typeof VAULT_TABS)[number];
export const PAGE_SIZE = 50;
export const CURRENCIES = ['SAR', 'AED', 'USD', 'EUR', 'GBP', 'KWD', 'QAR', 'BHD', 'OMR', 'EGP', 'JOD'] as const;
export const RECEIPT_FIELDS = ['merchant', 'date', 'total', 'currency', 'tax', 'reference'] as const;
export const DATE_FIELDS = ['issue_date', 'expiry_date'] as const;
export type ReviewField = (typeof RECEIPT_FIELDS)[number] | (typeof DATE_FIELDS)[number];

/** `#vault`, `#vault-review` ...: the tab a link opens. */
export function tabFromHash(hash: string): VaultTab {
  const id = hash.replace(/^#/, '');
  const tab = id.startsWith('vault-') ? id.slice(6) : 'overview';
  return (VAULT_TABS as readonly string[]).includes(tab) ? tab as VaultTab : 'overview';
}
export const hashOf = (tab: VaultTab) => (tab === 'overview' ? '#vault' : `#vault-${tab}`);

export function categoryLabel(id: string, categories: readonly VaultCategory[] = []): string {
  const found = categories.find((item) => item.id === id);
  if (found && !found.built_in && found.label) return found.label;
  if (found?.built_in || !id.startsWith('vcat_')) return t(`vault.cat.${id}` as MessageKey);
  return t('vault.cat.unknown');
}

export function expiryText(document: Pick<VaultDocument, 'expiry_state' | 'days_left' | 'expiry_date'>): string {
  const state: DocumentExpiryState = document.expiry_state;
  if (state === 'none' || document.days_left === null) return t('vault.expiry.none');
  if (state === 'expired') return t('vault.expiry.expired', { count: -document.days_left });
  if (document.days_left === 0) return t('vault.expiry.today');
  return t(state === 'soon' ? 'vault.expiry.soon' : 'vault.expiry.valid', { count: document.days_left });
}

export const expiryTone = (state: DocumentExpiryState) => (state === 'expired' ? 'danger' : state === 'soon' ? 'warn' : state === 'valid' ? 'ok' : '');

/** The warnings an extraction carries, as sentences (unknown codes are left out). */
export function warningTexts(extraction: Pick<DocumentExtraction, 'warnings'>): string[] {
  const known = ['instructions_in_text', 'items_unreliable', 'hijri_date_seen', 'no_text', 'total_not_found', 'total_from_subtotal',
    'dates_without_labels', 'dates_swapped', 'dates_not_found'];
  return extraction.warnings.filter((code) => known.includes(code)).map((code) => t(`vault.warn.${code}` as MessageKey));
}

export function confidenceText(confidence: number): string {
  if (confidence >= 0.8) return t('vault.conf.high');
  if (confidence >= 0.55) return t('vault.conf.medium');
  return t('vault.conf.low');
}

/** The review form's starting values: the proposed value of each field, empty when none was read. */
export function reviewDraft(extraction: DocumentExtraction): Record<string, string> {
  const fields = extraction.kind === 'receipt' ? RECEIPT_FIELDS : DATE_FIELDS;
  return Object.fromEntries(fields.map((key) => [key, extraction.fields[key]?.value ?? '']));
}

const ISO = /^\d{4}-\d{2}-\d{2}$/;
const MONEY = /^\d{1,8}(\.\d{1,3})?$/;

function realDate(value: string): boolean {
  if (!ISO.test(value)) return false;
  const [y, m, d] = value.split('-').map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d;
}

/** Problems in a review form, by field (empty when it can be confirmed). */
export function reviewProblems(kind: DocumentExtraction['kind'], draft: Record<string, string>): Record<string, string> {
  const problems: Record<string, string> = {};
  const value = (key: string) => (draft[key] ?? '').trim();
  if (kind === 'receipt') {
    for (const key of ['total', 'tax']) if (value(key) && !MONEY.test(value(key).replace(/,/g, ''))) problems[key] = t('vault.check.money');
    if (value('date') && !realDate(value('date'))) problems.date = t('vault.check.date');
    if (value('total') && !value('currency')) problems.currency = t('vault.check.currency');
    if (value('currency') && !(CURRENCIES as readonly string[]).includes(value('currency'))) problems.currency = t('vault.check.currency');
  } else {
    for (const key of DATE_FIELDS) if (value(key) && !realDate(value(key))) problems[key] = t('vault.check.date');
    if (realDate(value('issue_date')) && realDate(value('expiry_date')) && value('issue_date') > value('expiry_date')) problems.expiry_date = t('vault.check.order');
  }
  return problems;
}

export interface EditDraft {
  title: string; category: string; access_class: 'private' | 'workspace'; issue_date: string; expiry_date: string; issuer: string; country: string;
  tags: string; description: string; number: string; reminder_days: string; retention_until: string; assistant_may_read: boolean;
}

export function editDraft(document: VaultDocument): EditDraft {
  return {
    title: document.title, category: document.category, access_class: document.access_class, issue_date: document.issue_date ?? '',
    expiry_date: document.expiry_date ?? '', issuer: document.issuer ?? '', country: document.country ?? '', tags: document.tags.join(', '),
    description: document.description, number: '', reminder_days: document.reminder_days ? String(document.reminder_days) : '',
    retention_until: document.retention_until ?? '', assistant_may_read: document.assistant_may_read,
  };
}

export const splitTags = (value: string) => [...new Set(value.split(',').map((tag) => tag.trim()).filter(Boolean))];

/** What the edit form changed, as a PATCH body; `null` with a problem when it cannot be saved. */
export function editPatch(document: VaultDocument, draft: EditDraft): { patch: VaultDocumentPatch | null; problem: string | null } {
  if (!draft.title.trim()) return { patch: null, problem: t('vault.check.title') };
  for (const key of ['issue_date', 'expiry_date', 'retention_until'] as const) {
    if (draft[key] && !realDate(draft[key])) return { patch: null, problem: t('vault.check.date') };
  }
  if (draft.issue_date && draft.expiry_date && draft.issue_date > draft.expiry_date) return { patch: null, problem: t('vault.check.order') };
  if (draft.country && !/^[A-Za-z]{2}$/.test(draft.country.trim())) return { patch: null, problem: t('vault.check.country') };
  const days = draft.reminder_days.trim();
  if (days && !(/^\d+$/.test(days) && Number(days) >= 1 && Number(days) <= 365)) return { patch: null, problem: t('vault.check.days') };
  const number = draft.number.replace(/[^A-Za-z0-9]/g, '');
  if (draft.number.trim() && (number.length < 4 || number.length > 40)) return { patch: null, problem: t('vault.check.number') };
  const patch: VaultDocumentPatch = { row_version: document.row_version };
  if (draft.title.trim() !== document.title) patch.title = draft.title.trim();
  if (draft.category !== document.category) patch.category = draft.category;
  if (draft.access_class !== document.access_class) patch.access_class = draft.access_class;
  if ((draft.issue_date || null) !== document.issue_date) patch.issue_date = draft.issue_date || null;
  if ((draft.expiry_date || null) !== document.expiry_date) patch.expiry_date = draft.expiry_date || null;
  if ((draft.issuer.trim() || null) !== document.issuer) patch.issuer = draft.issuer.trim() || null;
  if ((draft.country.trim().toUpperCase() || null) !== document.country) patch.country = draft.country.trim().toUpperCase() || null;
  const tags = splitTags(draft.tags);
  if (tags.join('\u0000') !== document.tags.join('\u0000')) patch.tags = tags;
  if (draft.description.trim() !== document.description) patch.description = draft.description.trim();
  if (draft.number.trim()) patch.number = draft.number.trim();
  const reminder = days ? Number(days) : null;
  if (reminder !== document.reminder_days) patch.reminder_days = reminder;
  if ((draft.retention_until || null) !== document.retention_until) patch.retention_until = draft.retention_until || null;
  if (draft.assistant_may_read !== document.assistant_may_read) patch.assistant_may_read = draft.assistant_may_read;
  if (draft.access_class === 'private' && draft.assistant_may_read) return { patch: null, problem: t('vault.check.assistantPrivate') };
  return { patch: Object.keys(patch).length > 1 ? patch : null, problem: Object.keys(patch).length > 1 ? null : t('vault.check.nothing') };
}

/** Refuse before sending what the server would refuse anyway (size and extension). */
export function uploadProblem(file: { name: string; size: number }, meta: Pick<VaultMeta, 'max_upload_bytes' | 'accepted_extensions'> | null): string | null {
  if (!file.size) return t('vault.check.empty', { name: file.name });
  if (!meta) return null;
  if (file.size > meta.max_upload_bytes) return t('vault.check.tooLarge', { name: file.name });
  const dot = file.name.lastIndexOf('.');
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : '';
  if (meta.accepted_extensions.length && !meta.accepted_extensions.includes(ext)) return t('vault.check.type', { name: file.name });
  return null;
}

/** Merge a further page into a list, keeping the first copy of each id. */
export function mergeDocuments(current: VaultDocument[], page: VaultDocument[]): VaultDocument[] {
  const seen = new Set(current.map((item) => item.id));
  return [...current, ...page.filter((item) => !seen.has(item.id))];
}

/** The name an activity code reads as. */
const ACTIVITY = new Set(['upload', 'update', 'view', 'download', 'link', 'link_opened', 'number_revealed', 'trash', 'restore', 'version_added',
  'version_restored', 'version_deleted', 'shared', 'unshared', 'linked', 'unlinked', 'extraction_requested', 'extraction_confirmed',
  'extraction_rejected', 'reminder_sent', 'retention_trash']);
export const activityLabel = (action: string) => t(`vault.act.${ACTIVITY.has(action) ? action : 'other'}` as MessageKey);

export function filterQuery(filters: { q: string; category: string; access: string; expiry: string }, status: 'active' | 'deleted' = 'active', cursor?: string | null): string {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), status });
  if (filters.q.trim()) params.set('q', filters.q.trim());
  if (filters.category) params.set('category', filters.category);
  if (filters.access) params.set('access_class', filters.access);
  if (filters.expiry) params.set('expiry', filters.expiry);
  if (cursor) params.set('cursor', cursor);
  return params.toString();
}
