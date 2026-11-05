import assert from 'node:assert/strict';
import { test } from 'node:test';
import { flightNote, type FlightPlace } from '../src/hq-nav/flightLive.ts';
import { translate, type MessageKey } from '../src/lib/i18n.ts';

const say = (locale: 'en' | 'ar') => (key: MessageKey, vars?: Record<string, string>) => translate(locale, key, vars);
const place: FlightPlace = { stopId: 'b1', name: 'Finance' };

test('a cancelled flight is announced once, politely, in English and Arabic, and the place is forgotten', () => {
  const en = flightNote({ phase: 'cancelled', frame: 'b1' }, place, say('en'));
  assert.equal(en.text, 'Stopped before reaching Finance.');
  assert.equal(en.place, null);
  assert.equal(en.arrived, false);
  const ar = flightNote({ phase: 'cancelled', frame: 'b1' }, place, say('ar'));
  assert.equal(ar.text, 'توقف الانتقال قبل الوصول إلى \u2068Finance\u2069.');
  for (const text of [en.text, ar.text]) assert.doesNotMatch(text ?? '', /[–—]/);
});

test('after a cancel no later event repeats the old place (no stale text)', () => {
  const after = flightNote({ phase: 'cancelled', frame: 'b1' }, place, say('en'));
  // A label card's own move (no named place) starts and ends: the live region is left alone.
  for (const phase of ['start', 'end', 'instant', 'cancelled'] as const) {
    const note = flightNote({ phase, frame: '' }, after.place, say('en'));
    assert.equal(note.text, null, phase);
    assert.equal(note.arrived, false, phase);
  }
});

test('start, arrival and a quick jump still read as before', () => {
  assert.equal(flightNote({ phase: 'start', frame: 'b1' }, place, say('en')).text, 'Moving to Finance.');
  const end = flightNote({ phase: 'end', frame: 'b1' }, place, say('en'));
  assert.equal(end.text, 'Now showing Finance.');
  assert.equal(end.arrived, true);
  assert.equal(end.place, place);
  assert.equal(flightNote({ phase: 'instant', frame: 'b1' }, place, say('ar')).text, 'عرض \u2068Finance\u2069.');
  assert.equal(flightNote({ phase: 'missing', frame: 'r9' }, null, say('en')).text, 'That place is not in the open building.');
});
