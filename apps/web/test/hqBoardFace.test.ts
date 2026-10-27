import assert from 'node:assert/strict';
import { mock, test } from 'node:test';
import { readFileSync } from 'node:fs';
import type { CampusBuilding, CampusTotals } from '../src/lib/campusModel.ts';
import { translate, type Locale } from '../src/lib/i18n.ts';
import { billboardModel, billboardTexts, BILLBOARD_FIELDS, type BillboardModel } from '../src/hq-billboards/billboardModel.ts';
import { boardFace, faceKey, faceTexts, type BoardFace } from '../src/hq-billboards/boardFace.ts';
import { BOARD_MIN_MS, BoardAtlas, startBoardFeed, wrapText, type CanvasMaker } from '../src/hq-billboards/billboardTexture.ts';
import { MAX_CHIPS, MAX_CHIPS_COMPACT, quietDensities } from '../src/hq-nav/navModel.ts';

// The 3D board of a building prints only what the billboard model already allows (docs/ANUM_MINE_HQ_BILLBOARDS.md, "The board"). Every name is plainly fake.
const NOW = Date.parse('2026-10-26T10:00:00Z');
const FRESH = '2026-10-26T09:59:30Z';
const OLD = '2026-10-26T09:50:00Z';
const LEAK = 'ZZLEAK';
const totals = (over: Partial<CampusTotals> = {}): CampusTotals => ({ agents: 0, working: 0, queued: 0, meeting: 0, waiting_approval: 0, blocked: 0, idle: 0, paused: 0, unknown: 0, ...over });
const building = (over: Record<string, unknown> = {}): CampusBuilding => ({
  workspace_id: 'ws~bld_hq', tenant_id: `${LEAK}TENANT`, name: 'OKNAME Tower', nameAr: 'برج OKNAME', kind: 'executive', kindKnown: true, icon: null, role: `${LEAK}ROLE`,
  rooms: 7, desks: 31, floors: 3, accessible: true, totals: totals({ agents: 9, working: 5, meeting: 2, idle: 1, paused: 1 }), presence_verified_at: FRESH,
  realWorkspaceId: 'ws', registryBuildingId: 'bld_hq', workspaceName: `${LEAK}WORKSPACE`, people: [`${LEAK}PEOPLE`], departments: [`${LEAK}ROLE`], archived: false, ...over,
} as unknown as CampusBuilding);
const model = (over: Record<string, unknown> = {}, locale: Locale = 'en', flow: unknown = null): BillboardModel =>
  billboardModel({ building: building(over), plan: null, flow: flow as never, currentWorkspaceId: 'ws', now: NOW, locale, tr: (k, v) => translate(locale, k, v) });
const face = (over: Record<string, unknown> = {}, opts: { live?: boolean; locale?: Locale; flow?: unknown } = {}): BoardFace => {
  const locale = opts.locale ?? 'en';
  return boardFace(model(over, locale, opts.flow ?? null), { live: opts.live ?? false, rtl: locale === 'ar', tr: (k, v) => translate(locale, k, v) });
};

test('a board says the live counts with the verified time, and the Live tick only while the stream is connected', () => {
  const f = face({}, { live: true });
  assert.equal(f.headline, '5 working, 2 in meetings');
  assert.equal(f.tone, 'ok');
  assert.match(f.foot, /^Verified /);
  assert.equal(f.live, true);
  assert.equal(f.states, '9 agents, Idle 1, Paused 1');
  assert.equal(f.purpose, 'An executive building: the leadership centre of the campus.');
  assert.equal(face({}, { live: false }).live, false, 'no Live tick without a connected stream');
});

test('old presence says Stale and never shows the Live tick; missing presence says Not connected, never a zero', () => {
  const stale = face({ presence_verified_at: OLD }, { live: true });
  assert.equal(stale.tone, 'stale');
  assert.match(stale.foot, /^Stale: last verified /);
  assert.equal(stale.live, false);
  const none = face({ totals: null }, { live: true });
  assert.equal(none.headline, 'Not connected');
  assert.equal(none.tone, 'off');
  assert.equal(none.foot, '');
  assert.equal(none.live, false);
  assert.ok(!/\b0\b/.test(none.headline));
  assert.equal(face({ accessible: false }).headline, 'Not connected');
  assert.equal(face({ totals: totals() }).headline, 'No agents');
  const noTime = face({ presence_verified_at: null });
  assert.equal(noTime.foot, 'Verification time not reported');
  assert.equal(noTime.tone, 'stale');
});

test('open work comes from the live flow only: Not connected without it, counts with it', () => {
  assert.equal(face().work, 'Open work: Not connected');
  assert.equal(face().workConnected, false);
  const flow = { status: 'ready', snapshot: { version: 1, workspace_id: 'ws', as_of: FRESH, sources: { tasks: 'connected' }, truncated: false, items: [
    { kind: 'task', stage: 'in_office', waiting: null, location: { building_id: 'bld_hq' }, title: `${LEAK}TITLE` },
    { kind: 'task', stage: 'blocked', waiting: null, location: { building_id: 'bld_hq' }, title: `${LEAK}TITLE2` },
    { kind: 'task', stage: 'in_office', waiting: null, location: { building_id: 'bld_other' } },
  ] } };
  const f = face({}, { flow });
  assert.equal(f.work, 'Open tasks 2, Blocked 1');
  assert.equal(f.workConnected, true);
  assert.ok(!faceTexts(f).join('|').includes(LEAK));
});

