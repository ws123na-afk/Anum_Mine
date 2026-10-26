import { ChevronRight, Filter, Search } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import type { OfficeHistoryEvidence } from '@anum/contracts';
import { CATEGORY_IDS, EMPTY_FILTERS, dateWindow, groupByDay, type HistoryFilters } from './lib/hqHistory';
import { actionLabel, categoryLabel, outcomeLabel, timeOnly, whoLabel, type NameMap } from './lib/hqHistoryText';
import { getLocale, intlLocale, t } from './lib/i18n';

export interface PickOption { id: string; label: string }

/** The filter form. It validates dates before anything is requested and applies only on submit. */
export function HistoryFilterForm(props: {
  applied: HistoryFilters; rooms: readonly PickOption[]; agents: readonly PickOption[]; roomId: string; agentId: string;
  onApply: (filters: HistoryFilters, roomId: string, agentId: string) => void; onReset: () => void; activeCount: number;
}) {
  const [draft, setDraft] = useState(props.applied);
  const [room, setRoom] = useState(props.roomId);
  const [agent, setAgent] = useState(props.agentId);
  const [open, setOpen] = useState(props.activeCount > 0);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setDraft(props.applied); setRoom(props.roomId); setAgent(props.agentId); }, [props.applied, props.roomId, props.agentId]);
  const set = <K extends keyof HistoryFilters>(key: K, value: HistoryFilters[K]) => setDraft((d) => ({ ...d, [key]: value }));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const window = dateWindow(draft);
    if (!window.ok) { setError(t(`hqHist.err.${window.error}` as 'hqHist.err.badFrom')); return; }
    if (draft.q.length > 200 || draft.actor.length > 160) { setError(t('hqHist.err.tooLong')); return; }
    setError(null);
    props.onApply(draft, room, agent);
  };
  return (
    <form className="hqHistFilters" onSubmit={submit} aria-label={t('hqHist.filters')}>
      <div className="hqHistSearchRow">
        <label className="hqHistSearch">
          <Search aria-hidden="true" />
          <span className="hqSrOnly">{t('hqHist.search')}</span>
          <input type="search" value={draft.q} onChange={(e) => set('q', e.target.value)} placeholder={t('hqHist.searchHint')} maxLength={200} />
        </label>
        <button type="button" className="hqGhost" aria-expanded={open} aria-controls="hqHistFilterBody" onClick={() => setOpen((v) => !v)}>
          <Filter aria-hidden="true" />{props.activeCount > 0 ? t('hqHist.filtersActive', { count: props.activeCount }) : t('hqHist.filters')}
        </button>
        <button type="submit" className="hqPrimary">{t('hqHist.apply')}</button>
      </div>
      <div id="hqHistFilterBody" className="hqHistFilterBody" hidden={!open}>
        <label>{t('hqHist.type')}
          <select value={draft.category} onChange={(e) => set('category', e.target.value as HistoryFilters['category'])}>
            {CATEGORY_IDS.map((id) => <option key={id} value={id}>{categoryLabel(id)}</option>)}
          </select>
        </label>
        <label>{t('hqHist.room')}
          <select value={room} onChange={(e) => setRoom(e.target.value)}>
            <option value="">{t('hqHist.anyRoom')}</option>
            {props.rooms.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
        </label>
        <label>{t('hqHist.agent')}
          <select value={agent} onChange={(e) => setAgent(e.target.value)}>
            <option value="">{t('hqHist.anyAgent')}</option>
            {props.agents.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
          </select>
        </label>
        <label>{t('hqHist.actor')}
          <input value={draft.actor} onChange={(e) => set('actor', e.target.value)} placeholder={t('hqHist.actorHint')} maxLength={160} list="hqHistActors" dir="ltr" />
          <datalist id="hqHistActors">{props.agents.map((a) => <option key={a.id} value={a.id} label={a.label} />)}</datalist>
        </label>
        <label>{t('hqHist.from')}<input type="date" value={draft.from} onChange={(e) => set('from', e.target.value)} /></label>
        <label>{t('hqHist.to')}<input type="date" value={draft.to} onChange={(e) => set('to', e.target.value)} /></label>
        <div className="hqHistFilterActions">
          <button type="button" className="hqGhost" onClick={() => { setDraft(EMPTY_FILTERS); setRoom(''); setAgent(''); setError(null); props.onReset(); }}>{t('hqHist.reset')}</button>
        </div>
        <p className="hqHistHint">{t('hqHist.noDepartment')}</p>
      </div>
      {error && <p className="hqHistError" role="alert">{error}</p>}
    </form>
  );
}

export function RecordRow({ record, names, onOpen, current }: { record: OfficeHistoryEvidence; names: NameMap; onOpen: (r: OfficeHistoryEvidence) => void; current?: boolean }) {
  const time = timeOnly(record.timestamp);
  return (
    <li>
      <button type="button" className="hqHistRow" data-record-id={record.id} data-outcome={record.outcome} aria-current={current ? 'true' : undefined}
        aria-label={`${t('hqHist.open.record', { action: actionLabel(record.action), time })}. ${t('hqHist.by', { actor: whoLabel(record.actor, names) })}`}
        onClick={() => onOpen(record)}>
        <span className="hqHistTime" aria-hidden="true">{time}</span>
        <span className="hqHistMain" aria-hidden="true">
          <strong>{actionLabel(record.action)}</strong>
          <small>{t('hqHist.by', { actor: whoLabel(record.actor, names) })} · {t('hqHist.on', { target: whoLabel(record.target, names) })}</small>
        </span>
        <span className="hqHistChips" aria-hidden="true">
          <span className="hqHistChip" data-category={record.category}>{categoryLabel(record.category)}</span>
          {record.outcome !== 'success' && <span className="hqHistChip" data-bad="true">{outcomeLabel(record.outcome)}</span>}
        </span>
        <ChevronRight aria-hidden="true" className="flip hqHistGo" />
      </button>
    </li>
  );
}

export function RecordList({ items, names, onOpen, selectedId }: { items: readonly OfficeHistoryEvidence[]; names: NameMap; onOpen: (r: OfficeHistoryEvidence) => void; selectedId?: string | null }) {
  const groups = useMemo(() => groupByDay(items, intlLocale(getLocale())), [items]);
  return (
    <div className="hqHistGroups">
      {groups.map((g) => (
        <section key={g.key} aria-label={g.label || t('hqHist.day.unknown')}>
          <h3 className="hqHistDay">{g.label || t('hqHist.day.unknown')}</h3>
          <ul className="hqHistList">{g.items.map((r) => <RecordRow key={r.id} record={r} names={names} onOpen={onOpen} current={selectedId === r.id} />)}</ul>
        </section>
      ))}
    </div>
  );
}

/** Calls `onVisible` while the sentinel is on screen: the infinite scroll. A button next to it does the same for keyboards. */
export function useSentinel(onVisible: () => void, enabled: boolean, version: number) {
  const ref = useRef<HTMLDivElement>(null);
  const latest = useRef(onVisible);
  latest.current = onVisible;
  useEffect(() => {
    const node = ref.current;
    if (!node || !enabled || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) latest.current(); }, { rootMargin: '200px' });
    observer.observe(node);
    return () => observer.disconnect();
  }, [enabled, version]);
  return ref;
}
