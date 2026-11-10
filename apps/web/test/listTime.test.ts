import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { listTime } from '../src/lib/listTime.ts';
import { setLocale } from '../src/lib/i18n.ts';

describe('listTime: the short time beside a sender in a message list', () => {
  const now = new Date(2026, 10, 10, 15, 30);
  it('shows the time for today, the day for this year and the full date before that', () => {
    setLocale('en');
    assert.match(listTime(new Date(2026, 10, 10, 7, 5), now), /^7:05\sAM$/);
    assert.equal(listTime(new Date(2026, 9, 14, 7, 0), now), 'Oct 14');
    assert.equal(listTime(new Date(2025, 9, 14, 7, 0), now), 'Oct 14, 2025');
  });
  it('is shorter than the full date and time it replaces', () => {
    setLocale('en');
    assert.ok(listTime(new Date(2026, 9, 14, 7, 0), now).length < 'Oct 14, 2026, 7:00 AM'.length / 2);
  });
  it('follows the interface language and never throws on a bad value', () => {
    setLocale('ar');
    assert.match(listTime(new Date(2026, 9, 14, 7, 0), now), /أكتوبر/);
    setLocale('en');
    assert.equal(listTime('not a date', now), '');
  });
});
