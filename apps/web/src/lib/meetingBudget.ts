// Workspace-wide monthly budget for live agent meetings (S-02). Pure helpers: no browser, no network.
import type { MeetingBudget } from './liveMeeting';

export const MAX_CAP_USD = 100000;

/** Dollars for a person: cents above one dollar, four decimals below it so small spend does not read as zero. */
export function usd(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0.00';
  if (value >= 1) return (Math.floor(value * 100 + 1e-9) / 100).toFixed(2); // cents are cut, never rounded up to the limit
  return value < 0.0001 ? '0.0001' : value.toFixed(4).replace(/0{1,2}$/, '');
}

export type BudgetTone = 'ok' | 'near' | 'full';
/** Amber from 80 percent (as the model budgets), red once no turn can start (the server says when). */
export function budgetTone(budget: Pick<MeetingBudget, 'percent' | 'exceeded'>): BudgetTone {
  return budget.exceeded ? 'full' : budget.percent >= 80 ? 'near' : 'ok';
}

export type CapInput = { ok: true; value: number } | { ok: false };
/** The owner's text for the cap: whole cents from 0 to 100000. Comma or dot as the decimal mark, Arabic-Indic digits accepted. */
export function parseCapInput(raw: string): CapInput {
  const western = raw.trim().replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x660)).replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x6f0)).replace(/٫|,/g, '.');
  if (!/^\d{1,6}(\.\d{1,2})?$/.test(western)) return { ok: false };
  const value = Number(western);
  return value >= 0 && value <= MAX_CAP_USD ? { ok: true, value } : { ok: false };
}

/** Whether starting, resuming or running a turn is pointless right now (the server refuses it too). */
export function budgetBlocks(budget: MeetingBudget | null): boolean {
  return budget !== null && budget.exceeded;
}
