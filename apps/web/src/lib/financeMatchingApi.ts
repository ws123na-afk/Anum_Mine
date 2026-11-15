// Automatic matching over the live API (docs/finance-matching.md). The server checks finance:read and finance:manage on
// every route and links only through the one links system.
import type {
  FinanceBackfillMode, FinanceBackfillPage, FinanceBackfillRun, FinanceBackfillUndo, FinanceCaptureHealth, FinanceInstallmentMatchView,
  FinanceMatchOutcome, FinanceMatchRecord, FinanceMatchRecordPage, FinanceMatchRunResult, FinanceMatchingPolicyInput, FinanceMatchingPolicyView,
} from '@anum/contracts';
import { apiRequest } from './api';

const BASE = '/api/v1/finance';
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown) =>
  apiRequest<T>(`${BASE}${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) });
const id = encodeURIComponent;

export const getPolicy = () => get<FinanceMatchingPolicyView>('/automation/matching');
export const savePolicy = (body: FinanceMatchingPolicyInput) => send<FinanceMatchingPolicyView>('PUT', '/automation/matching', body);
export const runMatching = () => send<FinanceMatchRunResult>('POST', '/matching/run');
export const listRecords = (outcome: FinanceMatchOutcome, limit: number, offset: number) =>
  get<FinanceMatchRecordPage>(`/matching/records?outcome=${id(outcome)}&limit=${limit}&offset=${offset}`);
export const decideRecord = (recordId: string, action: 'accept' | 'reject' | 'undo', version: number) =>
  send<FinanceMatchRecord>('POST', `/matching/records/${id(recordId)}/${action}`, { version });
export const installmentStatus = () => get<FinanceInstallmentMatchView>('/matching/installments');
export const listRuns = () => get<FinanceBackfillPage>('/matching/backfill?limit=5');
export const startRun = (mode: FinanceBackfillMode) => send<FinanceBackfillRun>('POST', '/matching/backfill', { mode });
export const continueRun = (runId: string) => send<FinanceBackfillRun>('POST', `/matching/backfill/${id(runId)}/continue`);
export const undoRun = (runId: string) => send<FinanceBackfillUndo>('POST', `/matching/backfill/${id(runId)}/undo`);
export const captureHealth = () => get<FinanceCaptureHealth>('/capture-health');
