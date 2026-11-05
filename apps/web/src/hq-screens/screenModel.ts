/**
 * What the screens in the rooms say (docs/ANUM_MINE_HQ_LIVE_SCREENS.md). Pure functions, no DOM.
 *
 * Data truth: every string is a fixed message of the catalogue (a label that already exists on the page, or a `hqScreen.*` sentence) or a
 * number or time read from a real record named in `SCREEN_FIELDS`. No task title, prompt, person or agent name, customer or id is ever read.
 * A value that is unavailable says "Not connected", never a zero that implies data, and nothing is estimated: there is no progress bar,
 * because no record carries step counts. `test/hqScreens.test.ts` reads every function here through a recording proxy and fails when one
 * touches a property outside `SCREEN_READS`.
 */
import type { OfficeFlowSnapshot } from '@anum/contracts';
import { STATUS_ORDER, type StatusKey } from '../lib/campusModel.ts';
import type { WallModel } from '../lib/hqCommandWall.ts';
import type { ModuleState, TyreLive } from '../lib/hqModules.ts';
import type { VisualState } from '../hqAssets/interior/stateVisual.ts';
import { formatNumber, getLocale, t, type Locale, type MessageKey, type Vars } from '../lib/i18n.ts';

export type Translate = (key: MessageKey, vars?: Vars) => string;

/** A value older than this is called stale: the same 90 seconds the billboards use for a presence time. */
export const SCREEN_STALE_MS = 90_000;
/** Flow rows listed on the wall. */
export const FLOW_ROWS = 4;

/** Every field a screen can show, with the record it comes from. The docs table is this list (a test keeps them equal). */
export const SCREEN_FIELDS = [
  { id: 'wall.tiles', source: 'WallModel.tiles: open tasks, blocked work, pending approvals, running now (the live flow read), the same numbers and labels as the Operations wall block' },
  { id: 'wall.agents', source: 'WallModel.agents: agent total and by state (the presence read), labels hq.status.*' },
  { id: 'wall.flow', source: 'OfficeFlowSnapshot.items: kind and stage of the newest items by `since`, and their age; never the title' },
  { id: 'wall.time', source: 'WallModel.asOf (the flow read time); Live only while the presence stream is connected; Stale after 90 seconds' },
  { id: 'wall.tyre', source: 'TyreLive (the tyre dashboard the Tyre fleet cards read): vehicles, tyres fitted, in stock, below minimum, overdue inspections, due in 30 days, labels hq.tyre.*' },
  { id: 'wall.building', source: 'the open building name (registry), only in the "Not connected" line of the tyre block' },
  { id: 'monitor.state', source: 'the desk visual state (registry shift plus the presence status), label hq.status.* or Off shift' },
  { id: 'monitor.record', source: 'a fixed word for the record type behind the state: task run, task queued, meeting, approval' },
  { id: 'monitor.elapsed', source: 'OfficeFlowItem.since (task start) or OfficeFlowItem.waiting.since (approval start) of the item the flow places at the desk' },
  { id: 'monitor.verified', source: 'the presence record last_verified_at (Verified hh:mm:ss) and its stale flag' },
] as const;
export type ScreenFieldId = (typeof SCREEN_FIELDS)[number]['id'];

/** The properties the model may read, by path (`[]` is an array element, `*` any key of a fixed set). Anything else read from an input is a data truth failure. */
export const SCREEN_READS: readonly string[] = [
  'wall.tiles', 'wall.tiles[]', 'wall.tiles[].id', 'wall.tiles[].state', 'wall.tiles[].value', 'wall.tiles[].atLeast',
  'wall.agents', 'wall.agents.state', 'wall.agents.total', 'wall.agents.byState', 'wall.agents.byState.*', 'wall.asOf',
  'flow', 'flow.status', 'flow.snapshot', 'flow.snapshot.sources', 'flow.snapshot.sources.tasks', 'flow.snapshot.items', 'flow.snapshot.items[]',
  'flow.snapshot.items[].kind', 'flow.snapshot.items[].stage', 'flow.snapshot.items[].since', 'flow.snapshot.items[].waiting', 'flow.snapshot.items[].waiting.since',
  'flow.snapshot.items[].location', 'flow.snapshot.items[].location.desk_id',
  'tyre', 'tyre.state', 'tyre.data', 'tyre.data.vehicles', 'tyre.data.fitted', 'tyre.data.inStock', 'tyre.data.belowMinimum', 'tyre.data.overdueInspections', 'tyre.data.dueNext30',
  'source', 'source.visual', 'source.presence', 'source.presence.status', 'source.presence.stale', 'source.presence.observedAt',
  'source.record', 'source.record.kind', 'source.record.stage', 'source.record.since', 'source.record.waitingSince',
];

