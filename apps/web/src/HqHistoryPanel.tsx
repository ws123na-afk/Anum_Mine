/**
 * Office history: a searchable timeline of the workspace's stored audit records, with room and agent views,
 * a side view of one record and its sources, and the room history slider. Read only.
 * Data truth and gaps: docs/ANUM_MINE_HQ_HISTORY_PANEL.md.
 */
import { Download, Lock, RefreshCcw, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { OfficeHistoryEvidence } from '@anum/contracts';
import { HistoryFilterForm, RecordList, useSentinel, type PickOption } from './HqHistoryParts';
import { RecordDetail } from './HqHistoryDetail';
import { RoomSlider } from './HqRoomSlider';
import { useHqHistory } from './useHqHistory';
import { downloadAuditExport, getWorkspacePermissions } from './lib/adminApi';
import { downloadNarrowedExport } from './lib/hqHistoryExportApi';
import { exportNarrowing, narrowedFilename } from './lib/hqHistoryExport';
import { describeError, isPermissionDenied } from './lib/errors';
import { EMPTY_FILTERS, OFFICE_SCOPE, countActive, exportFilters, filtersActive, type HistoryFilters, type HistoryScope } from './lib/hqHistory';
import type { NameMap } from './lib/hqHistoryText';
import { t } from './lib/i18n';
import { useLocale } from './useLocale';
import './hqHistory.css';

export interface HistoryRoom extends PickOption { deskIds: readonly string[] }
export interface HistoryLaunch { id: number; scope: HistoryScope; tab?: 'timeline' | 'room' }
export interface HqHistoryPanelProps {
  launch: HistoryLaunch;
  rooms: readonly HistoryRoom[];
  agents: readonly PickOption[];
  names: NameMap;
  onClose: () => void;
  onLocateRoom?: (roomId: string) => void;
  onNavigate?: (hash: string) => void;
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea, [tabindex]:not([tabindex="-1"])';

export function HqHistoryPanel(props: HqHistoryPanelProps) {
  useLocale();
  const { launch, rooms, agents } = props;
  const [scope, setScope] = useState<HistoryScope>(launch.scope);
  const [tab, setTab] = useState<'timeline' | 'room'>(launch.tab ?? 'timeline');
  const [filters, setFilters] = useState<HistoryFilters>(EMPTY_FILTERS);
  const [selected, setSelected] = useState<OfficeHistoryEvidence | null>(null);
  const [sliderRoom, setSliderRoom] = useState(launch.scope.kind === 'room' ? launch.scope.roomId : '');
  const [exportNote, setExportNote] = useState<{ text: string; error: boolean } | null>(null);
  const [exporting, setExporting] = useState(false);
  // null until known; a failed lookup stays null and the server decides when the person clicks.
  const [mayExport, setMayExport] = useState<boolean | null>(null);
  useEffect(() => { let live = true; getWorkspacePermissions().then((p) => { if (live) setMayExport(p.canExportAudit); }).catch(() => undefined); return () => { live = false; }; }, []);
  const root = useRef<HTMLElement>(null);
  const opener = useRef<Element | null>(typeof document === 'undefined' ? null : document.activeElement);
  const lastRow = useRef<string | null>(null);

  // A new launch (another room or agent opened while the panel is open) replaces the view.
  useEffect(() => {
    setScope(launch.scope);
    setTab(launch.tab ?? 'timeline');
    setSelected(null);
    if (launch.scope.kind === 'room') setSliderRoom(launch.scope.roomId);
  }, [launch]);

  const history = useHqHistory(filters, scope, tab === 'timeline');
  const names = props.names;
  const roomIds = useMemo(() => new Set(rooms.map((r) => r.id)), [rooms]);

  const close = useCallback(() => {
    props.onClose();
    const target = opener.current;
    if (target instanceof HTMLElement && document.contains(target)) window.setTimeout(() => target.focus(), 0);
  }, [props]);

  useEffect(() => { root.current?.querySelector<HTMLElement>('[data-autofocus]')?.focus(); }, []);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key !== 'Tab' || !root.current) return;
    const nodes = [...root.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.offsetParent !== null);
    if (nodes.length === 0) return;
    const first = nodes[0];
    const lastNode = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); lastNode.focus(); }
    else if (!event.shiftKey && document.activeElement === lastNode) { event.preventDefault(); first.focus(); }
  };

  // Escape works wherever focus is (after a record closes the focus can sit on the page): it closes the open
  // record first, then the panel. It stops here so the office's own Escape handler does not also run.
  const escape = useRef<() => void>(() => undefined);
  escape.current = () => { if (selected) back(); else close(); };
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.stopPropagation();
      escape.current();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);

  const open = (record: OfficeHistoryEvidence) => { lastRow.current = record.id; setSelected(record); };
  function back() {
    setSelected(null);
    const id = lastRow.current;
    window.setTimeout(() => { if (id) root.current?.querySelector<HTMLElement>(`[data-record-id="${CSS.escape(id)}"]`)?.focus(); }, 0);
  }

  const roomScope = scope.kind === 'room' ? scope.roomId : '';
  const agentScope = scope.kind === 'agent' ? scope.agentId : '';
  const apply = (next: HistoryFilters, roomId: string, agentId: string) => {
    setFilters(next);
    setSelected(null);
    if (roomId) { const r = rooms.find((x) => x.id === roomId); if (r) { setScope({ kind: 'room', roomId, label: r.label, deskIds: r.deskIds }); setSliderRoom(roomId); } }
    else if (agentId) { const a = agents.find((x) => x.id === agentId); setScope({ kind: 'agent', agentId, name: a?.label ?? agentId }); }
    else if (scope.kind === 'room' || scope.kind === 'agent') setScope(OFFICE_SCOPE);
  };
  // From a record's source (task, run, approval, meeting): the stored records that name it, as a plain text search.
  const showRecordsNaming = (id: string) => { setScope(OFFICE_SCOPE); setFilters({ ...EMPTY_FILTERS, q: id }); setSelected(null); setTab('timeline'); };
  // From a record: the whole request (same correlation id) as the timeline, which can also be exported.
  const openRequest = (correlationId: string) => { setScope({ kind: 'correlation', correlationId }); setSelected(null); setTab('timeline'); };
  const reset = () => { setFilters(EMPTY_FILTERS); setScope(OFFICE_SCOPE); setSelected(null); };

  const more = useSentinel(history.loadOlder, history.status === 'ready' && history.state.cursor !== null && !history.busy, history.state.items.length + history.state.pagesRead);

  const csv = exportFilters(filters, scope);
  const exportKeep = exportNarrowing(filters, scope);
  const download = async () => {
    if (!csv) return;
    setExporting(true);
    setExportNote(null);
    try {
      const keep = exportNarrowing(filters, scope);
      if (keep) {
        const saved = await downloadNarrowedExport(csv, keep, narrowedFilename(scope, new Date().toISOString().slice(0, 10)));
        setExportNote({ text: saved.rows === 0 ? t('hqHist.export.none') : t('hqHist.export.doneRows', { name: saved.name, count: saved.rows }), error: false });
      } else {
        const name = await downloadAuditExport(csv, 'csv');
        setExportNote({ text: t('hqHist.export.done', { name }), error: false });
      }
    } catch (failure) {
      setExportNote({ text: isPermissionDenied(failure) ? t('hqHist.export.denied') : t('hqHist.export.error', { detail: describeError(failure) }), error: true });
    } finally { setExporting(false); }
  };

  const scopeLabel = scope.kind === 'room' ? t('hqHist.scope.room', { name: scope.label })
    : scope.kind === 'agent' ? t('hqHist.scope.agent', { name: scope.name })
      : scope.kind === 'correlation' ? t('hqHist.scope.correlation', { id: scope.correlationId }) : t('hqHist.scope.office');
  const { state, status } = history;
  const filtered = filtersActive(filters) || scope.kind !== 'office';

  return (
    <>
      <div className="hqHistScrim" onClick={close} aria-hidden="true" />
      <aside ref={root} className="hqHist" role="dialog" aria-modal="true" aria-labelledby="hqHistTitle" aria-label={undefined} onKeyDown={onKeyDown} data-testid="hq-history">
        <header className="hqHistHead">
          <div>
            <h2 id="hqHistTitle">{t('hqHist.title')}</h2>
            <p className="hqHistScope" data-scope={scope.kind}>{scopeLabel}
              {scope.kind !== 'office' && <button type="button" className="hqLinkButton" onClick={reset}>{t('hqHist.scope.clear')}</button>}
            </p>
          </div>
          <button type="button" className="hqIconButton" data-autofocus onClick={close} aria-label={t('hqHist.close')}><X aria-hidden="true" /></button>
        </header>
        <div className="hqHistTabs" role="tablist" aria-label={t('hqHist.region')}>
          {(['timeline', 'room'] as const).map((id) => (
            <button key={id} type="button" role="tab" id={`hqHistTab-${id}`} aria-selected={tab === id} aria-controls="hqHistBody" tabIndex={tab === id ? 0 : -1}
              onClick={() => { setTab(id); setSelected(null); }}
              onKeyDown={(e) => { if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); setTab(id === 'timeline' ? 'room' : 'timeline'); setSelected(null); } }}>
              {t(`hqHist.tab.${id}` as 'hqHist.tab.room')}
            </button>
          ))}
        </div>
        <div className="hqHistBody" id="hqHistBody" role="tabpanel" aria-labelledby={`hqHistTab-${tab}`}>
          {selected ? (
            <RecordDetail record={selected} names={names} roomIds={roomIds} onBack={back} onOpenRecord={open} onLocateRoom={props.onLocateRoom} onNavigate={props.onNavigate} onShowRecords={showRecordsNaming} onOpenRequest={openRequest} />
          ) : tab === 'room' ? (
            <RoomSlider rooms={rooms} roomId={sliderRoom} onPickRoom={setSliderRoom} names={names} onOpenRecord={open} />
          ) : (
            <>
              <p className="hqHistHint">{t('hqHist.intro')}</p>
              {scope.kind !== 'correlation' && (
                <HistoryFilterForm applied={filters} rooms={rooms} agents={agents} roomId={roomScope} agentId={agentScope}
                  activeCount={countActive(filters, scope)} onApply={apply} onReset={reset} />
              )}
              {(roomScope || agentScope) && <p className="hqHistHint">{t('hqHist.scope.note')}</p>}
              <div className="hqHistExport">
                {csv ? (
                  <button type="button" className="hqGhost" onClick={() => void download()} disabled={exporting || mayExport === false} title={t('hqHist.export.why')}>
                    <Download aria-hidden="true" />{exporting ? t('hqHist.export.working') : t('hqHist.export')}
                  </button>
                ) : <p className="hqHistHint">{t('hqHist.export.off')}</p>}
                {csv && mayExport === false && <p className="hqHistHint" role="note">{t('hqHist.export.denied')}</p>}
                {csv && exportKeep && <p className="hqHistHint">{t('hqHist.export.narrowed')}</p>}
                {exportNote && <p className={exportNote.error ? 'hqHistError' : 'hqHistHint'} role={exportNote.error ? 'alert' : 'status'}>{exportNote.text}</p>}
              </div>

              <div aria-live="polite" aria-busy={status === 'loading' || history.busy}>
                {status === 'loading' && <p className="hqHistLoading" role="status"><span className="hqSpinner" aria-hidden="true" />{t('hqHist.loading')}</p>}
                {status === 'denied' && (
                  <div className="hqHistDenied" role="alert"><Lock aria-hidden="true" /><p>{t('hqHist.denied')}</p></div>
                )}
                {(status === 'error' || status === 'invalid') && (
                  <div className="hqHistErrorBox" role="alert"><p>{t('hqHist.error', { detail: history.error })}</p>
                    <button type="button" className="hqGhost" onClick={history.reload}><RefreshCcw aria-hidden="true" />{t('hqHist.retry')}</button></div>
                )}
              </div>

              {status === 'ready' && (
                <>
                  {state.items.length === 0 && state.cursor === null && (
                    <div className="hqHistEmpty">
                      <h3>{t('hqHist.empty.title')}</h3>
                      <p>{scope.kind !== 'office' && scope.kind !== 'correlation' ? t('hqHist.empty.scoped') : filtered ? t('hqHist.empty.filtered') : t('hqHist.empty.plain')}</p>
                      {filtered ? <button type="button" className="hqGhost" onClick={reset}>{t('hqHist.empty.clear')}</button>
                        : <button type="button" className="hqGhost" onClick={() => props.onNavigate?.('#tasks')}>{t('hqHist.empty.openTasks')}</button>}
                    </div>
                  )}
                  {state.items.length > 0 && (
                    <>
                      <p className="hqHistCount" role="status">{t('hqHist.count', { count: state.items.length })}</p>
                      <RecordList items={state.items} names={names} onOpen={open} selectedId={lastRow.current} />
                    </>
                  )}
                  {history.error && <p className="hqHistError" role="alert">{t('hqHist.error', { detail: history.error })}</p>}
                  {state.cursor !== null ? (
                    <div className="hqHistMore">
                      <div ref={more} aria-hidden="true" className="hqHistSentinel" />
                      {state.paused && !history.busy && <p className="hqHistHint">{t('hqHist.morePaused')}</p>}
                      <button type="button" className="hqGhost" onClick={history.loadOlder} disabled={history.busy}>{history.busy ? t('hqHist.loadingMore') : t('hqHist.more')}</button>
                    </div>
                  ) : state.items.length > 0 && <p className="hqHistHint">{t('hqHist.end')}</p>}
                </>
              )}
              <p className="hqHistNotice">{t('hqHist.notice')}</p>
            </>
          )}
        </div>
      </aside>
    </>
  );
}

export default HqHistoryPanel;
