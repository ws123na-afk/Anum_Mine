// Symbol research routes (docs/finance-research.md#api). The server gathers every figure, checks every
// AI summary number against the data and applies the owner's limits; this client only asks.
import type { BacktestInput, BacktestResult, IbkrBuyPlan, ResearchPortfolio, ResearchReport, ResearchSummary, ResearchSymbols } from '@anum/contracts';
import { apiRequest } from './api';

const BASE = '/api/v1/finance/research';
const id = encodeURIComponent;
const send = <T>(method: string, path: string, body?: unknown) => apiRequest<T>(`${BASE}${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) });

export const listSymbols = () => send<ResearchSymbols>('GET', '/symbols');
export const getPortfolio = () => send<ResearchPortfolio>('GET', '/portfolio');
export const getResearch = (symbol: string, refresh = false) => send<ResearchReport>('GET', `/symbols/${id(symbol)}${refresh ? '?refresh=true' : ''}`);
export const writeSummary = (symbol: string) => send<ResearchSummary>('POST', `/symbols/${id(symbol)}/summary`);
export const runBacktest = (body: BacktestInput) => send<BacktestResult>('POST', '/backtests', body);
export const paperRun = (symbol: string, amount: string, dayOfMonth: number) =>
  send<{ plan: IbkrBuyPlan; mode: 'paper' }>('POST', '/paper-run', { symbol, amount, day_of_month: dayOfMonth });
