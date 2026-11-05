// Mock of the planning routes (docs/finance.md#bills-and-budgets, #debts-and-goals) on top of financeApi.ts:
// bill schedule with matches, payment candidates, debt payments, the debt plan and budgets. Like the real
// API, a due date is "paid" only when a payment was matched to it.
import type { FinanceState } from './financeApi';

type Row = Record<string, unknown>;
interface Planning { plans: Row[]; alerts: Row[]; billLinks: Row[]; debtLinks: Row[]; payments: Row[] }
const store = new WeakMap<FinanceState, Planning>();
const iso = (d: Date) => d.toISOString().slice(0, 10);

function planning(state: FinanceState): Planning {
  let found = store.get(state);
  if (!found) {
    const today = new Date();
    const back = (days: number) => { const d = new Date(today); d.setDate(d.getDate() - days); return iso(d); };
    const payments = state.accounts.length ? [
      { canonical_id: 'fin_je_rent', posted_on: back(1), merchant: 'RENT TRANSFER', category: 'housing', account_id: 'fin_acct_current', amount: '6500.00', currency: 'AED', remaining: '6500.00' },
      { canonical_id: 'fin_je_card', posted_on: back(2), merchant: 'CARD PAYMENT', category: 'transfer', account_id: 'fin_acct_current', amount: '250.00', currency: 'AED', remaining: '250.00' },
    ] : [];
    found = { plans: [], alerts: [], billLinks: [], debtLinks: state.accounts.length ? [{ link_id: 'fin_link_old', debt_id: 'fin_debt_card', canonical_id: 'fin_je_old', posted_on: back(30), amount: '250.00', currency: 'AED', status: 'active' }] : [], payments };
    store.set(state, found);
  }
  return found;
}

function addMonths(day: string, n: number): string {
  const [y, m, d] = day.split('-').map(Number);
  const index = y * 12 + (m - 1) + n, year = Math.floor(index / 12), month = (index % 12) + 1;
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return `${year}-${String(month).padStart(2, '0')}-${String(Math.min(d, last)).padStart(2, '0')}`;
}

