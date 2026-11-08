// Unit tests for the Marketing overview and audience helpers (src/lib/marketingHub.ts).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { ContactList } from '../src/lib/brevo.ts';
import { MARKETING_TABS, dayKey, type Campaign, type Summary } from '../src/lib/marketing.ts';
import { audienceTotals, campaignCounts, campaignRows, postCountByDay, reachOf, reachablePercent, trendPath, trendPoints } from '../src/lib/marketingHub.ts';

const campaign = (id: string, status: Campaign['status'], updated: string, counts: Record<string, number> = {}): Campaign => ({
  id, name: id, objective: '', product: '', brief: '', languages: ['en'], platforms: ['facebook'], start_date: null, end_date: null, status, utm_campaign: '',
  auto_post: false, auto_post_accounts: [], auto_post_approved_by: null, auto_post_enabled_at: null, created_by: 'u', created_at: updated, updated_at: updated, counts, auto_post_approval: null,
});
const totals = { posts: 1, impressions: 10, reach: 0, engagement: 5, clicks: 1, likes: 0, comments: 0, shares: 0, views: 7, engagement_rate: null, without_metrics: 0 };

describe('tabs', () => {
  test('the overview is first, so #marketing opens it', () => {
    assert.equal(MARKETING_TABS[0], 'overview');
    assert.ok(MARKETING_TABS.includes('audience'));
    assert.ok(MARKETING_TABS.includes('posts'), 'old #marketing-posts links keep working');
  });
});

describe('campaignRows', () => {
  test('active first, newest change first, with post counts and reach from the summary', () => {
    const summary = { campaigns: [{ ...totals, campaign_id: 'b', name: 'b' }, { ...totals, campaign_id: null, name: 'none' }] } as unknown as Summary;
    const rows = campaignRows([
      campaign('a', 'draft', '2026-11-03'), campaign('b', 'active', '2026-11-01', { published: 2, draft: 1 }), campaign('c', 'active', '2026-11-02'),
    ], summary);
    assert.deepEqual(rows.map((r) => r.id), ['c', 'b', 'a']);
    assert.equal(rows[1].posts, 3);
    assert.equal(rows[1].published, 2);
    assert.equal(rows[1].reach, 7, 'views stand in when the platform reports no reach');
    assert.equal(rows[0].reach, null, 'no figure is invented for a campaign without results');
  });
  test('the table stops at the limit and counts by status', () => {
    const many = Array.from({ length: 8 }, (_, i) => campaign(`c${i}`, i % 2 ? 'paused' : 'active', `2026-11-0${i + 1}`));
    assert.equal(campaignRows(many, null).length, 5);
    assert.deepEqual(campaignCounts(many), { active: 4, draft: 0, paused: 4, completed: 0, total: 8 });
    assert.equal(reachOf({ reach: 3, views: 9 }), 3);
  });
});

describe('calendar and trend', () => {
  test('posts count on their publish day, else their scheduled day', () => {
    const counts = postCountByDay([
      { scheduled_at: '2026-11-06T09:00:00', published_at: null }, { scheduled_at: '2026-11-05T09:00:00', published_at: '2026-11-06T10:00:00' }, { scheduled_at: null, published_at: null },
    ], dayKey);
    assert.equal(counts.get('2026-11-06'), 2);
    assert.equal(counts.size, 1);
  });
  test('trend points span the box, highest value at the top', () => {
    const points = trendPoints([{ day: 'a', engagement: 0 }, { day: 'b', engagement: 10 }], 100, 50, 0);
    assert.deepEqual(points.map((p) => [p.x, p.y]), [[0, 50], [100, 0]]);
    assert.equal(trendPath(points), 'M0 50 L100 0');
    assert.deepEqual(trendPoints([], 100, 50), []);
    assert.equal(trendPoints([{ day: 'a', engagement: 4 }, { day: 'b', engagement: 4 }], 100, 50)[0].y, 25);
  });
});

describe('audience', () => {
  test('totals and the share that can still be emailed', () => {
    const lists: ContactList[] = [{ id: 1, name: 'News', subscribers: 1200, blacklisted: 3, folder_id: null }, { id: 2, name: 'VIP', subscribers: 40, blacklisted: 0, folder_id: null }];
    const t = audienceTotals(lists);
    assert.equal(t.subscribers, 1240);
    assert.equal(t.blocked, 3);
    assert.equal(t.largest?.name, 'News');
    assert.equal(reachablePercent(lists[0]), 99.8);
    assert.equal(reachablePercent({ subscribers: 0, blacklisted: 0 }), null);
    assert.equal(audienceTotals([]).largest, null);
  });
});
