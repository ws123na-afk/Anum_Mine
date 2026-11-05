/**
 * Agent walkers on the campus (docs/ANUM_MINE_HQ_AGENT_WALKERS.md). A lazy component: it reads what the headquarters already holds (the office
 * registry, the presence answer and the live meetings), turns what CHANGED between two answers into walker records, runs them through the planner
 * and reports a plan for the 3D layer plus the "Because" lines for the building cards. It draws the focusable hover/tap targets over the figures,
 * the status chip and the polite live region. With the meeting feed unreadable there are no records, so nothing walks and the chip says so.
 */
import { useEffect, useMemo, useRef, useState, type MutableRefObject } from 'react';
import * as api from './lib/api.ts';
import type { CameraState } from './lib/headquartersSpatial.ts';
import type { SceneBuilding } from './lib/headquartersProjection.ts';
import { figureLook, roleFamily } from './lib/hqAvatar.ts';
import { viewProjection, projectToNdc } from './lib/hqMath.ts';
import { eligibleState, startedRecords, movedRecords, endedRecords, vanishedMeetings, topologyOf, buildRoute, becauseLine, newWalkPose, restPose, walkPoseAt, WalkerPlanner, type MeetingEntry, type SeatEntry, type SiteGeo, type SpokeLike, type WalkRoute, type WalkSnapshot, type WalkerKind, type WalkerRecord } from './lib/hqWalkers.ts';
import type { LayerWalker, WalkerLayerPlan } from './lib/hqWalkerLayer.ts';
import type { HqAgentPresence, MeetingEvidence } from './lib/hqPresence.ts';
import { t } from './lib/i18n.ts';
import { pickName, sceneName } from './lib/registryNames.ts';
import { useLocale } from './useLocale';
import './hqWalkers.css';

type AgentStyleLike = Readonly<{ avatar?: Parameters<typeof figureLook>[1]; department?: string | null }>;

export type WalkerFeed = Readonly<{
  workspaceId: string;
  registry: readonly SceneBuilding[];
  presence: readonly HqAgentPresence[] | null;
  meetings: MeetingEvidence | null;
  /** True once the first attempt to read the meetings has finished (so "null" means "could not be read", not "still loading"). */
  meetingsRead: boolean;
  styles: ReadonlyMap<string, AgentStyleLike> | undefined;
}>;

export type WalkerCard = Readonly<{ kind: WalkerKind; because: string; at: string }>;
export type WalkerUi = Readonly<{ plan: WalkerLayerPlan; cards: Readonly<Record<string, WalkerCard>>; off: boolean; count: number; waiting: number }>;
export const NO_WALKERS: WalkerUi = { plan: { walkers: [] }, cards: {}, off: false, count: 0, waiting: 0 };

export type ViewSink = MutableRefObject<((camera: CameraState, aspect: number) => void) | null>;

type Props = Readonly<{
  feed: WalkerFeed | null;
  sites: readonly SiteGeo[];
  spokes: readonly SpokeLike[];
  reducedMotion: boolean;
  viewSink: ViewSink;
  onChange: (ui: WalkerUi) => void;
}>;

// One planner for the whole page: a record that has been shown is not shown again when the campus is left and entered once more.
const planner = new WalkerPlanner();

type Shown = Readonly<{ id: string; who: string; because: string; to: string; animate: boolean }>;

