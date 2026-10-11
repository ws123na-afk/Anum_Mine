// WhatsApp inbox (docs/whatsapp.md): API shapes and pure helpers for WhatsAppView.
// No browser or Vite APIs, so `pnpm test:unit` exercises them directly.
import { t, type MessageKey } from './i18n.ts';

export type Category = 'order_status' | 'price_stock' | 'complaint' | 'supplier' | 'other';
export type MessageStatus = 'received' | 'awaiting_approval' | 'sending' | 'sent' | 'delivered' | 'read' | 'failed' | 'not_sent';
export type ConversationFilter = 'all' | 'unread' | 'needs_reply' | 'awaiting_approval';
export type AccountStatus = 'unverified' | 'connected' | 'error';

export interface WaAccount {
  configured: boolean;
  can_manage: boolean;
  webhook_path: string;
  deployment_problems: string[];
  phone_number_id: string | null;
  business_account_id: string | null;
  display_phone_number: string | null;
  verified_name: string | null;
  quality_rating: string | null;
  access_token_hint: string | null;
  app_secret_set: boolean;
  verify_token_set: boolean;
  owner_test_number: string | null;
  auto_send_templates: string[];
  triage_enabled: boolean;
  status: AccountStatus | null;
  last_error: string | null;
  last_checked_at: string | null;
  last_webhook_at: string | null;
  updated_at: string | null;
  updated_by: string | null;
}

export interface WaSuggestion {
  text: string;
  category: Category;
  summary: string;
  language: string | null;
  source: 'model' | 'rules';
  provider: string | null;
  model: string | null;
  generated_at: string;
  generated_by: string;
  untrusted_messages: number;
}

export interface WaConversation {
  id: string;
  contact_id: string;
  wa_id: string;
  contact_name: string | null;
  last_message_at: string;
  last_message_preview: string;
  last_direction: 'inbound' | 'outbound';
  last_inbound_at: string | null;
  unread_count: number;
  category: Category | null;
  category_source: 'model' | 'rules' | null;
  summary: string | null;
  suggestion: WaSuggestion | null;
  needs_triage: boolean;
  window_open: boolean;
  window_closes_at: string | null;
  awaiting_approval: number;
}

export interface WaMedia { id: string; mime_type: string | null; filename: string | null; caption: string | null; voice: boolean }

export interface WaMessage {
  id: string;
  conversation_id: string;
  wamid: string | null;
  direction: 'inbound' | 'outbound';
  kind: string;
  body: string;
  media: WaMedia | null;
  template: { name: string; language: string; parameters: string[] } | null;
  status: MessageStatus;
  error: string | null;
  sent_by: string | null;
  approval_id: string | null;
  timestamp: string;
  /** The pending approval (API shape) while the message waits for one. */
  approval: Record<string, unknown> | null;
}

export interface WaThread { conversation: WaConversation; messages: WaMessage[]; has_more: boolean }

export interface WaTemplate {
  name: string;
  language: string;
  status: string;
  category: string;
  body: string;
  parameter_count: number;
  header: string | null;
  footer: string | null;
  buttons: string[];
  auto_send: boolean;
}

export interface WaSendResult { outcome: 'sent' | 'awaiting_approval' | 'failed'; message: WaMessage; approval: Record<string, unknown> | null; detail: string }

export interface AccountForm {
  phoneNumberId: string;
  businessAccountId: string;
  accessToken: string;
  appSecret: string;
  verifyToken: string;
  ownerTestNumber: string;
  triageEnabled: boolean;
  autoSend: string[];
}

export const MAX_TEXT = 4096;
export const MIN_VERIFY_TOKEN = 16;
export const PAGE = 30;
export const FILTERS: ConversationFilter[] = ['all', 'unread', 'needs_reply', 'awaiting_approval'];
export const WINDOW_HOURS = 24;

export function emptyForm(account: WaAccount | null): AccountForm {
  return {
    phoneNumberId: account?.phone_number_id ?? '',
    businessAccountId: account?.business_account_id ?? '',
    accessToken: '',
    appSecret: '',
    verifyToken: '',
    ownerTestNumber: account?.owner_test_number ?? '',
    triageEnabled: account?.triage_enabled ?? true,
    autoSend: account?.auto_send_templates ?? [],
  };
}

const DIGITS = /^[0-9]{5,20}$/;

/** The body for PUT /account, or the first problem to fix (in the current locale). */
export function accountBody(form: AccountForm, configured: boolean): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  const phone = form.phoneNumberId.trim();
  const waba = form.businessAccountId.trim();
  if (!DIGITS.test(phone)) return { ok: false, error: t('wa.setup.error.phoneId') };
  if (!DIGITS.test(waba)) return { ok: false, error: t('wa.setup.error.wabaId') };
  const missing = !configured && (!form.accessToken.trim() || !form.appSecret.trim() || !form.verifyToken.trim());
  if (missing) return { ok: false, error: t('wa.setup.error.secrets') };
  if (form.verifyToken.trim() && form.verifyToken.trim().length < MIN_VERIFY_TOKEN) return { ok: false, error: t('wa.setup.error.verifyToken', { count: MIN_VERIFY_TOKEN }) };
  const test = form.ownerTestNumber.replace(/[\s+()-]/g, '');
  if (test && !/^[0-9]{6,20}$/.test(test)) return { ok: false, error: t('wa.setup.error.testNumber') };
  const value: Record<string, unknown> = {
    phone_number_id: phone,
    business_account_id: waba,
    owner_test_number: test || null,
    triage_enabled: form.triageEnabled,
    auto_send_templates: [...new Set(form.autoSend)].sort(),
  };
  if (form.accessToken.trim()) value.access_token = form.accessToken.trim();
  if (form.appSecret.trim()) value.app_secret = form.appSecret.trim();
  if (form.verifyToken.trim()) value.verify_token = form.verifyToken.trim();
  return { ok: true, value };
}

