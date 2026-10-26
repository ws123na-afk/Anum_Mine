import { ArrowLeft, ExternalLink, MapPin } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { OfficeHistoryEvidence } from '@anum/contracts';
import { RecordRow } from './HqHistoryParts';
import { historyClient } from './useHqHistory';
import { describeError, isPermissionDenied } from './lib/errors';
import { HISTORY_PATH, PAGE_SIZE, mergeItems, sourceRefs, type SourceKind, type SourceRef } from './lib/hqHistory';
import { type SourceSummary } from './lib/hqHistoryApi';
import { actionLabel, fieldLabel, outcomeLabel, valueLabel, whenLabel, whoLabel, type NameMap } from './lib/hqHistoryText';
import { recordDiffs } from './lib/hqRoomHistory';
import { formatDateTime, t, type MessageKey } from './lib/i18n';

const VIEW_HASH: Readonly<Partial<Record<SourceKind, string>>> = { task: '#tasks', run: '#tasks', approval: '#approvals', meeting: '#meetings' };
const SHOWN_KEYS = new Set(['before', 'after', 'office_resource', 'resource_kind']);

const kindLabel = (kind: SourceKind) => t(`hqHist.source.${kind}` as MessageKey);

function SourceCard({ source, names, onLocateRoom, canLocate, onNavigate, onShowRecords }: {
  source: SourceRef; names: NameMap; onLocateRoom?: (roomId: string) => void; canLocate: boolean; onNavigate?: (hash: string) => void; onShowRecords?: (id: string) => void;
}) {
  const [state, setState] = useState<{ kind: 'idle' } | { kind: 'loading' } | { kind: 'ready'; summary: SourceSummary } | { kind: 'error'; denied: boolean; missing: boolean; detail: string }>({ kind: 'idle' });
  const kind = kindLabel(source.kind);
  const show = async () => {
    setState({ kind: 'loading' });
    try { setState({ kind: 'ready', summary: await historyClient.source(source.kind, source.id) }); }
    catch (failure) {
      const status = (failure as { status?: number }).status;
      setState({ kind: 'error', denied: isPermissionDenied(failure), missing: status === 404, detail: describeError(failure) });
    }
  };
  const hash = VIEW_HASH[source.kind];
  return (
    <li className="hqHistSource" data-kind={source.kind}>
      <div className="hqHistSourceHead">
        <span className="hqHistChip">{kind}</span>
        <code dir="ltr">{source.kind === 'agent' ? whoLabel(source.id, names) : source.id}</code>
        {source.readable && state.kind !== 'ready' && (
          <button type="button" className="hqGhost" onClick={() => void show()} disabled={state.kind === 'loading'}>{t('hqHist.source.show', { kind, id: source.id })}</button>
        )}
        {state.kind === 'ready' && <button type="button" className="hqGhost" onClick={() => setState({ kind: 'idle' })}>{t('hqHist.source.hide')}</button>}
        {onShowRecords && (source.kind === 'task' || source.kind === 'run' || source.kind === 'approval' || source.kind === 'meeting') && (
          <button type="button" className="hqGhost" onClick={() => onShowRecords(source.id)}>{t('hqHist.source.records', { kind })}</button>
        )}
        {source.kind === 'room' && canLocate && onLocateRoom && (
          <button type="button" className="hqGhost" onClick={() => onLocateRoom(source.id)}><MapPin aria-hidden="true" />{t('hqHist.source.locate')}</button>
        )}
      </div>
      <div aria-live="polite">
        {state.kind === 'loading' && <p className="hqHistHint" role="status">{t('hqHist.source.loading', { kind })}</p>}
        {state.kind === 'error' && <p className="hqHistError" role="alert">
          {state.denied ? t('hqHist.source.denied', { kind }) : state.missing ? t('hqHist.source.notFound', { kind }) : t('hqHist.source.error', { kind, detail: state.detail })}
        </p>}
        {state.kind === 'ready' && (
          <div className="hqHistSummary">
            <strong>{state.summary.title}</strong>
            <dl>{state.summary.fields.map((f) => (
              <div key={f.label}><dt>{t(f.label as MessageKey)}</dt><dd>{f.time ? formatDateTime(f.value) : f.value}</dd></div>
            ))}</dl>
            {hash && onNavigate && (
              <button type="button" className="hqLinkButton" onClick={() => onNavigate(hash)}>
                <ExternalLink aria-hidden="true" />{t('hqHist.source.openIn', { view: t(`hqHist.source.view.${source.kind}` as MessageKey) })}
              </button>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

function Related({ record, names, onOpen, onOpenRequest }: { record: OfficeHistoryEvidence; names: NameMap; onOpen: (r: OfficeHistoryEvidence) => void; onOpenRequest?: (correlationId: string) => void }) {
  const [state, setState] = useState<{ kind: 'idle' } | { kind: 'loading' } | { kind: 'ready'; items: OfficeHistoryEvidence[]; cursor: string | null; busy: boolean } | { kind: 'error'; detail: string }>({ kind: 'idle' });
  useEffect(() => { setState({ kind: 'idle' }); }, [record.id]);
  const load = async () => {
    setState({ kind: 'loading' });
    try {
      const page = await historyClient.page(`${HISTORY_PATH}/correlations/${encodeURIComponent(record.correlation_id)}?limit=${PAGE_SIZE}`);
      setState({ kind: 'ready', items: page.items, cursor: page.next_cursor, busy: false });
    } catch (failure) { setState({ kind: 'error', detail: describeError(failure) }); }
  };
  const older = async () => {
    if (state.kind !== 'ready' || !state.cursor || state.busy) return;
    const { items, cursor } = state;
    setState({ ...state, busy: true });
    try {
      const page = await historyClient.page(`${HISTORY_PATH}/correlations/${encodeURIComponent(record.correlation_id)}?limit=${PAGE_SIZE}&cursor=${encodeURIComponent(cursor)}`);
      setState({ kind: 'ready', items: mergeItems(items, page.items), cursor: page.next_cursor, busy: false });
    } catch (failure) { setState({ kind: 'error', detail: describeError(failure) }); }
  };
  if (!record.correlation_id.trim()) {
    return <section aria-labelledby="hqHistRelated"><h3 id="hqHistRelated">{t('hqHist.related')}</h3><p className="hqHistHint">{t('hqHist.related.missing')}</p></section>;
  }
  return (
    <section aria-labelledby="hqHistRelated">
      <h3 id="hqHistRelated">{t('hqHist.related')}</h3>
      {state.kind === 'idle' && <button type="button" className="hqGhost" onClick={() => void load()}>{t('hqHist.related.show')}</button>}
      <div aria-live="polite">
        {state.kind === 'loading' && <p className="hqHistHint" role="status">{t('hqHist.related.loading')}</p>}
        {state.kind === 'error' && <p className="hqHistError" role="alert">{t('hqHist.related.error', { detail: state.detail })} <button type="button" className="hqLinkButton" onClick={() => void load()}>{t('hqHist.retry')}</button></p>}
        {state.kind === 'ready' && (state.items.length <= 1
          ? <p className="hqHistHint">{t('hqHist.related.none')}</p>
          : <>
            <p className="hqHistCount" role="status">{t(state.cursor ? 'hqHist.related.countMore' : 'hqHist.related.count', { count: state.items.length })}</p>
            <ul className="hqHistList">{state.items.map((r) => <RecordRow key={r.id} record={r} names={names} onOpen={onOpen} current={r.id === record.id} />)}</ul>
            {state.cursor && <button type="button" className="hqGhost" onClick={() => void older()} disabled={state.busy}>{state.busy ? t('hqHist.loadingMore') : t('hqHist.more')}</button>}
            {onOpenRequest && <button type="button" className="hqGhost" onClick={() => onOpenRequest(record.correlation_id)}>{t('hqHist.related.timeline')}</button>}
          </>)}
      </div>
    </section>
  );
}

/** One audit record in the side panel: its facts, the before and after values, the records it points at and the others of the same request. */
export function RecordDetail(props: {
  record: OfficeHistoryEvidence; names: NameMap; roomIds: ReadonlySet<string>;
  onBack: () => void; onOpenRecord: (r: OfficeHistoryEvidence) => void; onLocateRoom?: (roomId: string) => void; onNavigate?: (hash: string) => void;
  onShowRecords?: (id: string) => void; onOpenRequest?: (correlationId: string) => void;
}) {
  const { record, names } = props;
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, [record.id]);
  const diffs = recordDiffs(record);
  const hasSnaps = 'before' in record.metadata || 'after' in record.metadata;
  const extra = Object.entries(record.metadata).filter(([k]) => !SHOWN_KEYS.has(k));
  const sources = sourceRefs(record);
  return (
    <article className="hqHistDetail" aria-labelledby="hqHistDetailTitle">
      <button type="button" className="hqGhost" onClick={props.onBack}><ArrowLeft aria-hidden="true" className="flip" />{t('hqHist.back')}</button>
      <h2 id="hqHistDetailTitle" ref={heading} tabIndex={-1}>{actionLabel(record.action)}</h2>
      <p className="hqHistHint">{t('hqHist.detail.verified')}</p>
      <dl className="hqHistFacts">
        <div><dt>{t('hqHist.detail.when')}</dt><dd>{whenLabel(record.timestamp)}</dd></div>
        <div><dt>{t('hqHist.detail.actor')}</dt><dd>{whoLabel(record.actor, names)}</dd></div>
        <div><dt>{t('hqHist.detail.action')}</dt><dd><code dir="ltr">{record.action}</code></dd></div>
        <div><dt>{t('hqHist.detail.target')}</dt><dd><code dir="ltr">{record.target}</code></dd></div>
        <div><dt>{t('hqHist.detail.outcome')}</dt><dd>{outcomeLabel(record.outcome)}</dd></div>
        <div><dt>{t('hqHist.detail.id')}</dt><dd><code dir="ltr">{record.id}</code></dd></div>
        <div><dt>{t('hqHist.detail.correlation')}</dt><dd><code dir="ltr">{record.correlation_id}</code></dd></div>
      </dl>

      {hasSnaps && (
        <section aria-labelledby="hqHistChanges">
          <h3 id="hqHistChanges">{t('hqHist.detail.changes')}</h3>
          {diffs.length === 0 ? <p className="hqHistHint">{t('hqHist.detail.noChange')}</p> : diffs.map((g) => (
            <div key={g.key} className="hqHistDiffGroup">
              {g.label && <h4>{g.label}</h4>}
              <table className="hqHistDiff">
                <thead><tr><th scope="col">{t('hqHist.detail.changes')}</th><th scope="col">{t('hqHist.detail.before')}</th><th scope="col">{t('hqHist.detail.after')}</th></tr></thead>
                <tbody>{g.fields.map((f) => (
                  <tr key={f.field}><th scope="row">{fieldLabel(f.field)}</th><td>{valueLabel(f.field, f.before, names)}</td><td>{valueLabel(f.field, f.after, names)}</td></tr>
                ))}</tbody>
              </table>
            </div>
          ))}
        </section>
      )}

      <section aria-labelledby="hqHistSources">
        <h3 id="hqHistSources">{t('hqHist.sources')}</h3>
        {sources.length === 0 ? <p className="hqHistHint">{t('hqHist.sources.none')}</p> : (
          <ul className="hqHistSources">{sources.map((s) => (
            <SourceCard key={`${s.kind}:${s.id}`} source={s} names={names} onLocateRoom={props.onLocateRoom} canLocate={props.roomIds.has(s.id)} onNavigate={props.onNavigate} onShowRecords={props.onShowRecords} />
          ))}</ul>
        )}
      </section>

      <Related record={record} names={names} onOpen={props.onOpenRecord} onOpenRequest={props.onOpenRequest} />

      <section aria-labelledby="hqHistMeta">
        <h3 id="hqHistMeta">{t('hqHist.detail.metadata')}</h3>
        {extra.length === 0 ? <p className="hqHistHint">{t('hqHist.detail.noMetadata')}</p> : (
          <dl className="hqHistFacts">{extra.map(([k, v]) => (
            <div key={k}><dt><code dir="ltr">{k}</code></dt><dd>{typeof v === 'object' && v !== null ? <code dir="ltr">{JSON.stringify(v)}</code> : String(v)}</dd></div>
          ))}</dl>
        )}
      </section>
    </article>
  );
}
