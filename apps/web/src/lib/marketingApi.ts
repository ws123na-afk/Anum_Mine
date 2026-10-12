// Marketing over the live API (docs/marketing.md). Permissions are the server's: reading is open to
// members, drafting needs marketing:write, accounts and auto-post need marketing:manage.
import type { Approval } from '@anum/contracts';
import { apiRequest, apiRequestVoid, mapApproval, type ApiApproval } from './api';
import type {
  Account, Campaign, CampaignStatus, IdeasResult, Language, MediaFile, Platform, Post, PostStatus, Preferences, Provider, Setup, Summary, TimeSlot, WeeklyReport, DeliveryResult,
} from './marketing';
import { postsPath, type PostFilter } from './marketing';
export type { PostFilter } from './marketing';

const BASE = '/api/v1/marketing';
const json = (body: unknown) => ({ body: JSON.stringify(body) });
const id = (value: string) => encodeURIComponent(value);

export const getSetup = (): Promise<Setup> => apiRequest(`${BASE}/setup`, { method: 'GET' });
export const listAccounts = (): Promise<Account[]> => apiRequest(`${BASE}/accounts`, { method: 'GET' });
export const startConnect = (provider: Provider): Promise<{ authorize_url: string }> => apiRequest(`${BASE}/oauth/start`, { method: 'POST', ...json({ provider }) });
export const finishConnect = (code: string, state: string): Promise<Account[]> => apiRequest(`${BASE}/oauth/callback`, { method: 'POST', ...json({ code, state }) });
export const checkAccount = (accountId: string): Promise<Account> => apiRequest(`${BASE}/accounts/${id(accountId)}/check`, { method: 'POST' });
export const disconnectAccount = (accountId: string): Promise<Account> => apiRequest(`${BASE}/accounts/${id(accountId)}`, { method: 'DELETE' });

export const getPreferences = (): Promise<Preferences> => apiRequest(`${BASE}/preferences`, { method: 'GET' });
export const savePreferences = (body: Omit<Preferences, 'updated_by' | 'updated_at'>): Promise<Preferences> => apiRequest(`${BASE}/preferences`, { method: 'PUT', ...json(body) });

export function listCampaigns(options: { status?: CampaignStatus | ''; query?: string; before?: string; limit?: number } = {}): Promise<Campaign[]> {
  const params = new URLSearchParams({ limit: String(options.limit ?? 20) });
  if (options.status) params.set('status', options.status);
  if (options.query?.trim()) params.set('q', options.query.trim());
  if (options.before) params.set('before', options.before);
  return apiRequest(`${BASE}/campaigns?${params.toString()}`, { method: 'GET' });
}
export type CampaignBody = Partial<Pick<Campaign, 'name' | 'objective' | 'product' | 'brief' | 'languages' | 'platforms' | 'start_date' | 'end_date' | 'status' | 'utm_campaign'>>;
export const createCampaign = (body: CampaignBody): Promise<Campaign> => apiRequest(`${BASE}/campaigns`, { method: 'POST', ...json(body) });
export const updateCampaign = (campaignId: string, body: CampaignBody): Promise<Campaign> => apiRequest(`${BASE}/campaigns/${id(campaignId)}`, { method: 'PUT', ...json(body) });
export const deleteCampaign = (campaignId: string): Promise<void> => apiRequestVoid(`${BASE}/campaigns/${id(campaignId)}`, { method: 'DELETE' });
export const setAutoPost = (campaignId: string, enabled: boolean, accountIds: string[]): Promise<Campaign> => apiRequest(`${BASE}/campaigns/${id(campaignId)}/auto-post`, { method: 'POST', ...json({ enabled, account_ids: accountIds }) });

export const listPosts = (filter: PostFilter = {}): Promise<Post[]> => apiRequest(postsPath(filter), { method: 'GET' });
export interface MediaInput { file_id: string; width?: number | null; height?: number | null; duration_seconds?: number | null }
export interface VariantBody { account_id: string; language: Language; text: string; title: string; hashtags: string[]; link_url: string | null; media: MediaInput[] }
export const createPosts = (body: { campaign_id: string | null; scheduled_at: string | null; ai_generated: boolean; untrusted_source: boolean; variants: VariantBody[] }): Promise<Post[]> =>
  apiRequest(`${BASE}/posts`, { method: 'POST', ...json(body) });
