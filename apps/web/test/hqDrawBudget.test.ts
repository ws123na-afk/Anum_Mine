// Draw call, pixel and upload budgets of the headquarters scene (docs/ANUM_MINE_HQ_GPU_CHECK.md, "Draw calls", "Render scale",
// "Texture uploads"). The owner's second report (2 Nov, Intel UHD 620) measured 160 draw calls on the campus where the first had 74: every
// building was drawn as one mesh per surface class (7 to 12 each, plus its roof detail). These tests fail if the meshes stop being merged.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { batchClasses, batchItems, plannedDraws, pickNumbers } from '../src/lib/hqBatches.ts';
import { buildCampusScene } from '../src/lib/campusScene.ts';
import { layoutCampus, type CampusBuilding } from '../src/lib/campusModel.ts';
import { boardMesh, boardSites } from '../src/hq-billboards/billboardSites.ts';
import { buildBuildingScene } from '../src/lib/buildingScene.ts';
import { projectHeadquarters, type Registry } from '../src/lib/headquartersProjection.ts';
import { UploadGate } from '../src/lib/hqUploadGate.ts';
import { HQ_TIERS, budgetedDpr, pixelBudget, tierById } from '../src/lib/hqQualityTiers.ts';
import { BoardAtlas } from '../src/hq-billboards/billboardTexture.ts';
import type { RenderItem } from '../src/lib/hqRenderTypes.ts';

const bld = (id: string, kind: CampusBuilding['kind'] = 'business', floors = 3): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name: id, kind, icon: null, floors, accessible: true, totals: null, presence_verified_at: null });
const KINDS: CampusBuilding['kind'][] = ['executive', 'business', 'personal', 'business', 'business', 'business'];

/** The campus items exactly as CampusView hands them to the renderer: the scenery (never culled), one item per building, the billboards. */
function campusItems(count: number): RenderItem[] {
  const list = Array.from({ length: count }, (_, i) => bld(`w${i}`, KINDS[i % KINDS.length], 2 + (i % 4)));
  const placements = layoutCampus(list);
  const scene = buildCampusScene(placements);
  const sites = boardSites({ placements, buildings: scene.buildings.map((b) => ({ workspaceId: b.workspaceId, box: b.box, door: b.door })), monument: scene.monument, extentRadius: scene.extent.radius, canalRadius: scene.canalRadius, plaza: scene.plaza });
  const boards = boardMesh(sites);
  return [
    { id: 'scenery', vertices: scene.scenery.vertices, bounds: scene.scenery.bounds, cull: false },
    ...scene.buildings.map((b) => ({ id: b.workspaceId, vertices: b.mesh.vertices, bounds: b.mesh.bounds })),
    { id: 'billboards', vertices: boards.vertices, bounds: boards.bounds, board: true },
  ];
}

/** What the renderer drew before this change: one mesh per surface class of every item. */
const unmerged = (items: readonly RenderItem[]) => items.reduce((n, item) => n + batchClasses([item]).length, 0);

/** The campus scene's own meshes (the ones `setScene` makes) may cost at most this many draw calls, however many buildings stand. */
const CAMPUS_DRAW_BUDGET = 32;
/** Each building after the first may add at most this many on average (a class no other building used yet). */
const PER_BUILDING_BUDGET = 0.5;

test('the campus is drawn with one mesh per surface class, not one per class per building (the owner report counted 160)', () => {
  for (const count of [8, 16, 24]) {
    const items = campusItems(count);
    const merged = plannedDraws(items);
    const before = unmerged(items);
    if (process.env.HQ_DRAW_LOG) console.log(`HQ_DRAWS buildings=${count} merged=${merged} unmerged=${before}`);
    assert.ok(merged <= CAMPUS_DRAW_BUDGET, `${count} buildings: ${merged} draw calls (budget ${CAMPUS_DRAW_BUDGET}, unmerged ${before})`);
    assert.ok(before > merged * 2, `${count} buildings: merging saves most of the calls (${before} to ${merged})`);
  }
  const few = plannedDraws(campusItems(8)), many = plannedDraws(campusItems(24));
  assert.ok((many - few) / 16 <= PER_BUILDING_BUDGET, `16 more buildings add ${many - few} draw calls`);
});

