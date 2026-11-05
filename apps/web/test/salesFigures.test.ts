// The Sales overview figures come from the one lead list (docs/business.md#whatsapp-leads).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { FIGURES_NOTE, salesFigures } from '../src/lib/salesFigures.ts';
import type { SalesOverview } from '../src/lib/salesApi.ts';

const overview: SalesOverview = {
  leads: 4, qualified: 2, quoted: 1, won: 1, lost: 1, waiting: 3, by_intent: { website: 4 },
  source: 'business_leads', scope: 'all_whatsapp_leads', revenue_estimate: null,
};

describe('Sales overview figures', () => {
  test('every figure in order, with the waiting enquiries last', () => {
    assert.deepEqual(salesFigures(overview, 'en').map((item) => [item.key, item.label, item.value]), [
      ['leads', 'WhatsApp leads', 4], ['qualified', 'Qualified', 2], ['quoted', 'Offered', 1],
      ['won', 'Won', 1], ['lost', 'Lost', 1], ['waiting', 'Waiting to join the list', 3],
    ]);
  });
  test('Arabic labels, the same numbers', () => {
    const arabic = salesFigures(overview, 'ar');
    assert.equal(arabic.find((item) => item.key === 'waiting')?.label, 'بانتظار الإضافة إلى القائمة');
    assert.deepEqual(arabic.map((item) => item.value), [4, 2, 1, 1, 1, 3]);
  });
  test('no overview yet means no figures, never invented zeros', () => {
    assert.deepEqual(salesFigures(null, 'en'), []);
  });
  test('an older server without the waiting count reads as zero waiting', () => {
    const older = { ...overview } as Partial<SalesOverview>;
    delete older.waiting;
    assert.equal(salesFigures(older as SalesOverview, 'en').at(-1)?.value, 0);
  });
  test('the note names where the figures come from in both languages', () => {
    assert.match(FIGURES_NOTE.en, /Business, Leads/);
    assert.match(FIGURES_NOTE.ar, /واتساب/);
  });
});
