import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { CampusBuilding } from '../src/lib/campusModel.ts';
import { boardsFromApi, mainSign, startRemoteBoards, withRemote, NO_REMOTE, type RemoteState } from '../src/hq-billboards/remoteBoards.ts';

// The campus boards read (docs/ANUM_MINE_HQ_BILLBOARDS.md#every-building). Every value is plainly fake.
const SCOPE = { tenantId: 't1', workspaceId: 'ws_open' };
const SHA = 'cd'.repeat(32);
const board = (ws: string, over: Record<string, unknown> = {}) => ({
  workspace_id: ws, accessible: true, as_of: '2026-10-29T10:00:00Z', buildings_total: 1,
  buildings: [{ id: 'b1', name: 'Example', name_ar: null, floors: 1, rooms: 2, sign: { style: 'plaque', color: '#1b3a6b', logo_sha256: SHA } }],
  rooms: { total: 2, items: [{ name: 'Room one', name_ar: null, kind: 'office', team_department: 'sales', building_id: 'b1' }, { name: 'Room two', name_ar: 'غرفة', kind: 'meeting', team_department: null, building_id: 'b1' }] },
  departments: ['sales'], work: { source: 'connected', open: 2, waiting: 0, blocked: 1, truncated: false, by_building: {} }, ...over,
});
const answer = (boards: unknown[], over: Record<string, unknown> = {}) => ({ version: 1, tenant_id: 't1', current_workspace_id: 'ws_open', generated_at: 'x', next_cursor: null, boards, ...over });

test('boards are read defensively and only for the scope that asked', () => {
  const { boards } = boardsFromApi(answer([board('ws_b'), board('ws_b'), board('ws_open'), { workspace_id: 'ws_c', accessible: false }, 'junk',
    board('ws_d', { work: { source: 'connected', open: 'many', waiting: 0, blocked: 0 }, buildings: [{ id: 'b1', sign: { color: 'red', logo_sha256: 'nope', style: null } }] })]), SCOPE);
  assert.deepEqual(boards.map((b) => [b.workspaceId, b.accessible]), [['ws_b', true], ['ws_c', false], ['ws_d', true]]);
  const b = boards[0];
  assert.deepEqual(b.sign, { buildingId: 'b1', workspaceId: 'ws_b', style: 'plaque', color: '#1b3a6b', logoSha256: SHA });
  assert.deepEqual(b.facts?.work, { source: 'connected', open: 2, waiting: 0, blocked: 1, truncated: false });
  assert.deepEqual(b.facts?.rooms.map((r) => r.label), ['Room one', 'Room two']);
  assert.equal(boards[2].facts?.work.open, null);
  assert.equal(boards[2].sign, null);
  assert.throws(() => boardsFromApi(answer([], { tenant_id: 't2' }), SCOPE));
  assert.throws(() => boardsFromApi(answer([], { current_workspace_id: 'ws_b' }), SCOPE));
  assert.throws(() => boardsFromApi({ version: 2, boards: [] }, SCOPE));
});

test('a workspace\'s sign is its only building\'s, or the starter headquarters\', never a guess', () => {
  const s = (id: string) => ({ buildingId: id, style: null, color: '#123456', logoSha256: null });
  assert.equal(mainSign([{ id: 'x', sign: s('x') }])?.buildingId, 'x');
  assert.equal(mainSign([{ id: 'campus-02-commerce', sign: s('c') }, { id: 'campus-01-hq', sign: s('h') }])?.buildingId, 'h');
  assert.equal(mainSign([{ id: 'a', sign: s('a') }, { id: 'b', sign: s('b') }]), null);
  assert.equal(mainSign([]), null);
});

const bld = (id: string, over: Partial<CampusBuilding> = {}): CampusBuilding => ({ workspace_id: id, tenant_id: 't1', name: id, kind: 'business', icon: null, floors: 1, accessible: true, totals: null, presence_verified_at: null, ...over });

test('the facts are laid on other workspaces\' buildings only, and a registry sign is never replaced', () => {
  const { boards } = boardsFromApi(answer([board('ws_b'), board('ws_c'), { workspace_id: 'ws_x', accessible: false }]), SCOPE);
  const state: RemoteState = { status: 'ready', boards: new Map(boards.map((b) => [b.workspaceId, b])) };
  const own = bld('ws_open~b1', { realWorkspaceId: 'ws_open' });
  const kept = { buildingId: 'mine', style: null, color: '#000000', logoSha256: null };
  const out = withRemote([own, bld('ws_b'), bld('ws_c', { sign: kept }), bld('ws_x'), bld('ws_locked', { accessible: false }), bld('ws_new')], state, 'ws_open');
  assert.equal(out[0], own);
  assert.equal(out[1].remote?.board?.work.open, 2);
  assert.equal(out[1].sign?.workspaceId, 'ws_b');
  assert.equal(out[2].sign, kept);
  assert.deepEqual(out[3].remote, { status: 'ready', board: null });
  assert.equal(out[4].remote, undefined);
  assert.deepEqual(out[5].remote, { status: 'ready', board: null });
  assert.deepEqual(withRemote([bld('ws_b')], NO_REMOTE, 'ws_open')[0].remote, { status: 'loading', board: null });
});

const tick = () => new Promise((r) => setTimeout(r, 0));

test('the feed reads every page, keeps the last answer after a failure, retries, and calls a 404 unsupported', async () => {
  const states: RemoteState[] = [];
  const asked: Array<string | null> = [];
  let fail: unknown = null;
  const load = async (cursor: string | null) => {
    asked.push(cursor);
    if (fail) throw fail;
    return cursor ? answer([board('ws_c')]) : answer([board('ws_b')], { next_cursor: 'ws_b' });
  };
  const feed = startRemoteBoards({ load, scope: () => SCOPE, onChange: (s) => states.push(s), intervalMs: 60_000 });
  await tick(); await tick(); await tick();
  assert.deepEqual(states.map((s) => s.status), ['loading', 'ready']);
  assert.deepEqual([...states[1].boards.keys()], ['ws_b', 'ws_c']);
  assert.deepEqual(asked, [null, 'ws_b']);
  fail = Object.assign(new Error('down'), { status: 503 });
  feed.retry();
  await tick(); await tick();
  assert.equal(states.at(-1)!.status, 'error');
  assert.equal(states.at(-1)!.boards.size, 2, 'the last answer is kept (the board calls it stale)');
  fail = Object.assign(new Error('gone'), { status: 404 });
  feed.retry();
  await tick(); await tick();
  assert.equal(states.at(-1)!.status, 'unsupported');
  assert.equal(states.at(-1)!.boards.size, 0);
  feed.stop();
});

test('a first read that fails is an error with nothing kept, and Try again reads again', async () => {
  const states: RemoteState[] = [];
  let ok = false;
  const feed = startRemoteBoards({ load: async () => { if (!ok) throw new TypeError('Failed to fetch'); return answer([board('ws_b')]); }, scope: () => SCOPE, onChange: (s) => states.push(s), intervalMs: 60_000 });
  await tick(); await tick();
  assert.deepEqual(states.map((s) => s.status), ['loading', 'error']);
  ok = true;
  feed.retry();
  await tick(); await tick(); await tick();
  assert.deepEqual(states.map((s) => s.status), ['loading', 'error', 'loading', 'ready']);
  feed.stop();
});
