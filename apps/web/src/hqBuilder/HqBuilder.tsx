// The owner's visual office builder (docs/help/office-builder.md). A 2D plan of one floor in metres:
// drag, resize and rotate rooms, place desks, edit room details, with every change staged as a draft
// that is saved as one atomic layout. Only owners (workspace:manage) edit; everyone else gets a
// read-only view with the reason. All data comes from /api/v1/office-spaces; nothing is kept in
// the browser.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { ArrowLeft, Maximize2, Redo2, RefreshCw, RotateCw, Save, Undo2, X, ZoomIn, ZoomOut } from 'lucide-react';
import * as api from '../lib/api.ts';
import { loadOrgSources } from '../lib/orgChart.ts';
import { createOfficeSpaceClient } from '../lib/officeSpaceClient.ts';
import { t, type MessageKey } from '../lib/i18n.ts';
import { useLocale } from '../useLocale';
import { createBuilderApi } from './builderApi.ts';
import { changedDeskIds, changedRoomIds } from './draftModel.ts';
import { explainIssue } from './explain.ts';
import { planView } from './geometry.ts';
import { SNAP_STEPS } from './labels.ts';
import { PLAN_MARGIN_M, PlanCanvas, type CanvasDesk, type CanvasRoom } from './PlanCanvas.tsx';
import { FloorTab } from './FloorTab.tsx';
import { RoomDetails } from './RoomDetails.tsx';
import { RoomsTab } from './RoomsTab.tsx';
import { roomSeverity } from './validate.ts';
import type { Person } from './DeskPanel.tsx';
import { useBuilder } from './useBuilderState.ts';
import './hqBuilder.css';

const builderApi = createBuilderApi(api.apiRequest, api.apiRequestVoid);
const officeClient = createOfficeSpaceClient(api.apiRequest, api.apiRequestVoid, api.currentScope);

type Tab = 'room' | 'rooms' | 'floor';
type Role = { state: 'checking' } | { state: 'known'; role: string } | { state: 'unknown' };

function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const list = window.matchMedia(query);
    const on = () => setMatches(list.matches);
    on();
    list.addEventListener('change', on);
    return () => list.removeEventListener('change', on);
  }, [query]);
  return matches;
}

