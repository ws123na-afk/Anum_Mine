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

  async function submit() {
    if (!preview.entries.length || preview.errors.length || busy) return;
    setBusy(true); setError(''); let count = 0;
    for (const row of preview.entries) {
      const name = `Tamara | ${row.merchant}${row.installment ? ` | ${row.installment}` : ''}`.slice(0, 120);
      const body: FinanceBillInput = {
        name, amount: row.amount, currency: row.currency, cadence: 'once', next_due_on: row.date, category: 'shopping',
        source: 'manual', active: true,
        notes: [row.order ? `Order: ${row.order}` : '', row.installment ? `Installment: ${row.installment}` : '', 'CSV installment import; confirm actual payment against transaction ledger.'].filter(Boolean).join(' | '),
      };
      try { await api.createBill(body); count++; }
      catch (e) { setError(`Imported ${count} bills, then stopped at CSV row ${row.line}: ${describeError(e)}. Reopen to continue; already added rows will be skipped.`); setBusy(false); onDone(count); if (count) onClose(); return; }
    }
    setResult(`Created ${count} one-time bills. ${preview.skipped.length} rows skipped.`);
    setBusy(false); onDone(count); onClose();
  }

  return <div className="finForm">
    <p>Import scheduled unpaid installments into Bills, not Transactions. Paid, refunded and disputed rows are excluded. Review the preview before saving.</p>
    <label className="field"><span>Installment CSV</span><input type="file" accept=".csv,text/csv" onChange={(e) => void readFile(e.target.files?.[0])} /></label>
    {headers.length > 0 && <>
      <div className="cmFormGrid">
        {fields.map((field) => <label className="field" key={field}><span>{({ date: 'Due date', merchant: 'Merchant / description', amount: 'Amount', status: 'Payment status', order: 'Order ID (optional)', installment: 'Installment number (optional)', currency: 'Currency (optional)' } as Record<Field, string>)[field]}</span>
          <select aria-label={field + ' column'} value={mapping[field]} onChange={(e) => setMapping((prev) => ({ ...prev, [field]: e.target.value }))}>
            <option value="">Not mapped</option>{headers.map((h) => <option key={h} value={h}>{h}</option>)}
          </select></label>)}
        <label className="field"><span>Currency when not in CSV</span><input value={defaultCurrency} maxLength={3} onChange={(e) => setDefaultCurrency(e.target.value.toUpperCase())} /></label>
      </div>
      <p role="status">Ready: {preview.entries.length} unpaid installments. Skipped: {preview.skipped.length}. Errors: {preview.errors.length}.</p>
      {preview.errors.length > 0 && <div role="alert">{preview.errors.slice(0, 12).map((e) => <p key={e}>{e}</p>)}</div>}
      {preview.entries.length > 0 && <div className="finTableWrap"><table className="finGrid"><thead><tr><th>Date</th><th>Merchant</th><th>Amount</th><th>Order</th></tr></thead><tbody>
        {preview.entries.slice(0, 12).map((e) => <tr key={e.line}><td>{e.date}</td><td>{e.merchant}</td><td>{e.amount} {e.currency}</td><td>{e.order}</td></tr>)}
      </tbody></table>{preview.entries.length > 12 && <p>Showing first 12 of {preview.entries.length} entries.</p>}</div>}
    </>}
    {error && <p role="alert">{error}</p>}
    {result && <p role="status">{result}</p>}
    <div className="finFormActions"><button type="button" onClick={onClose}>Close</button><button type="button" disabled={busy || !preview.entries.length || preview.errors.length > 0} onClick={() => void submit()}>{busy ? 'Importing…' : `Import ${preview.entries.length} bills`}</button></div>
  </div>;
}
