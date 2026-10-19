// Social media marketing (docs/marketing.md): types and pure helpers for the Marketing view.
// No browser or Vite APIs, so `pnpm test:unit` exercises them directly. The server is the authority
// on limits (POST /api/v1/marketing/posts/validate and every post's `issues`); these mirror
// services/api/anum_api/marketing/platforms.py for live counters while typing.
import { formatDateTime, t, type MessageKey } from './i18n.ts';

export type Platform = 'facebook' | 'instagram' | 'linkedin' | 'x' | 'tiktok' | 'youtube';
export type Provider = 'meta' | 'linkedin' | 'x' | 'tiktok' | 'google';
export type Language = 'en' | 'ar';
export type PostStatus = 'draft' | 'pending_approval' | 'scheduled' | 'publishing' | 'published' | 'sent_to_inbox' | 'failed' | 'cancelled';
export type CampaignStatus = 'draft' | 'active' | 'paused' | 'completed' | 'archived';
export type AccountStatus = 'connected' | 'needs_reauth' | 'error' | 'revoked';

export const PLATFORMS: Platform[] = ['facebook', 'instagram', 'linkedin', 'x', 'tiktok', 'youtube'];
export const POST_STATUSES: PostStatus[] = ['draft', 'pending_approval', 'scheduled', 'publishing', 'published', 'sent_to_inbox', 'failed', 'cancelled'];
export const CAMPAIGN_STATUSES: CampaignStatus[] = ['draft', 'active', 'paused', 'completed', 'archived'];

export interface PlatformError { code: string; message: string; fix: string; retryable: boolean; status?: number | null }
export interface Issue { code: string; severity: 'error' | 'warning'; message: string }
export interface MediaRef { file_id: string; kind: 'image' | 'video'; name: string; content_type: string; size_bytes: number; width: number | null; height: number | null; duration_seconds: number | null }
export interface PostMetrics { impressions: number | null; reach: number | null; engagement: number | null; clicks: number | null; likes: number | null; comments: number | null; shares: number | null; saves: number | null; views: number | null; note: string | null }

export interface Account {
  id: string; platform: Platform; provider: Provider; external_id: string; display_name: string; handle: string | null;
  status: AccountStatus; scopes: string[]; token_expires_at: string | null; details: Record<string, unknown>;
  last_checked_at: string | null; last_error: PlatformError | null; connected_by: string; created_at: string; updated_at: string;
}

export interface Post {
  id: string; campaign_id: string | null; group_id: string; account_id: string; platform: Platform; language: Language; status: PostStatus;
  text: string; title: string; hashtags: string[]; link_url: string | null; media: MediaRef[]; scheduled_at: string | null;
  published_at: string | null; external_id: string | null; external_url: string | null; attempts: number; last_error: PlatformError | null;
  approval_id: string | null; approved_by: string | null; authorised_by: 'approval' | 'auto_post' | null; approved_hash: string | null;
  metrics: PostMetrics | null; metrics_updated_at: string | null; ai_generated: boolean; untrusted_source: boolean;
  created_by: string; created_at: string; updated_at: string;
  account_name: string; account_handle: string | null; account_status: AccountStatus | null; issues: Issue[]; length: number; limit: number;
  approval: Record<string, unknown> | null; approval_state: 'none' | 'pending' | 'approved' | 'rejected' | 'expired';
}

export interface Campaign {
  id: string; name: string; objective: string; product: string; brief: string; languages: Language[]; platforms: Platform[];
  start_date: string | null; end_date: string | null; status: CampaignStatus; utm_campaign: string; auto_post: boolean;
  auto_post_accounts: string[]; auto_post_approved_by: string | null; auto_post_enabled_at: string | null;
  created_by: string; created_at: string; updated_at: string; counts: Record<string, number>; auto_post_approval: Record<string, unknown> | null;
}

