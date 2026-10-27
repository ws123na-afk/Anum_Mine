/**
 * Record-backed work motion for the campus (docs/ANUM_MINE_HQ_CAMPUS_MOTION.md, layer 2). A lazy component that renders nothing: it reads the
 * three real feeds the campus already has (presence totals per building, the live flow of the open workspace, and the Tyre Pulse reader for the
 * open workspace), turns what changed into `WorkRecord`s, runs them through the planner and reports a plan for the 3D layer plus the "Because"
 * lines and feed notes for the building cards. With a feed off there are no records for it, so nothing moves and the card says why.
 */
import { useEffect, useMemo, useRef } from 'react';
import type { OfficeFlowSnapshot } from '@anum/contracts';
import type { Lane } from './hqAssets/environment/scenery.ts';
import { workspaceOf, type Campus } from './lib/campusModel.ts';
import { t, type MessageKey } from './lib/i18n.ts';
import { loadTyreFeed } from './lib/hqTyreFeed.ts';
import type { LayerMotion, WorkLayerPlan } from './lib/hqWorkLayer.ts';
import {
  KIND_VEHICLE, WorkPlanner, becauseLine, flowRecords, presenceRecords, routeTo, sampleRoad, siteOf, stagesOf, tyreRecords,
  type Box2, type CardWork, type FeedNote, type FlowView, type PresenceView, type TyreFeed, type WorkKind, type WorkRoute, type WorkSite,
} from './lib/hqWorkMotion.ts';
import { useLocale } from './useLocale';

export type WorkSiteInput = Readonly<{ key: string; /** The workspace the building belongs to (its scene key is not always a workspace id). */ workspaceId: string; workshop: boolean; door: Readonly<{ x: number; z: number }>; box: Box2 }>;
export type WorkUi = Readonly<{ plan: WorkLayerPlan; cards: Readonly<Record<string, CardWork>>; feeds: Readonly<Record<string, FeedNote>> }>;

type Props = Readonly<{
  campus: Campus;
  sites: readonly WorkSiteInput[];
  lanes: readonly Lane[];
  flow: OfficeFlowSnapshot | null;
  /** The scene key of the open building. The Tyre Pulse feed is read for its workspace only. */
  openKey: string;
  reducedMotion: boolean;
  onChange: (ui: WorkUi) => void;
}>;

const TYRE_POLL_MS = 60_000;
// One planner for the whole page: a record that has been shown is not shown again when the campus is left and entered once more.
const planner = new WorkPlanner();

