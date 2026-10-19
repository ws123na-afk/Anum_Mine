// Small building blocks shared by the Business tabs (docs/business.md): notices, badges, the empty
// state with its next action, the "show more" pager, the approval box and the reason prompt.
import { useId, useState, type FormEvent, type ReactNode } from 'react';
import { CheckCircle2, CircleDot, Fingerprint, Inbox, Lock, ShieldAlert } from 'lucide-react';
import type { ProposalApproval } from '@anum/contracts';
import * as api from './lib/api';
import { argumentRows, shortHash } from './lib/approvals';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import type { Tone } from './lib/business';
import { Modal } from './Dialog';

export function Note({ children, error, id }: { children: ReactNode; error?: boolean; id?: string }) {
  return <p id={id} className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`statusBadge ${tone}`}>{children}</span>;
}

export function Empty({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return <div className="emptyState bzEmpty"><Inbox aria-hidden="true" /><h3>{title}</h3><p>{text}</p>{action && <div className="bzInline bzCenter">{action}</div>}</div>;
}

export function Denied({ text }: { text: string }) {
  return <section className="surface bzPanel" role="note"><Lock aria-hidden="true" /><div><h2>{t('biz.deniedTitle')}</h2><p className="muted">{text}</p></div></section>;
}

export function Failed({ text, onRetry }: { text: string; onRetry: () => void }) {
  return <section className="surface bzPanel" role="alert"><ShieldAlert aria-hidden="true" /><div><h2>{t('biz.errorTitle')}</h2><p className="muted">{t('biz.errorText', { message: text })}</p>
    <div className="bzInline"><button type="button" onClick={onRetry}>{t('common.tryAgain')}</button></div></div></section>;
}

/** What a failed action says: the API's refusal (it names who may do it) or the error. */
export const failure = (error: unknown, what: string) => isPermissionDenied(error)
  ? t('biz.denied', { message: describeError(error) }) : t('biz.failed', { what, message: describeError(error) });

export const when = (value: string | null | undefined) => (value ? formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' }) : t('biz.unknown'));
export const day = (value: string | null | undefined) => (value ? formatDateTime(`${value.length === 10 ? `${value}T12:00:00` : value}`, { dateStyle: 'medium' }) : t('biz.unknown'));

export function Pager({ shown, total, busy, error, onMore }: { shown: number; total: number; busy: boolean; error: string | null; onMore: () => void }) {
  if (shown >= total && !error) return null;
  return <div className="bzMore">
    {shown < total && <button type="button" className="secondary" disabled={busy} onClick={onMore}>{busy ? t('biz.loadingMore') : t('biz.showMore', { shown, total })}</button>}
    {error && <Note error>{error}</Note>}
  </div>;
}

/** Asks for a short reason before an action that needs one (lost, suppressed, withdrawn). */
export function ReasonDialog({ title, label, confirm, onConfirm, onCancel }: { title: string; label: string; confirm: string; onConfirm: (reason: string) => Promise<void>; onCancel: () => void }) {
  const [reason, setReason] = useState(''), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const field = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (reason.trim().length < 3) { setProblem(t('biz.form.reasonRequired')); return; }
    setBusy(true); setProblem(null);
    try { await onConfirm(reason.trim()); } catch (error) { setProblem(describeError(error)); setBusy(false); }
  }
  return <Modal title={title} className="formDialog" onClose={onCancel}>
    <form onSubmit={(event) => void submit(event)} aria-label={title}>
      <label className="field" htmlFor={field}><span>{label}</span><textarea id={field} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} data-autofocus /></label>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : confirm}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

const ARG_LABELS: Record<string, MessageKey> = {
  title: 'biz.arg.title', client: 'biz.arg.client', version: 'biz.arg.version', currency: 'biz.arg.currency', subtotal: 'biz.arg.subtotal', tax: 'biz.arg.tax',
  tax_note: 'biz.arg.taxNote', total: 'biz.arg.total', milestones: 'biz.arg.milestones', deliverables: 'biz.arg.deliverables', exclusions: 'biz.arg.exclusions',
};

/** The waiting acceptance: exactly what the owner approves, with the payload hash they send back. */
export function AcceptanceApproval({ approval, canDecide, onDecided }: { approval: ProposalApproval; canDecide: boolean; onDecided: (message: string) => void }) {
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const rows = argumentRows(approval.arguments ?? {}).filter((row) => ARG_LABELS[row.key.split('[')[0]] && row.value !== '[]');
  const mapped = api.mapApproval(approval as unknown as Parameters<typeof api.mapApproval>[0]);
  async function decide(kind: 'approve' | 'reject') {
    setBusy(true); setProblem(null);
    try {
      const result = kind === 'approve' ? await api.approveTask(mapped) : await api.rejectTask(mapped);
      onDecided(kind === 'approve' ? (result.run?.result ?? t('biz.proposal.accepted')) : t('biz.proposal.rejected'));
    } catch (error) { setProblem(describeError(error)); } finally { setBusy(false); }
  }
  return <div className="bzApproval" role="group" aria-label={t('biz.approval.label')}>
    <p className="bzApprovalTitle"><ShieldAlert aria-hidden="true" />{t('biz.approval.waiting')}</p>
    <dl className="bzArgs">{rows.map((row) => <div key={row.key}><dt>{t(ARG_LABELS[row.key.split('[')[0]])}</dt><dd dir="auto">{row.value}</dd></div>)}</dl>
    <p className="bzHash"><Fingerprint aria-hidden="true" />{t('biz.approval.hash')} <code title={approval.payload_hash ?? ''}>{shortHash(approval.payload_hash)}</code></p>
    {canDecide ? <div className="bzInline"><button type="button" disabled={busy} onClick={() => void decide('approve')}><CheckCircle2 aria-hidden="true" />{t('biz.approval.approve')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => void decide('reject')}>{t('biz.approval.reject')}</button></div>
      : <p className="muted">{t('biz.approval.ownerDecides')}</p>}
    {problem && <Note error>{problem}</Note>}
  </div>;
}
