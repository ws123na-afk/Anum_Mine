// Card FX details of one transaction (docs/finance.md#card-fx-and-fees): pure helpers, covered by test/financeFx.test.ts.
// Every figure is the API's; the web never works out a rate.
import type { FinanceTransactionFx } from '@anum/contracts';

/** Whether the drawer shows the FX section: a foreign purchase, a fee to read with it, or an own transfer in two currencies. */
export function showsFx(view: Pick<FinanceTransactionFx, 'foreign' | 'fee' | 'transfer'> | null): boolean {
  return Boolean(view && (view.foreign || view.fee || view.transfer));
}

/** The message key of the rate line: printed, from a column, worked out from the two amounts, or not stated. */
export function rateKey(view: Pick<FinanceTransactionFx, 'rate' | 'rate_kind'>): string {
  if (view.rate === null || view.rate_kind === 'not_stated') return 'ffx.rate.notStated';
  return `ffx.rate.${view.rate_kind}`;
}

/** What the separate button does: split an inline fee into its own bank fee line, or point a separate fee row at its purchase. */
export function separateAction(view: Pick<FinanceTransactionFx, 'split' | 'can_correct' | 'fee'>): 'split' | 'link' | null {
  if (!view.fee || !view.can_correct) return null;
  if (view.split === 'available') return 'split';
  if (view.split === 'link') return 'link';
  return null;
}

/** A rate as written by the server, without trailing zeros ("3.750000" reads "3.75"); never rounded further. */
export function plainRate(value: string | null): string {
  if (value === null || value === '') return '-';
  if (!value.includes('.')) return value;
  const trimmed = value.replace(/0+$/, '').replace(/\.$/, '');
  return trimmed || '0';
}

/** A realised FX difference: a gain, a loss, or nothing to say. */
export function differenceTone(value: string | null): 'gain' | 'loss' | 'none' {
  if (value === null) return 'none';
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return 'none';
  return n > 0 ? 'gain' : 'loss';
}
