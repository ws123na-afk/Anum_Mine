import { useEffect, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronUp, Hourglass, MapPin, RefreshCcw, X } from 'lucide-react';
import type { OfficeFlowItem, OfficeFlowSnapshot, OfficeFlowSourceState, OfficeFlowTrail } from '@anum/contracts';
import { t, formatDateTime, type MessageKey } from './lib/i18n';
import { roomNameFor } from './lib/registryNames.ts';
import { actionHash, actionText, ageText, changeAnnouncement, reasonText } from './lib/officeFlow';
import { flowClient, type ChangeBatch, type FlowLink, type FlowStatus } from './useOfficeFlow';
import { useLocale } from './useLocale';
import { FLOW_STALE_MS, flowLive } from './lib/hqPanelStatus.ts';
import { LiveChip } from './hq-nav/LiveChip';
import { useNow } from './hq-nav/useNow';
import { openAssign, openTaskPanel } from './lib/hqAssign';
import './hqFlow.css';

export interface FlowRoomInfo { label: string }
export interface HqFlowDockProps {
  snapshot: OfficeFlowSnapshot | null; status: FlowStatus; error: string; link: FlowLink; batch: ChangeBatch;
  rooms: ReadonlyMap<string, FlowRoomInfo>;
  open: boolean; onToggle: () => void; onReload: () => void;
  roomFilter: string | null; onRoomFilter: (roomId: string | null) => void;
  onFocusRoom: (roomId: string) => void; onNavigate: (hash: string) => void;
}

const STAGE_ORDER = ['waiting_human', 'in_office', 'arrived', 'done'] as const;

