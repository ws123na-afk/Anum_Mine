// Commerce over the live API (docs/commerce.md). The server checks every permission; the view only
// hides what the caller's role cannot do and explains who can.
import type {
  Approval, CommerceAlert, CommerceConnector, CommerceConnectorCreated, CommerceConnectorInput, CommerceDashboard, CommerceListing,
  CommerceOrder, CommerceOrderDetail, CommercePage, CommerceProduct, CommerceProductInput, CommerceReturn, CommerceSettings, CommerceSetup,
  CommerceSummary, CommerceSyncReport,
} from '@anum/contracts';
import { apiRequest, apiRequestVoid, mapApproval, type ApiApproval } from './api';

const BASE = '/api/v1/commerce';
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown) => apiRequest<T>(`${BASE}${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) });
const query = (params: Record<string, string | number | null | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : '';
};
const timeZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { return undefined; } };

export const getSetup = () => get<CommerceSetup>('/setup');
export const getDashboard = (from: string, to: string, currency?: string | null, channel?: string | null) =>
  get<CommerceDashboard>(`/dashboard${query({ from, to, currency, channel, tz: timeZone() })}`);
export const getSummary = (period: string, lang: string) => get<CommerceSummary>(`/summary${query({ period, lang, tz: timeZone() })}`);

export const listConnectors = () => get<CommerceConnector[]>('/connectors');
export const createConnector = (body: CommerceConnectorInput) => send<CommerceConnectorCreated>('POST', '/connectors', body);
export const updateConnector = (id: string, body: CommerceConnectorInput) => send<CommerceConnector>('PATCH', `/connectors/${encodeURIComponent(id)}`, body);
export const deleteConnector = (id: string) => apiRequestVoid(`${BASE}/connectors/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const testConnector = (id: string) => send<{ ok: boolean; message: string; connector: CommerceConnector }>('POST', `/connectors/${encodeURIComponent(id)}/test`);
export const syncConnector = (id: string) => send<CommerceSyncReport>('POST', `/connectors/${encodeURIComponent(id)}/sync`);
export const rotateWebhookSecret = (id: string) => send<CommerceConnectorCreated>('POST', `/connectors/${encodeURIComponent(id)}/webhook-secret`);

export const listProducts = (q: string, limit: number, offset: number) => get<CommercePage<CommerceProduct>>(`/products${query({ q: q.trim(), limit, offset })}`);
export const createProduct = (body: CommerceProductInput) => send<CommerceProduct>('POST', '/products', body);
export const updateProduct = (id: string, body: CommerceProductInput) => send<CommerceProduct>('PATCH', `/products/${encodeURIComponent(id)}`, body);
export const deleteProduct = (id: string) => apiRequestVoid(`${BASE}/products/${encodeURIComponent(id)}`, { method: 'DELETE' });

export const listOrders = (params: { status?: string; channel?: string; q?: string; limit: number; offset: number }) =>
  get<CommercePage<CommerceOrder>>(`/orders${query({ ...params, q: params.q?.trim(), tz: timeZone() })}`);
export const getOrder = (id: string) => get<CommerceOrderDetail>(`/orders/${encodeURIComponent(id)}`);
export const listReturns = (days: number, limit: number, offset: number) => get<CommercePage<CommerceReturn>>(`/returns${query({ days, limit, offset })}`);

export const listAlerts = (status: string, limit: number, offset: number) => get<CommercePage<CommerceAlert>>(`/alerts${query({ status, limit, offset })}`);
export const acknowledgeAlert = (id: string) => send<CommerceAlert>('POST', `/alerts/${encodeURIComponent(id)}/acknowledge`);

export const getSettings = () => get<CommerceSettings>('/settings');
export const saveSettings = (body: CommerceSettings) => send<CommerceSettings>('PUT', '/settings', body);

/** Propose a price or stock change: the API answers with the pending approval (tool, arguments, payload hash). */
export async function proposeChange(listingId: string, kind: 'price' | 'stock', value: number): Promise<{ listing: CommerceListing; approval: Approval; task_id: string }> {
  const result = await send<{ listing: CommerceListing; approval: ApiApproval; task_id: string }>(
    'POST',
    `/listings/${encodeURIComponent(listingId)}/${kind === 'price' ? 'price-change' : 'stock-change'}`,
    kind === 'price' ? { price: value } : { quantity: value },
  );
  return { ...result, approval: mapApproval(result.approval) };
}
