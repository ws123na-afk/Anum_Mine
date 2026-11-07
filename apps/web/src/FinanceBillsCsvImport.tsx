import { useMemo, useState } from 'react';
import type { FinanceBill, FinanceBillInput } from '@anum/contracts';
import * as api from './lib/financeApi';
import { describeError } from './lib/errors';

type Entry = { line: number; date: string; merchant: string; amount: string; order: string; installment: string; status: string; currency: string };
const aliases = {
  date: ['due date', 'due_date', 'due_on', 'payment date', 'date'],
  merchant: ['merchant', 'description', 'bill', 'name', 'payee'],
  amount: ['amount (sar)', 'amount sar', 'amount', 'installment amount', 'expected amount'],
  status: ['status', 'reconciliation status', 'payment status', 'installment status'],
  order: ['order number', 'order id', 'order_number', 'order_id', 'tamara order id', 'reference'],
  installment: ['installment number', 'installment #', 'installment', 'instalment', 'payment number'],
  currency: ['currency', 'ccy'],
};
type Field = keyof typeof aliases;
const fields: Field[] = ['date', 'merchant', 'amount', 'status', 'order', 'installment', 'currency'];
const normalize = (s: string) => s.trim().toLowerCase().replace(/[_-]+/g, ' ').replace(/\s+/g, ' ');

// RFC 4180-style quoted fields, escaped quotes, and CRLF; no third-party parser.
export function parseBillCsv(source: string): string[][] {
  const rows: string[][] = []; let row: string[] = [], cell = '', quoted = false;
  const data = source.replace(/^\uFEFF/, '');
  for (let i = 0; i < data.length; i++) {
    const char = data[i];
    if (char === '"') {
      if (quoted && data[i + 1] === '"') { cell += '"'; i++; }
      else quoted = !quoted;
    } else if (char === ',' && !quoted) { row.push(cell); cell = ''; }
    else if ((char === '\n' || char === '\r') && !quoted) {
      if (char === '\r' && data[i + 1] === '\n') i++;
      row.push(cell); if (row.some((v) => v.trim())) rows.push(row);
      row = []; cell = '';
    } else cell += char;
  }
  if (quoted) throw new Error('CSV has an unclosed quoted field.');
  row.push(cell); if (row.some((v) => v.trim())) rows.push(row);
  return rows;
}

const isIso = (s: string) => /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s + 'T12:00:00Z')) && new Date(s + 'T12:00:00Z').toISOString().slice(0, 10) === s;
const decimal = (s: string) => {
  const raw = s.trim().replace(/,/g, '');
  if (!/^\d+(?:\.\d{1,2})?$/.test(raw) || Number(raw) <= 0) return null;
  return Number(raw).toFixed(2);
};
const pending = (s: string) => /^(pending|unpaid|due|planned|scheduled|overdue|due soon|due_soon)$/i.test(s.trim());
const excluded = (s: string) => /^(paid|completed|refunded|refund|cancelled|canceled|void|disputed|review|needs review)$/i.test(s.trim()) || /refund|review|disput|cancel/i.test(s);
export function importBillKey(date: string, name: string, amount: string, currency: string) {
  return [date, normalize(name), Number(amount).toFixed(2), currency.toUpperCase()].join('|');
}


// A source-file overview includes paid entries that are deliberately not imported as bills.
export function summarizeImportedCsv(headers: string[], data: string[][], mapping: Record<Field, string>, fallbackCurrency: string) {
  const index = (field: Field) => headers.indexOf(mapping[field]);
  const read = (r: string[], field: Field) => index(field) >= 0 ? (r[index(field)] ?? '').trim() : '';
  const orders = new Map<string, { merchant: string; reference: string; currency: string; paid: number; pending: number; review: number; paidAmount: number; pendingAmount: number; nextDue: string | null; finalDue: string | null }>();
  if (!mapping.date || !mapping.merchant || !mapping.amount || !mapping.status) return [];
  for (const r of data) {
    const merchant = read(r, 'merchant'), reference = read(r, 'order');
    const amount = decimal(read(r, 'amount')), due = read(r, 'date');
    const currency = (read(r, 'currency') || fallbackCurrency).toUpperCase();
    if (!merchant || !amount || !isIso(due)) continue;
    const key = [currency, reference || merchant.toLowerCase()].join('|');
    const item = orders.get(key) ?? { merchant, reference, currency, paid: 0, pending: 0, review: 0, paidAmount: 0, pendingAmount: 0, nextDue: null, finalDue: null };
    const status = read(r, 'status');
    if (/^paid$/i.test(status)) { item.paid++; item.paidAmount += Number(amount); }
    else if (pending(status)) {
      item.pending++; item.pendingAmount += Number(amount);
      if (!item.nextDue || due < item.nextDue) item.nextDue = due;
    } else item.review++;
    if (!item.finalDue || due > item.finalDue) item.finalDue = due;
    orders.set(key, item);
  }
  return [...orders.values()].sort((a, b) => (a.nextDue ?? a.finalDue ?? '').localeCompare(b.nextDue ?? b.finalDue ?? ''));
}

