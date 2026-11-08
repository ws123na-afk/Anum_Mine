// The Marketing overview and audience (docs/marketing.md#overview): the campaign rows, the content
// calendar dots, the engagement trend and the contact lists, all from what the API returns (the
// marketing summary, campaigns, posts and Brevo lists). Spend, leads, conversions, cost per lead and
// return on spend are not tracked by ANUM, so nothing here computes them. Pure, so
// `pnpm --filter @anum/web test:unit` covers it.
import type { ContactList } from './brevo.ts';
import type { Campaign, CampaignStatus, Platform, Post, Summary, Totals } from './marketing.ts';

/** Active first, then drafts, paused, completed and archived; newest change first inside each. */
const STATUS_ORDER: Record<CampaignStatus, number> = { active: 0, draft: 1, paused: 2, completed: 3, archived: 4 };

export interface CampaignRow {
  id: string; name: string; status: CampaignStatus; platforms: Platform[]; start: string | null; end: string | null;
  posts: number; published: number; reach: number | null; engagement: number | null; autoPost: boolean;
}

/** The overview's campaign table: the first `limit` campaigns with their post counts and reach. */
export function campaignRows(campaigns: readonly Campaign[], summary: Summary | null, limit = 5): CampaignRow[] {
  const results = new Map((summary?.campaigns ?? []).filter((c) => c.campaign_id).map((c) => [c.campaign_id as string, c]));
  return [...campaigns]
    .sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || b.updated_at.localeCompare(a.updated_at))
    .slice(0, limit)
    .map((c) => {
      const total = Object.values(c.counts ?? {}).reduce((n, v) => n + v, 0);
      const result = results.get(c.id);
      return {
        id: c.id, name: c.name, status: c.status, platforms: c.platforms, start: c.start_date, end: c.end_date,
        posts: total, published: (c.counts?.published ?? 0) + (c.counts?.sent_to_inbox ?? 0),
        reach: result ? reachOf(result) : null, engagement: result ? result.engagement : null, autoPost: c.auto_post,
      };
    });
}

/** Reach when the platform reports it, else views (video platforms report views only). */
export const reachOf = (totals: Pick<Totals, 'reach' | 'views'>): number => totals.reach || totals.views;

export interface CampaignCounts { active: number; draft: number; paused: number; completed: number; total: number }
export function campaignCounts(campaigns: readonly Pick<Campaign, 'status'>[]): CampaignCounts {
  const out: CampaignCounts = { active: 0, draft: 0, paused: 0, completed: 0, total: campaigns.length };
  for (const c of campaigns) if (c.status in out) out[c.status as keyof Omit<CampaignCounts, 'total'>] += 1;
  return out;
}

/** How many posts fall on each day key ("2026-11-06"), by their publish or scheduled time. */
export function postCountByDay(posts: readonly Pick<Post, 'scheduled_at' | 'published_at'>[], key: (date: Date) => string): Map<string, number> {
  const days = new Map<string, number>();
  for (const post of posts) {
    const at = post.published_at ?? post.scheduled_at;
    if (!at) continue;
    const day = key(new Date(at));
    days.set(day, (days.get(day) ?? 0) + 1);
  }
  return days;
}

export interface TrendPoint { day: string; value: number; x: number; y: number }

/**
 * The engagement trend as points in a `width` x `height` box (y grows down), with `pad` around it.
 * An empty series has no points; a flat series sits on the middle line.
 */
export function trendPoints(series: readonly Pick<Summary['series'][number], 'day' | 'engagement'>[], width: number, height: number, pad = 8): TrendPoint[] {
  if (!series.length) return [];
  const values = series.map((s) => s.engagement);
  const max = Math.max(...values), min = Math.min(...values);
  const span = max - min;
  const step = series.length > 1 ? (width - pad * 2) / (series.length - 1) : 0;
  return series.map((s, i) => ({
    day: s.day, value: s.engagement,
    x: Math.round((pad + step * i) * 10) / 10,
    y: Math.round((span === 0 ? height / 2 : pad + (1 - (s.engagement - min) / span) * (height - pad * 2)) * 10) / 10,
  }));
}
export const trendPath = (points: readonly TrendPoint[]): string => points.map((p, i) => `${i ? 'L' : 'M'}${p.x} ${p.y}`).join(' ');

export interface AudienceTotals { lists: number; subscribers: number; blocked: number; largest: ContactList | null }
/** Totals over the contact lists on the page (Brevo counts a contact once per list). */
export function audienceTotals(lists: readonly ContactList[]): AudienceTotals {
  let largest: ContactList | null = null;
  for (const list of lists) if (!largest || list.subscribers > largest.subscribers) largest = list;
  return { lists: lists.length, subscribers: lists.reduce((n, l) => n + l.subscribers, 0), blocked: lists.reduce((n, l) => n + l.blacklisted, 0), largest };
}

/** Share of a list's contacts that can still be emailed, 0 to 100, or null for an empty list. */
export function reachablePercent(list: Pick<ContactList, 'subscribers' | 'blacklisted'>): number | null {
  const all = list.subscribers + list.blacklisted;
  return all ? Math.round((list.subscribers / all) * 1000) / 10 : null;
}
