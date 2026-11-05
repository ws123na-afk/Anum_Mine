// How long records are kept (owner decision O5, docs/record-retention.md): parsing the API's answer, the days input,
// the save body and the client.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { createRetentionClient, parseDays, parseRetention, saveBody, type RecordRetention } from '../src/lib/recordRetention.ts';
import { CATALOGS, translate } from '../src/lib/i18n.ts';

const answer = {
  transcripts: { days: 90, is_default: true, default_days: 90, min_days: 7, max_days: 1825 },
  visitors: { days: 730, is_default: false, default_days: 365, min_days: 30, max_days: 1825 },
  revision: 3, updated_by: 'owner_a', updated_at: '2026-10-28T09:00:00+00:00', snapshots_stored: false, can_manage: true,
};

describe('parseRetention', () => {
  test('reads the periods, the revision and who may change them', () => {
    const r = parseRetention(answer)!;
    assert.equal(r.transcripts.days, 90);
    assert.equal(r.transcripts.isDefault, true);
    assert.deepEqual(r.visitors, { days: 730, isDefault: false, defaultDays: 365, minDays: 30, maxDays: 1825 });
    assert.equal(r.revision, 3);
    assert.equal(r.canManage, true);
    assert.equal(r.snapshotsStored, false);
    assert.equal(r.updatedBy, 'owner_a');
  });
  test('refuses anything that is not a real answer instead of inventing a period', () => {
    assert.equal(parseRetention(null), null);
    assert.equal(parseRetention({ ...answer, transcripts: undefined }), null);
    assert.equal(parseRetention({ ...answer, revision: -1 }), null);
    assert.equal(parseRetention({ ...answer, visitors: { ...answer.visitors, days: 5 } }), null);
    assert.equal(parseRetention({ ...answer, visitors: { ...answer.visitors, days: 90.5 } }), null);
    assert.equal(parseRetention({ ...answer, can_manage: 'yes' })!.canManage, false);
  });
});

describe('parseDays', () => {
  const period = parseRetention(answer)!.transcripts;
  test('accepts whole days inside the range, Western or Arabic-Indic digits', () => {
    assert.deepEqual(parseDays(' 30 ', period), { ok: true, value: 30 });
    assert.deepEqual(parseDays('٣٠', period), { ok: true, value: 30 });
    assert.deepEqual(parseDays('7', period), { ok: true, value: 7 });
    assert.deepEqual(parseDays('1825', period), { ok: true, value: 1825 });
  });
  test('says why other input is refused', () => {
    assert.deepEqual(parseDays('', period), { ok: false, reason: 'empty' });
    assert.deepEqual(parseDays('12.5', period), { ok: false, reason: 'notWhole' });
    assert.deepEqual(parseDays('-3', period), { ok: false, reason: 'notWhole' });
    assert.deepEqual(parseDays('ninety', period), { ok: false, reason: 'notWhole' });
    assert.deepEqual(parseDays('6', period), { ok: false, reason: 'range' });
    assert.deepEqual(parseDays('1826', period), { ok: false, reason: 'range' });
  });
});

describe('saveBody', () => {
  const current = parseRetention(answer) as RecordRetention;
  test('changes the edited period and keeps the other as the owner has it', () => {
    assert.deepEqual(saveBody(current, 'transcripts', 30), { transcript_days: 30, visitor_days: 730, expected_revision: 3, input_mode: 'click' });
    assert.deepEqual(saveBody(current, 'visitors', 400), { transcript_days: null, visitor_days: 400, expected_revision: 3, input_mode: 'click' });
  });
  test('null returns a period to the standard one', () => {
    assert.deepEqual(saveBody(current, 'visitors', null), { transcript_days: null, visitor_days: null, expected_revision: 3, input_mode: 'click' });
  });
});

describe('client', () => {
  test('reads and saves through the one route with a click', async () => {
    const calls: { path: string; init: RequestInit }[] = [];
    const client = createRetentionClient(async <T,>(path: string, init: RequestInit) => { calls.push({ path, init }); return answer as T; });
    assert.equal((await client.get()).revision, 3);
    await client.save(saveBody(parseRetention(answer)!, 'transcripts', 45));
    assert.deepEqual(calls.map((c) => [c.path, c.init.method]), [['/api/v1/workspace/record-retention', 'GET'], ['/api/v1/workspace/record-retention', 'PUT']]);
    assert.equal(JSON.parse(String(calls[1].init.body)).input_mode, 'click');
  });
  test('an unreadable answer is an error, never a made-up period', async () => {
    const client = createRetentionClient(async <T,>() => ({ nothing: true }) as T);
    await assert.rejects(client.get());
  });
});

describe('copy', () => {
  test('every key has English and Arabic text and says how long records are kept', () => {
    const keys = Object.keys(CATALOGS.en).filter((k) => k.startsWith('recordRetention.'));
    assert.ok(keys.length > 20);
    for (const key of keys) assert.ok((CATALOGS.ar as Record<string, unknown>)[key], key);
    assert.equal(translate('en', 'recordRetention.days', { count: 90 }), '90 days');
    assert.match(translate('en', 'recordRetention.transcripts.text', { period: '90 days' }), /deleted 90 days after it ends/);
    assert.match(translate('ar', 'recordRetention.visitors.text', { period: '365 يوم' }), /365/);
  });
});