export function BillsCsvImport({ existing, onDone, onClose }: { existing: FinanceBill[]; onDone: (count: number) => void; onClose: () => void }) {
  const [headers, setHeaders] = useState<string[]>([]);
  const [data, setData] = useState<string[][]>([]);
  const [mapping, setMapping] = useState<Record<Field, string>>({ date: '', merchant: '', amount: '', status: '', order: '', installment: '', currency: '' });
  const [defaultCurrency, setDefaultCurrency] = useState('SAR');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState('');

  async function readFile(file: File | undefined) {
    if (!file) return;
    setError(''); setResult('');
    if (file.size > 1024 * 1024) { setError('Maximum CSV size is 1 MB.'); return; }
    try {
      const parsed = parseBillCsv(await file.text());
      if (parsed.length < 2) throw new Error('CSV needs a header row and at least one installment.');
      if (parsed.length > 501) throw new Error('Maximum 500 installment rows per import.');
      const next = parsed[0].map((h, i) => h.trim() || `Column ${i + 1}`);
      const pick = (key: Field) => next.find((h) => aliases[key].some((a) => normalize(a) === normalize(h))) ?? '';
      setHeaders(next); setData(parsed.slice(1));
      setMapping({ date: pick('date'), merchant: pick('merchant'), amount: pick('amount'), status: pick('status'), order: pick('order'), installment: pick('installment'), currency: pick('currency') });
    } catch (e) { setHeaders([]); setData([]); setError(describeError(e)); }
  }

  const preview = useMemo(() => {
    const entries: Entry[] = [], errors: string[] = [], skipped: string[] = [];
    if (!headers.length) return { entries, errors, skipped };
    if (!mapping.date || !mapping.merchant || !mapping.amount || !mapping.status) {
      errors.push('Map Due date, Merchant, Amount and Status before importing. Status is required to prevent importing paid installments.');
      return { entries, errors, skipped };
    }
    const read = (r: string[], key: Field) => mapping[key] ? (r[headers.indexOf(mapping[key])] ?? '').trim() : '';
    const seen = new Set(existing.map((b) => importBillKey(b.next_due_on, b.name, b.amount, b.currency)));
    for (let i = 0; i < data.length; i++) {
      const r = data[i], date = read(r, 'date'), merchant = read(r, 'merchant'), amount = decimal(read(r, 'amount'));
      const status = read(r, 'status'), order = read(r, 'order'), installment = read(r, 'installment');
      const currency = (read(r, 'currency') || defaultCurrency).toUpperCase();
      if (excluded(status)) { skipped.push(`Row ${i + 2}: ${status}`); continue; }
      if (!pending(status)) { errors.push(`Row ${i + 2}: unknown status "${status}".`); continue; }
      if (!isIso(date) || !merchant || !amount || !/^[A-Z]{3}$/.test(currency)) { errors.push(`Row ${i + 2}: invalid date, merchant, amount or currency.`); continue; }
      const name = `Tamara | ${merchant}${installment ? ` | ${installment}` : ''}`.slice(0, 120);
      const key = importBillKey(date, name, amount, currency);
      // Also recognise bills imported with the same stable order reference in notes.
      const byReference = !!order && existing.some((b) => b.next_due_on === date && b.notes?.includes(`Order: ${order}`) && Number(b.amount) === Number(amount) && b.currency === currency);
      if (seen.has(key) || byReference) { skipped.push(`Row ${i + 2}: already present`); continue; }
      seen.add(key);
      entries.push({ line: i + 2, date, merchant, amount, order, installment, status, currency });
    }
    return { entries, errors, skipped };
  }, [headers, data, mapping, existing, defaultCurrency]);

  const orderSummary = useMemo(() => summarizeImportedCsv(headers, data, mapping, defaultCurrency), [headers, data, mapping, defaultCurrency]);

  // Repeat imports are a sync: never duplicate an order installment or alter real payment matches.
  // Keep source history on existing bills so older imports gain the order summary metadata.
  const reconciliation = useMemo(() => {
    const updates: { bill: FinanceBill; notes: string }[] = [];
    const conflicts: string[] = [];
    const orderFor = (order: string, merchant: string, currency: string) =>
      orderSummary.find(s => s.currency === currency && (order ? s.reference === order : s.merchant === merchant));
    const get = (r: string[], field: Field) => mapping[field] ? (r[headers.indexOf(mapping[field])] ?? '').trim() : '';
    const matched = new Set<string>();
    for (let i = 0; i < data.length; i++) {
      const cells = data[i], order = get(cells, 'order'), merchant = get(cells, 'merchant');
      const date = get(cells, 'date'), installment = get(cells, 'installment');
      const amount = decimal(get(cells, 'amount')), currency = (get(cells, 'currency') || defaultCurrency).toUpperCase();
      const status = get(cells, 'status');
      if (!pending(status) || !amount || !isIso(date)) continue;
      const byOrder = order && existing.find(b => b.name.startsWith('Tamara |') && b.currency === currency &&
        b.notes?.includes(`Order: ${order}`) && (installment ? b.notes?.includes(`Installment: ${installment}`) : b.next_due_on === date));
      const byLegacy = existing.find(b => b.name.startsWith('Tamara |') && b.currency === currency &&
        b.next_due_on === date && Number(b.amount) === Number(amount) && b.name.includes(`| ${merchant}`));
      const bill = byOrder || byLegacy;
      if (!bill || matched.has(bill.id)) continue;
      matched.add(bill.id);
      if (bill.next_due_on !== date || Number(bill.amount) !== Number(amount)) {
        conflicts.push(`Row ${i + 2}: ${merchant} ${order || ''} changed amount or due date. Review this bill manually; existing financial terms were preserved.`);
        continue;
      }
      const summary = orderFor(order, merchant, currency);
      if (!summary) continue;
      const history = `CSV Paid Count: ${summary.paid} | CSV Paid Amount: ${summary.paidAmount.toFixed(2)} | CSV Scheduled Count: ${summary.pending} | CSV Review Count: ${summary.review} | CSV Total: ${(summary.paidAmount + summary.pendingAmount).toFixed(2)}`;
      const original = (bill.notes ?? '').split(' | ').filter(t => !/^CSV (?:Paid|Scheduled|Review|Total):|^CSV installment import;|^CSV Paid Count:|^CSV Paid Amount:|^CSV Scheduled Count:|^CSV Review Count:/.test(t));
      const preserved = original.filter(t => t && t !== 'CSV installment import; historical paid statuses are unverified until matched in the transaction ledger.');
      if (order && !preserved.some(t => t.startsWith('Order: '))) preserved.push(`Order: ${order}`);
      if (installment && !preserved.some(t => t.startsWith('Installment: '))) preserved.push(`Installment: ${installment}`);
      const notes = [...preserved, history].join(' | ').slice(0, 1000);
      if (notes !== (bill.notes ?? '')) updates.push({ bill, notes });
    }
    return { updates, conflicts };
  }, [headers, data, mapping, existing, orderSummary, defaultCurrency]);

  async function submit() {
    if (busy || preview.errors.length || reconciliation.conflicts.length || (!preview.entries.length && !reconciliation.updates.length)) return;
    setBusy(true); setError('');
    let created = 0, updated = 0;
    // Update existing metadata first. Do not touch bill amount, due date, status, or matched payments.
    for (const { bill, notes } of reconciliation.updates) {
      try {
        await api.updateBill(bill.id, { name: bill.name, amount: bill.amount, currency: bill.currency,
          cadence: bill.cadence, next_due_on: bill.next_due_on, category: bill.category,
          account_id: bill.account_id, source: bill.source, active: bill.active, notes });
        updated++;
      } catch (e) {
        setError(`Synchronized ${updated} existing records, then stopped: ${describeError(e)}. Reopen this file and retry. The updated records will be recognized.`);
        setBusy(false); onDone(updated); return;
      }
    }
    for (const row of preview.entries) {
      const name = `Tamara | ${row.merchant}${row.installment ? ` | ${row.installment}` : ''}`.slice(0, 120);
      const summary = orderSummary.find(s => s.currency === row.currency && (row.order ? s.reference === row.order : s.merchant === row.merchant));
      const history = summary
        ? `CSV Paid Count: ${summary.paid} | CSV Paid Amount: ${summary.paidAmount.toFixed(2)} | CSV Scheduled Count: ${summary.pending} | CSV Review Count: ${summary.review} | CSV Total: ${(summary.paidAmount + summary.pendingAmount).toFixed(2)}` : '';
      const body: FinanceBillInput = {
        name, amount: row.amount, currency: row.currency, cadence: 'once', next_due_on: row.date, category: 'shopping',
        source: 'manual', active: true,
        notes: [row.order ? `Order: ${row.order}` : '', row.installment ? `Installment: ${row.installment}` : '', history,
          'CSV installment import; historical paid statuses are unverified until matched in the transaction ledger.'].filter(Boolean).join(' | ').slice(0, 1000),
      };
      try { await api.createBill(body); created++; }
      catch (e) {
        setError(`Updated ${updated}, created ${created}; stopped at row ${row.line}: ${describeError(e)}. Reopen and retry. Existing rows will not be duplicated.`);
        setBusy(false); onDone(created + updated); return;
      }
    }
    setResult(`Synchronized ${updated} existing bills and created ${created} new bills. ${preview.skipped.length} paid, refund-review or duplicate rows skipped.`);
    setBusy(false); onDone(created + updated); onClose();
  }

  return <div className="finForm">
    <p>Synchronize a full installment statement with existing Bills. The upload updates missing order history and creates new unpaid installments; real payment matches are never overwritten. Paid, refunded and disputed rows are excluded from payable bills.</p>
    <label className="field"><span>Installment CSV</span><input type="file" accept=".csv,text/csv" onChange={(e) => void readFile(e.target.files?.[0])} /></label>
    {headers.length > 0 && <>
      <div className="cmFormGrid">
        {fields.map((field) => <label className="field" key={field}><span>{({ date: 'Due date', merchant: 'Merchant / description', amount: 'Amount', status: 'Payment status', order: 'Order ID (optional)', installment: 'Installment number (optional)', currency: 'Currency (optional)' } as Record<Field, string>)[field]}</span>
          <select aria-label={field + ' column'} value={mapping[field]} onChange={(e) => setMapping((prev) => ({ ...prev, [field]: e.target.value }))}>
            <option value="">Not mapped</option>{headers.map((h) => <option key={h} value={h}>{h}</option>)}
          </select></label>)}
        <label className="field"><span>Currency when not in CSV</span><input value={defaultCurrency} maxLength={3} onChange={(e) => setDefaultCurrency(e.target.value.toUpperCase())} /></label>
      </div>
      {orderSummary.length > 0 && <section aria-label="Imported order payment summary">
        <h3>Purchase summary from this CSV</h3>
        <p>Includes paid installments from the file, even though only unpaid rows will become Bills. Review/refund items are not treated as payable.</p>
        <div className="finTableWrap"><table className="finGrid">
          <thead><tr><th>Purchase</th><th>Paid</th><th>Pending</th><th>To review</th><th>Paid amount</th><th>Remaining</th><th>Next due</th><th>Last due</th></tr></thead>
          <tbody>{orderSummary.map(r => <tr key={r.currency + '/' + (r.reference || r.merchant)}>
            <td>{r.merchant}{r.reference && <small style={{ display: 'block' }}>{r.reference}</small>}</td>
            <td>{r.paid}</td><td>{r.pending}</td><td>{r.review}</td>
            <td>{r.paidAmount.toFixed(2)} {r.currency}</td><td>{r.pendingAmount.toFixed(2)} {r.currency}</td>
            <td>{r.nextDue ?? 'None'}</td><td>{r.finalDue ?? 'None'}</td>
          </tr>)}</tbody>
        </table></div>
      </section>}
      <p role="status">New: {preview.entries.length}. Existing to update: {reconciliation.updates.length}. Skipped: {preview.skipped.length}. Errors: {preview.errors.length}. Conflicts: {reconciliation.conflicts.length}.</p>
      {reconciliation.conflicts.length > 0 && <div role="alert"><strong>Conflicting payment terms, import blocked:</strong>{reconciliation.conflicts.map(e => <p key={e}>{e}</p>)}</div>}
      {preview.errors.length > 0 && <div role="alert">{preview.errors.slice(0, 12).map((e) => <p key={e}>{e}</p>)}</div>}
      {preview.entries.length > 0 && <div className="finTableWrap"><table className="finGrid"><thead><tr><th>Date</th><th>Merchant</th><th>Amount</th><th>Order</th></tr></thead><tbody>
        {preview.entries.slice(0, 12).map((e) => <tr key={e.line}><td>{e.date}</td><td>{e.merchant}</td><td>{e.amount} {e.currency}</td><td>{e.order}</td></tr>)}
      </tbody></table>{preview.entries.length > 12 && <p>Showing first 12 of {preview.entries.length} entries.</p>}</div>}
    </>}
    {error && <p role="alert">{error}</p>}
    {result && <p role="status">{result}</p>}
    <div className="finFormActions"><button type="button" onClick={onClose}>Close</button><button type="button" disabled={busy || (!preview.entries.length && !reconciliation.updates.length) || preview.errors.length > 0 || reconciliation.conflicts.length > 0} onClick={() => void submit()}>{busy ? 'Synchronizing…' : `Sync ${preview.entries.length} new + ${reconciliation.updates.length} existing bills`}</button></div>
  </div>;
}
