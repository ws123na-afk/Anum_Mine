// The optional look per person in "Add Green Concrete" (src/hq-green-concrete/client.ts): the preview and the confirm carry the same
// looks, neutral is the default and the first choice, and nothing is read from a name.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { LOOK_CHOICES, createGreenConcreteClient, previewQuery } from '../src/hq-green-concrete/client.ts';

const HASH = 'a'.repeat(64);
const preview = { version: 1, workspace_id: 'w', payload_hash: HASH, timezone: 'Asia/Dubai', people: [], can_confirm: true } as never;

test('neutral is the default and the first choice', () => {
  assert.deepEqual([...LOOK_CHOICES], ['neutral', 'masculine', 'feminine']);
});

test('the preview query carries the default look and each choice, and nothing without looks', () => {
  assert.equal(previewQuery(undefined), '');
  assert.equal(previewQuery({ default_look: 'neutral', appearance: {} }), '?default_look=neutral');
  const q = new URLSearchParams(previewQuery({ default_look: 'neutral', appearance: { raed: 'masculine', mai: 'feminine' } }).slice(1));
  assert.equal(q.get('default_look'), 'neutral');
  assert.deepEqual(q.getAll('look'), ['raed:masculine', 'mai:feminine']);
});

test('the preview is asked for with the looks and the confirm sends the same looks by a click', async () => {
  const calls: Array<{ path: string; init: RequestInit }> = [];
  const request = (async (path: string, init: RequestInit) => { calls.push({ path, init }); return init.method === 'GET' ? preview : { version: 1, outcome: 'created' }; }) as never;
  const client = createGreenConcreteClient(request, () => ({ tenantId: 't', workspaceId: 'w' }));
  const looks = { default_look: 'neutral' as const, appearance: { raed: 'masculine' as const } };
  await client.preview(looks);
  assert.match(calls[0].path, /\/preview\?default_look=neutral&look=raed%3Amasculine$/);
  await client.confirm(preview, looks);
  assert.deepEqual(JSON.parse(String(calls[1].init.body)), { payload_hash: HASH, input_mode: 'click', timezone: 'Asia/Dubai', default_look: 'neutral', appearance: { raed: 'masculine' } });
  // An old caller that sends no looks gets the previous body: nothing is stored for the look.
  await client.confirm(preview);
  assert.deepEqual(JSON.parse(String(calls[2].init.body)), { payload_hash: HASH, input_mode: 'click', timezone: 'Asia/Dubai' });
});
