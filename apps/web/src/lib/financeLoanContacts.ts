// A loan's person from Contacts (docs/finance.md#people-from-contacts). The loan keeps the name typed on it as
// history and the contact's id; the current name is read live by the API for the caller. Nothing else from
// Contacts is kept in finance.
import type { FinanceLoanContactState, FinancePersonLoan } from '@anum/contracts';
import type { PersonView } from './contacts';

type LoanPerson = Pick<FinancePersonLoan, 'person'> & { contact_id?: string | null; contact_name?: string | null; contact_state?: FinanceLoanContactState | null };

/** The name to show: the contact's current name when the caller can read it, else the typed one. */
export function loanName(loan: LoanPerson): string {
  return loan.contact_state === 'linked' && loan.contact_name ? loan.contact_name : loan.person;
}

/** What to say under the name: the typed name when Contacts calls the person something else, or that the person was removed. */
export function contactNote(loan: LoanPerson): { kind: 'typed'; name: string } | { kind: 'deleted' } | { kind: 'hidden' } | null {
  if (!loan.contact_id) return null;
  if (loan.contact_state === 'deleted') return { kind: 'deleted' };
  if (loan.contact_state === 'hidden') return { kind: 'hidden' };
  const fold = (value: string) => value.trim().replace(/\s+/g, ' ').toLocaleLowerCase();
  return loan.contact_name && fold(loan.contact_name) !== fold(loan.person) ? { kind: 'typed', name: loan.person } : null;
}

/** Whether a search found someone with exactly this name (case and spacing ignored), so "Add to Contacts" is not offered twice. */
export function hasExactName(people: readonly Pick<PersonView, 'name'>[], name: string): boolean {
  const fold = (value: string) => value.trim().replace(/\s+/g, ' ').toLocaleLowerCase();
  const wanted = fold(name);
  return wanted !== '' && people.some((p) => fold(p.name) === wanted);
}

/** A short detail to tell two people with the same name apart: their organisation, if any. */
export function personHint(person: Pick<PersonView, 'organisation'>): string | null {
  return person.organisation.trim() || null;
}
