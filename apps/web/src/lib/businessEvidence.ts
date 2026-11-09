// Supporting documents of the business books over the live API (docs/business-books.md#supporting-documents-and-evidence).
// Documents stay in the Document vault: this attaches a workspace document to a bill, an invoice, an adjustment or a manual
// journal entry, lists what is attached and downloads the evidence pack. The server checks every permission again.
import type { BooksEvidenceMissing, BooksEvidencePage, BooksEvidenceSource, VaultDocumentPage } from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { fileNameFromDisposition, saveBlob } from './fileSave';
import { packQuery } from './businessEvidenceModel';

export { documentHref, offerable, packProblem, packQuery } from './businessEvidenceModel';

const BASE = '/api/v1/business/books';
const id = encodeURIComponent;

export const listEvidence = (sourceType: BooksEvidenceSource, ids: string[]) =>
  apiRequest<BooksEvidencePage>(`${BASE}/evidence?source_type=${id(sourceType)}&ids=${ids.map(id).join(',')}`, { method: 'GET' });
export const attachEvidence = (sourceType: BooksEvidenceSource, sourceId: string, documentId: string) => apiRequest<{ link_id: string }>(`${BASE}/evidence`, {
  method: 'POST', body: JSON.stringify({ source_type: sourceType, source_id: sourceId, document_id: documentId }), headers: { 'content-type': 'application/json' },
});
export const detachEvidence = (sourceType: BooksEvidenceSource, sourceId: string, linkId: string) =>
  apiRequestVoid(`${BASE}/evidence/${id(sourceType)}/${id(sourceId)}/${id(linkId)}`, { method: 'DELETE' });
export const missingEvidence = (span: { from?: string; to?: string }) =>
  apiRequest<BooksEvidenceMissing>(`${BASE}/evidence/missing${packQuery(span)}`, { method: 'GET' });

/** Workspace documents of this business's vault the person can attach (private ones are hidden from the other owners). */
export const attachableDocuments = (q: string) => {
  const params = new URLSearchParams({ access_class: 'workspace', status: 'active', sort: 'updated', limit: '20' });
  if (q.trim()) params.set('q', q.trim());
  return apiRequest<VaultDocumentPage>(`/api/v1/documents?${params.toString()}`, { method: 'GET' });
};

/** Download the period's evidence index (CSV) or the whole pack (zip with the files you may read). */
export async function downloadPack(kind: 'csv' | 'zip', span: { from?: string; to?: string }): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}/evidence-pack.${kind}${packQuery(span)}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  saveBlob(await response.blob(), fileNameFromDisposition(response.headers.get('content-disposition'), `evidence-pack.${kind}`));
}

