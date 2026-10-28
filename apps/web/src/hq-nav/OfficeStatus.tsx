/**
 * Total agents, as a docked panel (side column) or a thin chip row (under the scene). Numbers come from the campus read and the
 * presence the server verified; with no record behind them the panel says "Presence unavailable", never a zero. Every state has its
 * words: reading, not allowed, failed (with a retry), nobody reports, and out of date.
 */
import { AlertTriangle, Lock } from 'lucide-react';
import { useMemo } from 'react';
import { Donut, statusLabel } from '../HqChrome';
import { campusTotals, STATUS_ORDER, STATUS_TONE, type Campus, type StatusKey } from '../lib/campusModel.ts';
import { freshness, PRESENCE_STALE_MS, type PanelLive, type TotalsSource } from '../lib/hqPanelStatus.ts';
import { t } from '../lib/i18n.ts';
import { clockText } from '../lib/hqPanelStatus.ts';
import { useLocale } from '../useLocale';
import { LiveChip } from './LiveChip.tsx';
import { PanelSection } from './PanelSection.tsx';
import { useNow } from './useNow.ts';

export type OfficeStatusProps = Readonly<{
  campus: Campus | null;
  /** The campus read failed (or was refused) and nothing was loaded yet. */
  issue: Readonly<{ denied: boolean; message: string }> | null;
  unreported: readonly StatusKey[];
  /** Where the open workspace's numbers come from (the presence stream while it is live, else the campus read) and the words for it. */
  source: TotalsSource;
  state: PanelLive;
  variant: 'panel' | 'strip';
  onRetry: () => void;
  onGo: (hash: string) => void;
}>;

/** The newest time the server verified presence for any building, or null (never guessed). */
export function latestVerified(campus: Campus | null): string | null {
  let best: string | null = null;
  let bestMs = -1;
  for (const b of campus?.buildings ?? []) {
    const ms = b.presence_verified_at ? Date.parse(b.presence_verified_at) : NaN;
    if (Number.isFinite(ms) && ms > bestMs) { bestMs = ms; best = b.presence_verified_at ?? null; }
  }
  return best;
}

export function OfficeStatus(props: OfficeStatusProps) {
  const locale = useLocale();
  const now = useNow(10_000);
  const sums = useMemo(() => campusTotals(props.campus?.buildings ?? []), [props.campus]);
  const verified = latestVerified(props.campus);
  const stale = freshness(verified, now, PRESENCE_STALE_MS) === 'stale';
  const totals = sums.totals;
  const unreported = new Set(props.unreported);
  const shown = STATUS_ORDER.filter((k) => k === 'working' || k === 'meeting' || k === 'idle' || (totals?.[k] ?? 0) > 0);
  const partial = totals && sums.missing > 0 ? t('hq.totals.partial', { reporting: sums.reporting, total: props.campus?.buildings.length ?? 0 }) : null;
  const hint = props.state === 'live' ? t('hqPanels.status.liveHint') : props.state === 'polling' ? t('hqPanels.status.pollingHint') : props.state === 'reconnecting' ? t('hqPanels.status.failedHint') : undefined;
  const live = (
    <span className="hqStatusLive" data-source={props.source} data-testid="hq-status-source">
      <LiveChip state={props.state} at={verified} staleAfterMs={PRESENCE_STALE_MS} nowMs={now} timeKey="hqPanels.verified" pollingText={t('hqPanels.status.polling')} hint={hint} testId="hq-status-live" />
    </span>
  );

  let body;
  if (!props.campus && props.issue?.denied) {
    body = <p className="hqNote hqState" role="status"><Lock aria-hidden="true" />{t('hqPanels.denied')}</p>;
  } else if (!props.campus && props.issue) {
    body = <div className="hqState" role="status"><p className="hqNote"><AlertTriangle aria-hidden="true" />{t('hqPanels.status.failed', { reason: props.issue.message })}</p><button type="button" className="hqGhost" onClick={props.onRetry}>{t('hq.retry')}</button></div>;
  } else if (!props.campus) {
    body = <p className="hqNote hqState hqSkeleton" role="status" aria-busy="true">{t('hqPanels.status.loading')}</p>;
  } else if (!totals) {
    body = <div className="hqUnavailable" role="status"><strong>{t('hq.totals.unavailable')}</strong><span>{t('hq.totals.unavailableHint')}</span></div>;
  } else if (totals.agents === 0) {
    body = <div className="hqState" role="status"><p className="hqNote">{t('hqPanels.status.empty')}</p><button type="button" className="hqGhost" onClick={() => props.onGo('#agents')}>{t('hqPanels.status.emptyAction')}</button></div>;
  } else if (props.variant === 'strip') {
    body = (
      <ul className="hqStatusChips">
        <li className="hqStatusTotal"><Donut totals={totals} size={32} /> <strong>{totals.agents}</strong> <span>{t('hq.totals.agentsCap')}</span></li>
        {shown.map((k) => <li key={k} className={`tone-${STATUS_TONE[k]}`}><span className="hqDot" aria-hidden="true" /><span>{statusLabel(k)}</span> <strong>{unreported.has(k) ? '-' : totals[k]}</strong></li>)}
      </ul>
    );
  } else {
    body = (
      <div className="hqTotalsBody">
        <Donut totals={totals} size={76} />
        <ul className="hqLegend">
          {shown.map((k) => <li key={k} className={`tone-${STATUS_TONE[k]}`}><span className="dot" aria-hidden="true" /><span className="name">{statusLabel(k)}</span><strong>{unreported.has(k) ? '-' : totals[k]}</strong></li>)}
        </ul>
      </div>
    );
  }
  const notes = <>{partial && <p className="hqNote">{partial}</p>}{stale && verified && <p className="hqNote" data-testid="hq-status-stale">{t('hqPanels.status.stale', { time: clockText(verified, locale) })}</p>}</>;
  if (props.variant === 'strip') {
    return (
      <section className="hqStatusStrip" aria-label={t('hq.totals.title')} data-testid="hq-status-strip" data-stale={stale}>
        <h2 className="hqStatusStripTitle">{t('hq.totals.title')}</h2>
        {body}
        {live}
        {notes}
      </section>
    );
  }
  return (
    <PanelSection id="status" title={t('hq.totals.title')} purpose={t('hqPanels.status.purpose')} live={live} className="hqStatusPanel">
      <div data-testid="hq-status-panel" data-stale={stale}>{body}{notes}</div>
    </PanelSection>
  );
}
