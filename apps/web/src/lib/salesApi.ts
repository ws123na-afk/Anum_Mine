import { apiRequest } from './api';

export type SalesStage = 'new' | 'qualifying' | 'qualified' | 'quoted' | 'negotiating' | 'won' | 'lost' | 'disqualified';
export type SalesIntent = 'website' | 'mobile_app' | 'business_software' | 'ecommerce' | 'other';

export interface SalesLead {
  id: string;
  contact_id: string;
  company_name: string;
  contact_name: string;
  phone_e164: string | null;
  intent: SalesIntent;
  status: SalesStage;
  revision: number;
  language: 'ar' | 'en';
  requirements: string;
  budget_min: string | null;
  budget_max: string | null;
  currency: string;
  assigned_to: string | null;
  campaign_hint: string | null;
  marketing_consent: boolean;
  created_at: string;
  updated_at: string;
}

export interface SalesOverview {
  leads: number;
  qualified: number;
  quoted: number;
  won: number;
  lost: number;
  by_intent: Record<string, number>;
  scope: string;
  revenue_estimate: string | null;
}

const BASE = '/api/v1/sales';
const json = (method: string, body: unknown): RequestInit => ({method, body: JSON.stringify(body)});
const id = (value: string) => encodeURIComponent(value);

export const listSalesLeads = (status?: SalesStage): Promise<{items: SalesLead[]; count: number}> =>
  apiRequest(`${BASE}/leads?limit=100${status ? `&status=${encodeURIComponent(status)}` : ''}`, {method: 'GET'});

export const getSalesOverview = (): Promise<SalesOverview> => apiRequest(`${BASE}/overview`, {method: 'GET'});

export const updateSalesLead = (lead: SalesLead, fields: Record<string, unknown>): Promise<SalesLead> =>
  apiRequest(`${BASE}/leads/${id(lead.id)}`, json('PATCH', {expected_revision: lead.revision, ...fields}));

export const transitionSalesLead = (lead: SalesLead, status: SalesStage): Promise<SalesLead> =>
  apiRequest(`${BASE}/leads/${id(lead.id)}/transition`, json('POST', {expected_revision: lead.revision, status}));

export const createSalesQuote = (
  leadId: string, items: {description: string; amount: string}[], currency: string, terms: string
): Promise<{id: string; total: string; currency: string; status: string; approval_required: boolean}> =>
  apiRequest(`${BASE}/leads/${id(leadId)}/quotes`, json('POST', {items, currency, terms}));

export const proposeSalesFollowup = (
  leadId: string, dueAt: string, channel: string, purpose: string
): Promise<{id: string; status: string; approval_required: boolean}> =>
  apiRequest(`${BASE}/leads/${id(leadId)}/followups`, json('POST', {due_at: dueAt, channel, purpose}));

export interface SalesHistory {
  events: Array<{id:string; event:string; actor_id:string; status:string|null; occurred_at:string}>;
  quotes: Array<{id:string; status:string; currency:string; total:string; line_items:Array<{description:string;amount:string}>; terms:string; created_at:string}>;
  followups: Array<{id:string; status:string; channel:string; purpose:string; due_at:string}>;
}
export const getSalesLeadHistory = (leadId: string): Promise<SalesHistory> =>
  apiRequest(`${BASE}/leads/${id(leadId)}/history`, {method:'GET'});
