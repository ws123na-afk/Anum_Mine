// Email Center outgoing mail over the live API (docs/email.md#attachments, #scheduled-send): files
// attached to a draft and scheduled messages. Files go to the ANUM API only, which checks and scans them.
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFrom, apiErrorFromResponse } from './errors';
import { t } from './i18n';
import type { MailDraft, MailOutgoingFile } from './mail';

const base = '/api/v1/mail';
const id = encodeURIComponent;

/** Attach one file (the raw body, the name in the query so any name works), with progress and cancel. */
export async function uploadMailAttachment(draftId: string, file: File, onProgress?: (loaded: number, total: number) => void, signal?: AbortSignal): Promise<MailOutgoingFile> {
  const headers = await authHeaders();
  return new Promise<MailOutgoingFile>((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('Upload cancelled', 'AbortError')); return; }
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${apiBaseUrl}${base}/drafts/${id(draftId)}/attachments?${new URLSearchParams({ name: file.name }).toString()}`);
    for (const [key, value] of Object.entries(headers)) xhr.setRequestHeader(key, value);
    xhr.setRequestHeader('content-type', file.type || 'application/octet-stream');
    xhr.upload.onprogress = (event) => onProgress?.(event.loaded, event.lengthComputable ? event.total : file.size);
    xhr.onload = () => {
      let body: unknown;
      try { body = JSON.parse(xhr.responseText); } catch { body = undefined; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as MailOutgoingFile);
      else reject(apiErrorFrom(xhr.status, body));
    };
    xhr.onerror = () => reject(new Error(t('mailo.files.lost')));
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'));
    signal?.addEventListener('abort', () => xhr.abort(), { once: true });
    xhr.send(file);
  });
}

export const removeMailAttachment = (draftId: string, fileId: string): Promise<void> =>
  apiRequestVoid(`${base}/drafts/${id(draftId)}/attachments/${id(fileId)}`, { method: 'DELETE' });

/** Download one of the person's own attached files through the API (never opened inline). */
export async function downloadOutgoingAttachment(draftId: string, file: MailOutgoingFile): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${base}/drafts/${id(draftId)}/attachments/${id(file.id)}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = file.filename;
  link.rel = 'noopener';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Scheduled messages (approved or still waiting for approval), soonest first. */
export const listScheduledMail = (accountId?: string): Promise<MailDraft[]> =>
  apiRequest(`${base}/scheduled${accountId ? `?account_id=${id(accountId)}` : ''}`, { method: 'GET' });

/** Stop a scheduled send: the message becomes a draft again. */
export const cancelMailSchedule = (draftId: string): Promise<MailDraft> =>
  apiRequest(`${base}/drafts/${id(draftId)}/cancel-schedule`, { method: 'POST' });
