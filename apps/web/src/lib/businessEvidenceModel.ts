// The pure part of the business books' supporting documents (docs/business-books.md#supporting-documents-and-evidence),
// covered by test/businessEvidence.test.ts without a browser.
import type { VaultDocumentRow } from '@anum/contracts';

const id = encodeURIComponent;

export function packQuery(span: { from?: string; to?: string }): string {
  const params = new URLSearchParams();
  if (span.from) params.set('from', span.from);
  if (span.to) params.set('to', span.to);
  const text = params.toString();
  return text ? `?${text}` : '';
}

/** The vault page that opens one document. */
export const documentHref = (documentId: string) => `#vault-documents?doc=${id(documentId)}`;

/** Documents already attached are not offered again. */
export function offerable(rows: readonly VaultDocumentRow[], attached: readonly string[]): VaultDocumentRow[] {
  const taken = new Set(attached);
  return rows.filter((row) => !taken.has(row.id));
}

/** A period problem to fix first (a message key), or null. */
export function packProblem(span: { from?: string; to?: string }): string | null {
  if (span.from && span.to && span.from > span.to) return 'books.evidence.pack.badRange';
  return null;
}