export default function HqCampusWalkers(props: Props) {
  const locale = useLocale();
  const { feed, sites, spokes, reducedMotion, viewSink, onChange } = props;
  const topo = useMemo(() => (feed ? topologyOf(feed.workspaceId, feed.registry) : topologyOf('', [])), [feed?.workspaceId, feed?.registry]); // eslint-disable-line react-hooks/exhaustive-deps
  const siteMap = useMemo(() => new Map(sites.map((s) => [s.key, s] as const)), [sites]);
  const spokeMap = useMemo(() => new Map(spokes.map((s) => [s.id, s] as const)), [spokes]);
  const cache = useRef(new Map<string, WalkRoute | null>());
  useEffect(() => { cache.current = new Map(); }, [siteMap, spokeMap]);
  const route = useRef((from: string, to: string): WalkRoute | null => null);
  route.current = (from, to) => {
    const k = `${from}>${to}`;
    if (!cache.current.has(k)) {
      const a = siteMap.get(from), b = siteMap.get(to);
      cache.current.set(k, a && b ? buildRoute(a, b, spokeMap) : null);
    }
    return cache.current.get(k) ?? null;
  };

  const names = useMemo(() => {
    const rooms = new Map<string, string>(), buildings = new Map<string, string>();
    for (const b of feed?.registry ?? []) {
      buildings.set(`${feed!.workspaceId}~${b.id}`, sceneName(b, locale));
      for (const f of b.floors) for (const r of f.rooms) rooms.set(r.id, pickName(r.label, r.labelAr, locale));
    }
    return { room: (id: string) => rooms.get(id) ?? '', building: (key: string) => buildings.get(key) ?? '' };
  }, [feed?.registry, feed?.workspaceId, locale]); // eslint-disable-line react-hooks/exhaustive-deps

  const latest = useRef({ feed, topo, names, locale, reducedMotion, onChange });
  latest.current = { feed, topo, names, locale, reducedMotion, onChange };
  const [shown, setShown] = useState<readonly Shown[]>([]);
  const [chip, setChip] = useState<{ count: number; waiting: number; off: boolean }>({ count: 0, waiting: 0, off: false });
  const lastSig = useRef('');
  const timer = useRef<number | null>(null);
  const live = useRef<readonly { walker: ReturnType<typeof planner.drawn>[number]; route: WalkRoute }[]>([]);

  const publish = useRef(() => {});
  publish.current = () => {
    const c = latest.current;
    const now = performance.now();
    planner.prune(now);
    const walkers: LayerWalker[] = [];
    const cards: Record<string, WalkerCard> = {};
    const list: Shown[] = [];
    for (const w of planner.walkers) {
      const r = w.record;
      const because = becauseLine(r, c.names, c.locale, (key, vars) => t(key, vars));
      const prior = cards[r.to];
      if (!prior || Date.parse(r.at) >= Date.parse(prior.at)) cards[r.to] = { kind: r.kind, because, at: r.at };
      const p = c.feed?.presence?.find((x) => x.agentId === r.agentId);
      const who = p && p.name.trim() && eligibleState(p) ? p.name.trim() : t('hqWalkers.who.unnamed');
      list.push({ id: w.id, who, because, to: r.to, animate: w.animate });
      if (now < w.endMs) {
        const style = c.feed?.styles?.get(r.agentId);
        walkers.push({ id: w.id, seed: r.agentId, look: figureLook(r.agentId, style?.avatar, roleFamily({ roomKind: c.topo.homeRoomKind.get(r.agentId), department: style?.department ?? null })), startedMs: w.startedMs, animate: w.animate, route: w.route });
      }
    }
    live.current = planner.drawn(now).map((walker) => ({ walker, route: walker.route }));
    const off = !!c.feed && c.topo.known.size > 0 && c.feed.meetingsRead && c.feed.meetings === null;
    const waiting = planner.waiting;
    const sig = JSON.stringify([list.map((x) => [x.id, x.because, x.who]), walkers.map((x) => x.id), off, waiting]);
    if (sig !== lastSig.current) {
      lastSig.current = sig;
      setShown(list);
      setChip({ count: walkers.length, waiting, off });
      c.onChange({ plan: { walkers }, cards, off, count: walkers.length, waiting });
    }
    if (timer.current !== null) window.clearTimeout(timer.current);
    const next = planner.nextEnd(now);
    timer.current = next === null ? null : window.setTimeout(() => publish.current(), Math.max(50, next - now + 20));
  };

  // Each new answer: what changed since the last one is an event; the first answer replays nothing.
  const prev = useRef<WalkSnapshot | null>(null);
  const prevEntries = useRef<ReadonlyMap<string, MeetingEntry>>(new Map());
  // Every meeting id seen live in this page load: one that comes back after a pause is not a new start.
  const seenMeetings = useRef(new Set<string>());
  // A meeting record read must outlive the answer that asked for it (the next presence answer re-runs this effect), so only unmounting cancels it.
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!feed || topo.known.size === 0) { prev.current = null; publish.current(); return; }
    const seats = feed.presence ? new Map<string, SeatEntry>(feed.presence.filter((p) => !p.agentId.startsWith('desk:')).map((p) => [p.agentId, { deskId: p.deskId ?? null, verifiedAt: p.observedAt, eligible: eligibleState(p) }] as const)) : null;
    const meetings = feed.meetingsRead && feed.meetings
      ? new Map<string, MeetingEntry>([...feed.meetings.rooms].map(([roomId, m]) => [m.meetingId, { roomId, agentIds: m.agentIds, startedAt: m.startedAt ?? null }] as const))
      : null;
    // A failed read keeps the last good answer, so a flaky read never turns a meeting that was already live into a new one.
    const before = prev.current;
    const next: WalkSnapshot = { meetings: meetings ?? before?.meetings ?? null, seats: seats ?? before?.seats ?? null };
    const nowMs = Date.now();
    const opts = { reducedMotion: latest.current.reducedMotion, route: (a: string, b: string) => route.current(a, b) };
    const records: WalkerRecord[] = [...startedRecords(before, meetings ? next : { ...next, meetings: null }, topo, nowMs, seenMeetings.current), ...movedRecords(before, next, topo, nowMs)];
    if (planner.accept(records, performance.now(), opts)) publish.current();
    // A meeting that is gone: ask its record why. Only a finished, cancelled or failed meeting (with its end time) sends people back.
    const gone = meetings ? vanishedMeetings(before, next) : [];
    const entries = prevEntries.current;
    for (const id of gone.slice(0, 6)) {
      const entry = entries.get(id);
      if (!entry) continue;
      void api.apiRequest<{ status?: unknown; ended_at?: unknown }>(`/api/v1/agent-meetings/${encodeURIComponent(id)}`, { method: 'GET' }).then((detail) => {
        if (!mounted.current) return;
        const back = endedRecords(id, entry, detail, next.seats, topo, Date.now());
        if (planner.accept(back, performance.now(), opts)) publish.current();
      }).catch(() => undefined);
    }
    if (meetings) { prevEntries.current = meetings; for (const id of meetings.keys()) seenMeetings.current.add(id); }
    prev.current = next;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed?.meetings, feed?.presence, feed?.meetingsRead, topo]);

  useEffect(() => { lastSig.current = ''; publish.current(); }, [locale, siteMap, spokeMap, reducedMotion]);
  useEffect(() => () => { if (timer.current !== null) window.clearTimeout(timer.current); }, []);

  // Hover/tap/focus targets follow the figures: positioned from the stage's frame callback, never from a React render per frame.
  const layer = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    const pose = newWalkPose();
    viewSink.current = (camera, aspect) => {
      const host = layer.current;
      if (!host) return;
      const w = host.clientWidth, h = host.clientHeight;
      if (!w || !h) return;
      const vp = viewProjection(camera, aspect);
      const now = performance.now();
      for (const item of live.current) {
        const el = host.querySelector<HTMLElement>(`[data-walker-id="${CSS.escape(item.walker.id)}"]`);
        if (!el) continue;
        if (item.walker.animate) walkPoseAt(item.route, (now - item.walker.startedMs) / 1000, pose); else restPose(item.route, pose);
        const p = projectToNdc(vp, { x: pose.x, y: 2.4, z: pose.z });
        const visible = pose.visible && p.visible && Math.abs(p.x) <= 1.05 && Math.abs(p.y) <= 1.05;
        el.style.display = visible ? 'block' : 'none';
        const flip = (1 - (p.y * 0.5 + 0.5)) * h < 90 ? 'below' : '';
        if (el.dataset.flip !== flip) el.dataset.flip = flip;
        el.style.transform = `translate(${((p.x * 0.5 + 0.5) * w).toFixed(1)}px, ${((1 - (p.y * 0.5 + 0.5)) * h).toFixed(1)}px)`;
      }
    };
    return () => { viewSink.current = null; };
  }, [viewSink]);

  const walkingNow = shown.filter((s) => s.animate).length;
  return (
    <div className="hqWalkerLayer" ref={layer} data-testid="hq-walkers" data-walkers={chip.count} data-waiting={chip.waiting} data-walkers-feed={chip.off ? 'off' : 'on'}>
      <div className="hqSrOnly" role="status" aria-live="polite" data-testid="hq-walkers-live">
        {shown.map((s) => <p key={s.id}>{t(s.animate ? 'hqWalkers.walking' : 'hqWalkers.arrived', { who: s.who, building: names.building(s.to) || t('hqWalkers.place.building'), because: s.because })}</p>)}
        {chip.waiting > 0 && <p>{t('hqWalkers.status.more', { count: String(chip.waiting) })}</p>}
        {chip.off && <p>{t('hqWalkers.status.off')}</p>}
      </div>
      {shown.map((s) => (
        <button key={s.id} type="button" className="hqWalkerPin" data-walker-id={s.id} data-open={open === s.id} data-animate={s.animate}
          aria-label={t('hqWalkers.figure', { who: s.who, because: s.because })} aria-expanded={open === s.id}
          onClick={() => setOpen(open === s.id ? null : s.id)} onBlur={() => setOpen((o) => (o === s.id ? null : o))}>
          <span className="hqWalkerTip" role="tooltip"><strong>{s.who}</strong><span>{s.because}</span></span>
        </button>
      ))}
      {(chip.count > 0 || chip.off) && (
        <p className="hqWalkerChip" data-off={chip.off} title={chip.off ? t('hqWalkers.status.offHint') : t('hqWalkers.legend')}>
          {chip.off ? t('hqWalkers.status.off') : <>{t('hqWalkers.status.count', { count: String(walkingNow || chip.count) })}{chip.waiting > 0 && <> · {t('hqWalkers.status.more', { count: String(chip.waiting) })}</>}</>}
        </p>
      )}
    </div>
  );
}
