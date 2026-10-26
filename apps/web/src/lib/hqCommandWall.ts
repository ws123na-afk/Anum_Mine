/**
 * The operations command centre wall (docs/ANUM_MINE_HQ_LIVE_SCENE.md#command-wall): dashboards that show only numbers
 * the open workspace's own records give, read from endpoints that already exist (the live flow and the presence
 * stream). A source that fails or is not connected shows "Not connected" and no number; nothing is estimated.
 */
import type { OfficeFlowSnapshot, OfficeFlowSourceState } from '@anum/contracts';
import { STATUS_ORDER, type StatusKey } from './campusModel.ts';

export type WallTileId = 'openTasks' | 'blocked' | 'approvals' | 'running';
export type WallTileState = 'ready' | 'loading' | 'not_connected' | 'no_access';
export type WallTile = Readonly<{
  id: WallTileId;
  state: WallTileState;
  /** Null unless `state` is `ready`. */
  value: number | null;
  /** The flow read stops at a page limit: the number is a lower bound. */
  atLeast: boolean;
}>;
export type WallAgents =
  | Readonly<{ state: 'ready'; total: number; byState: Readonly<Record<StatusKey, number>> }>
  | Readonly<{ state: 'loading' | 'not_connected' }>;
export type WallModel = Readonly<{ tiles: readonly WallTile[]; agents: WallAgents; asOf: string | null }>;

export type WallFlow = Readonly<{ status: 'loading' | 'ready' | 'error' | 'denied'; snapshot: OfficeFlowSnapshot | null }>;
export type WallPresenceItem = Readonly<{ agent_id: string | null; state: string }>;

function sourceState(flow: WallFlow, source: OfficeFlowSourceState | undefined): WallTileState {
  if (flow.status === 'denied') return 'no_access';
  if (!flow.snapshot) return flow.status === 'loading' ? 'loading' : 'not_connected';
  if (source === 'connected') return 'ready';
  if (source === 'no_access') return 'no_access';
  return 'not_connected';
}

export function commandWall(flow: WallFlow, presence: readonly WallPresenceItem[] | null, presenceLoading = false): WallModel {
  const snap = flow.snapshot;
  const tasks = snap ? snap.items.filter((i) => i.kind === 'task') : [];
  const atLeast = snap?.truncated === true;
  const tile = (id: WallTileId, source: OfficeFlowSourceState | undefined, count: () => number): WallTile => {
    const state = sourceState(flow, source);
    return { id, state, value: state === 'ready' ? count() : null, atLeast: state === 'ready' && atLeast };
  };
  const tiles: WallTile[] = [
    tile('openTasks', snap?.sources.tasks, () => tasks.filter((i) => i.stage !== 'done').length),
    tile('blocked', snap?.sources.tasks, () => tasks.filter((i) => i.stage === 'blocked').length),
    tile('approvals', snap?.sources.approvals, () => tasks.filter((i) => i.waiting !== null).length),
    // A task that says "running" but whose run went silent or failed is not counted as running work.
    tile('running', snap?.sources.tasks, () => tasks.filter((i) => i.status === 'running' && i.stuck?.code !== 'run_silent' && i.stuck?.code !== 'run_failed').length),
  ];
  let agents: WallAgents;
  if (!presence) agents = { state: presenceLoading ? 'loading' : 'not_connected' };
  else {
    const byState = Object.fromEntries(STATUS_ORDER.map((k) => [k, 0])) as Record<StatusKey, number>;
    let total = 0;
    for (const p of presence) {
      if (p.agent_id === null) continue; // a desk whose agent you cannot see is not counted as an agent
      if (p.state in byState) byState[p.state as StatusKey] += 1;
      total += 1;
    }
    agents = { state: 'ready', total, byState };
  }
  return { tiles, agents, asOf: snap?.as_of ? String(snap.as_of) : null };
}