test('buildings share one batch; scenery, billboards, lifts, model stand-ins and glass stay apart', () => {
  const items = campusItems(10);
  const batches = batchItems(items);
  assert.equal(batches.length, 3, 'scenery (never culled), the buildings, the billboards');
  assert.equal(batches[1].length, 10, 'every building in the one batch');
  const v = items[1].vertices;
  const lifted: RenderItem[] = [
    { id: 'a', vertices: v, bounds: items[1].bounds, cull: false, lift: { group: 'cut:north', y: 0 } },
    { id: 'b', vertices: v, bounds: items[1].bounds, cull: false, lift: { group: 'cut:north', y: 3.6 } },
    { id: 'c', vertices: v, bounds: items[1].bounds, cull: false, lift: { group: 'cut:north', y: 3.6 } },
    { id: 'd', vertices: v, bounds: items[1].bounds, cull: false, fallbackOf: 'sofa' },
    { id: 'e', vertices: v, bounds: items[1].bounds, cull: false },
  ];
  assert.deepEqual(batchItems(lifted).map((b) => b.map((i) => i.id)), [['a'], ['b', 'c'], ['d'], ['e']]);
  assert.deepEqual(batchItems(lifted, true).map((b) => b.length), [1, 1, 1, 1, 1], 'transparent glass is sorted item by item');
});

