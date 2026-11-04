// iPhone SMS banking and the ledger over the live API (docs/finance-sms.md#api). Every record is the
// caller's own; the server checks finance:read / finance:manage on every route.
import type {
  FinanceJournalPage, FinanceLedgerDashboard, FinanceReconcileView, FinanceSmsBank, FinanceSmsCorrection, FinanceSmsCorrectionResult,
  FinanceSmsDevice, FinanceSmsDeviceToken, FinanceSmsIngestResult, FinanceSmsMessage, FinanceSmsMessagePage, FinanceSmsRule, FinanceSmsSelfTest,
  FinanceSmsSettings, FinanceSmsSettingsInput, FinanceSmsStatus, FinanceSmsStatusView, FinanceSmsSuggestion,
} from '@anum/contracts';
import { apiRequest, apiRequestVoid } from './api';

const BASE = '/api/v1/finance';
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown) => apiRequest<T>(`${BASE}${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) });
const id = encodeURIComponent;

export function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : '';
}

export const smsStatus = () => get<FinanceSmsStatusView>('/sms/status');
export const smsSettings = () => get<FinanceSmsSettings>('/sms/settings');
export const saveSmsSettings = (body: FinanceSmsSettingsInput) => send<FinanceSmsSettings>('PUT', '/sms/settings', body);
export const runSelfTest = () => send<FinanceSmsSelfTest>('POST', '/sms/self-test');
export const smsBanks = () => get<FinanceSmsBank[]>('/sms/banks');
export const smsDevices = () => get<FinanceSmsDevice[]>('/sms/devices');
export const createDevice = (name: string) => send<FinanceSmsDeviceToken>('POST', '/sms/devices', { name });
export const rotateDevice = (deviceId: string) => send<FinanceSmsDeviceToken>('POST', `/sms/devices/${id(deviceId)}/rotate`);
export const changeDevice = (deviceId: string, change: 'pause' | 'resume' | 'revoke') => send<FinanceSmsDevice>('POST', `/sms/devices/${id(deviceId)}/${change}`);
export const smsMessages = (status: FinanceSmsStatus | '', limit: number, offset: number) =>
  get<FinanceSmsMessagePage>(`/sms/messages${query({ status, limit, offset })}`);
export const postMessage = (messageId: string, body: FinanceSmsCorrection) => send<FinanceSmsCorrectionResult>('POST', `/sms/messages/${id(messageId)}/post`, body);
export const dismissMessage = (messageId: string) => send<FinanceSmsMessage>('POST', `/sms/messages/${id(messageId)}/dismiss`);
export const reverseMessage = (messageId: string) => send<FinanceSmsMessage>('POST', `/sms/messages/${id(messageId)}/reverse`);
export const suggestFields = (messageId: string) => send<FinanceSmsSuggestion>('POST', `/sms/messages/${id(messageId)}/suggest`);
export const pasteMessage = (sender: string, message: string) => send<FinanceSmsIngestResult>('POST', '/sms/paste', { sender, message });
export const smsRules = () => get<FinanceSmsRule[]>('/sms/rules');
export const changeRule = (ruleId: string, change: 'activate' | 'disable') => send<FinanceSmsRule>('POST', `/sms/rules/${id(ruleId)}/${change}`);
export const deleteRule = (ruleId: string) => apiRequestVoid(`${BASE}/sms/rules/${id(ruleId)}`, { method: 'DELETE' });
export const reconcilePreview = (importId: string) => get<FinanceReconcileView>(`/sms/reconcile/${id(importId)}`);
export const reconcileConfirm = (importId: string, ids: string[]) =>
  send<{ reconciled: number; removed_statement_rows: number }>('POST', `/sms/reconcile/${id(importId)}`, { statement_transaction_ids: ids });
export interface LedgerFilters { month?: string; account_id?: string; currency?: string; category?: string; kind?: string }
export const ledgerDashboard = (filters: LedgerFilters) => get<FinanceLedgerDashboard>(`/ledger/dashboard${query({ ...filters })}`);
export const ledgerEntries = (limit: number, offset: number) => get<FinanceJournalPage>(`/ledger/entries${query({ limit, offset })}`);

