// The History tab of the office builder: past saved versions of the open floor, a preview of each with what it
// changed, and a governed "restore" (docs/ANUM_MINE_LAYOUT_HISTORY.md). Mounted only while the tab is open, so
// it costs nothing until used.
import { useCallback, useEffect, useRef, useState } from 'react';
import type { OfficeLayoutRevision, OfficeLayoutRevisionDetail, OfficeLayoutRevisionList } from '@anum/contracts';
import { formatDateTime, t } from '../lib/i18n.ts';
import type { HistoryApi } from './historyApi.ts';
import { historyProblem, revisionTitle, STATUS_ORDER, summaryText, type HistoryProblem } from './historyModel.ts';
import { MiniPlan } from './MiniPlan.tsx';
import { RestoreDialog } from './RestoreDialog.tsx';
import type { BuilderController } from './useBuilderState.ts';

interface Props { b: BuilderController; api: HistoryApi; editable: boolean }
type Mode = 'now' | 'saved';

function when(iso: string): string {
  const stamp = Date.parse(iso);
  return Number.isNaN(stamp) ? iso : formatDateTime(stamp, { dateStyle: 'medium', timeStyle: 'short' });
}

export function HistoryTab({ b, api, editable }: Props) {
  const floorId = b.floorId;
  const [list, setList] = useState<OfficeLayoutRevisionList | null>(null);
  const [items, setItems] = useState<OfficeLayoutRevision[]>([]);
  const [loading, setLoading] = useState(true);
  const [listProblem, setListProblem] = useState<HistoryProblem | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [mode, setMode] = useState<Mode>('now');
  const [detail, setDetail] = useState<OfficeLayoutRevisionDetail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailProblem, setDetailProblem] = useState<HistoryProblem | null>(null);
  const [dialog, setDialog] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restoreProblem, setRestoreProblem] = useState<HistoryProblem | null>(null);
  const [notice, setNotice] = useState('');
  const seq = useRef(0);

  const loadList = useCallback(async () => {
    if (!floorId) return;
    const mine = ++seq.current;
    setLoading(true);
    setListProblem(null);
    try {
      const page = await api.revisions(floorId);
      if (mine !== seq.current) return;
      setList(page);
      setItems(page.items);
    } catch (error) {
      if (mine === seq.current) setListProblem(historyProblem(error));
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [api, floorId]);

  useEffect(() => { setSelected(null); setDetail(null); setNotice(''); void loadList(); }, [loadList]);

  const loadMore = async () => {
    if (!list?.next_before || !floorId) return;
    setLoading(true);
    try {
      const page = await api.revisions(floorId, list.next_before);
      setList({ ...page, head_revision: list.head_revision });
      setItems((previous) => [...previous, ...page.items]);
    } catch (error) {
      setListProblem(historyProblem(error));
    } finally {
      setLoading(false);
    }
  };

  const loadDetail = useCallback(async (no: number, how: Mode) => {
    if (!floorId) return;
    setDetailBusy(true);
    setDetailProblem(null);
    try {
      setDetail(await api.revision(floorId, no, how === 'now' ? 'current' : 'previous'));
    } catch (error) {
      setDetail(null);
      setDetailProblem(historyProblem(error));
    } finally {
      setDetailBusy(false);
    }
  }, [api, floorId]);

  const pick = (revision: OfficeLayoutRevision) => {
    const how: Mode = revision.is_current ? 'saved' : 'now';
    setSelected(revision.revision_no);
    setMode(how);
    setNotice('');
    void loadDetail(revision.revision_no, how);
  };
  const switchMode = (how: Mode) => { if (selected == null) return; setMode(how); void loadDetail(selected, how); };

  const restore = async (confirmUnassign: string[]) => {
    if (!detail || !floorId) return;
    setRestoring(true);
    setRestoreProblem(null);
    try {
      const result = await api.restore(floorId, detail.revision.revision_no, {
        payload_hash: detail.payload_hash, expected_head: detail.head_revision, confirm_unassign: confirmUnassign,
      });
      setDialog(false);
      setNotice(result.noop ? t('hqh.notice.noop') : t('hqh.notice.restored', { from: detail.revision.revision_no, no: result.revision_no }));
      await b.reload();
      setSelected(null);
      setDetail(null);
      await loadList();
    } catch (error) {
      setRestoreProblem(historyProblem(error));
    } finally {
      setRestoring(false);
    }
  };

  // After a refused restore the page it was based on is out of date: show the floor as it is now.
  const reloadAfterRefusal = async () => {
    setRestoreProblem(null);
    setDialog(false);
    await loadList();
    if (selected != null) await loadDetail(selected, mode);
  };

  // The dialog always shows what a restore does to the floor as it is now.
  const openDialog = async () => {
    if (selected == null) return;
    setRestoreProblem(null);
    if (mode !== 'now') { setMode('now'); await loadDetail(selected, 'now'); }
    setDialog(true);
  };

  if (!floorId) return <p className="hqbHint">{t('hqb.floor.none')}</p>;

  const blockedByDraft = b.dirty;
  const canRestore = editable && (list?.can_restore ?? false);
  const selectedRevision = items.find((r) => r.revision_no === selected) ?? null;
  const changedRooms = detail ? detail.diff.rooms.filter((r) => r.status !== 'same') : [];

  return (
    <div className="hqhTab">
      <section className="hqbSection" aria-labelledby="hqh-title">
        <div className="hqbRow">
          <h3 id="hqh-title" className="hqbGrow">{t('hqh.title', { floor: b.floor?.name ?? '-' })}</h3>
          <button type="button" disabled={loading} onClick={() => void loadList()}>{t('hqh.refresh')}</button>
        </div>
        <p className="hqbHint">{list ? t('hqh.keep', { cap: list.cap }) : t('hqh.keepGeneric')}</p>
        {notice && <p className="hqbBanner" role="status" data-tone="info">{notice}</p>}
        {!editable && <p className="hqbHint" role="note">{t('hqh.viewOnly')}</p>}
        {blockedByDraft && editable && <p className="hqbHint" role="note" data-testid="history-draft-note">{t('hqh.draftBlocks')}</p>}
      </section>

      {loading && items.length === 0 && <p className="hqbHint" role="status">{t('hqh.loading')}</p>}
      {listProblem && (
        <div className="hqbCallout" role="alert">
          <p>{listProblem.kind === 'denied' ? t('hqh.err.deniedList') : listProblem.text}</p>
          {listProblem.kind !== 'denied' && <button type="button" onClick={() => void loadList()}>{t('hqb.common.retry')}</button>}
        </div>
      )}
      {!loading && !listProblem && items.length === 0 && (
        <div className="hqbCallout" data-testid="history-empty"><p>{t('hqh.empty')}</p></div>
      )}

      {items.length > 0 && (
        <ol className="hqhList" aria-label={t('hqh.listLabel')}>
          {items.map((r) => (
            <li key={r.revision_no}>
              <button
                type="button" className="hqhItem" aria-pressed={selected === r.revision_no} data-current={r.is_current ? 'true' : 'false'}
                onClick={() => pick(r)}
              >
                <span className="hqhItemHead">
                  <strong>{t('hqh.revision', { no: r.revision_no })}</strong>
                  {r.is_current && <span className="hqhBadge">{t('hqh.current')}</span>}
                </span>
                <span>{revisionTitle(r)}</span>
                <small>{summaryText(r.summary)}</small>
                <small><bdi>{r.actor}</bdi> {t('hqh.at')} {when(r.created_at)}</small>
              </button>
            </li>
          ))}
        </ol>
      )}
      {list?.next_before && <button type="button" disabled={loading} onClick={() => void loadMore()}>{t('hqh.more')}</button>}

      {selected != null && (
        <section className="hqbSection hqhDetail" aria-labelledby="hqh-detail-title" aria-busy={detailBusy}>
          <h3 id="hqh-detail-title">{t('hqh.detail.title', { no: selected })}</h3>
          <div className="hqhToggle" role="group" aria-label={t('hqh.detail.view')}>
            <button type="button" aria-pressed={mode === 'now'} onClick={() => switchMode('now')}>{t('hqh.detail.ifRestored')}</button>
            <button type="button" aria-pressed={mode === 'saved'} onClick={() => switchMode('saved')}>{t('hqh.detail.changedHere')}</button>
          </div>
          {detailBusy && <p className="hqbHint" role="status">{t('hqh.loading')}</p>}
          {detailProblem && (
            <div className="hqbCallout" role="alert">
              <p>{detailProblem.text}</p>
              <button type="button" onClick={() => void loadDetail(selected, mode)}>{t('hqb.common.retry')}</button>
            </div>
          )}
          {detail && !detailBusy && (
            <>
              <p className="hqbHint">{mode === 'now' ? t('hqh.detail.hintNow') : t('hqh.detail.hintSaved')}</p>
              <MiniPlan
                diff={detail.diff} floor={detail.floor}
                label={mode === 'now' ? t('hqh.plan.restoreLabel', { no: selected }) : t('hqh.plan.savedLabel', { no: selected })}
              />
              <ul className="hqhLegend" aria-label={t('hqh.legend')}>
                {STATUS_ORDER.map((s) => <li key={s}><span className="hqhChip" data-status={s}>{t(`hqh.status.${s}` as 'hqh.status.added')}</span></li>)}
              </ul>
              <p data-testid="history-summary">{summaryText(detail.diff.summary)}</p>
              {changedRooms.length > 0 && (
                <ul className="hqhChanges">
                  {changedRooms.slice(0, 20).map((r) => (
                    <li key={r.id}><span className="hqhChip" data-status={r.status}>{t(`hqh.status.${r.status}` as 'hqh.status.added')}</span> <bdi>{r.from_name ? `${r.from_name} > ${r.name}` : r.name}</bdi></li>
                  ))}
                  {changedRooms.length > 20 && <li>{t('hqb.err.more', { count: changedRooms.length - 20 })}</li>}
                </ul>
              )}
              {mode === 'now' && detail.unassign.length > 0 && (
                <p className="hqbHint" role="note">{t('hqh.detail.unassignNote', { count: detail.unassign.length })}</p>
              )}
              {mode === 'now' && detail.matches_current && <p className="hqbHint">{t('hqh.detail.same')}</p>}
              {canRestore && selectedRevision && !selectedRevision.is_current && (
                <div className="hqbRow">
                  <button
                    type="button" className="hqbPrimary" disabled={blockedByDraft} onClick={() => void openDialog()}
                    title={blockedByDraft ? t('hqh.draftBlocks') : undefined}
                  >
                    {t('hqh.restore', { no: selected })}
                  </button>
                </div>
              )}
              {!canRestore && <p className="hqbHint" role="note">{t('hqh.restoreDenied')}</p>}
            </>
          )}
        </section>
      )}

      {dialog && detail && detail.against === 'current' && (
        <RestoreDialog
          detail={detail} floorName={b.floor?.name ?? '-'} busy={restoring} problem={restoreProblem}
          onRestore={(ids) => void restore(ids)} onClose={() => setDialog(false)} onReload={() => void reloadAfterRefusal()}
        />
      )}
    </div>
  );
}