const isTime = (value: unknown): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value));
const clock = (iso: string, locale: Locale): string => new Date(iso).toLocaleTimeString(locale === 'ar' ? 'ar-u-nu-latn' : undefined);

/** The figure as the page prints it: plain in English (the Tyre fleet cards print 1031), the locale's digits in Arabic. */
const plain = (value: number, locale: Locale): string => (locale === 'ar' ? formatNumber(value, locale) : String(value));

/** "45s", "12m", "3h 05m", "2d 4h", from a number of milliseconds; null for a time that is not usable or lies in the future. */
export function duration(ms: number, tr: Translate, locale: Locale): string | null {
  if (!Number.isFinite(ms) || ms < 0) return null;
  const s = Math.floor(ms / 1000);
  const f = (n: number) => formatNumber(n, locale);
  if (s < 60) return tr('hqScreen.dur.s', { n: f(s) });
  const m = Math.floor(s / 60);
  if (m < 60) return tr('hqScreen.dur.m', { n: f(m) });
  const h = Math.floor(m / 60);
  if (h < 24) return tr('hqScreen.dur.h', { h: f(h), m: f(m % 60).padStart(2, locale === 'ar' ? '٠' : '0') });
  return tr('hqScreen.dur.d', { d: f(Math.floor(h / 24)), h: f(h % 24) });
}

// ---------------------------------------------------------------- monitors

export type ScreenTone = 'work' | 'wait' | 'meet' | 'block' | 'idle' | 'off' | 'dim';

export type DeskRecord = Readonly<{ kind: string; stage: string; since: string; waitingSince: string | null }>;

/** The newest open task of each desk from the live flow, by desk id: what the monitor's elapsed time is read from. */
export function deskRecords(snapshot: Pick<OfficeFlowSnapshot, 'items' | 'sources'> | null | undefined): Map<string, DeskRecord> {
  const out = new Map<string, DeskRecord>();
  if (!snapshot || snapshot.sources.tasks !== 'connected') return out;
  for (const item of snapshot.items) {
    if (item.kind !== 'task' || item.stage === 'done' || !item.location) continue;
    const desk = item.location.desk_id;
    const known = out.get(desk);
    if (known && Date.parse(known.since) >= Date.parse(item.since)) continue;
    out.set(desk, { kind: item.kind, stage: item.stage, since: item.since, waitingSince: item.waiting ? item.waiting.since : null });
  }
  return out;
}

export type MonitorSource = Readonly<{
  visual: VisualState;
  presence: Readonly<{ status: StatusKey; stale?: boolean; observedAt: string }> | null;
  record: DeskRecord | null;
}>;

export type MonitorFace = Readonly<{
  tone: ScreenTone;
  headline: string;
  /** The record type word (task run, meeting, approval), or null. */
  record: string | null;
  /** How long the backing record has been running or waiting, from its real start; null when no record gives a start. */
  elapsed: string | null;
  foot: string | null;
  /** A scan line may run: the presence is fresh and says the agent is working or in a meeting. */
  scan: boolean;
  rtl: boolean;
}>;

const TONE: Record<VisualState, ScreenTone> = {
  working: 'work', queued: 'wait', meeting: 'meet', waiting: 'wait', blocked: 'block', idle: 'idle', paused: 'off', unknown: 'dim', assigned: 'dim', vacant: 'off', offshift: 'off',
};

export function monitorFace(source: MonitorSource, now: number, locale: Locale = getLocale(), tr: Translate = (key, vars) => t(key, vars)): MonitorFace {
  const visual = source.visual;
  const rtl = locale === 'ar';
  const face = (headline: string, record: string | null, elapsed: string | null, foot: string | null, scan = false): MonitorFace => ({ tone: TONE[visual], headline, record, elapsed, foot, scan, rtl });
  if (visual === 'offshift') return face(tr('hqScreen.offShift'), null, null, null);
  if (visual === 'vacant') return face(tr('hqScreen.vacant'), null, null, null);
  const presence = source.presence;
  if (!presence) return face(tr('hqScreen.notConnected'), null, null, null);
  const at = isTime(presence.observedAt) ? presence.observedAt : null;
  const foot = at ? tr('hqScreen.verified', { time: clock(at, locale) }) : null;
  const stale = presence.stale === true;
  const rec = source.record;
  const since = (iso: string | null | undefined): string | null => (isTime(iso) ? duration(now - Date.parse(iso), tr, locale) : null);
  switch (visual) {
    case 'working': {
      const d = rec ? since(rec.since) : null;
      return face(tr('hq.status.working'), tr('hqScreen.record.task'), d ? tr('hqScreen.running', { time: d }) : null, foot, !stale);
    }
    case 'queued': {
      const d = rec ? since(rec.since) : null;
      return face(tr('hq.status.queued'), tr('hqScreen.record.queued'), d ? tr('hqScreen.queuedFor', { time: d }) : null, foot);
    }
    case 'meeting': return face(tr('hq.status.meeting'), tr('hqScreen.record.meeting'), null, foot, !stale);
    case 'waiting': {
      const d = rec ? since(rec.waitingSince) : null;
      return face(tr('hq.status.waiting_approval'), tr('hqScreen.record.approval'), d ? tr('hqScreen.waitingFor', { time: d }) : null, foot);
    }
    case 'blocked': return face(tr('hq.status.blocked'), null, null, foot);
    case 'idle': return face(tr('hq.status.idle'), null, null, foot);
    case 'paused': return face(tr('hq.status.paused'), null, null, foot);
    default: return face(stale ? tr('hqScreen.stale') : tr('hq.status.unknown'), null, null, foot);
  }
}

