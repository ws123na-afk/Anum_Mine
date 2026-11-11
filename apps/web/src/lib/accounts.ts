// The Accounts tab of Business (docs/business.md#accounts-and-contacts): pure helpers for the list's order, the
// account and contact forms and their checks. The server checks everything again and names what to fix.
import type { BusinessAccount, BusinessAccountRow, BusinessContact, ConsentBasis } from '@anum/contracts';
import { t } from './i18n.ts';
import type { Check } from './business.ts';

/** "Riyadh, SA" from whichever of the city and the country the account has. */
export function accountPlace(account: { city: string; country: string }): string {
  return [account.city, account.country].map((part) => part.trim()).filter(Boolean).join(', ');
}

/** The columns GET /business/accounts orders by (the table's sortable headers). */
export type AccountSort = 'name' | 'place' | 'industry' | 'updated';
export const ACCOUNT_SORTS: readonly AccountSort[] = ['name', 'place', 'industry', 'updated'];
export interface AccountOrder { sort: AccountSort; desc: boolean }
export const DEFAULT_ORDER: AccountOrder = { sort: 'name', desc: false };

/** Clicking a header again turns the order round; a new column starts ascending (newest first for dates). */
export function nextOrder(current: AccountOrder, sort: AccountSort): AccountOrder {
  if (current.sort === sort) return { sort, desc: !current.desc };
  return { sort, desc: sort === 'updated' };
}

export const ariaSort = (current: AccountOrder, sort: AccountSort): 'ascending' | 'descending' | 'none' =>
  (current.sort === sort ? (current.desc ? 'descending' : 'ascending') : 'none');

const URLISH = /^(https?:\/\/)?[a-z0-9.-]+\.[a-z]{2,}(\/\S*)?$/i;
const EMAILISH = /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/;

export interface AccountDraft { name: string; website: string; industry: string; country: string; city: string; notes: string }
export const emptyAccountDraft = (): AccountDraft => ({ name: '', website: '', industry: '', country: '', city: '', notes: '' });
export const accountDraftOf = (account: BusinessAccount | BusinessAccountRow): AccountDraft =>
  ({ name: account.name, website: account.website ?? '', industry: account.industry, country: account.country, city: account.city, notes: account.notes });

/** The POST or PATCH /accounts body, or what to fix. The website is sent as typed; the server normalises it. */
export function accountBody(draft: AccountDraft): Check<Record<string, unknown>> {
  const name = draft.name.trim();
  if (name.length < 2) return { ok: false, error: t('biz.form.companyRequired') };
  const website = draft.website.trim();
  if (website && !URLISH.test(website)) return { ok: false, error: t('biz.form.websiteInvalid') };
  return { ok: true, value: { name, website: website || null, industry: draft.industry.trim(), country: draft.country.trim(), city: draft.city.trim(), notes: draft.notes.trim() } };
}

export interface ContactDraft { name: string; roleTitle: string; email: string; phone: string; profileUrl: string; consent: ConsentBasis; consentNote: string }
export const emptyContactDraft = (): ContactDraft => ({ name: '', roleTitle: '', email: '', phone: '', profileUrl: '', consent: 'unknown', consentNote: '' });
export const contactDraftOf = (contact: BusinessContact): ContactDraft =>
  ({ name: contact.name, roleTitle: contact.role_title, email: contact.email ?? '', phone: contact.phone ?? '', profileUrl: contact.profile_url ?? '', consent: contact.consent_basis, consentNote: contact.consent_note });

/** The POST /accounts/{id}/contacts or PATCH /contacts/{id} body, or what to fix. */
export function contactBody(draft: ContactDraft): Check<Record<string, unknown>> {
  const name = draft.name.trim();
  if (!name) return { ok: false, error: t('acc.form.contactNameRequired') };
  const email = draft.email.trim();
  if (email && !EMAILISH.test(email)) return { ok: false, error: t('biz.form.emailInvalid') };
  const phone = draft.phone.trim();
  if (phone && phone.replace(/\D/g, '').length < 8) return { ok: false, error: t('biz.form.phoneInvalid') };
  const profile = draft.profileUrl.trim();
  if (profile && !URLISH.test(profile)) return { ok: false, error: t('biz.form.websiteInvalid') };
  return {
    ok: true,
    value: { name, role_title: draft.roleTitle.trim(), email: email || null, phone: phone || null, profile_url: profile || null, consent_basis: draft.consent, consent_note: draft.consentNote.trim() },
  };
}
