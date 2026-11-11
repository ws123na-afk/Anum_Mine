// What an import did with each statement row (docs/finance.md#import-history): imported, attached to a record already
// there, already recorded, held for review, left out by the person, or left out by the reader. Its own lazy chunk,
// opened from Finance, Connections, Import history. Held rows wait in Finance, iPhone SMS, Review; the button opens it.
import { useCallback, useEffect, useId, useState } from 'react';
import type { FinanceImport, FinanceImportDetail, FinanceRowDispositionKind } from '@anum/contracts';
import { getImport, undoSplitMatch } from './lib/financeApi';
import { describeError } from './lib/errors';
import { formatAmount } from './lib/finance';
import { t, type MessageKey } from './lib/i18n';
import { importRowFilters, type ImportRowFilter } from './lib/financeImportRows';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, LoadError, Notice, failed, type Load } from './FinanceParts';

const PAGE = 100;

export function ImportRows({ item, currency, onClose, onOpenReview }: { item: FinanceImport; currency: string; onClose: () => void; onOpenReview: () => void }) {
  const [filter, setFilter] = useState<ImportRowFilter>((item.not_in_ledger ?? 0) > 0 ? 'not_in_ledger' : 'all');
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Load<FinanceImportDetail>>({ kind: 'loading' });
  const filterId = useId();
  const [undoing, setUndoing] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const undo = async (groupId: string) => {
    setBusy(true); setNotice(null);
    try { await undoSplitMatch(item.id, groupId); setNotice({ text: t('fimprows.split.undone') }); load(); }
    catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setBusy(false); setUndoing(null); }
  };
  const load = useCallback(() => {
    setPage({ kind: 'loading' });
    getImport(item.id, { disposition: filter === 'all' ? null : filter, limit: PAGE, offset })
      .then((value) => setPage({ kind: 'ready', value }), (e) => setPage(failed(e)));
  }, [item.id, filter, offset]);
  useEffect(() => { load(); }, [load]);
  const missing = item.not_in_ledger ?? 0;
  return <Modal title={t('fimprows.title', { file: item.filename })} className="fimpRows" onClose={onClose}>
    <p className="muted">{t('fimprows.intro')}</p>
    {item.reconciliation?.status === 'tied' && missing > 0 && <Notice error>{t('fimprows.tiedMissing', { count: missing })}</Notice>}
    {(item.held_for_review ?? 0) > 0 && <div className="fimpRowsHeld"><p>{t('fimprows.heldWhere', { count: item.held_for_review ?? 0 })}</p>
      <button type="button" className="secondary" onClick={onOpenReview}>{t('fimprows.openReview')}</button></div>}
    <label className="cmInline" htmlFor={filterId}><span>{t('fimprows.show')}</span>
      <select id={filterId} value={filter} onChange={(e) => { setFilter(e.target.value as ImportRowFilter); setOffset(0); }}>
        {importRowFilters(item).map((option) => <option key={option.id} value={option.id}>{t(`fimprows.filter.${option.id}` as MessageKey)}{option.count !== null ? ` (${option.count})` : ''}</option>)}
      </select></label>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {page.kind === 'loading' && <Notice busy>{t('fimprows.loading')}</Notice>}
    {page.kind === 'denied' && <Denied />}
    {page.kind === 'error' && <LoadError message={page.message} retry={load} />}
    {page.kind === 'ready' && (page.value.rows.length === 0
      ? <p className="muted">{t(filter === 'all' ? 'fimprows.noneStored' : 'fimprows.none')}</p>
      : <>
        <ul className="cmList fimpRowList" aria-label={t('fimprows.caption', { file: item.filename })}>{page.value.rows.map((row, index) => <li key={`${row.line}-${index}`}>
          <div><strong dir="auto">{row.description || row.message || '-'}</strong>
            <small>{row.line > 0 ? `${t('fimprows.col.line')} ${row.line} · ` : ''}<bdi dir="ltr">{row.posted_on ?? '-'}</bdi> · <bdi dir="ltr">{row.amount ? formatAmount(row.amount, currency) : '-'}</bdi></small></div>
          <div className="fimpRowWhat"><strong>{t(`fimprows.kind.${row.disposition satisfies FinanceRowDispositionKind}` as MessageKey)}</strong>
            {row.reason && <small className="muted">{t(`fimprows.reason.${row.reason}` as MessageKey)}</small>}
            {row.disposition === 'dropped_by_reader' && row.message && <small className="muted" dir="auto">{row.message}</small>}
            {row.split_group && row.disposition === 'attached_to_existing' && <>
              <small className="muted">{t('fimprows.split.records', { count: row.split_records?.length ?? 0 })}</small>
              <button type="button" className="secondary" disabled={busy} onClick={() => setUndoing(row.split_group ?? null)}>{t('fimprows.split.undo')}</button></>}</div>
        </li>)}</ul>
        {page.value.rows_total > PAGE && <div className="cmPager">
          <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>{t('fsms.prev')}</button>
          <span className="muted cmPagerText">{t('fsms.range', { from: offset + 1, to: Math.min(offset + PAGE, page.value.rows_total), total: page.value.rows_total })}</span>
          <button type="button" className="secondary" disabled={offset + PAGE >= page.value.rows_total} onClick={() => setOffset(offset + PAGE)}>{t('fsms.next')}</button></div>}
      </>)}
    <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('fimprows.close')}</button></div>
    {undoing && <ConfirmDialog title={t('fimprows.split.undoTitle')} confirmLabel={t('fimprows.split.undo')} danger busy={busy} onConfirm={() => void undo(undoing)} onCancel={() => setUndoing(null)}>
      <p>{t('fimprows.split.undoText')}</p>
    </ConfirmDialog>}
  </Modal>;
}