/** A random verify token to paste into Meta's webhook settings (32 url-safe characters). */
export function randomVerifyToken(random: (n: number) => Uint8Array): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_';
  return Array.from(random(32), (byte) => alphabet[byte % alphabet.length]).join('');
}

export interface WindowState { open: boolean; hoursLeft: number; minutesLeft: number }

/** The 24-hour customer-service window: open until 24 hours after the customer's last message. */
export function windowState(conversation: Pick<WaConversation, 'window_closes_at'>, now: Date): WindowState {
  if (!conversation.window_closes_at) return { open: false, hoursLeft: 0, minutesLeft: 0 };
  const left = new Date(conversation.window_closes_at).getTime() - now.getTime();
  if (left <= 0) return { open: false, hoursLeft: 0, minutesLeft: 0 };
  return { open: true, hoursLeft: Math.floor(left / 3_600_000), minutesLeft: Math.floor((left % 3_600_000) / 60_000) };
}

export function windowLabel(conversation: Pick<WaConversation, 'window_closes_at' | 'last_inbound_at'>, now: Date): string {
  const state = windowState(conversation, now);
  if (state.open) return state.hoursLeft > 0 ? t('wa.window.openHours', { count: state.hoursLeft }) : t('wa.window.openMinutes', { count: Math.max(1, state.minutesLeft) });
  return conversation.last_inbound_at ? t('wa.window.closed') : t('wa.window.never');
}

export function categoryLabel(category: Category | null): string {
  return category ? t(`wa.category.${category}` as MessageKey) : t('wa.category.none');
}

export function statusLabel(status: MessageStatus): string {
  return t(`wa.status.${status}` as MessageKey);
}

export function statusTone(status: MessageStatus): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  if (status === 'failed' || status === 'not_sent') return 'dangerTone';
  if (status === 'awaiting_approval' || status === 'sending') return 'warning';
  if (status === 'delivered' || status === 'read' || status === 'sent') return 'success';
  return 'neutral';
}

export function filterLabel(filter: ConversationFilter): string {
  return t(`wa.filter.${filter}` as MessageKey);
}

/** The name to show for a conversation: the WhatsApp profile name, else the number. */
export function contactLabel(conversation: Pick<WaConversation, 'contact_name' | 'wa_id'>): string {
  return conversation.contact_name?.trim() || `+${conversation.wa_id}`;
}

/** Distinct `{{1}}` / `{{name}}` placeholders in a template body (same rule as the API). */
export function parameterCount(body: string): number {
  return new Set([...body.matchAll(/\{\{\s*([0-9]+|[a-z_][a-z0-9_]*)\s*\}\}/g)].map((m) => m[1])).size;
}

/** The template as the customer will read it; unfilled placeholders stay visible. */
export function fillTemplate(body: string, parameters: string[]): string {
  const order = new Map<string, number>();
  return body.replace(/\{\{\s*([0-9]+|[a-z_][a-z0-9_]*)\s*\}\}/g, (whole, key: string) => {
    const index = /^\d+$/.test(key) ? Number(key) - 1 : (order.has(key) ? order.get(key)! : order.set(key, order.size).get(key)!);
    const value = parameters[index]?.trim();
    return value ? value : whole;
  });
}

/** Templates that can be sent now (Meta approved them). */
export function sendableTemplates(templates: WaTemplate[]): WaTemplate[] {
  return templates.filter((template) => template.status === 'APPROVED');
}

export function templateKey(template: Pick<WaTemplate, 'name' | 'language'>): string {
  return `${template.name}|${template.language}`;
}

/** The text a provenance label shows under a suggestion. */
export function provenanceLabel(suggestion: WaSuggestion): string {
  if (suggestion.source === 'rules') return t('wa.draft.fromRules');
  return t('wa.draft.fromModel', { model: [suggestion.provider, suggestion.model].filter(Boolean).join(' / ') || 'model', count: suggestion.untrusted_messages });
}

/** Messages oldest first with no duplicates, after loading an older page or polling the newest. */
export function mergeMessages(current: WaMessage[], incoming: WaMessage[]): WaMessage[] {
  const byId = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort((a, b) => a.timestamp.localeCompare(b.timestamp) || a.id.localeCompare(b.id));
}

/** The webhook URL to paste into Meta: the API's public address plus the webhook path. */
export function webhookUrl(apiBase: string, path: string): string {
  return `${apiBase.replace(/\/+$/, '')}${path}`;
}

/** Inline-viewable media (images, audio, video) open in a new tab; anything else downloads. */
export function opensInline(mime: string | null | undefined): boolean {
  return /^(image\/(jpeg|png|webp|gif)|audio\/|video\/(mp4|3gpp))/.test(mime ?? '');
}
