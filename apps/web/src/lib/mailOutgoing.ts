// Email Center outgoing mail (docs/email.md#attachments, docs/email.md#scheduled-send): the pure
// helpers the composer uses for attached files and the send time. No browser APIs at import time, so
// the unit tests run these directly. The server checks everything again; these only explain early.
import type { MailDraft, MailOutgoingFile, MailSetup } from './mail';

const pad = (n: number) => String(n).padStart(2, '0');

/** A `datetime-local` value (the person's own time zone) for an instant. */
export function toLocalInput(value: Date | string): string {
  const d = typeof value === 'string' ? new Date(value) : value;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** The instant a `datetime-local` value names in the person's zone, or null when it is not a time. */
export function fromLocalInput(value: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value)) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export type ScheduleProblem = 'empty' | 'tooSoon' | 'tooFar';

/** Why a picked time cannot be used: at least a minute ahead and at most `maxDays` days. */
export function scheduleProblem(when: Date | null, now: Date, maxDays: number): ScheduleProblem | null {
  if (!when) return 'empty';
  if (when.getTime() < now.getTime() + 60_000) return 'tooSoon';
  if (when.getTime() > now.getTime() + maxDays * 86_400_000) return 'tooFar';
  return null;
}

/** Two quick picks in the person's zone: tomorrow at 08:00 and next Monday at 08:00. */
export function quickTimes(now: Date): { key: 'tomorrow' | 'monday'; when: Date }[] {
  const at8 = (days: number) => { const d = new Date(now); d.setDate(d.getDate() + days); d.setHours(8, 0, 0, 0); return d; };
  const toMonday = ((8 - now.getDay()) % 7) || 7;
  return [{ key: 'tomorrow', when: at8(1) }, { key: 'monday', when: at8(toMonday) }];
}

/** The time a draft shows as scheduled: only while it is scheduled or waiting for that approval. */
export function scheduledFor(draft: MailDraft): string | null {
  if (!draft.send_at) return null;
  return draft.status === 'scheduled' || draft.status === 'pending_approval' ? draft.send_at : null;
}

export function totalBytes(files: Pick<MailOutgoingFile, 'size'>[]): number {
  return files.reduce((sum, f) => sum + f.size, 0);
}

export type FileProblem = { key: 'type' | 'size' | 'count' | 'total' | 'empty'; name: string };

/** Files the server would refuse, said before the upload starts (it checks again, with the scanner). */
export function fileProblems<F extends { name: string; size: number }>(files: F[], attached: Pick<MailOutgoingFile, 'size'>[], setup: MailSetup): { ok: F[]; problems: FileProblem[] } {
  const extensions = setup.attachment_extensions ?? [];
  const maxFile = setup.attachment_max_bytes ?? Infinity;
  const maxCount = setup.attachments_per_message ?? Infinity;
  const maxTotal = setup.attachments_total_bytes ?? Infinity;
  let count = attached.length;
  let total = totalBytes(attached);
  const ok: F[] = [];
  const problems: FileProblem[] = [];
  for (const file of files) {
    const dot = file.name.lastIndexOf('.');
    const ext = dot > 0 ? file.name.slice(dot).toLowerCase() : '';
    if (extensions.length && !extensions.includes(ext)) problems.push({ key: 'type', name: file.name });
    else if (file.size === 0) problems.push({ key: 'empty', name: file.name });
    else if (file.size > maxFile) problems.push({ key: 'size', name: file.name });
    else if (count + 1 > maxCount) problems.push({ key: 'count', name: file.name });
    else if (total + file.size > maxTotal) problems.push({ key: 'total', name: file.name });
    else { ok.push(file); count += 1; total += file.size; }
  }
  return { ok, problems };
}

/** The send time the approval binds (`arguments.send_at`), if any. */
export function approvalSendAt(argumentsValue: unknown): string | null {
  const value = (argumentsValue as { send_at?: unknown } | null | undefined)?.send_at;
  return typeof value === 'string' && value ? value : null;
}
