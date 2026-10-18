// Finance over the live API (docs/finance.md#api). Every record is the caller's own; the server
// checks finance:read / finance:manage on every route and the view only explains who may.
import type {
  FinanceAccount, FinanceAccountInput, FinanceBill, FinanceBillInput, FinanceBillSuggestion, FinanceDebt, FinanceDebtInput, FinanceFormats,
  FinanceGoal, FinanceGoalInput, FinanceHolding, FinanceHoldingInput, FinanceImport, FinanceImportInput, FinanceImportPreview, FinancePage,
  FinanceSettings, FinanceSummary, FinanceTransaction, FinanceTransactionInput, FinanceUpcomingBill,
  FinanceConnection, FinanceConnectionInput, FinanceEvaluateInput, FinanceEvaluation, FinanceFxRate, FinanceFxRateInput, FinanceGuardian,
  FinanceGuardianAlerts, FinanceGuardianChange, FinanceGuardianRules, FinanceGuardianVersion, FinancePlan, FinancePlanInput, FinanceProvider,
  FinanceSafeToSpend, FinanceSyncReport,
  FinanceExecutionStatus, FinanceIntent, FinanceIntentInput, FinanceIntentStatus, FinanceLive, FinanceLiveChangeInput, FinancePaperOrder,
  FinancePaperOrderStatus, FinanceWider, FinanceWidening, FinanceWideningInput, FinancePortfolio, FinancePortfolioInput, FinancePortfolioSummary, FinancePrice, FinancePriceInput, FinancePriceSaved,
  FinanceAgentDecision, FinanceEquity, FinanceIntelAgent, FinanceIntelOverview, FinanceStrategy, FinanceStrategyRun, FinanceStrategyState,
  FinanceIntelBarsInput, FinanceIntelBarsResult, FinanceIntelCycle, FinanceIntelInstrument, FinanceIntelInstrumentInput, FinanceIntelPhase,
  FinanceIntelRule, FinanceStrategyInput, FinanceDailyReport, FinanceDailyReportItem,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';

const BASE = '/api/v1/finance';
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown) => apiRequest<T>(`${BASE}${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) });
const remove = (path: string) => apiRequestVoid(`${BASE}${path}`, { method: 'DELETE' });
const id = encodeURIComponent;

export function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : '';
}

export interface TransactionQuery { q?: string; account_id?: string; category?: string; from?: string; to?: string; direction?: 'in' | 'out' | ''; limit: number; offset: number }

export const getSummary = (month?: string) => get<FinanceSummary>(`/summary${query({ month })}`);
export const getSettings = () => get<FinanceSettings>('/settings');
export const saveSettings = (body: Omit<FinanceSettings, 'updated_at'>) => send<FinanceSettings>('PUT', '/settings', body);
export const getFormats = () => get<FinanceFormats>('/formats');

export const listAccounts = () => get<FinanceAccount[]>('/accounts');
export const createAccount = (body: FinanceAccountInput) => send<FinanceAccount>('POST', '/accounts', body);
export const updateAccount = (accountId: string, body: Partial<FinanceAccountInput>) => send<FinanceAccount>('PATCH', `/accounts/${id(accountId)}`, body);
export const deleteAccount = (accountId: string) => remove(`/accounts/${id(accountId)}`);

export const listTransactions = (q: TransactionQuery) => get<FinancePage<FinanceTransaction>>(`/transactions${query({ ...q })}`);
export const createTransaction = (body: FinanceTransactionInput) => send<FinanceTransaction>('POST', '/transactions', body);
export const updateTransaction = (transactionId: string, body: Partial<FinanceTransactionInput>) => send<FinanceTransaction>('PATCH', `/transactions/${id(transactionId)}`, body);
export const deleteTransaction = (transactionId: string) => remove(`/transactions/${id(transactionId)}`);

export const previewImport = (body: FinanceImportInput) => send<FinanceImportPreview>('POST', '/imports/preview', body);
export const confirmImport = (body: FinanceImportInput) => send<FinanceImport>('POST', '/imports', body);
export const listImports = (limit: number, offset: number) => get<FinancePage<FinanceImport>>(`/imports${query({ limit, offset })}`);
export const deleteImport = (importId: string) => remove(`/imports/${id(importId)}`);

export const listBills = () => get<FinanceBill[]>('/bills');
export const upcomingBills = (days = 30) => get<FinanceUpcomingBill[]>(`/bills/upcoming${query({ days })}`);
export const billSuggestions = () => get<FinanceBillSuggestion[]>('/bills/suggestions');
export const createBill = (body: FinanceBillInput) => send<FinanceBill>('POST', '/bills', body);
export const updateBill = (billId: string, body: FinanceBillInput) => send<FinanceBill>('PATCH', `/bills/${id(billId)}`, body);
export const deleteBill = (billId: string) => remove(`/bills/${id(billId)}`);

export const listDebts = () => get<FinanceDebt[]>('/debts');
export const createDebt = (body: FinanceDebtInput) => send<FinanceDebt>('POST', '/debts', body);
export const updateDebt = (debtId: string, body: FinanceDebtInput) => send<FinanceDebt>('PATCH', `/debts/${id(debtId)}`, body);
export const deleteDebt = (debtId: string) => remove(`/debts/${id(debtId)}`);

export const listGoals = () => get<FinanceGoal[]>('/goals');
export const createGoal = (body: FinanceGoalInput) => send<FinanceGoal>('POST', '/goals', body);
export const updateGoal = (goalId: string, body: FinanceGoalInput) => send<FinanceGoal>('PATCH', `/goals/${id(goalId)}`, body);
export const deleteGoal = (goalId: string) => remove(`/goals/${id(goalId)}`);

export const listHoldings = () => get<FinanceHolding[]>('/holdings');
export const createHolding = (body: FinanceHoldingInput) => send<FinanceHolding>('POST', '/holdings', body);
export const updateHolding = (holdingId: string, body: FinanceHoldingInput) => send<FinanceHolding>('PATCH', `/holdings/${id(holdingId)}`, body);
export const deleteHolding = (holdingId: string) => remove(`/holdings/${id(holdingId)}`);

/** Downloads the matching transactions as CSV (formula-safe on the server). */
export async function downloadTransactions(q: Omit<TransactionQuery, 'limit' | 'offset'>): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}/transactions/export.csv${query({ ...q })}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url; link.download = 'anum-transactions.csv'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---- Stage 3: safe-to-spend ----
export const getSafeToSpend = () => get<FinanceSafeToSpend>('/safe-to-spend');
export const getPlan = () => get<FinancePlan>('/plan');
export const savePlan = (body: FinancePlanInput) => send<FinancePlan>('PUT', '/plan', body);

// ---- Stage 4: Guardian. A change applies only when confirmed with the payload hash shown. ----
export const getGuardian = () => get<FinanceGuardian>('/guardian');
export const guardianVersions = (limit: number, offset: number) => get<FinancePage<FinanceGuardianVersion>>(`/guardian/versions${query({ limit, offset })}`);
export const guardianAlerts = (month?: string) => get<FinanceGuardianAlerts>(`/guardian/alerts${query({ month })}`);
export const proposeRules = (rules: FinanceGuardianRules, reason?: string) => send<FinanceGuardianChange>('POST', '/guardian/changes', { rules, reason: reason || null });
export const confirmChange = (changeId: string, payloadHash: string) => send<FinanceGuardian>('POST', `/guardian/changes/${id(changeId)}/confirm`, { payload_hash: payloadHash });
export const rejectChange = (changeId: string) => send<FinanceGuardianChange>('POST', `/guardian/changes/${id(changeId)}/reject`);
export const evaluateAction = (body: FinanceEvaluateInput) => send<FinanceEvaluation>('POST', '/guardian/evaluate', body);

// ---- Stage 5: read-only connectors ----
export const listProviders = () => get<FinanceProvider[]>('/connectors');
export const listConnections = () => get<FinanceConnection[]>('/connections');
export const createConnection = (body: FinanceConnectionInput) => send<FinanceConnection>('POST', '/connections', body);
export const syncConnection = (connectionId: string) => send<FinanceSyncReport>('POST', `/connections/${id(connectionId)}/sync`);
export const disconnectConnection = (connectionId: string) => send<FinanceConnection>('POST', `/connections/${id(connectionId)}/disconnect`);
export const deleteConnection = (connectionId: string) => remove(`/connections/${id(connectionId)}`);
export const listRates = (limit = 100) => get<FinanceFxRate[]>(`/fx-rates${query({ limit })}`);
export const addRate = (body: FinanceFxRateInput) => send<FinanceFxRate>('POST', '/fx-rates', body);
export const deleteRate = (rateId: string) => remove(`/fx-rates/${id(rateId)}`);


// ---- Stage 6: paper and shadow trading, SIMULATED --------------------------------------------------
export const listPrices = (symbol?: string, limit = 100) => get<FinancePrice[]>(`/prices${query({ symbol, limit })}`);
export const addPrice = (body: FinancePriceInput) => send<FinancePriceSaved>('POST', '/prices', body);
export const deletePrice = (priceId: string) => remove(`/prices/${id(priceId)}`);
export const listPortfolios = () => get<FinancePortfolioSummary[]>('/paper/portfolios');
export const getPortfolio = (portfolioId: string) => get<FinancePortfolio>(`/paper/portfolios/${id(portfolioId)}`);
export const createPortfolio = (body: FinancePortfolioInput) => send<FinancePortfolio>('POST', '/paper/portfolios', body);
export const updatePortfolio = (portfolioId: string, body: Partial<FinancePortfolioInput>) => send<FinancePortfolio>('PATCH', `/paper/portfolios/${id(portfolioId)}`, body);
export const resetPortfolio = (portfolioId: string, startingCash?: { currency: string; amount: string }[]) =>
  send<FinancePortfolio>('POST', `/paper/portfolios/${id(portfolioId)}/reset`, startingCash ? { starting_cash: startingCash } : {});
export const deletePortfolio = (portfolioId: string) => remove(`/paper/portfolios/${id(portfolioId)}`);
export const checkWaiting = (portfolioId: string) => send<FinancePortfolio>('POST', `/paper/portfolios/${id(portfolioId)}/check`);
export const listOrders = (portfolioId: string, q: { status?: FinancePaperOrderStatus | ''; run?: 'current' | 'all'; limit: number; offset: number }) =>
  get<FinancePage<FinancePaperOrder>>(`/paper/portfolios/${id(portfolioId)}/orders${query({ ...q })}`);
export const cancelOrder = (orderId: string) => send<FinancePaperOrder>('POST', `/paper/orders/${id(orderId)}/cancel`, {});

// ---- Stage 7: the execution gateway ------------------------------------------------------------------
export const createIntent = (body: FinanceIntentInput) => send<FinanceIntent>('POST', '/execution/intents', body);
export const listIntents = (q: { status?: FinanceIntentStatus | ''; mode?: string; limit: number; offset: number }) =>
  get<FinancePage<FinanceIntent>>(`/execution/intents${query({ ...q })}`);
export const approveIntent = (intentId: string, payloadHash: string) => send<FinanceIntent>('POST', `/execution/intents/${id(intentId)}/approve`, { payload_hash: payloadHash });
export const rejectIntent = (intentId: string) => send<FinanceIntent>('POST', `/execution/intents/${id(intentId)}/reject`, {});
export const getExecutionStatus = () => get<FinanceExecutionStatus>('/execution/status');
export const setKillSwitch = (scope: 'person' | 'global', engaged: boolean, reason?: string) =>
  send<FinanceExecutionStatus>('PUT', '/execution/kill-switch', { scope, engaged, reason: reason || null });

// ---- Stage 8: limited live, framework only and off -------------------------------------------------------
export const getLive = () => get<FinanceLive>('/execution/live');
export const proposeLiveChange = (body: FinanceLiveChangeInput) => send<FinanceIntent>('POST', '/execution/live/changes', body);
export const optOutLive = () => send<FinanceLive>('POST', '/execution/live/opt-out', {});

// ---- Stage 9: wider live use, framework only and off ---------------------------------------------------
export const getWider = () => get<FinanceWider>('/execution/wider');
export const proposeWidening = (body: FinanceWideningInput) => send<FinanceWidening>('POST', '/execution/wider/widenings', body);
export const approveWidening = (wideningId: string, payloadHash: string) => send<FinanceWidening>('POST', `/execution/wider/widenings/${id(wideningId)}/approve`, { payload_hash: payloadHash });
export const rejectWidening = (wideningId: string) => send<FinanceWidening>('POST', `/execution/wider/widenings/${id(wideningId)}/reject`, {});
export const reviewIncidents = () => send<FinanceWider>('POST', '/execution/wider/review', {});

// ---- Trading intelligence dashboard, read only (docs/finance-trading-intel.md#dashboard) ---------------

export const getIntelOverview = () => get<FinanceIntelOverview>('/intel/overview');
export const getPortfolioEquity = (portfolioId: string, days: number) => get<FinanceEquity>(`/intel/portfolios/${id(portfolioId)}/equity${query({ days })}`);
export const listStrategies = (q: { state?: FinanceStrategyState | ''; limit: number; offset: number }) =>
  get<FinancePage<FinanceStrategy>>(`/intel/strategies${query({ ...q })}`);
export const listStrategyRuns = (strategyId: string, q: { limit: number; offset: number }) =>
  get<FinancePage<FinanceStrategyRun>>(`/intel/strategies/${id(strategyId)}/runs${query({ ...q })}`);
// Trading intelligence actions (finance/intel_control_api.py). Every result is SIMULATED; nothing here approves an order.
export const listIntelRules = () => get<FinanceIntelRule[]>('/intel/rules');
export const listInstruments = () => get<FinanceIntelInstrument[]>('/intel/instruments');
export const createInstrument = (body: FinanceIntelInstrumentInput) => send<FinanceIntelInstrument>('POST', '/intel/instruments', body);
export const importBars = (instrumentId: string, body: FinanceIntelBarsInput) => send<FinanceIntelBarsResult>('POST', `/intel/instruments/${id(instrumentId)}/bars`, body);
export const createStrategy = (body: FinanceStrategyInput) => send<FinanceStrategy>('POST', '/intel/strategies', body);
export const moveStrategy = (strategyId: string, state: FinanceStrategyState, reason: string) =>
  send<FinanceStrategy>('POST', `/intel/strategies/${id(strategyId)}/transition`, { state, reason });
export const chooseStrategyPortfolio = (strategyId: string, portfolioId: string) =>
  send<FinanceStrategy>('POST', `/intel/strategies/${id(strategyId)}/portfolio`, { portfolio_id: portfolioId });
export const evaluateStrategy = (strategyId: string, kind: 'backtest' | 'walk_forward') =>
  send<FinanceStrategyRun>('POST', `/intel/strategies/${id(strategyId)}/evaluate`, { kind });
export const runSupervisor = (phase: FinanceIntelPhase) => send<FinanceIntelCycle>('POST', '/intel/supervisor/run', { phase });
export const pauseAllStrategies = (reason: string) => send<{ paused: string[]; simulated: true }>('POST', '/intel/pause-all', { reason });
export const listAgentDecisions = (q: { agent?: FinanceIntelAgent | ''; strategy_id?: string; limit: number; offset: number }) =>
  get<FinancePage<FinanceAgentDecision>>(`/intel/decisions${query({ ...q })}`);
// Post-close daily reports (docs/finance-trading-intel.md#daily-report): read only, the caller's own.
export const listDailyReports = (q: { limit: number; offset: number }) => get<FinancePage<FinanceDailyReportItem>>(`/intel/daily-reports${query({ ...q })}`);
export const getDailyReport = (reportId: string) => get<FinanceDailyReport>(`/intel/daily-reports/${id(reportId)}`);