export function HqFlowDock(props: HqFlowDockProps) {
  useLocale();
  const { snapshot, status, error, link, batch, rooms, open, onToggle, onReload, roomFilter, onRoomFilter, onFocusRoom, onNavigate } = props;
  const [announce, setAnnounce] = useState('');
  const now = useNow(10_000);
  const roomName = (id: string | null) => (id ? rooms.get(id)?.label ?? id : t('hqFlow.unlocated'));
  useEffect(() => {
    if (!batch.changes.length) return;
    setAnnounce(batch.changes.slice(0, 3).map((c) => changeAnnouncement(c, roomName)).join(' '));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch.id]);

  const totals = snapshot?.totals;
  const shown = (snapshot?.items ?? []).filter((i) => !roomFilter || i.location?.room_id === roomFilter);
  const stuck = shown.filter((i) => i.stuck);
  const calm = shown.filter((i) => !i.stuck);
  const summary = totals
    ? t('hqFlow.statusLine', { items: totals.items - totals.by_stage.done, waiting: totals.waiting_human, stuck: totals.stuck })
    : status === 'denied' ? t('hqFlow.denied') : status === 'error' ? t('hqFlow.error', { detail: error }) : t('hqFlow.loading');

  return <section className="hqFlowDock" aria-label={t('hqFlow.region')} data-open={open} data-status={status}>
    <div className="hqFlowBar">
      <button type="button" className="hqFlowToggle" aria-expanded={open} aria-controls="hqFlowBody" onClick={onToggle}
        title={open ? t('hqFlow.collapse') : t('hqFlow.expand')}>
        {totals && totals.critical > 0 ? <AlertTriangle size={16} aria-hidden="true" /> : <Hourglass size={16} aria-hidden="true" />}
        <strong>{t('hqFlow.title')}</strong>
        <span className="hqFlowSummary">{summary}</span>
        {open ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronUp size={16} aria-hidden="true" />}
      </button>
      <LiveChip state={flowLive(link, status)} at={snapshot?.as_of ?? null} staleAfterMs={FLOW_STALE_MS} nowMs={now} pollingText={t('hqPanels.flow.polling')} testId="hq-flow-live" />
    </div>
    <div className="hqFlowLive" role="status" aria-live="polite" aria-atomic="true">{announce}</div>
    {open && <div id="hqFlowBody" className="hqFlowBody">
      {status === 'loading' && <p className="hqFlowNote" role="status">{t('hqFlow.loading')}</p>}
      {status === 'denied' && <p className="hqFlowNote" role="alert">{t('hqFlow.denied')}</p>}
      {status === 'error' && <div className="hqFlowNote" role="alert">
        <p>{t('hqFlow.error', { detail: error })}</p>
        <button type="button" onClick={onReload}><RefreshCcw size={14} aria-hidden="true" /> {t('hqFlow.retry')}</button>
      </div>}
      {link === 'offline' && snapshot && <p className="hqFlowNote" role="status">
        {t('hqFlow.offline', { time: formatDateTime(snapshot.as_of, { timeStyle: 'medium' }) })} {error}
        <button type="button" onClick={onReload}>{t('hqFlow.retry')}</button>
      </p>}
      {snapshot && <>
        {roomFilter && <p className="hqFlowFilter">{t('hqFlow.filtered', { room: roomName(roomFilter) })}
          <button type="button" onClick={() => onRoomFilter(null)}><X size={13} aria-hidden="true" /> {t('hqFlow.clearFilter')}</button></p>}
        <SourceNotes snapshot={snapshot} />
        <h3 className="hqFlowH">{t('hqFlow.stuckTitle')} <span>{t('hqFlow.stuckCount', { count: stuck.length })}</span></h3>
        {stuck.length === 0 ? <p className="hqFlowNote">{t('hqFlow.noStuck')}</p> :
          <ul className="hqFlowList" aria-label={t('hqFlow.stuckTitle')}>
            {stuck.map((item) => <FlowCard key={item.key} item={item} roomName={roomName} onFocusRoom={onFocusRoom} onNavigate={onNavigate} />)}
          </ul>}
        <h3 className="hqFlowH">{t('hqFlow.inProgress')}</h3>
        {calm.length === 0 ? <div className="hqFlowNote">
          <p>{roomFilter ? t('hqFlow.noItemsInRoom') : t('hqFlow.empty')}</p>
          {!roomFilter && <button type="button" onClick={() => { if (!openAssign({ source: 'flow_dock' })) onNavigate('#tasks'); }}>{t('hqFlow.emptyAction')}</button>}
        </div> : STAGE_ORDER.map((stage) => {
          const rows = calm.filter((i) => i.stage === stage);
          if (!rows.length) return null;
          return <div key={stage} className="hqFlowGroup">
            <h4>{t(`hqFlow.stage.${stage}` as MessageKey)} <span>{rows.length}</span></h4>
            <ul className="hqFlowList" aria-label={t(`hqFlow.stage.${stage}` as MessageKey)}>
              {rows.map((item) => <FlowCard key={item.key} item={item} roomName={roomName} onFocusRoom={onFocusRoom} onNavigate={onNavigate} />)}
            </ul>
          </div>;
        })}
        {snapshot.truncated && <p className="hqFlowNote">{t('hqFlow.truncated', { count: snapshot.items.length })}</p>}
        <details className="hqFlowRules">
          <summary>{t('hqFlow.rules')}</summary>
          <ul>{Object.entries(snapshot.thresholds).map(([key, value]) =>
            <li key={key}>{t(`hqFlow.rule.${key}` as MessageKey, { value })}</li>)}</ul>
        </details>
        <p className="hqFlowLegend">{t('hqFlow.legend')} {t('hqFlow.asOf', { time: formatDateTime(snapshot.as_of, { timeStyle: 'medium' }) })}</p>
      </>}
    </div>}
  </section>;
}

function SourceNotes({ snapshot }: { snapshot: OfficeFlowSnapshot }) {
  const bad = (Object.entries(snapshot.sources) as [keyof OfficeFlowSnapshot['sources'], OfficeFlowSourceState][]).filter(([, s]) => s !== 'connected');
  if (!bad.length) return null;
  return <ul className="hqFlowSources" aria-label={t('hqFlow.sources')}>
    {bad.map(([name, state]) => <li key={name} data-state={state}>
      <strong>{t(`hqFlow.source.${name}` as MessageKey)}:</strong> {t(`hqFlow.sourceState.${state}` as MessageKey)}.
      {' '}{state === 'no_access' ? t('hqFlow.sourceHint.no_access') : state === 'not_connected' ? t('hqFlow.sourceHint.not_connected') : ''}
    </li>)}
  </ul>;
}

