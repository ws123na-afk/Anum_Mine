// Unit tests for src/lib/library.ts and the report cards in src/lib/chat.ts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { ReportCard } from '@anum/contracts';
import { applyAskEvent, fromStored, PENDING_ASSISTANT, startAsk } from '../src/lib/chat.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { NAV, navParent, viewFromHash, visibleViews } from '../src/lib/shell.ts';
import {
  cardsByTurn, changeLabel, changeTone, downloadName, favoriteTarget, favoriteTypeLabel, fileSize, hashTarget, metricValue,
  parseTags, periodLabel, reportTypeLabel, visibleReply,
} from '../src/lib/library.ts';

const card: ReportCard = {
  report_id: 'report_1', turn_id: 'turn_a', title: 'Tyre cost', type: 'tyre', period_start: '2026-09-01', period_end: '2026-09-30',
  metrics: [{ label: 'Total', value: '12,400', unit: 'SAR', change_percent: 8 }], highlights: ['Punctures doubled'],
};

describe('visibleReply', () => {
  test('hides a complete report block', () => {
    assert.equal(visibleReply('Cost rose 8%.\n```anum-report\n{"title":"x"}\n```'), 'Cost rose 8%.');
  });
  test('hides a block that is still streaming, and a fence that is still arriving', () => {
    assert.equal(visibleReply('Cost rose.\n```anum-report\n{"title": "x", "metr'), 'Cost rose.');
    assert.equal(visibleReply('Cost rose.\n```anum-rep'), 'Cost rose.');
  });
  test('keeps ordinary code blocks and text', () => {
    const text = 'Run:\n```bash\nls\n```';
    assert.equal(visibleReply(text), text);
    assert.equal(visibleReply('No block'), 'No block');
  });
});

describe('report formatting', () => {
  test('metric values, changes and tones', () => {
    assert.equal(metricValue({ label: 'Total', value: '12,400', unit: 'SAR' }), '12,400 SAR');
    assert.equal(metricValue({ label: 'Count', value: '3' }), '3');
    assert.equal(changeLabel(8.46), '+8.5%');
    assert.equal(changeLabel(-3), '-3%');
    assert.equal(changeLabel(null), null);
    assert.equal(changeTone(2), 'up');
    assert.equal(changeTone(-2), 'down');
    assert.equal(changeTone(0), 'flat');
  });
  test('periods and types in English and Arabic', () => {
    setLocale('en');
    assert.match(periodLabel('2026-09-01', '2026-09-30') ?? '', /Sep 1, 2026 to Sep 30, 2026/);
    assert.equal(periodLabel(null, null), null);
    assert.equal(reportTypeLabel('tyre'), 'Tyre report');
    assert.equal(reportTypeLabel('weekly_stock'), 'Weekly stock');
    assert.equal(favoriteTypeLabel('collection'), 'Collection');
    setLocale('ar');
    assert.equal(reportTypeLabel('tyre'), 'تقرير الإطارات');
    assert.match(periodLabel('2026-09-01', '2026-09-30') ?? '', /^من .* إلى /);
    setLocale('en');
  });
  test('file sizes', () => {
    assert.equal(fileSize(512), '512 B');
    assert.equal(fileSize(2048), '2 KB');
    assert.equal(fileSize(3 * 1024 * 1024), '3 MB');
    assert.equal(fileSize(null), '');
  });
});

describe('links and inputs', () => {
  test('deep links from hashes', () => {
    assert.equal(hashTarget('#reports/report_abc', 'reports'), 'report_abc');
    assert.equal(hashTarget('#knowledge/kbc_1', 'knowledge'), 'kbc_1');
    assert.equal(hashTarget('#reports', 'reports'), null);
    assert.equal(hashTarget('#reports/../x', 'reports'), null);
    assert.equal(hashTarget('#reports/%E0%A4%A', 'reports'), null);
  });
  test('favorite targets', () => {
    assert.deepEqual(favoriteTarget('report', 'r1'), { view: 'reports', hash: '#reports/r1' });
    assert.deepEqual(favoriteTarget('collection', 'k 1'), { view: 'knowledge', hash: '#knowledge/k%201' });
    assert.equal(favoriteTarget('conversation', 'c').view, 'assistant');
  });
  test('tags are trimmed, lowercased, unique and bounded', () => {
    assert.deepEqual(parseTags(' Tyres, policy ,tyres,, Winter'), ['tyres', 'policy', 'winter']);
    assert.equal(parseTags(Array.from({ length: 20 }, (_, i) => `t${i}`).join(',')).length, 12);
  });
  test('download names', () => {
    assert.equal(downloadName('attachment; filename="tyre-cost-2026-10-11.csv"', 'x.csv'), 'tyre-cost-2026-10-11.csv');
    assert.equal(downloadName(null, 'x.csv'), 'x.csv');
  });
});

describe('report cards in the chat transcript', () => {
  test('a stored conversation attaches cards to their turns', () => {
    const turns = fromStored([
      { id: 'turn_q', conversation_id: 'c', role: 'user', content: 'Cost?', status: 'complete', created_at: '' },
      { id: 'turn_a', conversation_id: 'c', role: 'assistant', content: 'Rose.', status: 'complete', created_at: '' },
    ], [card]);
    assert.equal(turns[0].report, undefined);
    assert.equal(turns[1].report?.report_id, 'report_1');
    assert.equal(cardsByTurn([card]).get('turn_a')?.title, 'Tyre cost');
  });
  test('the done event carries the card; a malformed card is ignored', () => {
    let turns = startAsk([], 'Cost?');
    turns = applyAskEvent(turns, { event: 'done', data: { assistant_turn: { id: 'turn_a', conversation_id: 'c', role: 'assistant', content: 'Rose.', status: 'complete', created_at: '' }, report: card } });
    assert.equal(turns.find((t) => t.id === 'turn_a')?.report?.title, 'Tyre cost');
    let other = startAsk([], 'Cost?');
    other = applyAskEvent(other, { event: 'done', data: { assistant_turn: { id: 'turn_b', conversation_id: 'c', role: 'assistant', content: 'Ok', status: 'complete', created_at: '' }, report: { title: 'no id' } } });
    assert.equal(other.find((t) => t.id === 'turn_b')?.report, undefined);
    assert.ok(!other.some((t) => t.id === PENDING_ASSISTANT));
  });
});

describe('shell routing for the library views', () => {
  test('reports and collections open by link; Collections is a tab of the Knowledge Base entry', () => {
    assert.equal(viewFromHash('#reports/report_1'), 'reports');
    assert.equal(viewFromHash('#knowledge/kc_1'), 'knowledge');
    assert.equal(viewFromHash('#knowledge'), 'knowledge');
    assert.equal(viewFromHash('#favorites'), 'favorites');
    assert.equal(navParent('knowledge'), 'memory');
    assert.equal(navParent('reports'), 'reports');
    const visible = visibleViews(null);
    assert.ok(visible.includes('reports') && visible.includes('favorites') && !visible.includes('knowledge'));
    assert.equal(NAV.flatMap((g) => g.views).filter((v) => v === 'memory' || v === 'knowledge').length, 1);
  });
});
