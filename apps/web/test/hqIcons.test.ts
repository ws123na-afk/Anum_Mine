import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { DEPARTMENT_ICONS, KIND_ICONS, iconPath, roomIconKey } from '../src/lib/hqIcons.ts';
import { CATALOGS } from '../src/lib/i18n.ts';
import { boardSpec, HQ_TIERS } from '../src/lib/hqQualityTiers.ts';
import { DOT_GAP, layoutLabels, type LabelInput } from '../src/lib/hqLabels.ts';

// One HQ icon set (docs/ANUM_MINE_HQ_LABELS.md, "Icons"): the room kind masks of hqLabels.css and the department icons are the same set.

const STYLE = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'><path d='";

test('the kind and department icons are exactly the masks the label style sheet draws, in one stroke style', () => {
  const css = readFileSync(new URL('../src/hqLabels.css', import.meta.url), 'utf8');
  const kinds = new Map<string, string>(), depts = new Map<string, string>();
  for (const m of css.matchAll(/\.hqLabelIcon(?:\[data-(kind|dept)="(\w+)"\])? \{ --hq-kind-icon: url\("data:image\/svg\+xml,([^"]+)"\)/g)) {
    const svg = decodeURIComponent(m[3]);
    assert.ok(svg.startsWith(STYLE), `same stroke style: ${m[2] ?? 'default'}`);
    (m[1] === 'dept' ? depts : kinds).set(m[2] ?? 'default', /d='([^']+)'/.exec(svg)![1]);
  }
  assert.deepEqual(Object.fromEntries(kinds), KIND_ICONS);
  assert.deepEqual(Object.fromEntries(depts), DEPARTMENT_ICONS);
  // The department masks come after the kind masks, so a room with a department shows its department icon.
  assert.ok(css.indexOf('[data-dept="sales"]') > css.indexOf('[data-kind="command"] {'));
});

test('every department of the catalogue has an icon, drawn with path commands only, in the same stroke style', () => {
  const depts = Object.keys(CATALOGS.en).filter((k) => k.startsWith('team.dept.')).map((k) => k.slice('team.dept.'.length));
  assert.deepEqual(Object.keys(DEPARTMENT_ICONS).sort(), depts.sort());
  for (const path of [...Object.values(DEPARTMENT_ICONS), ...Object.values(KIND_ICONS)]) assert.match(path, /^M[0-9MLHVCSQTAZmlhvcsqtaz .,-]+$/);
});

test('a room takes its stored department icon, else its kind, else the default building', () => {
  assert.equal(roomIconKey({ kind: 'office', teamDepartment: 'finance' }), 'dept:finance');
  assert.equal(roomIconKey({ kind: 'meeting', teamDepartment: 'not_a_department' }), 'kind:meeting');
  assert.equal(roomIconKey({ kind: 'meeting', teamDepartment: null }), 'kind:meeting');
  assert.equal(roomIconKey({ kind: 'spaceship' }), 'kind:default');
  assert.equal(roomIconKey({ kind: 'office', teamDepartment: 'toString' }), 'kind:office', 'no prototype names');
  assert.equal(iconPath('dept:nope'), KIND_ICONS.default);
  assert.equal(iconPath('kind:lounge'), KIND_ICONS.lounge);
});

test('quality tiers: the lowest tier and the simple materials draw a cheap unlit board with smaller cells', () => {
  assert.deepEqual(HQ_TIERS.map((t) => [t.key, t.boardCell, t.boardUnlit]), [['low', 256, true], ['medium', 512, false], ['high', 512, false], ['ultra', 512, false]]);
  const settings = { dprScale: 1, shadowSize: 0, bloom: false, lean: false };
  assert.deepEqual(boardSpec(null), { cell: 512, unlit: false });
  assert.deepEqual(boardSpec({ tier: 0, settings }), { cell: 256, unlit: true });
  assert.deepEqual(boardSpec({ tier: 3, settings }), { cell: 512, unlit: false });
  assert.deepEqual(boardSpec({ tier: 3, settings: { ...settings, lean: true } }), { cell: 256, unlit: true });
  assert.deepEqual(boardSpec({ tier: 4, settings }), { cell: 512, unlit: false }, 'the photographic pin is the top tier');
});

test('a room card shows its stored department icon only for a department of the catalogue, else its kind', () => {
  const src = readFileSync(new URL('../src/HqLabelCards.tsx', import.meta.url), 'utf8');
  assert.match(src, /departmentText\(props\.department\) !== null \? props\.department : undefined/);
  assert.match(src, /data-kind=\{props\.kind\} data-dept=\{dept\}/);
  assert.ok(!src.includes("from './lib/hqIcons"), 'the entry chunk does not import the icon paths');
});

test('the renderer routes the board frame to the flat material on the cheap tiers', () => {
  const src = readFileSync(new URL('../src/lib/hqThreeRenderer.ts', import.meta.url), 'utf8');
  assert.match(src, /item\.board \? \(cls === 'sign' \? 'board' : this\.boardUnlit \? 'boardFlat' : cls\) : cls/);
  assert.match(src, /'boardFlat', new MeshBasicMaterial\(\{ vertexColors: true/);
});

test('on a phone-sized scene two status dots never touch: a dot with no free spot is crowded, not drawn on another', () => {
  const vp = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  const dot = { w: 26, h: 26 };
  // Fourteen buildings whose anchors are almost on top of each other in a 360 x 300 scene.
  const labels: LabelInput[] = Array.from({ length: 14 }, (_, i) => ({ id: `b${i}`, anchor: { x: (i % 5) * 0.03 - 0.06, y: Math.floor(i / 5) * 0.03, z: 0.5 }, priority: 1, tiers: i < 2 ? ['compact', 'dot'] : ['dot'], keep: true }));
  const sizeOf = (_: string, d: string) => (d === 'compact' ? { w: 130, h: 36 } : dot);
  const out = layoutLabels(labels, vp, 360, 300, sizeOf, 0, { squeeze: true });
  const shown = out.filter((p) => p.shown);
  assert.ok(shown.length >= 8, `most dots find a spot (${shown.length})`);
  const rect = (p: (typeof out)[number]) => { const s = sizeOf(p.id, p.density); return { x0: p.x - s.w / 2, x1: p.x + s.w / 2, y0: p.y - s.h, y1: p.y }; };
  for (let i = 0; i < shown.length; i++) {
    for (let j = i + 1; j < shown.length; j++) {
      const a = rect(shown[i]), b = rect(shown[j]);
      if (shown[i].density !== 'dot' && shown[j].density !== 'dot') continue;
      const gap = Math.max(a.x0 - b.x1, b.x0 - a.x1, a.y0 - b.y1, b.y0 - a.y1);
      assert.ok(gap >= DOT_GAP - 0.01, `${shown[i].id} and ${shown[j].id} touch (gap ${gap.toFixed(1)})`);
    }
  }
  // Nothing is lost: a dot with no spot stays in the page for the keyboard (crowded).
  for (const p of out) assert.ok(p.shown || p.crowded === true, `${p.id} is kept`);
  // A wide scene keeps the old rule: building cards are shown.
  assert.ok(layoutLabels(labels, vp, 1400, 900, sizeOf, 0).every((p) => p.shown));
});
