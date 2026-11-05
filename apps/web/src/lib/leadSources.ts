// One lead list (docs/business.md#whatsapp-leads): pure helpers for the WhatsApp leads waiting to join
// the Business list and for the links back to the WhatsApp conversation and its sales tools.
import type { WhatsAppWaitingLead } from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

const SAFE_ID = /^[\w-]{1,80}$/;
export const SALES_STAGES = ['new', 'qualifying', 'qualified', 'quoted', 'negotiating', 'won', 'lost', 'disqualified'] as const;

/** Who the enquiry is from: company, then person, then phone; never invented. */
export function waitingName(item: Pick<WhatsAppWaitingLead, 'company_name' | 'contact_name' | 'phone'>): string {
  return item.company_name.trim() || item.contact_name.trim() || item.phone || t('bizw.unknown');
}

export function stageText(stage: string): string {
  return (SALES_STAGES as readonly string[]).includes(stage) ? t(`bizw.stageName.${stage}` as MessageKey) : stage;
}

/** The WhatsApp conversation a lead came from (`#whatsapp/<conversation id>`). */
export function conversationHref(conversationId: string | null | undefined): string {
  return conversationId && SAFE_ID.test(conversationId) ? `#whatsapp/${encodeURIComponent(conversationId)}` : '#whatsapp';
}

/** Quotes, follow-ups and consent of the WhatsApp row a lead was mapped from (`#whatsapp/sales/<id>`). */
export function salesToolsHref(salesLeadId: string | null | undefined): string {
  return salesLeadId && SAFE_ID.test(salesLeadId) ? `#whatsapp/sales/${encodeURIComponent(salesLeadId)}` : '#whatsapp';
}

/** What a WhatsApp link opens: a conversation, the sales tools of one lead, or the WhatsApp page. */
export function whatsappTarget(hash: string): { conversationId: string | null; salesLeadId: string | null } {
  const id = hash.replace(/^#/, '');
  const read = (value: string) => { try { const v = decodeURIComponent(value); return SAFE_ID.test(v) ? v : null; } catch { return null; } };
  if (id.startsWith('whatsapp/sales/')) return { conversationId: null, salesLeadId: read(id.slice('whatsapp/sales/'.length)) };
  if (id.startsWith('whatsapp/')) return { conversationId: read(id.slice('whatsapp/'.length)), salesLeadId: null };
  return { conversationId: null, salesLeadId: null };
}
