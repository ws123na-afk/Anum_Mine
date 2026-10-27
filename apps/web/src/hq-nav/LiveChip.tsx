/**
 * The small live status every panel shows in its header: whether the data updates by itself (Live, Reconnecting, Polling, Not connected),
 * the real time it was last confirmed, and "Stale" once that is older than the panel's window. It is small on purpose: the dock of the
 * headquarters chunk and the lazy panels share it. The words come from the link the page already has; nothing here opens a connection.
 */
import { clockText, effectiveLive, freshness, liveTone, toMs, type PanelLive } from '../lib/hqPanelStatus.ts';
import { t, type MessageKey } from '../lib/i18n.ts';
import { useLocale } from '../useLocale';

export type LiveChipProps = Readonly<{
  state: PanelLive;
  /** When the data was last confirmed (ISO string or epoch), or null when no time is known. */
  at?: string | number | null;
  /** Older than this the chip says Stale and never says Live. */
  staleAfterMs: number;
  nowMs: number;
  /** The message that frames the time, for example `hqPanels.updated` ("Updated {time}") or `hqPanels.verified`. */
  timeKey?: MessageKey;
  /** Replaces the Polling words when the panel is read on its own clock (the flow checks every 8 s, the office every 10 s). */
  pollingText?: string;
  testId?: string;
}>;

export function LiveChip(props: LiveChipProps) {
  const locale = useLocale();
  const fresh = freshness(props.at, props.nowMs, props.staleAfterMs);
  const state = effectiveLive(props.state, fresh);
  const clock = clockText(props.at, locale);
  const text = state === 'polling' && props.pollingText ? props.pollingText : t(`hq.live.${state}` as MessageKey);
  const hint = fresh === 'stale' ? t('hqPanels.staleHint') : t(`hq.live.${state}.hint` as MessageKey);
  return (
    <span className={`hqLiveChip tone-${liveTone(state)}`} data-testid={props.testId} data-live={state} data-fresh={fresh} title={hint}>
      <span className="hqLiveDot" aria-hidden="true" />
      <span className="hqLiveText">{text}</span>
      {clock && <time className="hqLiveClock" dateTime={new Date(toMs(props.at) ?? 0).toISOString()}>{t(props.timeKey ?? 'hqPanels.updated', { time: clock })}</time>}
      {fresh === 'stale' && <b className="hqLiveStale">{t('hqPanels.stale')}</b>}
    </span>
  );
}