// ---------------------------------------------------------------- the wall

export type WallStatus = 'live' | 'stale' | 'ok' | 'off';

export type WallFace = Readonly<{
  rtl: boolean;
  title: string;
  tiles: ReadonlyArray<Readonly<{ id: string; label: string; text: string; ready: boolean }>>;
  agents: Readonly<{ label: string; text: string; ready: boolean; parts: ReadonlyArray<Readonly<{ key: string; text: string }>> }>;
  flowTitle: string;
  /** One line per newest flow item: type, stage and age. Never a title. */
  flowRows: readonly string[];
  /** Shown instead of the rows: "Nothing is moving right now." or "Not connected". */
  flowNote: string | null;
  /** The tyre figures of a Tyre Pulse building, labelled as the Tyre fleet cards label them, or the reason there are none. */
  tyre: null | Readonly<{ title: string; items: ReadonlyArray<Readonly<{ label: string; text: string }>>; note: string | null }>;
  status: WallStatus;
  statusText: string | null;
  updated: string | null;
}>;

export type WallInput = Readonly<{
  wall: WallModel;
  flow: Readonly<{ status: string; snapshot: OfficeFlowSnapshot | null }> | null;
  /** The tyre dashboard state when the open building shows a tyre panel; null for any other building. */
  tyre: ModuleState<TyreLive> | null;
  buildingName: string;
  /** The presence stream is connected right now: the Live mark shows only then. */
  live: boolean;
}>;

export function wallFace(input: WallInput, now: number, locale: Locale = getLocale(), tr: Translate = (key, vars) => t(key, vars)): WallFace {
  const wall = input.wall;
  const f = (n: number) => formatNumber(n, locale);
  const tiles = wall.tiles.map((tile) => {
    const ready = tile.state === 'ready';
    const text = ready ? `${tile.atLeast ? '≥ ' : ''}${f(tile.value ?? 0)}`
      : tile.state === 'loading' ? tr('hq.wall.loading') : tile.state === 'no_access' ? tr('hq.wall.noAccess') : tr('hq.wall.notConnected');
    return { id: tile.id, label: tr(`hq.wall.${tile.id}` as MessageKey), text, ready };
  });
  const a = wall.agents;
  let agents: WallFace['agents'];
  if (a.state === 'ready') {
    agents = {
      label: tr('hq.wall.agents'), text: f(a.total), ready: true,
      parts: STATUS_ORDER.filter((k) => a.byState[k] > 0).map((k) => ({ key: k, text: `${tr(`hq.status.${k}` as MessageKey)} ${f(a.byState[k])}` })),
    };
  } else agents = { label: tr('hq.wall.agents'), text: a.state === 'loading' ? tr('hq.wall.loading') : tr('hq.wall.notConnected'), ready: false, parts: [] };

  // The newest flow items by type and stage (the fixed words of the live flow), never a title.
  const snap = input.flow?.snapshot ?? null;
  let flowRows: string[] = [];
  let flowNote: string | null = null;
  if (!snap || snap.sources.tasks !== 'connected') flowNote = tr('hq.wall.notConnected');
  else {
    const items = snap.items.filter((i) => isTime(i.since)).sort((x, y) => Date.parse(y.since) - Date.parse(x.since)).slice(0, FLOW_ROWS);
    flowRows = items.map((i) => {
      const time = duration(now - Date.parse(i.since), tr, locale);
      const vars = { kind: tr(`hqFlow.kind.${i.kind}` as MessageKey), stage: tr(`hqFlow.stage.${i.stage}` as MessageKey), time: time ?? '' };
      // A start that is not usable (a clock set back) says no age, never an empty one.
      return time === null ? tr('hqScreen.flowRowNoAge', vars) : tr('hqScreen.flowRow', vars);
    });
    if (!flowRows.length) flowNote = tr('hqScreen.flowNone');
  }

  let tyre: WallFace['tyre'] = null;
  const ty = input.tyre;
  if (ty) {
    const title = tr('hq.tyre.title');
    if (ty.state === 'ready') {
      const d = ty.data;
      const items = [
        { label: tr('hq.tyre.vehicles'), text: plain(d.vehicles, locale) },
        { label: tr('hq.tyre.fitted'), text: plain(d.fitted, locale) },
        ...(d.inStock !== null ? [{ label: tr('hq.tyre.stock'), text: plain(d.inStock, locale) }] : []),
        { label: tr('hq.tyre.below'), text: plain(d.belowMinimum, locale) },
        { label: tr('hq.tyre.overdue'), text: plain(d.overdueInspections, locale) },
        { label: tr('hq.tyre.due'), text: plain(d.dueNext30, locale) },
      ];
      tyre = { title, items, note: null };
    } else if (ty.state === 'loading') tyre = { title, items: [], note: tr('hq.wall.loading') };
    else if (ty.state === 'forbidden') tyre = { title, items: [], note: tr('hqScreen.tyreNoAccess', { name: input.buildingName }) };
    else tyre = { title, items: [], note: tr('hqScreen.tyreOff', { name: input.buildingName }) };
  }

  // Time and link: "Updated" is the flow read time; Live only while the stream is connected; Stale after the threshold.
  const asOf = isTime(wall.asOf) ? wall.asOf : null;
  const unavailable = !asOf && tiles.every((x) => !x.ready) && !agents.ready;
  let status: WallStatus = 'ok';
  let statusText: string | null = null;
  if (unavailable) { status = 'off'; statusText = tr('hqScreen.notConnected'); }
  else if (asOf && now - Date.parse(asOf) > SCREEN_STALE_MS) { status = 'stale'; statusText = tr('hqScreen.stale'); }
  else if (input.live) { status = 'live'; statusText = tr('hqScreen.live'); }
  return {
    rtl: locale === 'ar', title: tr('hq.wall.title'), tiles, agents, flowTitle: tr('hqScreen.flowTitle'), flowRows, flowNote, tyre,
    status, statusText, updated: asOf ? tr('hqScreen.updated', { time: clock(asOf, locale) }) : null,
  };
}