test('every text on a board is a fixed message or a model value, and nothing from a person, task, room id or workspace name', () => {
  const flow = { status: 'ready', snapshot: { version: 1, workspace_id: 'ws', as_of: FRESH, sources: { tasks: 'connected' }, truncated: false, items: [{ kind: 'task', stage: 'blocked', waiting: null, location: { building_id: 'bld_hq' }, title: `${LEAK}TITLE` }] } };
  for (const locale of ['en', 'ar'] as const) {
    const m = model({}, locale, flow);
    const f = boardFace(m, { live: true, rtl: locale === 'ar', tr: (k, v) => translate(locale, k, v) });
    const allowed = new Set(billboardTexts(m).map((x) => x.text));
    allowed.add(translate(locale, 'hq.live.live'));
    for (const text of faceTexts(f)) {
      assert.ok(!text.includes(LEAK), `leak in ${text}`);
      // A composed line is a list of model texts joined by a comma.
      const pieces = text.split(locale === 'ar' ? /، |, / : ', ');
      for (const piece of pieces) {
        const known = allowed.has(piece) || allowed.has(text) || [...allowed].some((a) => a.includes(piece)) || piece === translate(locale, 'hq.building.working', { count: 5 }) || piece === translate(locale, 'hq.building.inMeeting', { count: 2 });
        assert.ok(known, `unknown text on the board (${locale}): ${piece}`);
      }
    }
    assert.equal(f.rtl, locale === 'ar');
    assert.equal(f.name, locale === 'ar' ? 'برج OKNAME' : 'OKNAME Tower');
  }
  assert.ok(BILLBOARD_FIELDS.some((x) => x.id === 'streamLink'), 'the Live tick source is listed in the field table');
});

test('a board whose building type is not set says nothing about what it does', () => {
  assert.equal(face({ kindKnown: false }).purpose, null);
});

test('the face key moves exactly when something visible moves', () => {
  assert.equal(faceKey(face()), faceKey(face()));
  assert.notEqual(faceKey(face()), faceKey(face({ totals: totals({ agents: 9, working: 6, meeting: 2 }) })));
  assert.notEqual(faceKey(face({}, { live: true })), faceKey(face({}, { live: false })));
  const withFonts = boardFace(model(), { live: false, rtl: true, fontsReady: true, tr: (k, v) => translate('en', k, v) });
  assert.notEqual(faceKey(withFonts), faceKey(boardFace(model(), { live: false, rtl: true, fontsReady: false, tr: (k, v) => translate('en', k, v) })));
});

// ---- painter: only the boards that changed, at most once a second each

function stubCanvas() {
  const stats = { fills: 0, texts: [] as string[] };
  const ctx = {
    fillStyle: '', font: '', textAlign: 'left', textBaseline: 'alphabetic', direction: 'ltr', globalAlpha: 1,
    fillRect() { stats.fills += 1; }, fillText(text: string) { stats.texts.push(text); }, measureText(text: string) { return { width: text.length * 8 }; },
    beginPath() {}, arc() {}, fill() {}, createLinearGradient() { return { addColorStop() {} }; }, save() {}, restore() {}, clearRect() {},
  };
  const make: CanvasMaker = (width, height) => ({ canvas: { width, height } as unknown as HTMLCanvasElement, ctx: ctx as never });
  return { stats, make };
}
const faces = (n: number, working = 1): BoardFace[] => Array.from({ length: n }, (_, i) => face({ workspace_id: `ws~b${i}`, name: `Fake ${i}`, totals: totals({ agents: 3, working: i === 0 ? working : 1 }) }));

test('the atlas draws every board once, then only the boards that changed, and not more than once a second', () => {
  const { stats, make } = stubCanvas();
  const atlas = new BoardAtlas(13, make);
  assert.equal(atlas.layout.cellW, 512);
  let r = atlas.update(faces(13), 1000);
  assert.equal(r.drawn, 13);
  assert.equal(atlas.version, 1);
  const names = stats.texts.length;
  r = atlas.update(faces(13), 1200);
  assert.deepEqual(r, { drawn: 0, nextDueMs: null });
  assert.equal(atlas.version, 1, 'nothing changed: nothing to upload');
  assert.equal(stats.texts.length, names);
  // One board changes: only it is drawn (first change after the first draw is held until a second has passed).
  r = atlas.update(faces(13, 4), 1300);
  assert.equal(r.drawn, 0);
  assert.equal(r.nextDueMs, BOARD_MIN_MS - 300);
  r = atlas.update(faces(13, 4), 2000);
  assert.equal(r.drawn, 1);
  assert.equal(atlas.version, 2);
  assert.ok(stats.texts.includes('4 working'), 'the new count is on the board');
  // Another change right after is held for the rest of the second.
  r = atlas.update(faces(13, 5), 2400);
  assert.deepEqual(r, { drawn: 0, nextDueMs: 600 });
  assert.equal(atlas.update(faces(13, 5), 3000).drawn, 1);
  assert.equal(atlas.draws, 15);
});