function FlowCard({ item, roomName, onFocusRoom, onNavigate }: {
  item: OfficeFlowItem; roomName: (id: string | null) => string;
  onFocusRoom: (roomId: string) => void; onNavigate: (hash: string) => void;
}) {
  const [history, setHistory] = useState<'closed' | 'loading' | OfficeFlowTrail | 'failed'>('closed');
  const stuck = item.stuck;
  const loc = item.location;
  const toggleHistory = (event: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (!event.currentTarget.open || (history !== 'closed' && history !== 'failed')) return;
    setHistory('loading');
    flowClient.trail(item.kind, item.id).then(setHistory, () => setHistory('failed'));
  };
  return <li className="hqFlowCard" data-stuck={stuck?.severity ?? 'none'} data-stage={item.stage} data-kind={item.kind}>
    <div className="hqFlowCardHead">
      <span className="hqFlowKind">{t(`hqFlow.kind.${item.kind}` as MessageKey)}</span>
      <strong>{item.title}</strong>
      <span className="hqFlowStage">{t(`hqFlow.stage.${item.stage}` as MessageKey)}</span>
    </div>
    <p className="hqFlowWhere">
      <MapPin size={13} aria-hidden="true" />
      {loc ? <>{t('hqFlow.where', { room: roomNameFor(loc.room_id, loc.room_name) })}{loc.agent_name ? ` · ${loc.agent_name}` : ''}{' '}
        <button type="button" className="hqLinkButton" aria-label={t('hqFlow.showRoom', { room: roomNameFor(loc.room_id, loc.room_name) })} onClick={() => onFocusRoom(loc.room_id)}>
          {t('hqFlow.showInOffice')}</button></>
        : <span>{item.location_reason ? t(`hqFlow.noOffice.${item.location_reason}` as MessageKey) : t('hqFlow.unlocated')}</span>}
    </p>
    {item.waiting && !stuck && <p className="hqFlowWait">{item.waiting.action ? t('hqFlow.waitingFor', { action: item.waiting.action }) : t('hqFlow.waitingForPlain')}</p>}
    {stuck && <div className="hqFlowStuck" role="group" aria-label={t('hqFlow.stuckSince', { age: ageText(stuck.age_seconds) })}>
      <p className="hqFlowReason"><AlertTriangle size={14} aria-hidden="true" /> <span>{reasonText(item)}</span></p>
      <p className="hqFlowNext"><span>{t('hqFlow.next')}</span>
        <button type="button" onClick={() => {
          // A task opens beside the office; every other next step goes to the page that owns it.
          if (item.kind === 'task' && (stuck.action === 'open_task' || stuck.action === 'retry_task') && openTaskPanel(item.id)) return;
          onNavigate(actionHash(stuck.action));
        }}>{actionText(stuck.action)}</button>
        {loc?.agent_id && <button type="button" onClick={() => openAssign({ agentId: loc.agent_id, roomId: loc.room_id, roomName: roomNameFor(loc.room_id, loc.room_name), source: 'flow_dock' })}>{t('hqAssign.openFor', { name: loc.agent_name ?? '' })}</button>}</p>
    </div>}
    <details className="hqFlowHistory" onToggle={toggleHistory}>
      <summary>{t('hqFlow.history')}</summary>
      {history === 'loading' && <p role="status">{t('hqFlow.loading')}</p>}
      {history === 'failed' && <p role="alert">{t('hqFlow.historyFailed')}</p>}
      {typeof history === 'object' && (history.source === 'not_recorded' ? <p>{t('hqFlow.historyNotRecorded')}</p>
        : history.source === 'not_connected' ? <p>{t('hqFlow.historyNotConnected')}</p>
        : history.steps.length === 0 ? <p>{t('hqFlow.historyEmpty')}</p>
        : <ol>{history.steps.map((s) => <li key={s.id}><time dateTime={s.at}>{formatDateTime(s.at, { dateStyle: 'short', timeStyle: 'medium' })}</time> <code>{s.type}</code>
          {s.data.to_status ? ` ${s.data.from_status ?? ''} > ${s.data.to_status}` : ''}</li>)}</ol>)}
    </details>
  </li>;
}