/** Every string on a monitor, with the field it comes from (the test and the docs read this). */
export function monitorTexts(face: MonitorFace): Array<{ field: ScreenFieldId; text: string }> {
  const out: Array<{ field: ScreenFieldId; text: string }> = [{ field: 'monitor.state', text: face.headline }];
  if (face.record) out.push({ field: 'monitor.record', text: face.record });
  if (face.elapsed) out.push({ field: 'monitor.elapsed', text: face.elapsed });
  if (face.foot) out.push({ field: 'monitor.verified', text: face.foot });
  return out;
}

/** Every string on the wall, with the field it comes from. */
export function wallTexts(face: WallFace): Array<{ field: ScreenFieldId; text: string }> {
  const out: Array<{ field: ScreenFieldId; text: string }> = [];
  for (const tile of face.tiles) out.push({ field: 'wall.tiles', text: tile.label }, { field: 'wall.tiles', text: tile.text });
  out.push({ field: 'wall.agents', text: face.agents.label }, { field: 'wall.agents', text: face.agents.text });
  for (const p of face.agents.parts) out.push({ field: 'wall.agents', text: p.text });
  out.push({ field: 'wall.flow', text: face.flowTitle });
  for (const row of face.flowRows) out.push({ field: 'wall.flow', text: row });
  if (face.flowNote) out.push({ field: 'wall.flow', text: face.flowNote });
  if (face.tyre) {
    out.push({ field: 'wall.tyre', text: face.tyre.title });
    for (const item of face.tyre.items) out.push({ field: 'wall.tyre', text: item.label }, { field: 'wall.tyre', text: item.text });
    if (face.tyre.note) out.push({ field: 'wall.building', text: face.tyre.note });
  }
  if (face.updated) out.push({ field: 'wall.time', text: face.updated });
  if (face.statusText) out.push({ field: 'wall.time', text: face.statusText });
  out.push({ field: 'wall.tiles', text: face.title });
  return out;
}

/** A stable text of a face, for the throttle (an unchanged face is not drawn again) and for the stage's data attribute. */
export const monitorKey = (f: MonitorFace): string => [f.tone, f.headline, f.record, f.elapsed, f.foot, f.scan ? 1 : 0, f.rtl ? 1 : 0].join('|');
export const wallKey = (f: WallFace): string => JSON.stringify([f.rtl, f.tiles, f.agents, f.flowRows, f.flowNote, f.tyre, f.status, f.statusText, f.updated]);