test('the feed draws what is due on a timer, ages Stale without new data, and stops cleanly', () => {
  mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  try {
    const { make } = stubCanvas();
    let working = 1;
    const drawn: number[] = [];
    const feed = startBoardFeed({ count: 2, read: () => faces(2, working), onDraw: (a) => drawn.push(a.version), make, now: () => Date.now() });
    assert.deepEqual(drawn, [1]);
    working = 2;
    feed.touch();
    assert.deepEqual(drawn, [1], 'held back by the one second rule');
    mock.timers.tick(BOARD_MIN_MS + 10);
    assert.deepEqual(drawn, [1, 2], 'drawn by the timer when due');
    feed.stop();
    working = 3;
    mock.timers.tick(20_000);
    feed.touch();
    assert.deepEqual(drawn, [1, 2], 'stopped');
  } finally { mock.timers.reset(); }
});

test('no animation on the board texture: reduced motion changes nothing and the text still updates', () => {
  const src = readFileSync(new URL('../src/hq-billboards/billboardTexture.ts', import.meta.url), 'utf8');
  assert.ok(!/requestAnimationFrame|matchMedia|@keyframes/.test(src), 'the board is drawn only when its text changes');
});

test('wrapText fits words into the lines given and ends a too long text with an ellipsis', () => {
  const measure = (s: string) => s.length * 10;
  assert.deepEqual(wrapText(measure, 'one two three four', 100, 2), ['one two', 'three four']);
  assert.deepEqual(wrapText(measure, 'one two three four five six', 100, 2), ['one two', 'three fou…']);
  assert.deepEqual(wrapText(measure, 'short', 100, 2), ['short']);
  assert.deepEqual(wrapText(measure, '', 100, 2), []);
});

// ---- the quiet campus: chips

const b = (id: string, kind: CampusBuilding['kind'] = 'business'): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name: `Fake ${id}`, kind, icon: null, floors: 1, accessible: true, totals: null, presence_verified_at: null });

test('quiet campus: a few chips by priority and distance, the rest status dots, and every building keeps a button', () => {
  const list = [b('a'), b('b'), b('c', 'executive'), b('d'), b('e'), b('f'), b('g'), b('h'), b('i'), b('j'), b('k'), b('l'), b('m')];
  const at = new Map(list.map((x, i) => [x.workspace_id, { x: i * 10, z: 0 }]));
  const d = quietDensities(list, { selectedId: null, currentId: 'd', target: { x: 100, z: 0 }, at });
  assert.equal(d.size, list.length, 'no building is left out');
  const chips = [...d].filter(([, v]) => v === 'compact').map(([k]) => k);
  assert.equal(chips.length, MAX_CHIPS);
  // The open workspace's building, the executive, then the nearest to where the camera looks (index 10 is at x = 100).
  assert.deepEqual(chips.sort(), ['c', 'd', 'j', 'k'].sort());
  assert.ok([...d.values()].every((v) => v === 'compact' || v === 'dot'), 'never the old full card');
  // A selected building is a chip however far it is, and then there are as many chips as the budget plus the selection at most.
  const sel = quietDensities(list, { selectedId: 'a', currentId: 'd', target: { x: 100, z: 0 }, at });
  assert.equal(sel.get('a'), 'compact');
  assert.ok([...sel.values()].filter((v) => v === 'compact').length <= MAX_CHIPS);
  // A phone shows fewer.
  assert.equal([...quietDensities(list, { selectedId: null, currentId: 'd', target: { x: 0, z: 0 }, at, max: MAX_CHIPS_COMPACT }).values()].filter((v) => v === 'compact').length, MAX_CHIPS_COMPACT);
  // Same input, same output.
  assert.deepEqual([...quietDensities(list, { selectedId: null, currentId: 'd', target: { x: 100, z: 0 }, at })], [...d]);
});

test('the building chip carries no kind chips, avatar stack, counts or second line', () => {
  const src = readFileSync(new URL('../src/CampusView.tsx', import.meta.url), 'utf8');
  const card = src.slice(src.indexOf('function BuildingCard('), src.indexOf('function BuildingDetail('));
  assert.ok(card.includes('hqLabelText') && card.includes('hqStatusDot'));
  assert.ok(!/hqCardTags|hqAvatarStack/.test(card) && (card.match(/<small/g) ?? []).length === 2 && (card.match(/<small className="hqWorkLine"/g) ?? []).length === 2, 'a chip is the name and a status dot; the only second line is a record-backed work reason or feed note');
  assert.ok(/aria-label=\{`\$\{sceneName\(b\)\}\. \$\{summary\}\./.test(card), 'the accessible name keeps the name and the summary');
});
