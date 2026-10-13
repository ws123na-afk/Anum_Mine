// "Recent calls" on the Call page (docs/voice.md#recent-calls): the caller's own calls and Voice
// sessions, newest first with Load older, and a call's transcript opened in place.
import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, History, Lock, MessageSquareText, Phone } from 'lucide-react';
import { getCallTranscript, listCalls } from './lib/callApi';
import { callMinutes, mergeCalls, transcriptGone, type CallSummaryRow } from './lib/callHistory';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import type { TranscriptSegment } from './lib/voice';
import { PaneProblem } from './PageParts';

const LOCALES: Record<string, string> = { 'en-US': 'English (US)', 'en-GB': 'English (UK)', 'ar-SA': 'العربية (السعودية)', 'ar-AE': 'العربية (الإمارات)' };
type Rows = { state: 'loading' } | { state: 'ready'; rows: CallSummaryRow[]; next: string | null } | { state: 'denied'; message: string } | { state: 'error'; message: string };

export function RecentCalls({ refresh, onOpenChat }: { refresh: number; onOpenChat?: () => void }) {
  const [list, setList] = useState<Rows>({ state: 'loading' });
  const [open, setOpen] = useState<string | null>(null);
  const [older, setOlder] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);
  const load = useCallback(() => {
    setList({ state: 'loading' });
    listCalls(null).then((page) => setList({ state: 'ready', rows: page.items, next: page.next_cursor }), (e: unknown) => setList(isPermissionDenied(e) ? { state: 'denied', message: describeError(e) } : { state: 'error', message: describeError(e) }));
  }, []);
  useEffect(load, [load, refresh]);

  async function loadOlder() {
    if (list.state !== 'ready' || !list.next) return;
    setOlder(true); setOlderError(null);
    try { const page = await listCalls(list.next); setList({ state: 'ready', rows: mergeCalls(list.rows, page.items), next: page.next_cursor }); }
    catch (e) { setOlderError(t('pages2.call.olderFailed', { reason: describeError(e) })); }
    finally { setOlder(false); }
  }

  return <section className="pane recentCalls" aria-labelledby="recent-calls-title">
    <div className="paneHead"><h2 id="recent-calls-title"><History aria-hidden="true" className="inlineIcon" />{t('pages2.call.recent')}</h2>
      {list.state === 'ready' && <span className="muted">{list.next ? t('pages2.call.shownMore', { count: list.rows.length }) : t('pages2.call.shown', { count: list.rows.length })}</span>}</div>
    <div className="paneBody">
      <p className="muted recentCallsNote">{t('pages2.call.privateNote')}</p>
      {list.state === 'loading' && <p className="notice" role="status">{t('pages2.call.loading')}</p>}
      {list.state === 'error' && <PaneProblem text={t('pages2.call.loadFailed', { reason: list.message })} onRetry={load} />}
      {list.state === 'denied' && <div className="paneEmpty"><Lock aria-hidden="true" /><h3>{t('pages2.call.denied')}</h3><p>{list.message}</p></div>}
      {list.state === 'ready' && !list.rows.length && <div className="paneEmpty"><Phone aria-hidden="true" /><h3>{t('pages2.call.empty')}</h3><p>{t('pages2.call.emptyText')}</p></div>}
      {list.state === 'ready' && list.rows.length > 0 && <ul className="recentCallList">
        {list.rows.map((row) => <CallRow key={row.id} row={row} open={open === row.id} onToggle={() => setOpen(open === row.id ? null : row.id)} onOpenChat={onOpenChat} />)}
      </ul>}
      {olderError && <PaneProblem text={olderError} onRetry={() => void loadOlder()} />}
      {list.state === 'ready' && list.next && <div className="actions recentCallsMore"><button type="button" className="secondary" disabled={older} onClick={() => void loadOlder()}>{older ? t('common.loading') : t('pages2.call.older')}</button></div>}
    </div>
  </section>;
}

function CallRow({ row, open, onToggle, onOpenChat }: { row: CallSummaryRow; open: boolean; onToggle: () => void; onOpenChat?: () => void }) {
  const minutes = callMinutes(row);
  const gone = transcriptGone(row);
  const id = `call-${row.id}`;
  return <li className={open ? 'open' : undefined}>
    <button type="button" className="recentCallRow" aria-expanded={open} aria-controls={`${id}-detail`} onClick={onToggle}>
      <span className="recentCallMain">
        <strong dir="auto">{row.first_line ?? t(`pages2.call.noLine.${gone ?? 'empty'}` as MessageKey)}</strong>
        <small>{[formatDateTime(row.created_at, { dateStyle: 'medium', timeStyle: 'short' }), minutes ? t('pages2.call.minutes', { count: minutes }) : null, LOCALES[row.locale] ?? row.locale].filter(Boolean).join(' · ')}</small>
      </span>
      <span className={`statusBadge ${row.status === 'active' ? 'warning' : row.status === 'completed' ? 'success' : 'neutral'}`}>{t(`pages2.call.status.${row.status}` as MessageKey)}</span>
      <ChevronDown aria-hidden="true" className="recentCallChevron" />
    </button>
    {open && <CallDetail id={`${id}-detail`} row={row} gone={gone} onOpenChat={onOpenChat} />}
  </li>;
}

function CallDetail({ id, row, gone, onOpenChat }: { id: string; row: CallSummaryRow; gone: ReturnType<typeof transcriptGone>; onOpenChat?: () => void }) {
  const [lines, setLines] = useState<{ state: 'loading' } | { state: 'ready'; value: TranscriptSegment[] } | { state: 'error'; message: string }>(gone ? { state: 'ready', value: [] } : { state: 'loading' });
  const load = useCallback(() => {
    if (gone) return;
    setLines({ state: 'loading' });
    getCallTranscript(row.id).then((value) => setLines({ state: 'ready', value }), (e: unknown) => setLines({ state: 'error', message: describeError(e) }));
  }, [row.id, gone]);
  useEffect(load, [load]);
  return <div id={id} className="recentCallDetail">
    <dl className="railFacts">
      <dt>{t('pages2.call.retention')}</dt><dd>{t(`call.retention.${row.retention}` as MessageKey)}{row.expires_at && row.retention === '30_days' ? ` · ${t('pages2.call.until', { when: formatDateTime(row.expires_at, { dateStyle: 'medium' }) })}` : ''}</dd>
      <dt>{t('pages2.call.lines')}</dt><dd>{row.segment_count}</dd>
    </dl>
    {gone && <p className="muted">{t(`pages2.call.gone.${gone}` as MessageKey)}</p>}
    {lines.state === 'loading' && <p className="muted" role="status">{t('pages2.call.transcriptLoading')}</p>}
    {lines.state === 'error' && <PaneProblem text={t('pages2.call.transcriptFailed', { reason: lines.message })} onRetry={load} />}
    {lines.state === 'ready' && lines.value.length > 0 && <ol className="recentCallLines" aria-label={t('pages2.call.transcript')}>
      {lines.value.map((line) => <li key={line.id} className={line.role}><span className="recentCallWho">{line.role === 'user' ? t('pages2.call.you') : row.assistant_name}</span><p dir="auto">{line.text}</p></li>)}
    </ol>}
    {onOpenChat && row.status === 'completed' && <p className="muted recentCallChat"><MessageSquareText aria-hidden="true" className="inlineIcon" />{t('pages2.call.savedNote')} <button type="button" className="linkButton" onClick={onOpenChat}>{t('pages2.call.openChat')}</button></p>}
  </div>;
}
