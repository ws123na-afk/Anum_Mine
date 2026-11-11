// The own transfer a transaction belongs to, in the transaction drawer (docs/finance.md#own-transfers): the other side
// and an Unpair button. Unpairing voids the pair's link through the links API; both rows stay with their categories and
// the pair is offered again under Find transfers.
import { useCallback, useEffect, useId, useState } from 'react';
import { Link2Off, Repeat } from 'lucide-react';
import type { FinanceTransferPairOf } from '@anum/contracts';
import { apiRequest } from './lib/api';
import { describeError } from './lib/errors';
import { formatAmount, isolate } from './lib/finance';
import { t } from './lib/i18n';
import { ConfirmDialog } from './Dialog';
import { Notice, failed, type Load } from './FinanceParts';

const BASE = '/api/v1/finance/links';
export const getTransferPair = (transactionId: string) =>
  apiRequest<FinanceTransferPairOf>(`${BASE}/transfer?transaction_id=${encodeURIComponent(transactionId)}`, { method: 'GET' });
export const unpairTransfer = (linkId: string, version: number | null) =>
  apiRequest<{ id: string; status: string }>(`${BASE}/${encodeURIComponent(linkId)}/void`,
    { method: 'POST', body: JSON.stringify(version ? { version } : {}), headers: { 'content-type': 'application/json' } });

export function TransferPair({ transactionId, onChanged }: { transactionId: string; onChanged: () => void }) {
  const [state, setState] = useState<Load<FinanceTransferPairOf>>({ kind: 'loading' });
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const headingId = useId();
  const load = useCallback(() => {
    getTransferPair(transactionId).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, [transactionId]);
  useEffect(() => { load(); }, [load]);
  if (state.kind === 'loading' || state.kind === 'denied') return notice ? <Notice error={notice.error}>{notice.text}</Notice> : null;
  if (state.kind === 'error') return <p className="muted ffxError" role="status">{t('ftp.error')} <button type="button" className="cmLinkButton" onClick={load}>{t('ffx.retry')}</button></p>;
  const pair = state.value;
  if (!pair.paired || !pair.link_id) return notice ? <Notice error={notice.error}>{notice.text}</Notice> : null;
  const other = pair.other;
  const amount = other?.amount && other.currency ? isolate(formatAmount(String(other.amount), other.currency)) : '-';
  const unpair = async () => {
    setBusy(true); setNotice(null);
    try {
      await unpairTransfer(pair.link_id ?? '', pair.link_version);
      setAsking(false); setNotice({ text: t('ftp.done') }); load(); onChanged();
    } catch (e) { setAsking(false); setNotice({ text: describeError(e), error: true }); load(); } finally { setBusy(false); }
  };
  return <section className="ffxPanel" aria-labelledby={headingId}>
    <h3 id={headingId}><Repeat aria-hidden="true" />{t('ftp.title')}</h3>
    <p>{t(pair.role === 'out' ? 'ftp.sentTo' : 'ftp.receivedFrom', { account: other?.account_name ?? t('ftp.unknownAccount') })}</p>
    {other && <dl className="ffxFacts">
      <div><dt>{t('ftp.otherDay')}</dt><dd><bdi dir="ltr">{other.posted_on ?? '-'}</bdi></dd></div>
      <div><dt>{t('ftp.otherAmount')}</dt><dd dir="ltr">{amount}</dd></div>
      {other.description && <div><dt>{t('ftp.otherText')}</dt><dd dir="auto">{other.description}</dd></div>}
    </dl>}
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <button type="button" className="secondary" disabled={busy} onClick={() => setAsking(true)}><Link2Off aria-hidden="true" />{t('ftp.unpair')}</button>
    {asking && <ConfirmDialog title={t('ftp.confirmTitle')} confirmLabel={t('ftp.unpair')} danger busy={busy} onConfirm={() => void unpair()} onCancel={() => setAsking(false)}>
      <p>{t('ftp.confirmText')}</p>
    </ConfirmDialog>}
  </section>;
}