export const getPost = (postId: string): Promise<Post> => apiRequest(`${BASE}/posts/${id(postId)}`, { method: 'GET' });
export const updatePost = (postId: string, body: Omit<VariantBody, 'account_id' | 'language'> & { language?: Language; campaign_id?: string | null; clear_campaign?: boolean }): Promise<Post> =>
  apiRequest(`${BASE}/posts/${id(postId)}`, { method: 'PUT', ...json(body) });
export const deletePost = (postId: string): Promise<void> => apiRequestVoid(`${BASE}/posts/${id(postId)}`, { method: 'DELETE' });
export const schedulePost = (postId: string, scheduledAt: string | null): Promise<Post> => apiRequest(`${BASE}/posts/${id(postId)}/schedule`, { method: 'POST', ...json({ scheduled_at: scheduledAt }) });
export const reschedulePost = (postId: string, scheduledAt: string): Promise<Post> => apiRequest(`${BASE}/posts/${id(postId)}/reschedule`, { method: 'POST', ...json({ scheduled_at: scheduledAt }) });
export const cancelPost = (postId: string): Promise<Post> => apiRequest(`${BASE}/posts/${id(postId)}/cancel`, { method: 'POST' });
export const publishNow = (postId: string): Promise<Post> => apiRequest(`${BASE}/posts/${id(postId)}/publish`, { method: 'POST' });
export const retryPost = (postId: string): Promise<Post> => apiRequest(`${BASE}/posts/${id(postId)}/retry`, { method: 'POST' });
export const listMedia = (query = ''): Promise<MediaFile[]> => apiRequest(`${BASE}/media${query.trim() ? `?q=${encodeURIComponent(query.trim())}` : ''}`, { method: 'GET' });

export const suggestIdeas = (body: { brief: string; product: string; feature: string; campaign_id: string | null; platforms: Platform[]; languages: Language[]; count: number; source_text: string; source_label: string }): Promise<IdeasResult> =>
  apiRequest(`${BASE}/assist/ideas`, { method: 'POST', ...json(body) });
export const suggestHashtags = (text: string, platform: Platform, language: Language): Promise<{ hashtags: string[]; generator: string; note: string | null }> =>
  apiRequest(`${BASE}/assist/hashtags`, { method: 'POST', ...json({ text, platform, language }) });

export const getSummary = (days: number, platform: Platform | '', campaignId = ''): Promise<Summary> =>
  apiRequest(`${BASE}/analytics/summary?${new URLSearchParams({ days: String(days), ...(platform ? { platform } : {}), ...(campaignId ? { campaign_id: campaignId } : {}) }).toString()}`, { method: 'GET' });
export const refreshMetrics = (days: number): Promise<{ updated: number; failures: { account: string; platform: Platform; error: { message: string; fix: string } }[] }> =>
  apiRequest(`${BASE}/analytics/refresh`, { method: 'POST', ...json({ days }) });
export const getBestTimes = (platform: Platform | ''): Promise<TimeSlot[]> => apiRequest(`${BASE}/analytics/best-times${platform ? `?platform=${platform}` : ''}`, { method: 'GET' });
export const deliverWeeklyReport = (language: Language, body: { email: boolean; whatsapp_conversation_id?: string }): Promise<DeliveryResult> =>
  apiRequest(`${BASE}/reports/weekly/deliver?language=${language}`, { method: 'POST', ...json(body) });
export const getWeeklyReport = (language: Language): Promise<WeeklyReport> => apiRequest(`${BASE}/reports/weekly?language=${language}`, { method: 'GET' });

/** A pending approval from a post or campaign in the client's shape, or null. */
export function approvalOf(value: Record<string, unknown> | null): Approval | null {
  return value ? mapApproval(value as unknown as ApiApproval) : null;
}