export function planningReply(state: FinanceState, method: string, sub: string, body: unknown, url: URL): { status: number; body: unknown } | null {
  const ok = (value: unknown, status = 200) => ({ status, body: value });
  const p = planning(state);
  const today = iso(new Date());
  if (sub === '/bill-schedule') {
    const from = url.searchParams.get('from') ?? today.slice(0, 8) + '01', to = url.searchParams.get('to') ?? today;
    const items: Row[] = [];
    for (const bill of state.bills) {
      for (let n = -2; n <= 14; n++) {
        const due = bill.cadence === 'once' ? (n === 0 ? String(bill.next_due_on) : '') : addMonths(String(bill.next_due_on), n);
        if (!due || due < from || due > to) continue;
        const links = p.billLinks.filter((l) => l.bill_id === bill.id && l.due_on === due);
        if (!bill.active && !links.length) continue;
        const days = Math.round((Date.parse(due) - Date.parse(today)) / 86_400_000);
        const status = links.length ? 'paid' : days < 0 ? 'overdue' : days <= 7 ? 'due_soon' : 'planned';
        items.push({ bill_id: bill.id, name: bill.name, category: bill.category, cadence: bill.cadence, account_id: bill.account_id, due_on: due, days_until: days,
          expected: bill.amount, currency: bill.currency, status, paid_amount: links.length ? String(links[0].amount) : '0.00', link_ids: links.map((l) => l.id), canonical_ids: links.map((l) => l.canonical_id) });
      }
    }
    items.sort((a, b) => String(a.due_on).localeCompare(String(b.due_on)));
    return ok({ today, start: from, end: to, items });
  }
  if (sub === '/payment-candidates') return ok({ items: p.payments.filter((c) => !url.searchParams.get('currency') || c.currency === url.searchParams.get('currency')) });
  const match = /^\/bills\/([^/]+)\/matches(?:\/([^/]+))?$/.exec(sub);
  if (match && method === 'POST') {
    const input = body as { canonical_id: string; due_on: string };
    const bill = state.bills.find((b) => b.id === decodeURIComponent(match[1]));
    const link = { id: `fin_link_${p.billLinks.length + 1}`, bill_id: bill?.id, due_on: input.due_on, canonical_id: input.canonical_id, amount: bill?.amount, currency: 'AED' };
    p.billLinks.push(link);
    return ok({ link: { ...link, dimension: 'bill', role: 'payment', target_type: 'finance_bill_due', target_id: `${bill?.id}:${input.due_on}`, status: 'active' }, replayed: false }, 201);
  }
  if (match && method === 'DELETE') { p.billLinks = p.billLinks.filter((l) => l.id !== decodeURIComponent(match[2])); return ok(null, 204); }
  if (sub === '/debt-payments') {
    const items = p.debtLinks.map((l) => ({ ...l, debt_name: state.debts.find((d) => d.id === l.debt_id)?.name ?? null, merchant: 'CARD PAYMENT', account_id: 'fin_acct_current' }));
    return ok({ items, total: items.length, limit: 10, offset: 0, paid: [] });
  }
  const debtPay = /^\/debts\/([^/]+)\/payments(?:\/([^/]+))?$/.exec(sub);
  if (debtPay && method === 'POST') {
    const input = body as { canonical_id: string };
    const pay = p.payments.find((c) => c.canonical_id === input.canonical_id);
    p.debtLinks.unshift({ link_id: `fin_link_d${p.debtLinks.length}`, debt_id: decodeURIComponent(debtPay[1]), canonical_id: input.canonical_id, posted_on: pay?.posted_on, amount: pay?.amount, currency: 'AED', status: 'active' });
    return ok({ link: { id: 'x', status: 'active' }, replayed: false }, 201);
  }
  if (debtPay && method === 'DELETE') { p.debtLinks = p.debtLinks.filter((l) => l.link_id !== decodeURIComponent(debtPay[2])); return ok(null, 204); }
  if (sub === '/debt-plan' && method === 'GET') return ok({ plans: p.plans });
  if (sub === '/debt-plan' && method === 'PUT') { p.plans = (body as { plans: Row[] }).plans; return ok({ plans: p.plans }); }
  if (sub === '/budgets/alerts' && method === 'PUT') { p.alerts = (body as { alerts: Row[] }).alerts; return ok({ alerts: p.alerts }); }
  if (sub === '/budgets') {
    const caps = (state.guardian.rules.category_caps as { category: string; currency: string; amount: string }[] | undefined) ?? [];
    const month = url.searchParams.get('month') ?? today.slice(0, 7);
    const start = `${month}-01`, endDay = new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5)), 0)).getUTCDate(), end = `${month}-${String(endDay).padStart(2, '0')}`;
    const spend = state.transactions.filter((t) => String(t.posted_on) >= start && String(t.posted_on) <= end && Number(t.amount) < 0);
    const elapsed = month === today.slice(0, 7) ? Number(today.slice(8)) : endDay;
    const budgets = caps.map((c) => {
      const spent = spend.filter((t) => t.category === c.category).reduce((s, t) => s - Number(t.amount), 0);
      const alert = p.alerts.find((a) => a.category === c.category) as { alert_percent?: number; alert_near?: boolean; alert_over?: boolean } | undefined;
      const percent = Math.floor((spent * 100) / Number(c.amount)), threshold = alert?.alert_percent ?? 80;
      const projected = month === today.slice(0, 7) ? (spent * endDay) / elapsed : null;
      return { category: c.category, currency: c.currency, cap: Number(c.amount).toFixed(2), spent: spent.toFixed(2), remaining: Math.max(0, Number(c.amount) - spent).toFixed(2), over: Math.max(0, spent - Number(c.amount)).toFixed(2),
        percent, state: spent > Number(c.amount) ? 'over' : percent >= threshold ? 'near' : 'ok', alert_percent: threshold, alert_near: alert?.alert_near ?? true, alert_over: alert?.alert_over ?? true,
        projected: projected === null ? null : projected.toFixed(2), projected_over: projected !== null && projected > Number(c.amount) ? (projected - Number(c.amount)).toFixed(2) : null };
    });
    const points: Row[] = [];
    let running = 0;
    for (let d = 1; d <= elapsed; d++) {
      const day = `${month}-${String(d).padStart(2, '0')}`;
      running += spend.filter((t) => t.posted_on === day && caps.some((c) => c.category === t.category)).reduce((s, t) => s - Number(t.amount), 0);
      points.push({ day, spent: running.toFixed(2) });
    }
    const series = caps.length ? [{ currency: 'AED', budget: caps.reduce((s, c) => s + Number(c.amount), 0).toFixed(2), spent: running.toFixed(2), points }] : [];
    return ok({ month, start, end, today, rules_version: state.guardian.version, budgets, series });
  }
  return null;
}