export default function HqCampusWork(props: Props): null {
  const locale = useLocale();
  const { campus, sites, lanes, flow, openKey, reducedMotion, onChange } = props;
  const known = useMemo(() => new Set(sites.map((s) => s.key)), [sites]);
  const siteMap = useMemo(() => new Map(sites.map((s) => [s.key, siteOf(s)] as const)), [sites]);
  const roads = useMemo(() => { const pts: number[] = []; for (const lane of lanes) sampleRoad(lane.pts, 6, pts); return new Float32Array(pts); }, [lanes]);
  const boxes = useMemo(() => sites.map((s) => s.box), [sites]);
  const routes = useMemo(() => {
    const map = new Map<string, WorkRoute | null>();
    for (const [key, site] of siteMap) map.set(key, routeTo(site, roads, boxes));
    return map;
  }, [siteMap, roads, boxes]);
  // The Tyre Pulse feed belongs to the open workspace. It animates the workshop building of that workspace (the starter campus draws Tyre Pulse as one);
  // a workspace without a workshop building but with a connected source shows it on its single building.
  const openWorkspace = useMemo(() => { const b = campus.buildings.find((x) => x.workspace_id === openKey); return b ? workspaceOf(b) : null; }, [campus.buildings, openKey]);
  const mine = useMemo(() => sites.filter((s) => s.workspaceId === openWorkspace), [sites, openWorkspace]);
  const workshopKey = mine.find((s) => s.workshop)?.key ?? null;
  const soleKey = mine.length === 1 ? mine[0].key : null;
  const feedWanted = mine.length > 0;
  const tyreKeyFor = (state: TyreFeed['state']): string | null => workshopKey ?? (state === 'connected' ? soleKey : null);

  const latest = useRef({ known, siteMap, routes, reducedMotion, locale, tyreKeyFor, onChange });
  latest.current = { known, siteMap, routes, reducedMotion, locale, tyreKeyFor, onChange };
  const feed = useRef<TyreFeed>({ state: 'loading' });
  const lastSig = useRef('');
  const timer = useRef<number | null>(null);

  const publish = useRef(() => {});
  publish.current = () => {
    const c = latest.current;
    const now = performance.now();
    planner.prune(now);
    const motions: LayerMotion[] = [];
    const cards: Record<string, CardWork> = {};
    for (const m of planner.motions) {
      const site = c.siteMap.get(m.record.buildingKey);
      if (!site) continue;
      if (m.animate && planner.animating(m, now)) {
        motions.push({ id: m.id, kind: m.record.kind, startedMs: m.startedMs, site, route: c.routes.get(m.record.buildingKey) ?? null });
      }
      // The newest motion of a building owns its card line.
      const prior = cards[m.record.buildingKey];
      if (!prior || Date.parse(m.record.at) >= Date.parse(prior.at)) cards[m.record.buildingKey] = { kind: m.record.kind, at: m.record.at, because: becauseLine(m.record, c.locale, (key: MessageKey, vars) => t(key, vars)) };
    }
    const feeds: Record<string, FeedNote> = {};
    const fs = feed.current.state;
    const tyreKey = c.tyreKeyFor(fs);
    for (const s of sites) {
      if (s.key === tyreKey) { if (fs === 'not_connected' || fs === 'forbidden' || fs === 'error') feeds[s.key] = { reason: fs }; }
      else if (s.workshop) feeds[s.key] = { reason: 'elsewhere' };
    }
    const racks: WorkSite[] = tyreKey && fs === 'connected' ? [c.siteMap.get(tyreKey)].filter((x): x is WorkSite => !!x) : [];
    const sig = JSON.stringify([motions.map((m) => m.id), Object.entries(cards).map(([k, v]) => [k, v.because]), feeds, racks.map((r) => r.key)]);
    if (sig !== lastSig.current) {
      lastSig.current = sig;
      c.onChange({ plan: { motions, racks }, cards, feeds });
    }
    if (timer.current !== null) window.clearTimeout(timer.current);
    const next = planner.nextEnd(now);
    timer.current = next === null ? null : window.setTimeout(() => publish.current(), Math.max(50, next - now + 20));
  };

  const accept = (records: ReturnType<typeof tyreRecords>) => {
    if (!records.length) return;
    const c = latest.current;
    const changed = planner.accept(records, performance.now(), {
      reducedMotion: c.reducedMotion,
      routed: (key, kind: WorkKind) => (KIND_VEHICLE[kind] === 'tyre' ? c.siteMap.has(key) : !!c.routes.get(key)),
    });
    if (changed) publish.current();
  };

  // Tyre Pulse: read for the open workspace now and every minute while the tab is visible.
  useEffect(() => {
    let live = true;
    const read = async () => {
      if (document.visibilityState === 'hidden') return;
      const next = await loadTyreFeed();
      if (!live) return;
      feed.current = next;
      accept(tyreRecords(next, latest.current.tyreKeyFor(next.state), latest.current.known, Date.now()));
      publish.current();
    };
    feed.current = { state: 'loading' };
    if (!feedWanted) { publish.current(); return () => { live = false; }; }
    void read();
    const id = window.setInterval(() => { void read(); }, TYRE_POLL_MS);
    return () => { live = false; window.clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedWanted, openWorkspace]);

  // Presence: a rise in the working count of a building between two answers.
  const prevPresence = useRef<readonly PresenceView[] | null>(null);
  useEffect(() => {
    const next: PresenceView[] = campus.buildings.map((b) => ({ key: b.workspace_id, working: b.totals ? b.totals.working : null, verifiedAt: b.presence_verified_at }));
    accept(presenceRecords(prevPresence.current, next, known, Date.now()));
    prevPresence.current = next;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campus.buildings]);

  // Flow: a task of the open workspace that started running or finished between two answers.
  const prevStages = useRef<ReadonlyMap<string, string> | null>(null);
  useEffect(() => {
    if (!flow) { prevStages.current = null; return; }
    const registry = campus.buildings.filter((b) => b.registryBuildingId !== undefined);
    const own = campus.buildings.filter((b) => workspaceOf(b) === flow.workspace_id);
    const buildingOf = (id: string): string | null => registry.find((b) => b.registryBuildingId === id)?.workspace_id ?? (own.length === 1 && own[0].registryBuildingId === undefined ? own[0].workspace_id : null);
    const items: FlowView[] = flow.items.map((i) => ({ key: i.key, kind: i.kind, stage: i.stage, since: i.since, buildingId: i.location?.building_id ?? null }));
    accept(flowRecords(prevStages.current, items, buildingOf, known, Date.now()));
    prevStages.current = stagesOf(items);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flow]);

  // The language or the sites changed: the card lines and routes follow.
  useEffect(() => { lastSig.current = ''; publish.current(); }, [locale, siteMap, routes, reducedMotion, workshopKey, soleKey]);
  useEffect(() => () => { if (timer.current !== null) window.clearTimeout(timer.current); }, []);
  return null;
}
