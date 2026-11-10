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

/**
 * A rate for reading beside a balance: at most six decimals, without trailing zeros
 * ("3.7000000000" reads "3.7", "0.2722940776" reads "0.272294"). The stored rate is not changed.
 */
export function displayRate(value: string | null): string {
  if (value === null || value.trim() === '') return '-';
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!match) return value;
  const [, sign, whole, fraction = ''] = match;
  if (fraction.length <= 6) return plainRate(`${sign}${whole}${fraction ? `.${fraction}` : ''}`);
  // Round half up on the digits themselves, so a long decimal never passes through a float.
  const digits = (whole + fraction.slice(0, 6)).split('').map(Number);
  if (Number(fraction[6]) >= 5) {
    let i = digits.length - 1;
    while (i >= 0 && digits[i] === 9) { digits[i] = 0; i -= 1; }
    if (i >= 0) digits[i] += 1; else digits.unshift(1);
  }
  const text = digits.join('');
  const split = text.length - 6;
  const rounded = plainRate(`${text.slice(0, split)}.${text.slice(split)}`);
  return rounded === '0' ? '0' : `${sign}${rounded}`;
}

/** A realised FX difference: a gain, a loss, or nothing to say. */
export function differenceTone(value: string | null): 'gain' | 'loss' | 'none' {
  if (value === null) return 'none';
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return 'none';
  return n > 0 ? 'gain' : 'loss';
}