test('the renderer builds its meshes through the batches and lights a building by its pick number', () => {
  const src = readFileSync(new URL('../src/lib/hqThreeRenderer.ts', import.meta.url), 'utf8');
  assert.match(src, /for \(const batch of batchItems\(\[\.\.\.scene\.opaque, \.\.\.this\.addCampusDetail\(scene\)\]\)\) this\.addBatch\(batch, false, scene\);/);
  assert.match(src, /setAttribute\('aPick'/);
  assert.doesNotMatch(src, /onBeforeRender/, 'no per-mesh uniform writes: the glow reads the vertex pick number');
  const picks = pickNumbers(['scenery', 'w0', 'w1', 'w0']);
  assert.deepEqual([...picks], [['scenery', 1], ['w0', 2], ['w1', 3]]);
  assert.ok(![...picks.values()].includes(0), '0 means no item (a mesh without the attribute never glows)');
});

test('a grounded building (podium, columns, beams, core, walls) stays one mesh per surface class', () => {
  const starter = JSON.parse(readFileSync(new URL('./fixtures/starterCampus.json', import.meta.url), 'utf8'));
  const rev = { revision: 1 };
  const campus = projectHeadquarters({ tenantId: 't', workspaceId: 'w' }, {
    buildings: starter.buildings.map((b: object) => ({ ...b, ...rev, timezone: 'Asia/Dubai' })), floors: starter.floors.map((f: object) => ({ ...f, ...rev })),
    rooms: starter.rooms.map((r: object) => ({ ...r, ...rev })), desks: starter.desks.map((d: object) => ({ ...d, ...rev })),
  } as unknown as Registry, []).buildings;
  for (const b of campus) {
    for (const cutaway of [true, false]) {
      const s = buildBuildingScene(b.floors, { activeIndex: 0, cutaway });
      const item: RenderItem = { id: 'building', vertices: s.opaque.vertices, bounds: s.opaque.bounds, cull: false };
      const walls: RenderItem[] = s.walls.map((w) => ({ id: `cut-o:${w.side}:${w.floorIndex}`, vertices: w.opaque.vertices, bounds: w.opaque.bounds, cull: false, lift: { group: `cut:${w.side}`, y: w.y } }));
      const draws = plannedDraws([item, ...walls]);
      // The structure adds no draw call of its own: it shares the building's classes. 14 classes at most for the building, one per wall piece.
      assert.ok(plannedDraws([item]) <= 14, `${b.name}: ${plannedDraws([item])} classes`);
      assert.ok(draws <= 14 + walls.length * 2, `${b.name} ${cutaway ? 'cutaway' : 'closed'}: ${draws} draw calls`);
    }
  }
});

test('a live atlas is uploaded again only when its drawing changed', () => {
  const gate = new UploadGate<object>();
  const canvas = {};
  assert.equal(gate.step(canvas, 1), 'new');
  assert.equal(gate.step(canvas, 1), 'none', 'the same version is not uploaded again');
  assert.equal(gate.step(canvas, 1), 'none');
  assert.equal(gate.uploads, 0);
  assert.equal(gate.step(canvas, 2), 'upload');
  assert.equal(gate.uploads, 1);
  assert.equal(gate.step({}, 2), 'new', 'a new canvas makes a new texture');
  assert.equal(gate.step(null), 'new');
  assert.equal(gate.step(null), 'none');
  // The board atlas moves its version only when it drew: the same faces twice draw nothing.
  const make = (w: number, h: number) => ({ canvas: { width: w, height: h } as unknown as HTMLCanvasElement, ctx: new Proxy({}, { get: (_, k) => (k === 'measureText' ? () => ({ width: 10 }) : k === 'createLinearGradient' ? () => ({ addColorStop() {} }) : () => undefined), set: () => true }) as never });
  const atlas = new BoardAtlas(2, make);
  const face = { id: 'a', name: 'A', headline: 'x', tone: 'ok', status: 'working', icons: [], detail: false, rtl: false, live: false, liveLabel: '' } as never;
  atlas.update([face, face], 0);
  const v = atlas.version;
  atlas.update([face, face], 5000);
  assert.equal(atlas.version, v, 'unchanged faces leave the version (and the texture) alone');
});

test('the lowest tiers change a board at once (one upload) instead of fading through four', () => {
  const make = (w: number, h: number) => ({ canvas: { width: w, height: h } as unknown as HTMLCanvasElement, ctx: new Proxy({}, { get: (_, k) => (k === 'measureText' ? () => ({ width: 10 }) : k === 'createLinearGradient' ? () => ({ addColorStop() {} }) : () => undefined), set: () => true }) as never });
  const small = new BoardAtlas(2, make, { cell: 256 });
  small.fadeMs = 400;
  const a = { id: 'a', name: 'A', headline: 'x', tone: 'ok', status: 'working', icons: [], detail: false, rtl: false, live: false, liveLabel: '' } as never;
  const b = { ...(a as object), headline: 'y' } as never;
  small.update([a, a], 0);
  small.update([b, a], 5000);
  assert.equal(small.fading, false);
});

test('render scale: integrated GPUs draw Low and Medium (and High) within a pixel budget; discrete GPUs keep High and Ultra sharp', () => {
  // The owner's laptop: 1707 x 960 CSS pixels at a device ratio of 1.13 is 2.1 million device pixels.
  const css = 1707 * 960;
  for (const tier of HQ_TIERS) {
    const budget = pixelBudget(tier, 'integrated');
    const dpr = budgetedDpr(1.13, css, budget);
    const pixels = css * dpr * dpr;
    if (budget !== null) assert.ok(pixels <= budget + 1, `${tier.key}: ${Math.round(pixels)} pixels within ${budget}`);
    else assert.equal(dpr, 1.13);
  }
  assert.ok(css * budgetedDpr(1.13, css, pixelBudget(tierById(0), 'integrated')) ** 2 <= 0.5 * css * 1.13 * 1.13, 'Low draws at most half the pixels it drew in the report');
  assert.equal(pixelBudget(tierById(2), 'discrete'), null);
  assert.equal(pixelBudget(tierById(3), 'apple'), null);
  assert.equal(pixelBudget(tierById(0), 'discrete'), tierById(0).maxPixels, 'Low is a budget everywhere');
  assert.equal(budgetedDpr(1, 1280 * 720, 1_400_000), 1, 'a canvas under the budget keeps its ratio');
  assert.equal(budgetedDpr(2, 4000 * 3000, 1_000_000), 0.6, 'never under the floor');
});

test('the second owner report is kept as evidence: anonymous, and over the draw call budget the fix restores', async () => {
  const { reportLooksAnonymous } = await import('../src/lib/hqGpuReport.ts');
  const text = readFileSync(new URL('./fixtures/gpu-report-2026-11-02-uhd620-after.json', import.meta.url), 'utf8');
  assert.equal(reportLooksAnonymous(text), true);
  const report = JSON.parse(text) as { gpu: { viewport: { width: number; height: number }; dpr: number }; tiers: Array<{ drawCalls: number; gpu: { medianMs: number }; hitches: { compile: number; buffers: number } }> };
  for (const t of report.tiers) {
    assert.ok(t.drawCalls > CAMPUS_DRAW_BUDGET * 4, 'the regression: one mesh per class per building');
    assert.ok(t.gpu.medianMs > 20 && t.hitches.compile === 0 && t.hitches.buffers === 0, 'GPU bound, nothing compiled or allocated');
  }
  const { width, height } = report.gpu.viewport;
  assert.ok(width * height * report.gpu.dpr ** 2 > 2_000_000, 'about 2.1 million device pixels');
});