export function HqBuilder({ onClose }: { onClose: () => void }) {
  useLocale();
  const scopeKey = JSON.stringify(api.currentScope());
  const b = useBuilder({ api: builderApi, load: officeClient.load, scopeKey });
  const [role, setRole] = useState<Role>({ state: 'checking' });
  const [people, setPeople] = useState<Person[]>([]);
  const [peopleReady, setPeopleReady] = useState(false);
  const [tab, setTab] = useState<Tab>('rooms');
  const [snapStep, setSnapStep] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [announcement, setAnnouncement] = useState('');
  const [panelOpen, setPanelOpen] = useState(true);
  const [fitPpm, setFitPpm] = useState(12);
  const narrow = useMedia('(max-width: 900px)');
  const reducedMotion = useMedia('(prefers-reduced-motion: reduce)');
  const stageRef = useRef<HTMLDivElement | null>(null);

  // Who may edit: the owner role of this workspace (the server still checks every write).
  useEffect(() => {
    let active = true;
    setRole({ state: 'checking' });
    api.getCurrentMembership().then((mine) => {
      if (!active) return;
      setRole(mine.active ? { state: 'known', role: mine.role } : { state: 'unknown' });
    }).catch(() => { if (active) setRole({ state: 'unknown' }); });
    return () => { active = false; };
  }, [scopeKey]);
  const isOwner = role.state === 'known' && role.role === 'owner';
  const editable = isOwner;

  useEffect(() => {
    let active = true;
    setPeopleReady(false);
    loadOrgSources(api.apiRequest).then((src) => { if (active) { setPeople(src.agents.map((a) => ({ id: a.id, name: a.name }))); setPeopleReady(true); } })
      .catch(() => { if (active) { setPeople([]); setPeopleReady(false); } });
    return () => { active = false; };
  }, [scopeKey]);

  // A room picked on the plan opens its details.
  useEffect(() => { if (b.selectedRoomId) { setTab('room'); if (narrow) setPanelOpen(true); } }, [b.selectedRoomId, narrow]);
  useEffect(() => { setPanelOpen(!narrow); }, [narrow]);
  useEffect(() => { if (b.notice) setAnnouncement(b.notice); }, [b.notice]);

  // Leaving with unsaved placement asks first.
  useEffect(() => {
    if (!b.dirty) return undefined;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [b.dirty]);

  const canvasRooms = useMemo<CanvasRoom[]>(() => b.floorRecords.flatMap((r) => {
    const draft = b.present.rooms[r.id];
    return draft ? [{ id: r.id, name: r.name, kind: r.kind, color: r.color ?? null, draft }] : [];
  }), [b.floorRecords, b.present.rooms]);
  const canvasDesks = useMemo<CanvasDesk[]>(() => {
    const ids = new Set(b.floorRecords.map((r) => r.id));
    return b.data.desks.filter((d) => ids.has(d.room_id) && b.present.desks[d.id]).map((d) => ({ id: d.id, label: d.label, draft: b.present.desks[d.id], seated: d.agent_id != null }));
  }, [b.data.desks, b.floorRecords, b.present.desks]);
  const severity = useMemo(() => roomSeverity(b.issues), [b.issues]);
  const placedRooms = canvasRooms.filter((r) => r.draft.placed);
  const changeCount = useMemo(() => changedRoomIds(b.base, b.present).length + changedDeskIds(b.base, b.present).length, [b.base, b.present]);

  // Fit the whole floor into the stage, then let the owner zoom from there.
  useLayoutEffect(() => {
    const node = stageRef.current;
    if (!node || typeof ResizeObserver === 'undefined') return undefined;
    const view = planView(placedRooms.map((r) => ({ id: r.id, x: r.draft.x, z: r.draft.z, width: r.draft.width, depth: r.draft.depth })), b.bounds);
    const measure = () => {
      const availableW = node.clientWidth - 18;
      const availableH = node.clientHeight - 18;
      if (availableW > 0 && availableH > 0) {
        // Fit the whole floor on screen, by width or by height, whichever is tighter.
        setFitPpm(Math.max(2, Math.min(60, availableW / (view.width + PLAN_MARGIN_M * 2), availableH / (view.depth + PLAN_MARGIN_M * 2))));
      }
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [b.bounds, placedRooms.length, b.floorId, b.phase]);
  const pxPerMetre = Math.max(2, Math.min(160, fitPpm * zoom));

  const cycle = useCallback((direction: 1 | -1) => {
    const ordered = [...placedRooms].sort((x, y) => x.name.localeCompare(y.name));
    if (!ordered.length) return;
    const at = ordered.findIndex((r) => r.id === b.selectedRoomId);
    const next = ordered[(at + direction + ordered.length) % ordered.length];
    b.setSelectedRoomId(next.id);
    window.requestAnimationFrame(() => {
      document.querySelector<SVGGElement>(`.hqbSvg [data-room-id="${CSS.escape(next.id)}"]`)?.focus();
    });
  }, [placedRooms, b]);

  const confirmDiscard = (): boolean => !b.dirty || window.confirm(t('hqb.confirm.discard'));
  const nameForIssue = (id: string): string => b.nameOf(id);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); if (editable && b.dirty && !b.blocked && !b.busy) void b.save(); return; }
    if (typing || !mod) return;
    const key = e.key.toLowerCase();
    if (key === 'z' && !e.shiftKey) { e.preventDefault(); b.undo(); setAnnouncement(t('hqb.live.undone')); }
    else if ((key === 'z' && e.shiftKey) || key === 'y') { e.preventDefault(); b.redo(); setAnnouncement(t('hqb.live.redone')); }
  };

  const selectedRoom = canvasRooms.find((r) => r.id === b.selectedRoomId);
  const placedCount = placedRooms.length;
  const deskCount = canvasDesks.length;
  const floors = b.data.floors.filter((f) => f.building_id === b.buildingId).sort((x, y) => x.level - y.level);
  const errorCount = b.issues.filter((i) => i.severity === 'error').length;
  const warningCount = b.issues.length - errorCount;

  if (b.phase === 'loading') {
    return <BuilderFrame onClose={onClose}><p className="hqbState" role="status">{t('hqb.state.loading')}</p></BuilderFrame>;
  }
  if (b.phase === 'error') {
    return (
      <BuilderFrame onClose={onClose}>
        <div className="hqbState" role="alert">
          <h2>{b.loadError?.forbidden ? t('hqb.state.forbiddenTitle') : t('hqb.state.errorTitle')}</h2>
          <p>{b.loadError?.forbidden ? t('hqb.state.forbiddenBody') : b.loadError?.unavailable ? t('hqb.state.unavailableBody') : t('hqb.state.errorBody')}</p>
          <button type="button" onClick={() => void b.reload()}>{t('hqb.common.retry')}</button>
        </div>
      </BuilderFrame>
    );
  }
  if (b.data.buildings.length === 0 || floors.length === 0) {
    return (
      <BuilderFrame onClose={onClose}>
        <div className="hqbState">
          <h2>{b.data.buildings.length === 0 ? t('hqb.state.noBuildingTitle') : t('hqb.state.noFloorTitle')}</h2>
          <p>{t('hqb.state.noBuildingBody')}</p>
          <button type="button" onClick={onClose}>{t('hqb.common.backToManagement')}</button>
        </div>
      </BuilderFrame>
    );
  }

  return (
    <div className="hqb" role="dialog" aria-modal="false" aria-label={t('hqb.title')} data-reduced-motion={reducedMotion ? 'true' : undefined} onKeyDown={onKeyDown}>
      <header className="hqbHeader">
        <div className="hqbTitle"><small>{t('hqb.eyebrow')}</small><h2>{t('hqb.title')}</h2></div>
        <label className="hqbInline"><span>{t('hqb.header.building')}</span>
          <select value={b.buildingId} onChange={(e) => { if (confirmDiscard()) b.setBuildingId(e.target.value); }}>
            {b.data.buildings.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
        </label>
        <label className="hqbInline"><span>{t('hqb.header.floor')}</span>
          <select value={b.floorId} onChange={(e) => { if (confirmDiscard()) b.setFloorId(e.target.value); }}>
            {floors.map((f) => <option key={f.id} value={f.id}>{f.level === 0 ? 'G' : f.level} · {f.name}</option>)}
          </select>
        </label>
        <div className="hqbHeaderEnd">
          <button type="button" disabled={b.busy} aria-label={t('hqb.header.refresh')} title={t('hqb.header.refresh')} onClick={() => { if (confirmDiscard()) void b.reload(); }}><RefreshCw size={15} aria-hidden="true" /><span className="hqbBtnText">{t('hqb.header.refresh')}</span></button>
          <button type="button" aria-label={t('hqb.common.backToManagement')} title={t('hqb.common.backToManagement')} onClick={() => { if (confirmDiscard()) onClose(); }}><ArrowLeft size={15} aria-hidden="true" /><span className="hqbBtnText">{t('hqb.common.backToManagement')}</span></button>
        </div>
      </header>

      {!isOwner && role.state !== 'checking' && (
        <div className="hqbBanner" role="note" data-tone="info">
          <strong>{t('hqb.readonly.title')}</strong>
          <span>{role.state === 'known' ? t('hqb.readonly.body', { role: role.role }) : t('hqb.readonly.unknown')}</span>
        </div>
      )}
      {b.problem && (
        <div className="hqbBanner" role="alert" data-tone="error">
          <div>
            <strong>{b.problem.title}</strong>
            {b.problem.forbidden && <span> {t('hqb.readonly.body', { role: role.state === 'known' ? role.role : '-' })}</span>}
            {b.problem.lines.length > 0 && <ul>{b.problem.lines.slice(0, 8).map((line) => <li key={line}>{line}</li>)}{b.problem.lines.length > 8 && <li>{t('hqb.err.more', { count: b.problem.lines.length - 8 })}</li>}</ul>}
          </div>
          <button type="button" aria-label={t('hqb.common.dismiss')} onClick={() => b.setProblem(null)}><X size={14} aria-hidden="true" /></button>
        </div>
      )}
      {b.stale && (
        <div className="hqbBanner hqbStale" role="alertdialog" aria-labelledby="hqb-stale-title" data-tone="warn">
          <div>
            <strong id="hqb-stale-title">{t('hqb.stale.title')}</strong>
            <p>{t('hqb.stale.body', { count: changeCount })}</p>
          </div>
          <div className="hqbRow">
            <button type="button" className="hqbPrimary" disabled={b.busy} onClick={() => void b.reloadAndReapply()}>{t('hqb.stale.reapply')}</button>
            <button type="button" disabled={b.busy} onClick={() => void b.reloadDiscarding()}>{t('hqb.stale.discard')}</button>
          </div>
        </div>
      )}
      {b.reapplied && (
        <div className="hqbBanner" role="status" data-tone="info">
          <div>
            <strong>{t('hqb.reapplied.title', { count: b.reapplied.kept })}</strong>
            {b.reapplied.dropped.length > 0 && (
              <ul>{b.reapplied.dropped.map((d) => <li key={`${d.kind}:${d.id}`}>{t(d.reason === 'removed' ? 'hqb.reapplied.removed' : 'hqb.reapplied.changed', { name: nameForIssue(d.id) })}</li>)}</ul>
            )}
            <p>{t('hqb.reapplied.review')}</p>
          </div>
          <button type="button" aria-label={t('hqb.common.dismiss')} onClick={b.clearReapplied}><X size={14} aria-hidden="true" /></button>
        </div>
      )}

      <div className="hqbToolbar" role="toolbar" aria-label={t('hqb.toolbar.label')}>
        {editable && (
          <>
            <button type="button" disabled={!b.canUndo || b.busy} onClick={b.undo} aria-label={t('hqb.toolbar.undo')} title={t('hqb.toolbar.undoHint')}><Undo2 size={15} aria-hidden="true" /><span className="hqbBtnText">{t('hqb.toolbar.undo')}</span></button>
            <button type="button" disabled={!b.canRedo || b.busy} onClick={b.redo} aria-label={t('hqb.toolbar.redo')} title={t('hqb.toolbar.redoHint')}><Redo2 size={15} aria-hidden="true" /><span className="hqbBtnText">{t('hqb.toolbar.redo')}</span></button>
            <button type="button" aria-label={t('hqb.toolbar.rotate')} title={t('hqb.toolbar.rotate')} disabled={!selectedRoom?.draft.placed || b.busy} onClick={() => { b.rotate(b.selectedRoomId); setAnnouncement(t('hqb.live.rotated', { name: selectedRoom?.name ?? '' })); }}><RotateCw size={15} aria-hidden="true" /><span className="hqbBtnText">{t('hqb.toolbar.rotate')}</span></button>
          </>
        )}
        <label className="hqbInline"><span>{t('hqb.toolbar.snap')}</span>
          <select value={snapStep} onChange={(e) => setSnapStep(Number(e.target.value))}>
            {SNAP_STEPS.map((s) => <option key={s} value={s}>{s === 0 ? t('hqb.toolbar.snapOff') : t('hqb.toolbar.snapStep', { step: s })}</option>)}
          </select>
        </label>
        <button type="button" aria-label={t('hqb.toolbar.zoomOut')} onClick={() => setZoom((z) => Math.max(0.25, z / 1.25))}><ZoomOut size={15} aria-hidden="true" /></button>
        <button type="button" aria-label={t('hqb.toolbar.zoomIn')} onClick={() => setZoom((z) => Math.min(8, z * 1.25))}><ZoomIn size={15} aria-hidden="true" /></button>
        <button type="button" aria-label={t('hqb.toolbar.fit')} onClick={() => setZoom(1)}><Maximize2 size={15} aria-hidden="true" /></button>
        <span className="hqbSpacer" />
        {editable && (
          <div className="hqbSaveBar" data-dirty={b.dirty ? 'true' : 'false'}>
            <span className="hqbDirty" role="status" data-testid="draft-status">
              {b.dirty ? t('hqb.draft.unsaved', { count: changeCount }) : t('hqb.draft.saved')}
            </span>
            <button type="button" disabled={!b.dirty || b.busy} onClick={b.discard}>{t('hqb.draft.discard')}</button>
            <button type="button" className="hqbPrimary" disabled={!b.dirty || b.blocked || b.busy} onClick={() => void b.save()} title={b.blocked ? t('hqb.draft.blocked') : undefined}>
              <Save size={15} aria-hidden="true" /> {b.busy ? t('hqb.common.saving') : t('hqb.draft.save')}
            </button>
          </div>
        )}
      </div>

      <div className="hqbBody" data-panel={panelOpen ? 'open' : 'closed'}>
        <div className="hqbStage" ref={stageRef}>
          <div className="hqbScroll" tabIndex={-1}>
            <PlanCanvas
              rooms={canvasRooms} desks={canvasDesks} bounds={b.bounds} severity={severity}
              selectedRoomId={b.selectedRoomId} selectedDeskId={b.selectedDeskId} snapStep={snapStep} pxPerMetre={pxPerMetre} editable={editable}
              onSelectRoom={b.setSelectedRoomId} onSelectDesk={b.setSelectedDeskId}
              onMoveRoom={(id, rect, key) => b.moveRoom(id, rect, key)} onMoveDesk={(id, x, z, key) => b.moveDesk(id, x, z, key)}
              onRotate={b.rotate} onCycle={cycle} onAnnounce={setAnnouncement}
            />
          </div>
          {placedCount === 0 && (
            <div className="hqbEmpty">
              <h3>{t('hqb.empty.title')}</h3>
              <p>{editable ? t('hqb.empty.bodyOwner') : t('hqb.empty.bodyViewer')}</p>
              {editable && <button type="button" onClick={() => { setTab('rooms'); setPanelOpen(true); }}>{t('hqb.empty.action')}</button>}
            </div>
          )}
        </div>

        {narrow && (
          <button type="button" className="hqbSheetToggle" aria-expanded={panelOpen} onClick={() => setPanelOpen((v) => !v)}>
            {panelOpen ? t('hqb.sheet.hide') : t('hqb.sheet.show')}
          </button>
        )}
        <aside className="hqbPanel" aria-label={t('hqb.panel.label')} hidden={narrow && !panelOpen}>
          <div className="hqbTabs" role="tablist" aria-label={t('hqb.panel.label')}>
            {(['room', 'rooms', 'floor'] as const).map((id) => (
              <button key={id} type="button" role="tab" id={`hqb-tab-${id}`} aria-selected={tab === id} aria-controls="hqb-tabpanel" onClick={() => setTab(id)}>{t(`hqb.tab.${id}` as MessageKey)}</button>
            ))}
          </div>
          <div className="hqbTabPanel" role="tabpanel" id="hqb-tabpanel" aria-labelledby={`hqb-tab-${tab}`}>
            {tab === 'room' && (b.selectedRoomId
              ? <RoomDetails b={b} api={builderApi} roomId={b.selectedRoomId} people={people} peopleReady={peopleReady} editable={editable} />
              : <p className="hqbHint">{t('hqb.room.pick')}</p>)}
            {tab === 'rooms' && <RoomsTab b={b} editable={editable} />}
            {tab === 'floor' && <FloorTab b={b} editable={editable} />}
          </div>
        </aside>
      </div>

      <footer className="hqbStatus">
        <span>{t('hqb.status.floor', { name: b.floor?.name ?? '-', rooms: placedCount, desks: deskCount })}</span>
        <span>{b.bounds ? t('hqb.canvas.floorSize', { width: b.bounds.width, depth: b.bounds.depth }) : t('hqb.status.noBounds')}</span>
        <details className="hqbIssues" data-has-issues={b.issues.length ? 'true' : 'false'}>
          <summary>{b.issues.length === 0 ? t('hqb.status.noProblems') : t('hqb.status.problems', { errors: errorCount, warnings: warningCount })}</summary>
          {b.issues.length > 0 && (
            <ul>
              {b.issues.slice(0, 30).map((issue, i) => (
                <li key={`${issue.code}:${issue.roomId}:${issue.otherRoomId ?? issue.deskId ?? ''}:${i}`} data-severity={issue.severity}>
                  <button type="button" onClick={() => b.setSelectedRoomId(issue.roomId)}>
                    {explainIssue(issue.code, issue.deskId ? nameForIssue(issue.deskId) : nameForIssue(issue.roomId), issue.otherRoomId ? nameForIssue(issue.otherRoomId) : '')}
                    {issue.severity === 'warning' ? ` (${t('hqb.status.existing')})` : ''}
                  </button>
                </li>
              ))}
              {b.issues.length > 30 && <li>{t('hqb.err.more', { count: b.issues.length - 30 })}</li>}
            </ul>
          )}
        </details>
      </footer>
      <p className="hqbVisuallyHidden" role="status" aria-live="polite" data-testid="builder-live">{announcement}</p>
      <p className="hqbVisuallyHidden" id="hqb-keys">{t('hqb.keys.help')}</p>
    </div>
  );
}

function BuilderFrame({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="hqb hqbFramed" role="dialog" aria-modal="false" aria-label={t('hqb.title')}>
      <header className="hqbHeader">
        <div className="hqbTitle"><small>{t('hqb.eyebrow')}</small><h2>{t('hqb.title')}</h2></div>
        <div className="hqbHeaderEnd"><button type="button" onClick={onClose}><ArrowLeft size={15} aria-hidden="true" /> {t('hqb.common.backToManagement')}</button></div>
      </header>
      {children}
    </div>
  );
}
