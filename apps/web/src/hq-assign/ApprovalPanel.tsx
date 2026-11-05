/** Approvals decided inside the headquarters. The cards are the Approvals page's own (`PendingApproval`: the exact tool,
 * where it goes, every argument, the payload hash, routing, progress, expiry, the reason field) and a decision goes
 * through the same `api.approveTask` / `api.rejectTask` calls, which send back the payload hash shown. The server keeps
 * every rule: routing, the two person rule, expiry, audit. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, ShieldCheck } from 'lucide-react';
import type { Approval } from '@anum/contracts';
import * as api from '../lib/api';
import { decisionSummary, effectiveStatus } from '../lib/approvals';
import { describeError, isPermissionDenied } from '../lib/errors';
import { formatDateTime, t } from '../lib/i18n';
import { statusLabel } from '../lib/labels';
import { fullPageHash, openHqPanel, type HqWorkPanel } from '../lib/hqWorkPanels';
import { ArgumentList, HashLine, PendingApproval, TargetLine, type Decide } from '../ApprovalsView';
import { WorkDrawer } from './WorkDrawer';

const PAGE = 10;
type Load = { state: 'loading' } | { state: 'ready' } | { state: 'denied' } | { state: 'gone' } | { state: 'error'; reason: string };

export function ApprovalPanel({ panel, live, onClose, onChanged }: {
  panel: Extract<HqWorkPanel, { kind: 'approval' | 'approvals' }>;
  live?: number | string;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const single = panel.kind === 'approval' ? panel.id : null;
  const taskId = panel.kind === 'approvals' ? panel.taskId ?? null : null;
  const [rows, setRows] = useState<Approval[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  const [decided, setDecided] = useState<string | null>(null);
  const seq = useRef(0);

  const fetchFirst = useCallback(async (quiet = false) => {
    const mine = ++seq.current;
    if (!quiet) setLoad({ state: 'loading' });
    try {
      if (single) {
        const one = await api.getApproval(single);
        if (mine !== seq.current) return;
        setRows([one]); setCursor(null);
      } else {
        const page = await api.getApprovalPage({ status: 'pending', taskId: taskId ?? undefined, limit: PAGE });
        if (mine !== seq.current) return;
        setRows(page.items); setCursor(page.nextCursor);
      }
      setLoad({ state: 'ready' });
    } catch (error) {
      if (mine !== seq.current) return;
      const status = (error as { status?: number }).status;
      const failed: Load = isPermissionDenied(error) ? { state: 'denied' } : status === 404 ? { state: 'gone' } : { state: 'error', reason: describeError(error) };
      setLoad((current) => (quiet && current.state === 'ready' ? current : failed));
    }
  }, [single, taskId]);
  useEffect(() => { void fetchFirst(); }, [fetchFirst]);
  const first = useRef(true);
  useEffect(() => { if (first.current) { first.current = false; return; } void fetchFirst(true); }, [live]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadMore = async () => {
    if (!cursor || more) return;
    setMore(true);
    try {
      const page = await api.getApprovalPage({ status: 'pending', taskId: taskId ?? undefined, cursor, limit: PAGE });
      setRows((r) => [...r, ...page.items.filter((a) => !r.some((x) => x.id === a.id))]); setCursor(page.nextCursor);
    } catch (error) { setLoad({ state: 'error', reason: describeError(error) }); }
    finally { setMore(false); }
  };

  // The shell's own decision: a refusal the API gives (403: routing, the two person rule) is shown on the card; other
  // failures are said on the drawer. The list is read again either way, and the office reads its flow again.
  const decide: Decide = async (approval, decision, reason, securityCode) => {
    setBusy(true); setDecided(null);
    let refusal: string | null = null;
    try {
      const result = decision === 'approve' ? await api.approveTask(approval, reason, securityCode) : await api.rejectTask(approval, reason);
      setDecided(decision === 'approve' ? t('hqWork.appr.approved', { action: approval.action, status: statusLabel(result.task.status) }) : t('hqWork.appr.rejected', { action: approval.action }));
    } catch (error) {
      refusal = isPermissionDenied(error) || (error as { status?: number }).status === 409 ? describeError(error) : t('hqWork.appr.failed', { detail: describeError(error) });
    } finally {
      setBusy(false);
      onChanged?.();
      await fetchFirst(true);
    }
    return refusal;
  };

  const now = new Date();
  const pending = rows.filter((a) => effectiveStatus(a, now) === 'pending');
  const done = rows.filter((a) => effectiveStatus(a, now) !== 'pending');
  const region = single ? t('hqWork.appr.region') : t('hqWork.appr.listRegion');
  return <WorkDrawer region={region} title={taskId ? t('hqWork.appr.forTask') : region} onClose={onClose} onRefresh={() => void fetchFirst()} testId="hq-approval-panel"
    fullPage={{ hash: fullPageHash(panel), label: t('hqWork.fullPage') }}>
    {load.state === 'loading' && <p className="hqAsNote" role="status"><Loader2 size={14} className="hqAsSpin" aria-hidden="true" /> {t('hqWork.appr.loading')}</p>}
    {load.state === 'denied' && <div className="hqAsBox hqAsWarn" role="alert"><strong>{t('approvals.denied.title')}</strong><p>{t('approvals.denied.text')}</p></div>}
    {load.state === 'gone' && <p className="hqAsBox hqAsWarn" role="alert">{t('hqWork.appr.gone')}</p>}
    {load.state === 'error' && <div className="hqAsBox hqAsFail" role="alert"><p>{t('hqWork.appr.error', { detail: load.reason })}</p><button type="button" onClick={() => void fetchFirst()}>{t('hqAssign.retry')}</button></div>}
    {decided && <p className="hqWdOk" role="status">{decided}</p>}
    {load.state === 'ready' && <>
      {pending.map((a) => <div key={a.id}>
        <PendingApproval approval={a} busy={busy} decide={decide} now={now} userId={api.currentUserId()} />
        <button type="button" className="hqAsLink" onClick={() => openHqPanel({ kind: 'task', id: a.taskId })}>{t('hqWork.appr.openTask')}</button>
      </div>)}
      {done.map((a) => <section key={a.id} className="hqAsSection" aria-label={t('approvals.cardLabel', { action: a.action })}>
        <h3><ShieldCheck size={14} aria-hidden="true" /> <code dir="ltr">{a.action}</code> <span>{statusLabel(effectiveStatus(a, now))}</span></h3>
        <p className="hqAsNote">{decisionSummary(a, formatDateTime, now)}</p>
        {a.decisionReason && <p className="hqAsNote" dir="auto">{a.decisionReason}</p>}
        <TargetLine approval={a} /><ArgumentList approval={a} /><HashLine approval={a} />
        <button type="button" className="hqAsLink" onClick={() => openHqPanel({ kind: 'task', id: a.taskId })}>{t('hqWork.appr.openTask')}</button>
      </section>)}
      {rows.length === 0 && <div className="hqAsBox" role="status"><p>{taskId ? t('hqWork.appr.emptyTask') : t('hqWork.appr.empty')}</p>
        {taskId ? <button type="button" onClick={() => openHqPanel({ kind: 'task', id: taskId })}>{t('hqWork.appr.backToTask')}</button>
          : <button type="button" onClick={onClose}>{t('hqWork.backToOffice')}</button>}</div>}
      {cursor && <button type="button" onClick={() => void loadMore()} disabled={more}>{more ? t('common.loading') : t('approvals.loadMore')}</button>}
    </>}
  </WorkDrawer>;
}
