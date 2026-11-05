// Settings, Integrations, iPhone over the live API (docs/iphone-integrations.md#api). Every device and
// event is the caller's own; the server checks each integration's permission.
import type { IphoneDevice, IphoneDeviceToken, IphoneEventPage, IphoneIntegration, IphoneOverview, IphoneScope, IphoneTestResult } from '@anum/contracts';
import { apiRequest } from './api';

const BASE = '/api/v1/integrations/iphone';
const id = encodeURIComponent;
const send = <T>(method: string, path: string, body?: unknown) => apiRequest<T>(`${BASE}${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) });

export const overview = () => apiRequest<IphoneOverview>(BASE, { method: 'GET' });
export const createDevice = (name: string, scopes: IphoneScope[]) => send<IphoneDeviceToken>('POST', '/devices', { name, scopes });
export const rotateDevice = (deviceId: string) => send<IphoneDeviceToken>('POST', `/devices/${id(deviceId)}/rotate`);
export const changeDevice = (deviceId: string, change: 'pause' | 'resume' | 'revoke') => send<IphoneDevice>('POST', `/devices/${id(deviceId)}/${change}`);
export const setScopes = (deviceId: string, scopes: IphoneScope[]) => send<IphoneDevice>('POST', `/devices/${id(deviceId)}/scopes`, { scopes });
export const setSwitch = (key: string, enabled: boolean) => send<IphoneIntegration>('PUT', `/integrations/${id(key)}`, { enabled });
export const runTest = (key: string) => send<IphoneTestResult>('POST', `/integrations/${id(key)}/test`);
export const events = (key: string, offset: number, limit = 10) =>
  apiRequest<IphoneEventPage>(`${BASE}/integrations/${id(key)}/events?outcome=problem&limit=${limit}&offset=${offset}`, { method: 'GET' });
