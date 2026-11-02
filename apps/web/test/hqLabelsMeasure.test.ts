import assert from 'node:assert/strict';
import { test } from 'node:test';
import { placeLabels, type LabelInput } from '../src/lib/hqLabels.ts';

// A stand-in for a label wrapper: its size depends on the density it is drawn at, as in the page.
function fakeNode(): HTMLElement {
  const node = { style: { display: '', transform: '', zIndex: '' }, dataset: {} as Record<string, string> };
  Object.defineProperty(node, 'offsetWidth', { get: () => (node.dataset.density === 'compact' ? 130 : 26) });
  Object.defineProperty(node, 'offsetHeight', { get: () => (node.dataset.density === 'compact' ? 36 : 26) });
  return node as unknown as HTMLElement;
}

test('measuring a card never leaves it parked off the stage: a crowded card keeps its anchor transform', () => {
  const vp = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  // One building on the stage and one whose anchor is off the stage: the second is crowded (kept for the keyboard at the stage edge). Its
  // layout measured only its dot, so placing it measures the full card for the first time, after its transform was written.
  const labels: LabelInput[] = [
    { id: 'near', anchor: { x: 0, y: 0, z: 0.5 }, priority: 1, tiers: ['dot'], keep: true },
    { id: 'away', anchor: { x: 3, y: 0, z: 0.5 }, priority: 1, tiers: ['dot'], keep: true },
  ];
  const nodes = new Map(labels.map((l) => [l.id, fakeNode()]));
  placeLabels(labels, nodes, new Map(), vp, 1280, 800);
  assert.equal(nodes.get('away')!.dataset.hidden, 'crowded', 'the off-stage card is crowded');
  for (const node of nodes.values()) {
    assert.doesNotMatch(node.style.transform, /-9999px/, 'a card is never left at the measuring spot');
    assert.match(node.style.transform, /^translate3d\(-?[\d.]+px, -?[\d.]+px, 0\) translate\(-50%, -100%\)$/);
  }
});