export interface ProviderSetup { provider: Provider; label: string; platforms: Platform[]; configured: boolean; missing: string[] }
export interface PlatformLimits {
  platform: Platform; name: string; text_limit: number; counting: 'chars' | 'x_weighted' | 'utf8_bytes'; hashtag_limit: number | null;
  hashtag_recommended: number | null; requires_media: boolean; requires_video: boolean; requires_title: boolean; title_limit: number | null;
  max_images: number; max_videos: number; image_types: string[]; video_types: string[]; image_ratio: number[] | null; video_ratio: number[] | null;
  video_seconds: number[] | null; notes: string[]; review: string;
}
export interface Setup { providers: ProviderSetup[]; redirect_url: string | null; public_media: boolean; tiktok_direct_post: boolean; platforms: PlatformLimits[]; can_manage: boolean; can_write: boolean }
export interface Preferences { brand_voice: string; timezone: string; default_hashtags_en: string[]; default_hashtags_ar: string[]; updated_by: string | null; updated_at: string | null }
export interface MediaFile { id: string; name: string; content_type: string; kind: 'image' | 'video'; size_bytes: number; created_at: string }
export interface Idea { title: string; angle: string; platform: Platform; en: string | null; ar: string | null; hashtags_en: string[]; hashtags_ar: string[] }
export interface TimeSlot { platform: Platform | null; weekday: number; hour: number; posts: number; engagement_rate: number | null; basis: 'history' | 'default' }
export interface IdeasResult { ideas: Idea[]; generator: 'model' | 'template'; untrusted_source: boolean; brand_voice_used: boolean; note: string | null; best_times: TimeSlot[] }
export interface Totals { posts: number; impressions: number; reach: number; engagement: number; clicks: number; likes: number; comments: number; shares: number; views: number; engagement_rate: number | null; without_metrics: number }
export interface Summary {
  start: string; end: string; totals: Totals; previous: Totals; platforms: (Totals & { platform: Platform })[]; campaigns: (Totals & { campaign_id: string | null; name: string })[];
  top_posts: { id: string; platform: Platform; account_id: string; excerpt: string; published_at: string | null; external_url: string | null; metrics: PostMetrics | null }[];
  series: { day: string; posts: number; impressions: number; engagement: number }[]; upcoming: number; pending_approval: number; failed: number; metrics_updated_at: string | null;
}
export type DeliveryOutcome = 'requested' | 'not_connected' | 'not_requested' | 'not_allowed' | 'failed';
export interface ChannelResult { outcome: DeliveryOutcome; detail: string; approval_id: string | null; target: string | null }
export interface DeliveryResult { email: ChannelResult; whatsapp: ChannelResult }
export interface WeeklyReport { week_start: string; week_end: string; language: Language; headline: string; lines: string[]; best_times: TimeSlot[]; delivery: Record<string, string> }

// ---- labels -----------------------------------------------------------------------------------

export const PLATFORM_NAMES: Record<Platform, string> = { facebook: 'Facebook', instagram: 'Instagram', linkedin: 'LinkedIn', x: 'X', tiktok: 'TikTok', youtube: 'YouTube' };
export function platformName(platform: Platform): string { return t(`marketing.platform.${platform}` as MessageKey); }
export function statusName(status: PostStatus): string { return t(`marketing.status.${status}` as MessageKey); }
export function campaignStatusName(status: CampaignStatus): string { return t(`marketing.campaignStatus.${status}` as MessageKey); }
export function accountStatusName(status: AccountStatus): string { return t(`marketing.accountStatus.${status}` as MessageKey); }

export function statusTone(status: PostStatus): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  if (status === 'published' || status === 'sent_to_inbox') return 'success';
  if (status === 'failed') return 'dangerTone';
  if (status === 'draft' || status === 'cancelled') return 'neutral';
  return 'warning';
}

export function accountTone(status: AccountStatus): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  return status === 'connected' ? 'success' : status === 'revoked' ? 'neutral' : status === 'needs_reauth' ? 'warning' : 'dangerTone';
}

/** What the owner can do with a post now (the server enforces the same rules). */
export function postActions(post: Pick<Post, 'status' | 'approved_hash'>, canWrite: boolean): { edit: boolean; schedule: boolean; cancel: boolean; publishNow: boolean; retry: boolean; remove: boolean } {
  const editable = ['draft', 'pending_approval', 'scheduled', 'failed', 'cancelled'].includes(post.status);
  return {
    edit: canWrite && editable,
    schedule: canWrite && ['draft', 'failed', 'cancelled'].includes(post.status),
    cancel: canWrite && (post.status === 'scheduled' || post.status === 'pending_approval'),
    publishNow: canWrite && post.status === 'scheduled' && Boolean(post.approved_hash),
    retry: canWrite && post.status === 'failed' && Boolean(post.approved_hash),
    remove: canWrite && post.status !== 'publishing',
  };
}

// ---- text and limits ------------------------------------------------------------------------------

const URL = /https?:\/\/\S+/gi;
export const HASHTAG = /(?<![\w&])#([\p{L}\p{N}_]+)/gu;
const X_LIGHT: [number, number][] = [[0, 4351], [8192, 8205], [8208, 8223], [8242, 8247]];

export function xWeightedLength(text: string): number {
  const urls = text.match(URL) ?? [];
  let length = 23 * urls.length;
  for (const char of text.replace(URL, '')) {
    const point = char.codePointAt(0) ?? 0;
    length += X_LIGHT.some(([low, high]) => point >= low && point <= high) ? 1 : 2;
  }
  return length;
}

