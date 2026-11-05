import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { localDateText } from '../src/lib/localDate.ts';

test('a chosen day is written in Arabic words with Western digits, a time too', () => {
  const day = localDateText('2026-10-19', 'date', 'ar');
  assert.match(day, /أكتوبر/);
  assert.match(day, /19/);
  assert.match(day, /2026/);
  assert.match(day, /الاثنين/);
  const at = localDateText('2026-10-28T09:30', 'datetime-local', 'ar');
  assert.match(at, /الأربعاء/);
  assert.match(at, /أكتوبر/);
  assert.match(at, /9:30/);
  assert.doesNotMatch(day + at, /[٠-٩]/, 'Arabic keeps Western digits like the rest of the app');
  assert.match(localDateText('2026-10-19', 'date', 'en'), /October 19, 2026/);
});

test('an empty, partial or impossible value writes nothing', () => {
  for (const value of ['', '2026-10', '2026-02-31', 'not a day', '2026-10-28T25:00']) {
    assert.equal(localDateText(value, value.includes('T') ? 'datetime-local' : 'date', 'ar'), '', value);
  }
  assert.equal(localDateText('2026-10-28', 'datetime-local', 'ar'), '', 'a day alone is not a date and time');
});

// Every HQ date field goes through LocalDateInput, so none is left with the browser's language only.
test('the HQ date fields follow the app language', () => {
  const files = ['../src/HqHistoryParts.tsx', '../src/HqRoomSlider.tsx', '../src/HqReceptionPanel.tsx', '../src/hq-assign/AssignDialog.tsx'];
  for (const file of files) {
    const source = readFileSync(new URL(file, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /<input[^>]*type="(date|datetime-local)"/, file);
    assert.match(source, /<LocalDateInput /, file);
  }
  const field = readFileSync(new URL('../src/hq-date/LocalDateInput.tsx', import.meta.url), 'utf8');
  assert.match(field, /lang=\{locale\}/);
  assert.match(field, /dir=\{directionOf\(locale\)\}/);
});
