import assert from 'node:assert/strict';
import { mock, test } from 'node:test';
import { readFileSync } from 'node:fs';
import type { CampusBuilding, CampusTotals } from '../src/lib/campusModel.ts';
import { translate, type Locale } from '../src/lib/i18n.ts';
import { billboardModel, billboardTexts, BILLBOARD_FIELDS, type BillboardModel } from '../src/hq-billboards/billboardModel.ts';
import { boardFace, faceKey, faceTexts, type BoardFace } from '../src/hq-billboards/boardFace.ts';
import { BOARD_FADE_MAX, BOARD_FADE_MS, BOARD_FADE_STEP_MS, BOARD_MIN_MS, BoardAtlas, startBoardFeed, wrapText, type CanvasMaker } from '../src/hq-billboards/billboardTexture.ts';
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

test('the board accent is the owner\'s brand colour only when it is a valid colour', () => {
  assert.equal(face({ sign: { buildingId: 'b', style: null, color: '#C0392B', logoSha256: null } }).accent, '#c0392b');
  assert.equal(face({ sign: { buildingId: 'b', style: null, color: 'red; x', logoSha256: null } }).accent, null);
  assert.equal(face().accent, null);
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

/** A stub canvas that can copy pixels (drawImage): it records each copy with the alpha it was drawn at, and every canvas it made. */
function fadeCanvas() {
  const copies: Array<{ alpha: number; args: number }> = [];
  let made = 0;
  const make: CanvasMaker = (width, height) => {
    made += 1;
    const ctx = {
      fillStyle: '', font: '', textAlign: 'left', textBaseline: 'alphabetic', direction: 'ltr', globalAlpha: 1,
      fillRect() {}, fillText() {}, measureText(text: string) { return { width: text.length * 8 }; },
      beginPath() {}, arc() {}, fill() {}, createLinearGradient() { return { addColorStop() {} }; }, save() {}, restore() {}, clearRect() {},
      drawImage(_img: unknown, ...rest: number[]) { copies.push({ alpha: this.globalAlpha, args: rest.length }); },
    };
    return { canvas: { width, height } as unknown as HTMLCanvasElement, ctx: ctx as never };
  };
  return { copies, make, made: () => made };
}

test('a changed board fades from its old face to the new one in BOARD_FADE_MS, with a reused snapshot, and a first draw does not fade', () => {
  const { copies, make, made } = fadeCanvas();
  const atlas = new BoardAtlas(3, make);
  atlas.fadeMs = BOARD_FADE_MS;
  atlas.update(faces(3), 1000);
  assert.equal(atlas.fading, false, 'the first draw of a board is at once');
  assert.equal(copies.length, 0);
  atlas.update(faces(3, 4), 2100);
  assert.equal(atlas.fading, true, 'a changed board fades');
  assert.equal(made(), 2, 'one cell-sized snapshot for the fading board');
  assert.equal(copies.at(-1)?.args, 8, 'the old cell is copied out of the atlas');
  const alphas: number[] = [];
  for (let at = 2100; atlas.fading; at += BOARD_FADE_STEP_MS) {
    const before = copies.length;
    assert.equal(atlas.fadeStep(at), true);
    if (copies.length > before) alphas.push(copies.at(-1)!.alpha);
  }
  assert.ok(alphas.length >= BOARD_FADE_MS / BOARD_FADE_STEP_MS - 1, `fades in steps: ${alphas.join(', ')}`);
  for (let i = 1; i < alphas.length; i++) assert.ok(alphas[i] < alphas[i - 1], 'the old face fades out step by step');
  assert.equal(atlas.fadeStep(9999), false, 'nothing left to draw');
  // The next change reuses the snapshot: no new canvas.
  atlas.update(faces(3, 5), 4000);
  assert.equal(atlas.fading, true);
  assert.equal(made(), 2);
});

test('without a fade time (reduced motion) a changed board changes at once, and at most BOARD_FADE_MAX boards fade together', () => {
  const { make, made } = fadeCanvas();
  const atlas = new BoardAtlas(BOARD_FADE_MAX + 3, make);
  atlas.update(faces(BOARD_FADE_MAX + 3), 1000);
  atlas.update(faces(BOARD_FADE_MAX + 3, 4), 2100);
  assert.equal(atlas.fading, false, 'no fade at fadeMs 0');
  const all = (w: number) => Array.from({ length: BOARD_FADE_MAX + 3 }, (_, i) => face({ workspace_id: `ws~b${i}`, name: `Fake ${i}`, totals: totals({ agents: 9, working: w }) }));
  atlas.fadeMs = BOARD_FADE_MS;
  const r = atlas.update(all(6), 3200);
  assert.equal(r.drawn, BOARD_FADE_MAX + 3, 'every changed board is drawn');
  assert.equal(made() - 1, BOARD_FADE_MAX, 'only BOARD_FADE_MAX snapshots');
});

test('the feed steps a fade on a timer until it is done, and reads the fade time at every change', () => {
  mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  try {
    const { make } = fadeCanvas();
    let working = 1;
    let fade = BOARD_FADE_MS;
    const drawn: number[] = [];
    const feed = startBoardFeed({ count: 2, read: () => faces(2, working), onDraw: (a) => drawn.push(a.version), make, now: () => Date.now(), fadeMs: () => fade });
    mock.timers.tick(BOARD_MIN_MS + 10);
    working = 2;
    feed.touch();
    assert.equal(feed.atlas.fading, true);
    for (let i = 0; i < BOARD_FADE_MS / BOARD_FADE_STEP_MS + 2; i++) mock.timers.tick(BOARD_FADE_STEP_MS);
    assert.equal(feed.atlas.fading, false, 'the fade ended');
    assert.ok(drawn.length >= BOARD_FADE_MS / BOARD_FADE_STEP_MS, `a draw per step: ${drawn.length}`);
    const count = drawn.length;
    mock.timers.tick(5000);
    assert.ok(drawn.length <= count + 1, 'no steps once the fade is over');
    fade = 0;
    mock.timers.tick(BOARD_MIN_MS);
    working = 3;
    feed.touch();
    assert.equal(feed.atlas.fading, false, 'reduced motion: at once');
    feed.stop();
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
  assert.ok(!/hqCardTags|hqAvatarStack/.test(card) && (card.match(/<small/g) ?? []).length === 4 && (card.match(/<small className="hq(Work|Walk)Line"/g) ?? []).length === 4, 'a chip is the name and a status dot; the only second line is a record-backed work or walker reason, or a feed note (two work lines and two walker lines)');
  assert.ok(/aria-label=\{`\$\{sceneName\(b\)\}\. \$\{summary\}\./.test(card), 'the accessible name keeps the name and the summary');
});

// ---- brand header, logo, icons, near and far (docs/ANUM_MINE_HQ_BILLBOARDS.md, "The board")

import { brandHeader, HEADER_DARK, HEADER_LIGHT, HEADER_MIN_CONTRAST } from '../src/hq-billboards/boardFace.ts';
import { closeBoards, boardAtlasLayout } from '../src/hq-billboards/billboardSites.ts';
import { drawBoardCell } from '../src/hq-billboards/billboardTexture.ts';
import { contrast } from '../src/hq-logo-signs/signModel.ts';

const SHA = 'a'.repeat(64);
const signed = (color: string | null, sha: string | null = SHA) => ({ sign: { buildingId: 'bld_hq', style: `${LEAK}STYLE`, color, logoSha256: sha } });

test('the header band takes the owner\'s brand colour only when it is valid and readable, with the more readable lettering', () => {
  const dark = brandHeader('#1b3a6b');
  assert.deepEqual([dark?.fill, dark?.text], ['#1b3a6b', HEADER_LIGHT]);
  const light = brandHeader('#f5c518');
  assert.deepEqual([light?.fill, light?.text], ['#f5c518', HEADER_DARK]);
  for (const h of [dark!, light!]) assert.ok(contrast(h.fill, h.text) >= HEADER_MIN_CONTRAST);
  // A mid tone that reaches 4.5 to 1 with neither lettering colour keeps the standard header (the colour stays on the accent rule).
  const mid = ['#777777', '#d24c4c', '#2f8f5b', '#3a7bd5'].filter((c) => brandHeader(c) === null);
  assert.ok(mid.length > 0, 'some mid tones fail the check');
  for (const c of mid) assert.ok(Math.max(contrast(c, HEADER_LIGHT), contrast(c, HEADER_DARK)) < HEADER_MIN_CONTRAST);
  assert.equal(brandHeader(null), null);
  assert.equal(brandHeader('red'), null);
  assert.equal(face(signed('#1B3A6B')).header?.fill, '#1b3a6b');
  assert.equal(face(signed('#777777')).header, null);
  assert.equal(face(signed('#777777')).accent, '#777777', 'the colour still draws the accent rule');
  assert.equal(face().header, null);
});

test('the logo is drawn only once the sign loader has decoded it; the sign style and anything else of the sign never reach the face', () => {
  const m = model(signed('#1b3a6b'));
  assert.deepEqual(m.logo, { buildingId: 'bld_hq', sha: SHA });
  const tr = (k: Parameters<typeof translate>[1], v?: Parameters<typeof translate>[2]) => translate('en', k, v);
  assert.equal(boardFace(m, { live: false, rtl: false, tr }).logo, null, 'not decoded: the plain header');
  assert.equal(boardFace(m, { live: false, rtl: false, tr, logoReady: () => false }).logo, null);
  assert.equal(boardFace(m, { live: false, rtl: false, tr, logoReady: (id, sha) => id === 'bld_hq' && sha === SHA }).logo, `bld_hq:${SHA}`);
  assert.equal(model(signed('#1b3a6b', 'not-a-hash')).logo, null, 'a malformed hash is no logo');
  assert.equal(model().logo, null);
  assert.ok(!faceKey(boardFace(m, { live: false, rtl: false, tr, detail: true, logoReady: () => true })).includes(LEAK));
});

test('far away a board prints the name and the key line only; the close view adds the detail lines', () => {
  const tr = (k: Parameters<typeof translate>[1], v?: Parameters<typeof translate>[2]) => translate('en', k, v);
  const far = boardFace(model(), { live: true, rtl: false, tr });
  assert.deepEqual(faceTexts(far), ['OKNAME Tower', '5 working, 2 in meetings', 'Live']);
  const near = boardFace(model(), { live: true, rtl: false, tr, detail: true });
  assert.ok(faceTexts(near).includes('9 agents, Idle 1, Paused 1'));
  assert.ok(faceTexts(near).some((x) => /^Verified /.test(x)));
  assert.ok(faceTexts(near).includes('Open work: Not connected'));
  assert.deepEqual(faceTexts(boardFace(model({ totals: null }), { live: true, rtl: false, tr })), ['OKNAME Tower', 'Not connected']);
  // A detail line that is not printed far away does not make a far board draw again.
  assert.equal(faceKey(boardFace(model({ presence_verified_at: '2026-10-26T09:59:40Z' }), { live: false, rtl: false, tr })), faceKey(boardFace(model(), { live: false, rtl: false, tr })));
  assert.notEqual(faceKey(boardFace(model({ presence_verified_at: '2026-10-26T09:59:40Z' }), { live: false, rtl: false, tr, detail: true })), faceKey(boardFace(model(), { live: false, rtl: false, tr, detail: true })));
});

test('the boards near the camera eye show the close view', () => {
  const site = (id: string, x: number) => ({ workspaceId: id, anchor: { x, y: 6, z: 0 } }) as never;
  const layer = { sites: [site('b', 30), site('a', 10), site('c', 200)] };
  assert.deepEqual(closeBoards(layer, { x: 0, y: 6, z: 0 }), ['a', 'b']);
  assert.deepEqual(closeBoards(layer, { x: 0, y: 300, z: 0 }), []);
});

test('icons: the departments of the building\'s registry rooms, then the kinds of rooms without a known one', () => {
  const plan = { floors: [{ rooms: [
    { label: 'A', labelAr: null, kind: 'office', teamDepartment: 'sales' },
    { label: 'B', labelAr: null, kind: 'meeting', teamDepartment: null },
    { label: 'C', labelAr: null, kind: 'office', teamDepartment: `${LEAK}DEPT` },
    { label: 'D', labelAr: null, kind: 'office', teamDepartment: 'finance' },
    { label: 'E', labelAr: null, kind: 'office', teamDepartment: 'sales' },
  ] }] } as never;
  const m = billboardModel({ building: building(), plan, flow: null, currentWorkspaceId: 'ws', now: NOW, locale: 'en', tr: (k, v) => translate('en', k, v) });
  assert.deepEqual(m.icons, ['dept:sales', 'dept:finance', 'kind:meeting', 'kind:office']);
  assert.ok(!JSON.stringify(m.icons).includes(LEAK));
  assert.deepEqual(model().icons, [], 'no registry plan: no icons');
});

/** A canvas stub that records what was drawn. */
function recordingCanvas() {
  const log: string[] = [];
  const ctx = {
    fillStyle: '' as unknown, strokeStyle: '' as unknown, font: '', textAlign: 'left', textBaseline: 'alphabetic', direction: 'ltr', globalAlpha: 1, lineWidth: 1, lineCap: 'butt', lineJoin: 'miter',
    fillRect(x: number, y: number, w: number, h: number) { log.push(`rect ${String(this.fillStyle)} ${y} ${h}`); void x; void w; },
    fillText(text: string) { log.push(`text ${text} ${String(this.fillStyle)}`); }, measureText(text: string) { return { width: text.length * 8 }; },
    beginPath() {}, arc() {}, fill() {}, createLinearGradient() { return { addColorStop() {} }; }, save() {}, restore() {}, clearRect() {},
    roundRect() { log.push('plate'); }, rect() { log.push('plate'); }, translate() {}, scale() {}, stroke() { log.push('icon'); },
    drawImage() { log.push('logo'); },
  };
  return { ctx, log };
}

test('the painter draws the brand header with readable lettering, the logo on a plate, and falls back to the plain header', () => {
  const tr = (k: Parameters<typeof translate>[1], v?: Parameters<typeof translate>[2]) => translate('en', k, v);
  const branded = boardFace(model(signed('#f5c518')), { live: false, rtl: false, tr, logoReady: () => true });
  const { ctx, log } = recordingCanvas();
  drawBoardCell(ctx as never, 0, 0, 1, branded, () => ({ image: {} as CanvasImageSource, width: 200, height: 100 }));
  assert.ok(log.includes('rect #f5c518 0 104'), 'the header band is the brand colour');
  assert.ok(log.includes(`text OKNAME Tower ${HEADER_DARK}`), 'dark lettering on a light brand colour');
  assert.ok(log.includes('plate') && log.includes('logo'));
  // The decoded logo is gone (closed, or not loaded): the plain header, nothing else changes.
  const plain = recordingCanvas();
  drawBoardCell(plain.ctx as never, 0, 0, 1, branded, () => null);
  assert.ok(!plain.log.includes('logo') && !plain.log.includes('plate'));
  assert.ok(plain.log.includes(`text OKNAME Tower ${HEADER_DARK}`));
  // A logo that fails to draw leaves the board drawn.
  const broken = recordingCanvas();
  broken.ctx.drawImage = () => { throw new Error('closed bitmap'); };
  assert.doesNotThrow(() => drawBoardCell(broken.ctx as never, 0, 0, 1, branded, () => ({ image: {} as CanvasImageSource, width: 2, height: 1 })));
  assert.ok(broken.log.some((l) => l.startsWith('text 5 working')));
});

test('the far board draws the key line large and the room icons; the lowest tier has smaller cells', () => {
  const tr = (k: Parameters<typeof translate>[1], v?: Parameters<typeof translate>[2]) => translate('en', k, v);
  const f = { ...boardFace(model(), { live: false, rtl: false, tr }), icons: ['dept:sales', 'kind:meeting'] as const };
  const { ctx, log } = recordingCanvas();
  const fonts: string[] = [];
  const orig = ctx.fillText.bind(ctx);
  ctx.fillText = (text: string) => { fonts.push(`${text}@${ctx.font}`); orig(text); };
  const hasPath = typeof (globalThis as { Path2D?: unknown }).Path2D !== 'undefined';
  drawBoardCell(ctx as never, 0, 0, 1, f as never);
  const head = fonts.find((x) => x.startsWith('5 working'))!;
  assert.ok(Number(/ (\d+)px/.exec(head)![1]) >= 42, `the key line is large: ${head}`);
  const name = fonts.find((x) => x.startsWith('OKNAME'))!;
  assert.ok(Number(/ (\d+)px/.exec(name)![1]) >= 42, `the name is large: ${name}`);
  if (hasPath) assert.equal(log.filter((l) => l === 'icon').length, 2);
  assert.equal(boardAtlasLayout(14, 256).cellW, 256);
  assert.equal(boardAtlasLayout(14).cellW, 512);
  assert.equal(new BoardAtlas(14, stubCanvas().make, { cell: 256 }).layout.cellW, 256);
});
