// IBKR trading routes (docs/finance-live-trading.md#api). The server checks the owner, every limit and every gate.
import type {
  IbkrBuyPlanInput, IbkrConnectInput, IbkrConnection, IbkrEnvironment, IbkrEventPage, IbkrIntent, IbkrKillReport, IbkrLimits, IbkrOrder,
  IbkrOrderInput, IbkrOrderState, IbkrOverview, IbkrPage, IbkrQuote, IbkrSettings,
} from '@anum/contracts';
import { apiRequest, apiRequestVoid } from './api';

const BASE = '/api/v1/finance/ibkr';
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown) => apiRequest<T>(`${BASE}${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) });
const id = encodeURIComponent;

export const getOverview = () => get<IbkrOverview>('/overview');
export const connect = (body: IbkrConnectInput) => send<IbkrConnection>('POST', '/connections', body);
export const checkConnection = (connectionId: string, reauthenticate = false) => send<IbkrConnection>('POST', `/connections/${id(connectionId)}/check${reauthenticate ? '?reauthenticate=true' : ''}`);
export const disconnect = (connectionId: string) => send<IbkrConnection>('DELETE', `/connections/${id(connectionId)}`);
export const sync = (environment: IbkrEnvironment) => send<{ reconciliation: string }>('POST', `/sync?environment=${environment}`);
export const statements = (environment: IbkrEnvironment) => send<Record<string, unknown>>('POST', `/statements?environment=${environment}`);
export const saveLimits = (limits: IbkrLimits, version: number) => send<IbkrSettings>('PUT', '/limits', { limits, version });
export const addSymbol = (symbol: string, confirmNotScreened = false) => send<IbkrSettings>('POST', '/symbols', { symbol, confirm_not_screened: confirmNotScreened });
export const removeSymbol = (symbol: string) => send<IbkrSettings>('DELETE', `/symbols/${id(symbol)}`);
export const setFractional = (enabled: boolean) => send<IbkrSettings>('PUT', '/fractional', { enabled });
export const savePlans = (plans: IbkrBuyPlanInput[], version: number) => send<IbkrSettings>('PUT', '/plans', { plans, version });
export const proposePlans = () => send<{ proposed: IbkrIntent[]; skipped: { plan_id: string; reason: string }[] }>('POST', '/plans/propose');
export const getQuote = (symbol: string) => get<IbkrQuote>(`/quote?symbol=${id(symbol)}`);
export const createOrder = (body: IbkrOrderInput) => send<IbkrIntent>('POST', '/orders', body);
export const listOrders = (limit: number, offset: number) => get<IbkrPage<IbkrOrder>>(`/orders?limit=${limit}&offset=${offset}`);
export const approve = (intentId: string, payloadHash: string) => send<{ order: IbkrOrder }>('POST', `/intents/${id(intentId)}/approve`, { payload_hash: payloadHash });
export const reject = (intentId: string) => send<IbkrIntent>('POST', `/intents/${id(intentId)}/reject`);
export const refreshOrder = (orderId: string) => send<IbkrOrder>('POST', `/orders/${id(orderId)}/refresh`);
export const resendOrder = (orderId: string) => send<{ order: IbkrOrder }>('POST', `/orders/${id(orderId)}/send`);
export const cancelOrder = (orderId: string) => send<IbkrOrder>('POST', `/orders/${id(orderId)}/cancel`);
export const kill = (reason: string) => send<IbkrKillReport>('POST', '/kill-switch', { reason: reason || null });
export const releaseKill = () => send<IbkrSettings>('POST', '/kill-switch/release', { confirm: 'RESUME' });
export const requestLive = () => send<{ code: string; expires_at: string; cooling_off_hours: number }>('POST', '/live/request');
export const confirmLive = (code: string) => send<IbkrSettings>('POST', '/live/confirm', { code });
export const liveOff = () => send<IbkrSettings>('POST', '/live/off');
export const acceptReconciliation = (environment: IbkrEnvironment, note: string) => send<IbkrSettings>('POST', '/reconciliation/accept', { environment, note });
export const listEvents = (alerts: boolean, limit: number, offset: number) => get<IbkrEventPage>(`/events?alerts=${alerts}&limit=${limit}&offset=${offset}`);
export const markAlertsRead = () => apiRequestVoid(`${BASE}/events/read`, { method: 'POST' });

