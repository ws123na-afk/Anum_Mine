// The transaction register and account overview over the live API (docs/finance.md#register-and-account-overview).
// Read only; the server checks finance:read and returns the caller's own rows.
import type { FinanceAccountActivity, FinanceAccountsOverview, FinanceRegisterDetail, FinanceRegisterPage } from '@anum/contracts';
import { apiRequest } from './api';
import { query } from './financeApi';
import { registerQuery, type RegisterFilters } from './financeRegister';

const BASE = '/api/v1/finance';
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });

export const getRegister = (filters: RegisterFilters, limit: number, offset: number) =>
  get<FinanceRegisterPage>(`/register${query({ ...registerQuery(filters), limit, offset })}`);
export const getRegisterDetail = (transactionId: string) => get<FinanceRegisterDetail>(`/register/${encodeURIComponent(transactionId)}`);
export const getAccountsOverview = () => get<FinanceAccountsOverview>('/accounts/overview');
export const getAccountActivity = (accountId: string, days: number) =>
  get<FinanceAccountActivity>(`/accounts/${encodeURIComponent(accountId)}/activity${query({ days })}`);
