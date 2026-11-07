// Order-level view of installment bills already stored in Finance.
// Never infer that a missing bill is paid. Status "paid" comes only from matched transactions.
import { useEffect, useMemo, useState } from 'react';
import type { FinanceBill, FinanceBillOccurrence } from '@anum/contracts';
import * as plan from './lib/financePlanningApi';

type OrderRow = {
  key: string; merchant: string; reference: string; currency: string;
  total: number; paid: number; pending: number; review: number;
  totalAmount: number; paidAmount: number; pendingAmount: number;
  nextDue: string | null; lastDue: string | null;
  installments: { date: string; amount: number; status: string }[];
};

function referenceOf(b: FinanceBill) {
  const match = b.notes?.match(/(?:^|\|\s*)Order:\s*([^|]+)/i);
  return match?.[1]?.trim() || '';
}
function merchantOf(b: FinanceBill) {
  const parts = b.name.split('|').map(s => s.trim());
  return parts[0]?.toLowerCase() === 'tamara' && parts[1] ? parts[1] : b.name;
}
export function summarizeInstallmentBills(bills: FinanceBill[], occurrences: FinanceBillOccurrence[]): OrderRow[] {
  const byBill = new Map(occurrences.map(o => [o.bill_id + '/' + o.due_on, o]));
  const grouped = new Map<string, OrderRow>();
  for (const bill of bills) {
    if (!bill.name.toLowerCase().startsWith('tamara |') || bill.cadence !== 'once') continue;
    const reference = referenceOf(bill), merchant = merchantOf(bill);
    const key = [bill.currency, reference || merchant.toLowerCase()].join('/');
    const row = grouped.get(key) ?? { key, merchant, reference, currency: bill.currency, total: 0,
      paid: 0, pending: 0, review: 0, totalAmount: 0, paidAmount: 0, pendingAmount: 0,
      nextDue: null, lastDue: null, installments: [] };
    const occurrence = byBill.get(bill.id + '/' + bill.next_due_on);
    // If no occurrence was returned, do not silently assume paid.
    const status = !bill.active ? 'paused' : occurrence?.status ?? 'unknown';
    const amount = Number(bill.amount);
    row.total++;
    row.totalAmount += amount;
    if (status === 'paid') { row.paid++; row.paidAmount += amount; }
    else if (status === 'review' || status === 'unknown' || status === 'paused') { row.review++; }
    else {
      row.pending++; row.pendingAmount += amount;
      if (!row.nextDue || bill.next_due_on < row.nextDue) row.nextDue = bill.next_due_on;
    }
    if (!row.lastDue || bill.next_due_on > row.lastDue) row.lastDue = bill.next_due_on;
    row.installments.push({ date: bill.next_due_on, amount, status });
    grouped.set(key, row);
  }
  return [...grouped.values()].map(r => ({ ...r, installments: r.installments.sort((a, b) => a.date.localeCompare(b.date)) }))
    .sort((a, b) => (a.nextDue ?? a.lastDue ?? '').localeCompare(b.nextDue ?? b.lastDue ?? ''));
}

const money = (amount: number, currency: string) =>
  new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount);

export function InstallmentBillsSummary({ bills, onRefresh }: { bills: FinanceBill[]; onRefresh?: () => void }) {
  const [occurrences, setOccurrences] = useState<FinanceBillOccurrence[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const relevant = useMemo(() => bills.filter(b => b.name.toLowerCase().startsWith('tamara |') && b.cadence === 'once'), [bills]);
  const signature = relevant.map(b => [b.id,b.updated_at,b.next_due_on,b.active].join(':')).sort().join(';');
  useEffect(() => {
    if (!relevant.length) { setOccurrences([]); return; }
    const years = [...new Set(relevant.map(b => b.next_due_on.slice(0, 4)))];
    let cancelled = false;
    setLoading(true); setError('');
    Promise.all(years.map(year => plan.billSchedule(year + '-01-01', year + '-12-31')))
      .then(results => { if (!cancelled) setOccurrences(results.flatMap(r => r.items)); })
      .catch(() => { if (!cancelled) setError('Installment payment statuses could not be loaded. Amounts cannot be verified until this succeeds.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [signature, version]); // Re-fetch when bill records or matched payment state changes.

  const rows = useMemo(() => summarizeInstallmentBills(relevant, occurrences), [relevant, occurrences]);
  const currencies = [...new Set(rows.map(r => r.currency))];
  const summary = currencies.map(currency => {
    const found = rows.filter(r => r.currency === currency);
    return { currency, plans: found.length, pending: found.reduce((n, r) => n + r.pendingAmount, 0),
      paid: found.reduce((n, r) => n + r.paidAmount, 0),
      nextDue: found.map(r => r.nextDue).filter((d): d is string => !!d).sort()[0] ?? null };
  });
  if (!relevant.length) return null;
  return <section className="finCard" aria-label="Tamara installment summary" style={{ marginBlock: 16 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
      <div><h2>Tamara purchase and installment summary</h2>
        <p className="muted">Grouped by merchant and order reference. Based on bills stored in ANUM. Previously paid installments excluded from the import cannot be reconstructed here.</p></div>
      <button type="button" onClick={() => { onRefresh?.(); setVersion(v => v + 1); }} disabled={loading}>Refresh payment status</button>
    </div>
    {loading && <p role="status">Checking matched payments…</p>}
    {error && <p role="alert">{error}</p>}
    {!loading && !error && <>
      <div className="finTableWrap"><table className="finGrid">
        <thead><tr><th>Currency</th><th>Orders</th><th>Matched paid</th><th>Still scheduled</th><th>Next payment</th></tr></thead>
        <tbody>{summary.map(r => <tr key={r.currency}><td>{r.currency}</td><td>{r.plans}</td><td>{money(r.paid, r.currency)}</td>
          <td>{money(r.pending, r.currency)}</td><td>{r.nextDue ?? 'None'}</td></tr>)}</tbody>
      </table></div>
      <div className="finTableWrap"><table className="finGrid">
        <thead><tr><th>Purchase</th><th>Installments in ANUM</th><th>Matched paid</th><th>Pending</th><th>Remaining amount</th><th>Next due</th><th>Final due</th><th>Details</th></tr></thead>
        <tbody>{rows.map(r => <tr key={r.key}>
          <td><strong>{r.merchant}</strong>{r.reference && <small style={{ display: 'block' }}>{r.reference}</small>}</td>
          <td>{r.total}</td><td>{r.paid}</td><td>{r.pending}{r.review > 0 ? ` (review ${r.review})` : ''}</td>
          <td>{money(r.pendingAmount, r.currency)}</td><td>{r.nextDue ?? 'None'}</td><td>{r.lastDue ?? 'None'}</td>
          <td><button type="button" aria-expanded={expanded === r.key} onClick={() => setExpanded(expanded === r.key ? null : r.key)}>View</button></td>
        </tr>).flatMap((row, i) => expanded === rows[i].key ? [row, <tr key={rows[i].key + '-details'}><td colSpan={8}>
          <ul>{rows[i].installments.map(p => <li key={p.date}>{p.date}: {money(p.amount, rows[i].currency)} ({p.status})</li>)}</ul>
        </td></tr>] : [row])}</tbody>
      </table></div>
      <p className="muted">Paid means matched to a real transaction in ANUM. Unmatched historical payments and refunds must be reconciled separately. No payment is made by this screen.</p>
    </>}
  </section>;
}
