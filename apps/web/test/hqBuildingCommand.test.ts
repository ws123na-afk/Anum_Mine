import assert from 'node:assert/strict';
import { test } from 'node:test';
import { answerQuestion, createBuildingClient, missingQuestion, parseBuildingRequest, parseFloorCount, proposalArgs } from '../src/lib/hqBuildingCommand.ts';

test('a typed sentence becomes a name and a floor count', () => {
  assert.deepEqual(parseBuildingRequest('add a building called Tyre Pulse with 3 floors'), { name: 'Tyre Pulse', floors: 3 });
  assert.deepEqual(parseBuildingRequest('Please create a new building named "North Depot" with three floors'), { name: 'North Depot', floors: 3 });
  assert.deepEqual(parseBuildingRequest('add building Workshop, 2 levels'), { name: 'Workshop', floors: 2 });
});

test('a missing name or floor count is reported, not guessed', () => {
  const noFloors = parseBuildingRequest('add a building called Tyre Pulse');
  assert.deepEqual(noFloors, { name: 'Tyre Pulse', floors: null });
  assert.equal(missingQuestion(noFloors!), 'floors');
  const noName = parseBuildingRequest('add a building with 3 floors');
  assert.equal(noName?.floors, 3);
  assert.equal(noName?.name, null);
  assert.equal(missingQuestion(noName!), 'name');
  assert.deepEqual(parseBuildingRequest('add a building'), { name: null, floors: null });
});

test('other sentences are not building requests', () => {
  for (const s of ['go to reception', 'approve the building permit', 'list blocked work', 'start a task to check the building', '']) assert.equal(parseBuildingRequest(s), null, s);
});

test('Arabic requests and Arabic digits work', () => {
  assert.deepEqual(parseBuildingRequest('أضف مبنى اسمه المستودع الشمالي بثلاثة طوابق'), { name: 'المستودع الشمالي', floors: 3 });
  assert.equal(parseBuildingRequest('أضف مبنى باسم برج ٤ طوابق')?.floors, 4);
});

test('the answer to the one question fills only the field that was asked', () => {
  assert.deepEqual(answerQuestion({ name: null, floors: 3 }, 'called Tyre Pulse'), { name: 'Tyre Pulse', floors: 3 });
  assert.deepEqual(answerQuestion({ name: 'Tyre Pulse', floors: null }, 'three floors'), { name: 'Tyre Pulse', floors: 3 });
  assert.equal(answerQuestion({ name: 'Tyre Pulse', floors: null }, 'a few').floors, null);
  assert.equal(parseFloorCount('٣'), 3);
});

test('proposal arguments omit what is missing', () => {
  assert.deepEqual(proposalArgs({ name: 'X', floors: null }, 'ar'), { name: 'X', lang: 'ar' });
});

test('confirm always sends the shown hash with input mode click and the attempt key', async () => {
  const calls: { path: string; init: RequestInit }[] = [];
  const client = createBuildingClient(async <T,>(path: string, init: RequestInit) => { calls.push({ path, init }); return { version: 1, replayed: false, building: { id: 'b', name: 'X' } } as T; });
  const proposal = { version: 1 as const, payload_hash: 'ab'.repeat(32), bounds: { width_m: 60, depth_m: 40 }, can_confirm: true, governed_by: '', note: '',
    creates: { buildings: 1, floors: 2, rooms: 4, desks: 0, agents: 0 },
    content: { lang: 'en' as const, building: { id: 'b', name: 'X', timezone: 'Asia/Dubai' }, floors: [{ id: 'f0', level: 0, name: 'G', width_m: 60, depth_m: 40, rooms: [] }, { id: 'f1', level: 1, name: '1', width_m: 60, depth_m: 40, rooms: [] }], totals: { buildings: 1, floors: 2, rooms: 4, desks: 0, agents: 0 } } };
  await client.confirm(proposal, 'assign-key-0001');
  assert.equal(calls[0].path, '/api/v1/office/building-proposals/confirm');
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), { name: 'X', floors: 2, lang: 'en', payload_hash: 'ab'.repeat(32), input_mode: 'click' });
  assert.equal((calls[0].init.headers as Record<string, string>)['Idempotency-Key'], 'assign-key-0001');
});
