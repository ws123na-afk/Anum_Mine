// Finance view helpers (src/lib/finance.ts, docs/finance.md). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import {
  amountTone, checkFile, dueLabel, formatAmount, formatAmounts, hashForTab, isLast4, minorDigits, monthLabel, parseAmount, pickAmount, rangeLabel,
  shiftMonth, spendBars, spendCurrencies, tabFromHash,
} from '../src/lib/finance.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { viewFromHash } from '../src/lib/shell.ts';

afterEach(() => setLocale('en'));

describe('money', () => {
  test('wire strings are shown with the currency code and its minor digits, Western digits in Arabic', () => {
    assert.equal(formatAmount('1234.5', 'AED', 'en').replace(/\s/g, ' '), 'AED 1,234.50');
    assert.match(formatAmount('1.250', 'KWD', 'en'), /1\.250/);
    assert.match(formatAmount('-80.25', 'AED', 'ar'), /80\.25/);
    assert.equal(formatAmount('abc', 'AED'), '-');
    assert.equal(minorDigits('JPY'), 0);
  });

  test('currencies are listed side by side, never added together', () => {
    const text = formatAmounts([{ currency: 'AED', amount: '10.00' }, { currency: 'USD', amount: '5.00' }], 'en').replace(/\s/g, ' ');
    assert.equal(text, 'AED 10.00 · USD 5.00');
    assert.equal(formatAmounts([]), '-');
    assert.deepEqual(pickAmount([{ currency: 'USD', amount: '1' }, { currency: 'AED', amount: '2' }], 'AED'), { currency: 'AED', amount: '2' });
    assert.deepEqual(pickAmount([], 'AED'), { currency: 'AED', amount: '0' });
  });

  test('typed amounts become exact wire strings or a reason', () => {
    assert.deepEqual(parseAmount('1,234.5', 'AED'), { ok: true, value: '1234.5' });
    assert.deepEqual(parseAmount('(12)', 'AED'), { ok: true, value: '-12' });
    assert.deepEqual(parseAmount('-45.50', 'AED'), { ok: true, value: '-45.50' });
    assert.deepEqual(parseAmount('١٢٫٥', 'AED'), { ok: true, value: '12.5' });
    assert.equal(parseAmount('1.234', 'AED').ok, false);
    assert.equal(parseAmount('1.234', 'KWD').ok, true);
    assert.equal(parseAmount('-5', 'AED', { signed: false }).ok, false);
    assert.equal(parseAmount('0', 'AED', { allowZero: false }).ok, false);
    assert.equal(parseAmount('ten', 'AED').ok, false);
  });

  test('only four digits of a card or account number are accepted', () => {
    assert.equal(isLast4('1234'), true);
    assert.equal(isLast4(''), true);
    assert.equal(isLast4('4111111111111111'), false);
  });

  test('a signed amount gets the in or out tone', () => {
    assert.equal(amountTone('-1.00'), 'out');
    assert.equal(amountTone('2.00'), 'in');
    assert.equal(amountTone('0.00'), '');
  });
});

describe('months, chart and labels', () => {
  test('months step across years and read in the person language', () => {
    assert.equal(shiftMonth('2026-01', -1), '2025-12');
    assert.equal(shiftMonth('2026-12', 1), '2027-01');
    assert.equal(monthLabel('2026-10', 'en'), 'October 2026');
  });

  test('spend bars are per currency, largest first, scaled to the largest', () => {
    const summary = { spend_by_category: [
      { category: 'groceries' as const, currency: 'AED', amount: '120.50', count: 2 },
      { category: 'utilities' as const, currency: 'AED', amount: '450.00', count: 1 },
      { category: 'dining' as const, currency: 'USD', amount: '30.00', count: 1 },
    ] };
    const bars = spendBars(summary, 'AED');
    assert.deepEqual(bars.map((b) => b.category), ['utilities', 'groceries']);
    assert.equal(bars[0].share, 1);
    assert.ok(Math.abs(bars[1].share - 120.5 / 450) < 1e-9);
    assert.deepEqual(spendCurrencies(summary, 'AED'), ['AED', 'USD']);
    assert.deepEqual(spendBars({ spend_by_category: [] }, 'AED'), []);
  });

  test('tabs are deep links the shell opens', () => {
    assert.equal(tabFromHash('#finance-transactions'), 'transactions');
    assert.equal(tabFromHash('#finance'), 'overview');
    assert.equal(tabFromHash('#finance-nope'), 'overview');
    assert.equal(hashForTab('overview'), '#finance');
    assert.equal(hashForTab('bills'), '#finance-bills');
    assert.equal(viewFromHash('#finance-bills'), 'finance');
    assert.equal(viewFromHash('#finance'), 'finance');
  });

  test('files are checked before they are sent', () => {
    assert.equal(checkFile({ name: 'oct.csv', size: 10 }, 2_000_000).ok, true);
    assert.equal(checkFile({ name: 'oct.csv', size: 0 }, 2_000_000).ok, false);
    assert.equal(checkFile({ name: 'oct.csv', size: 3_000_000 }, 2_000_000).ok, false);
    assert.equal(checkFile({ name: 'oct.xlsx', size: 10 }, 2_000_000).ok, false);
  });

  test('due and paging labels', () => {
    assert.equal(dueLabel(0), 'Due today');
    assert.equal(dueLabel(1), 'Due tomorrow');
    assert.equal(dueLabel(5), 'Due in 5 days');
    assert.equal(rangeLabel(0, 50, 230), '1 to 50 of 230');
    assert.equal(rangeLabel(0, 0, 0), 'Nothing to show');
    setLocale('ar');
    assert.equal(dueLabel(5), 'مستحقة بعد 5 أيام');
  });
});
