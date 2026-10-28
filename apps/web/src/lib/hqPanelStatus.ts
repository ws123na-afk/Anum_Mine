/**
 * What each side panel of the headquarters says about its own data: whether it updates by itself, when it was last
 * confirmed and whether that is too old to trust. Pure: the page passes in the links and the clocks it already has
 * (the presence stream, the flow poll, the campus read); nothing here opens a connection or invents a time.
 */

export type PanelLive = 'live' | 'reconnecting' | 'polling' | 'connecting' | 'offline' | 'denied';
export type PanelTone = 'ok' | 'warn' | 'stop';
export type Freshness = 'none' | 'fresh' | 'stale';

type PresenceLinkLike = 'connecting' | 'live' | 'reconnecting' | 'polling' | 'offline' | 'denied' | 'missing';
type FlowLinkLike = 'live' | 'polling' | 'offline';
type FlowStatusLike = 'loading' | 'ready' | 'error' | 'denied';

/** The server's own window for presence (office_presence.STALE_AFTER, lib/presenceStream.ts): 5 minutes. */
export const PRESENCE_STALE_MS = 5 * 60_000;
/** The flow is read every 20 s while the event stream is up and every 8 s otherwise (useOfficeFlow); three missed reads make it stale. */
export const FLOW_STALE_MS = 60_000;
/** The campus is read again at most every 30 s (HeadquartersLive); three missed reads make it stale. */
export const CAMPUS_STALE_MS = 90_000;

export function presenceLive(link: PresenceLinkLike): PanelLive {
  return link === 'missing' ? 'offline' : link;
}

export function flowLive(link: FlowLinkLike, status: FlowStatusLike): PanelLive {
  if (status === 'denied') return 'denied';
  if (status === 'loading') return 'connecting';
  if (status === 'error') return 'offline';
  return link;
}

/** The history checks for newer records this often while it is open (HqHistoryPanel); three missed checks make it stale. */
export const HISTORY_CHECK_MS = 30_000;
export const HISTORY_STALE_MS = 90_000;

export type TotalsSource = 'stream' | 'campus';

/**
 * Where Total agents reads the open workspace's numbers: the presence stream while it is live and has answered, otherwise the
 * campus read (every 30 s at most). Other workspaces' buildings always come from the campus read (no stream reaches them).
 */
export function totalsSource(link: PresenceLinkLike, hasPresence: boolean): TotalsSource {
  return link === 'live' && hasPresence ? 'stream' : 'campus';
}

/** The words for Total agents: Live on the stream; on the campus read, Checking every 30 s, or why it cannot be read. */
export function officeLive(source: TotalsSource, campus: Readonly<{ loaded: boolean; failed: boolean; denied: boolean }>): PanelLive {
  if (campus.denied && !campus.loaded) return 'denied';
  if (!campus.loaded) return campus.failed ? 'offline' : 'connecting';
  if (source === 'stream') return 'live';
  return campus.failed ? 'reconnecting' : 'polling';
}

/**
 * The words for the Team panel: it reads again whenever the live flow changes, so once it has answered it is as live as the flow.
 * A failed read with a roster on screen keeps the roster and says the link is being tried again.
 */
export function teamLive(status: 'loading' | 'ready' | 'error' | 'denied', hasTeam: boolean, failed: boolean, flow: PanelLive): PanelLive {
  if (status === 'denied') return 'denied';
  if (!hasTeam) return status === 'error' ? 'offline' : 'connecting';
  if (failed) return 'reconnecting';
  return flow;
}

/** The words for the History panel: it reads once when opened, then checks for newer records on a timer. */
export function historyLive(status: 'loading' | 'ready' | 'error' | 'denied' | 'invalid', checkFailed: boolean): PanelLive {
  if (status === 'denied') return 'denied';
  if (status === 'loading') return 'connecting';
  if (status === 'error' || status === 'invalid') return 'offline';
  return checkFailed ? 'reconnecting' : 'polling';
}

/** The words for the Executive assistant: its own tools read first, then the workspace event link the shell keeps open. */
export function assistantLive(tools: 'loading' | 'ready' | 'unavailable' | null, stream: 'connecting' | 'live' | 'offline' | null): PanelLive {
  if (tools === 'unavailable') return 'offline';
  if (tools === 'loading' || tools === null) return 'connecting';
  return stream ?? 'connecting';
}

export function liveTone(state: PanelLive): PanelTone {
  return state === 'live' ? 'ok' : state === 'offline' || state === 'denied' ? 'stop' : 'warn';
}

/** A moment as epoch milliseconds, or null when it is missing or not a date. */
export function toMs(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const ms = typeof value === 'number' ? (value < 1e11 ? value * 1000 : value) : Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

/** Fresh until `staleAfterMs` have passed since the data was last confirmed; `none` when no time is known (never guessed). */
export function freshness(at: string | number | null | undefined, nowMs: number, staleAfterMs: number): Freshness {
  const ms = toMs(at);
  if (ms === null) return 'none';
  return nowMs - ms > staleAfterMs ? 'stale' : 'fresh';
}

/** The words for a panel's live chip: a stale panel never claims Live, whatever the link says. */
export function effectiveLive(state: PanelLive, fresh: Freshness): PanelLive {
  return state === 'live' && fresh === 'stale' ? 'reconnecting' : state;
}

/** The clock a panel shows: the wall time of the last confirmation, in the page's language, or an empty string. */
export function clockText(at: string | number | null | undefined, locale: string): string {
  const ms = toMs(at);
  if (ms === null) return '';
  try { return new Intl.DateTimeFormat(locale === 'ar' ? 'ar-AE-u-nu-latn' : 'en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(ms)); } catch { return ''; }
}

/**
 * One calm sentence for the polite live region, only when the state really changed (never per tick): `null` while the
 * first state settles, and on a repeat.
 */
export function changeNote(previous: PanelLive | null, next: PanelLive, name: string, text: (state: PanelLive, name: string) => string): string | null {
  if (previous === null || previous === next) return null;
  return text(next, name);
}
