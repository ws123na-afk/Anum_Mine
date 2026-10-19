// The customer portal's own client (/api/v1/portal, docs/business-portal.md). It sends only
// `Authorization: Bearer <link>`: no workspace, tenant or user headers, and it never imports the
// signed-in app's client, so nothing authenticated runs on the public page.
import type {
  PortalChange, PortalChangeSummary, PortalDecisionInput, PortalInvoice, PortalInvoiceSummary, PortalMe, PortalOffer, PortalOfferSummary, PortalTicket,
  PortalTicketInput, PortalTicketSummary,
} from '@anum/contracts';
import { ApiError, apiErrorFromResponse } from './errors';
import { getLocale } from './i18n';

const BASE = `${(import.meta.env.VITE_ANUM_API_URL as string | undefined) ?? 'http://localhost:8000'}/api/v1/portal`;
const id = (value: string) => encodeURIComponent(value);

let link: string | null = null;
/** The link lives in memory (and the tab's sessionStorage, see PortalApp); this is the only place requests read it. */
export const setPortalToken = (token: string | null) => { link = token; };

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  if (!link) throw new ApiError(401, null, null);
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init, credentials: 'omit', referrerPolicy: 'no-referrer',
      headers: { authorization: `Bearer ${link}`, 'accept-language': getLocale(), ...(init.body ? { 'content-type': 'application/json' } : {}) },
    });
  } catch { throw new ApiError(0, null, null); }
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response;
}
const get = async <T,>(path: string) => (await send(path)).json() as Promise<T>;
const post = async <T,>(path: string, body: unknown) => (await send(path, { method: 'POST', body: JSON.stringify(body) })).json() as Promise<T>;

export const getMe = () => get<PortalMe>('/me');
export const listOffers = () => get<PortalOfferSummary[]>('/offers');
export const getOffer = (offerId: string) => get<PortalOffer>(`/offers/${id(offerId)}`);
export const decideOffer = (offerId: string, body: PortalDecisionInput) => post<PortalOffer>(`/offers/${id(offerId)}/decision`, body);
export const listChanges = () => get<PortalChangeSummary[]>('/change-orders');
export const getChange = (changeId: string) => get<PortalChange>(`/change-orders/${id(changeId)}`);
export const decideChange = (changeId: string, body: PortalDecisionInput) => post<PortalChange>(`/change-orders/${id(changeId)}/decision`, body);
export const listInvoices = () => get<PortalInvoiceSummary[]>('/invoices');
export const getInvoice = (invoiceId: string) => get<PortalInvoice>(`/invoices/${id(invoiceId)}`);
export const listTickets = () => get<PortalTicketSummary[]>('/tickets');
export const getTicket = (ticketId: string) => get<PortalTicket>(`/tickets/${id(ticketId)}`);
export const createTicket = (body: PortalTicketInput) => post<PortalTicket>('/tickets', body);

export async function invoicePrintable(invoiceId: string, lang: 'en' | 'ar'): Promise<Blob> {
  return (await send(`/invoices/${id(invoiceId)}/print?lang=${lang}`)).blob();
}