/** Hashtags typed with or without #, separated by spaces or commas; duplicates dropped. */
export function parseHashtags(value: string): string[] {
  const tags: string[] = [];
  for (const raw of value.split(/[\s,،]{1,50}/)) {
    const tag = raw.trim().replace(/^#{1,20}/, '');
    if (tag && !tags.some((existing) => existing.toLowerCase() === tag.toLowerCase())) tags.push(tag.slice(0, 100));
  }
  return tags;
}

/** The text a platform receives: copy, hashtags not already in it, then the link. Same as the API. */
export function composeText(text: string, hashtags: string[], link: string | null): string {
  let body = text.trim();
  const present = new Set([...body.matchAll(HASHTAG)].map((match) => match[1].toLowerCase()));
  const extra = hashtags.filter((tag) => !present.has(tag.toLowerCase())).map((tag) => `#${tag}`);
  if (extra.length) body = body ? `${body}\n\n${extra.join(' ')}` : extra.join(' ');
  if (link && !body.includes(link)) body = body ? `${body}\n${link}` : link;
  return body;
}

export function textLength(text: string, limits: Pick<PlatformLimits, 'counting'>): number {
  if (limits.counting === 'x_weighted') return xWeightedLength(text);
  if (limits.counting === 'utf8_bytes') return new TextEncoder().encode(text).length;
  return text.length; // UTF-16 code units, as the API counts Python characters for BMP text
}

export interface Draft { text: string; title: string; hashtags: string[]; link: string | null; media: { kind: 'image' | 'video'; content_type: string }[] }

/** Problems a draft has for a platform, as you type (a subset of the API's checks; the API has the last word). */
export function checkDraft(draft: Draft, limits: PlatformLimits): { length: number; limit: number; issues: Issue[] } {
  const composed = composeText(draft.text, draft.hashtags, draft.link);
  const length = textLength(composed, limits);
  const issues: Issue[] = [];
  const error = (code: string, message: string) => issues.push({ code, severity: 'error', message });
  if (!composed.trim() && draft.media.length === 0) error('empty', t('marketing.check.empty'));
  if (length > limits.text_limit) error('too_long', t('marketing.check.tooLong', { name: limits.name, limit: limits.text_limit, over: length - limits.text_limit }));
  const tags = new Set([...composed.matchAll(HASHTAG)].map((match) => match[1].toLowerCase())).size;
  if (limits.hashtag_limit !== null && tags > limits.hashtag_limit) error('too_many_hashtags', t('marketing.check.tooManyHashtags', { name: limits.name, limit: limits.hashtag_limit }));
  else if (limits.hashtag_recommended !== null && tags > limits.hashtag_recommended) issues.push({ code: 'many_hashtags', severity: 'warning', message: t('marketing.check.manyHashtags', { name: limits.name, count: limits.hashtag_recommended }) });
  const videos = draft.media.filter((item) => item.kind === 'video').length, images = draft.media.length - videos;
  if (limits.requires_video && videos === 0) error('video_required', t('marketing.check.videoRequired', { name: limits.name }));
  else if (limits.requires_media && draft.media.length === 0) error('media_required', t('marketing.check.mediaRequired', { name: limits.name }));
  if (images > limits.max_images) error('too_many_images', t('marketing.check.tooManyImages', { name: limits.name, count: limits.max_images }));
  if (videos > limits.max_videos) error('too_many_videos', t('marketing.check.oneVideo', { name: limits.name }));
  if (images && videos) error('mixed_media', t('marketing.check.mixed', { name: limits.name }));
  if (draft.media.some((item) => item.kind === 'image' && !limits.image_types.includes(item.content_type))) error('image_type', t('marketing.check.imageType', { name: limits.name, types: limits.image_types.map((type) => type.split('/')[1].toUpperCase()).join(', ') }));
  if (limits.requires_title && !draft.title.trim()) error('title_required', t('marketing.check.titleRequired', { name: limits.name }));
  if (limits.title_limit !== null && draft.title.length > limits.title_limit) error('title_too_long', t('marketing.check.titleTooLong', { name: limits.name, limit: limits.title_limit }));
  return { length, limit: limits.text_limit, issues };
}

// ---- UTM links ----------------------------------------------------------------------------------------

/** Mirrors the API's with_utm: store links keep attribution in their own parameters. */
export function withUtm(url: string, source: string, campaign = '', content = ''): string {
  const parsed = new globalThis.URL(url.trim());
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') throw new Error('bad link');
  const tracking = new URLSearchParams({ utm_source: source, utm_medium: 'social' });
  if (campaign) tracking.set('utm_campaign', campaign);
  if (content) tracking.set('utm_content', content);
  const host = parsed.hostname.toLowerCase();
  if (host === 'play.google.com') parsed.searchParams.set('referrer', tracking.toString());
  else if (host === 'apps.apple.com' || host === 'itunes.apple.com') parsed.searchParams.set('ct', [campaign || 'anum', source].join('-').slice(0, 40));
  else for (const [key, value] of tracking) parsed.searchParams.set(key, value);
  return parsed.toString();
}

export function isLink(value: string): boolean {
  try { const parsed = new globalThis.URL(value.trim()); return parsed.protocol === 'http:' || parsed.protocol === 'https:'; } catch { return false; }
}

// ---- calendar ---------------------------------------------------------------------------------------------

/** The weeks shown for a month: Sunday-first rows (the Gulf work week starts on Sunday). */
export function monthGrid(year: number, month: number): Date[][] {
  const first = new Date(year, month, 1);
  const start = new Date(year, month, 1 - first.getDay());
  const weeks: Date[][] = [];
  for (let week = 0; week < 6; week += 1) {
    const days: Date[] = [];
    for (let day = 0; day < 7; day += 1) days.push(new Date(start.getFullYear(), start.getMonth(), start.getDate() + week * 7 + day));
    if (week > 3 && days[0].getMonth() !== month) break;
    weeks.push(days);
  }
  return weeks;
}

export function dayKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

/** Posts by local day of their scheduled (or published) time. */
export function postsByDay(posts: Pick<Post, 'scheduled_at' | 'published_at'>[]): Map<string, number[]> {
  const days = new Map<string, number[]>();
  posts.forEach((post, index) => {
    const when = post.published_at ?? post.scheduled_at;
    if (!when) return;
    const key = dayKey(new Date(when));
    days.set(key, [...(days.get(key) ?? []), index]);
  });
  return days;
}

/** The post's time moved to another day, keeping the time of day (calendar drag and drop). */
export function moveToDay(current: string | null, day: Date, now = new Date()): Date {
  const base = current ? new Date(current) : new Date(day.getFullYear(), day.getMonth(), day.getDate(), 20, 0);
  const moved = new Date(day.getFullYear(), day.getMonth(), day.getDate(), base.getHours(), base.getMinutes());
  return moved.getTime() < now.getTime() ? new Date(now.getTime() + 5 * 60_000) : moved;
}

/** `datetime-local` value for a date, and back. */
export function toLocalInput(value: string | Date | null): string {
  if (!value) return '';
  const date = new Date(value);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export function when(value: string | null): string {
  return value ? formatDateTime(value) : t('marketing.notScheduled');
}

// ---- analytics ---------------------------------------------------------------------------------------------

export function rate(value: number | null): string {
  return value === null ? '-' : `${(value * 100).toFixed(1)}%`;
}

export function change(current: number | null, previous: number | null): string | null {
  if (current === null || previous === null) return null;
  const points = (current - previous) * 100;
  return `${points >= 0 ? '+' : '−'}${Math.abs(points).toFixed(1)}`;
}

export function slotLabel(slot: Pick<TimeSlot, 'weekday' | 'hour'>): string {
  // The API's weekday is 0 = Monday.
  return t('marketing.slot', { day: t(`marketing.weekday.${slot.weekday}` as MessageKey), hour: `${String(slot.hour).padStart(2, '0')}:00` });
}

/** The OAuth callback in this page's address (`?code=...&state=mkt....`), or null. */
export function oauthCallback(search: string): { code: string; state: string } | { error: string } | null {
  const params = new URLSearchParams(search);
  const state = params.get('state');
  if (!state || !state.startsWith('mkt.')) return null;
  const error = params.get('error_description') ?? params.get('error');
  if (error) return { error };
  const code = params.get('code');
  return code ? { code, state } : null;
}

// ---- API paths ------------------------------------------------------------------------------------

export interface PostFilter { status?: PostStatus | ''; campaignId?: string; platform?: Platform | ''; query?: string; before?: string; limit?: number; from?: string; to?: string }
export function postsPath(filter: PostFilter = {}): string {
  const params = new URLSearchParams({ limit: String(filter.limit ?? 20) });
  if (filter.status) params.set('status', filter.status);
  if (filter.campaignId) params.set('campaign_id', filter.campaignId);
  if (filter.platform) params.set('platform', filter.platform);
  if (filter.query?.trim()) params.set('q', filter.query.trim());
  if (filter.before) params.set('before', filter.before);
  if (filter.from) params.set('from', filter.from);
  if (filter.to) params.set('to', filter.to);
  return `/api/v1/marketing/posts?${params.toString()}`;
}

/** The Marketing tabs; each one but Posts is a deep link (#marketing-calendar). */
export type MarketingTab = 'posts' | 'calendar' | 'campaigns' | 'email' | 'accounts' | 'analytics';
export const MARKETING_TABS: readonly MarketingTab[] = ['posts', 'calendar', 'campaigns', 'email', 'accounts', 'analytics'];
