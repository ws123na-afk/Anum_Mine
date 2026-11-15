// The mocked API for budgets, the category tree, learned merchants and recurring payments
// (docs/finance.md#budgets-and-planning, #category-ontology, #learned-merchants, #recurring-payments). Hooked into
// financeReply; SYNTHETIC figures only. State lives beside the spec's FinanceState (a WeakMap), so specs need no setup.
type Reply = { status: number; body: unknown } | null;
interface BudgetsState {
  budgets: Record<string, unknown>[];
  mappings: Record<string, unknown>[];
  decided: Set<string>;
  next: number;
}
interface Owner { bills: Record<string, unknown>[]; transactions: unknown[] }

const states = new WeakMap<object, BudgetsState>();
const now = new Date();
const iso = (d: Date) => d.toISOString().slice(0, 10);
const day = (offset: number) => { const d = new Date(now); d.setDate(d.getDate() + offset); return iso(d); };
const monthStart = () => iso(new Date(now.getFullYear(), now.getMonth(), 1));
const monthEnd = () => iso(new Date(now.getFullYear(), now.getMonth() + 1, 0));
const stamp = now.toISOString();
const FOUND_KEY = 'subscription:fin_acct_current:NETFLIX.COM:AED:monthly';

function stateOf(owner: object): BudgetsState {
  let found = states.get(owner);
  if (!found) {
    found = { budgets: [], mappings: [{ merchant: 'quokka kitchen', direction: 'out', category: 'dining', subcategory_id: 'sys:restaurants', transaction_type: null,
      confirmations: 2, enabled: true, example: 'POS QUOKKA KITCHEN TESTCITY', source_transaction_id: 'fin_txn_1', created_at: stamp, updated_at: stamp }],
    decided: new Set(), next: 1 };
    states.set(owner, found);
  }
  return found;
}

const TREE = {
  month: null, spend: [], group_totals: { food_drinks: { AED: '420.00' } },
  groups: [
    { id: 'food_drinks', name: 'Food and drinks', name_ar: 'الطعام والمشروبات', debt_types: [], categories: [
      { id: 'groceries', name: 'Groceries', name_ar: 'البقالة', spending: true, essential: true, subcategories: [
        { id: 'sys:supermarket', name: 'Supermarket', name_ar: 'سوبرماركت', built_in: true, archived: false, essential: true },
        { id: 'sys:vegetables', name: 'Vegetables', name_ar: 'خضار', built_in: true, archived: false, essential: true }] },
      { id: 'dining', name: 'Eating out', name_ar: 'المطاعم', spending: true, essential: false, subcategories: [
        { id: 'sys:restaurants', name: 'Restaurants', name_ar: 'مطاعم', built_in: true, archived: false, essential: false },
        { id: 'sys:delivery', name: 'Food delivery', name_ar: 'توصيل الطعام', built_in: true, archived: false, essential: false }] }] },
    { id: 'housing', name: 'Housing', name_ar: 'السكن', debt_types: [], categories: [
      { id: 'housing', name: 'Housing', name_ar: 'السكن', spending: true, essential: true, subcategories: [
        { id: 'sys:rent', name: 'Apartment rent', name_ar: 'إيجار الشقة', built_in: true, archived: false, essential: true }] }] },
    { id: 'charity', name: 'Religious and charitable', name_ar: 'الديني والخيري', debt_types: [], categories: [
      { id: 'charity', name: 'Religious and charitable', name_ar: 'الديني والخيري', spending: true, essential: false, subcategories: [
        { id: 'sys:zakat', name: 'Zakat', name_ar: 'الزكاة', built_in: true, archived: false, essential: true }] }] },
    { id: 'debt', name: 'Debt flows', name_ar: 'تدفقات الديون', categories: [], debt_types: ['card_payment', 'loan_principal'] },
  ],
};

// Rollover (docs/finance.md#budget-rollover): a carrying budget brought 300.00 left from last month, capped by its cap.
function rollover(b: Record<string, unknown>, amount: number) {
  const mode = String(b.rollover ?? 'none'), overspend = String(b.overspend ?? 'absorb');
  const cap = mode === 'capped' ? Number(b.rollover_cap) : null;
  const carries = mode !== 'none' || overspend !== 'absorb';
  const carried = mode === 'none' ? 0 : Math.min(300, cap ?? 300);
  const money = (n: number) => n.toFixed(2);
  const previous = { start: '2026-10-01', end: '2026-10-31', currency: 'AED', amount: money(amount), carried_in: '0.00', available: money(amount), spent: money(amount - 300),
    rows: 4, left: '300.00', carried_out: money(carried), rollover: mode, cap: cap === null ? null : money(cap), overspend, restarted: false };
  const current = { ...previous, start: monthStart(), end: monthEnd(), carried_in: money(carried), available: money(amount + carried), spent: '420.00',
    left: money(amount + carried - 420), carried_out: null };
  return { rollover: mode, rollover_cap: cap === null ? null : money(cap), overspend, carried_in: money(carries ? carried : 0),
    available: money(amount + (carries ? carried : 0)), chain: carries ? [previous, current] : [], chain_periods: carries ? 2 : 0,
    starts_on: b.starts_on ?? null, ends_on: b.ends_on ?? null };
}

function line(b: Record<string, unknown>): Record<string, unknown> {
  const amount = Number(b.amount);
  const spent = (b.categories as string[]).includes('housing') ? 6500 : 420;
  const roll = rollover(b, amount);
  const available = Number(roll.available);
  const percent = Math.floor((spent * 100) / available);
  const risk = spent > available ? 'over' : spent * 100 >= available * Number(b.alert_percent) ? 'watch' : 'ok';
  return {
    ...b, period_start: b.period === 'custom' ? b.starts_on : monthStart(), period_end: b.period === 'custom' ? b.ends_on : monthEnd(), days_left: 10,
    allocation: b.allocation ?? ((b.categories as string[]).includes('housing') ? 'housing' : 'food'), essential: b.essential ?? true,
    spent: spent.toFixed(2), remaining: Math.max(available - spent, 0).toFixed(2), over: Math.max(spent - available, 0).toFixed(2), percent,
    coming: '0.00', coming_items: [], forecast: (spent * 1.4).toFixed(2), risk, rows: 3, basis: 'spending', goal_saved: null, goal_target: null,
    ...roll,
  };
}

export function budgetsReply(owner: Owner, method: string, sub: string, url: URL, body: unknown): Reply {
  const ok = (value: unknown, status = 200) => ({ status, body: value });
  const s = stateOf(owner);
  if (sub === '/categories' && method === 'GET') return ok(TREE);
  if (sub === '/category-mappings' && method === 'GET') {
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    const items = s.mappings.filter((m) => !q || String(m.merchant).includes(q));
    return ok({ items, total: items.length, limit: 50, offset: 0 });
  }
  if (sub === '/category-mappings' && method === 'PATCH') {
    const b = body as { merchant: string; direction: string; enabled: boolean };
    const found = s.mappings.find((m) => m.merchant === b.merchant && m.direction === b.direction);
    if (!found) return ok({ detail: 'Learned merchant not found.' }, 404);
    found.enabled = b.enabled;
    return ok(found);
  }
  if (sub === '/category-mappings' && method === 'DELETE') {
    s.mappings = s.mappings.filter((m) => !(m.merchant === url.searchParams.get('merchant') && m.direction === url.searchParams.get('direction')));
    return { status: 204, body: null };
  }
  if (sub === '/budgets/plans' && method === 'GET') {
    const lines = s.budgets.map(line);
    const monthly = lines.filter((l) => l.period === 'monthly');
    const sum = (key: string) => monthly.reduce((a, l) => a + Number(l[key]), 0).toFixed(2);
    return ok({ on: day(0), today: day(0), budgets: lines, totals: monthly.length ? [{ currency: 'AED', budget: sum('amount'), carried_in: sum('carried_in'), available: sum('available'), spent: sum('spent'), remaining: sum('remaining'), coming: '0.00', forecast: sum('forecast') }] : [] });
  }
  if (sub === '/budgets/plans' && method === 'POST') {
    const made = { ...(body as object), id: `fin_bud_${s.next++}`, created_at: stamp, updated_at: stamp } as Record<string, unknown>;
    s.budgets.push(made);
    return ok(line(made), 201);
  }
  const one = /^\/budgets\/plans\/([^/]+)$/.exec(sub);
  if (one) {
    const index = s.budgets.findIndex((b) => b.id === one[1]);
    if (index < 0) return ok({ detail: 'Budget not found.' }, 404);
    if (method === 'DELETE') { s.budgets.splice(index, 1); return { status: 204, body: null }; }
    s.budgets[index] = { ...s.budgets[index], ...(body as object) };
    return ok(line(s.budgets[index]));
  }
  if (sub === '/budgets/allocation') {
    const lines = s.budgets.filter((b) => b.active !== false).map((b) => ({ budget_id: b.id, name: b.name, bucket: line(b).allocation, monthly: Number(b.amount).toFixed(2) }));
    const buckets: Record<string, string> = { housing: '0.00', food: '0.00', transport: '0.00', family: '0.00', debts: '0.00', cards: '0.00', savings: '0.00', remittances: '0.00', other: '0.00' };
    for (const l of lines) buckets[String(l.bucket)] = (Number(buckets[String(l.bucket)]) + Number(l.monthly)).toFixed(2);
    const allocated = lines.reduce((a, l) => a + Number(l.monthly), 0);
    return ok({ month: day(0).slice(0, 7), currencies: lines.length ? [{ currency: 'AED', income: '12000.00', income_source: 'salary_profile', buckets,
      allocated: allocated.toFixed(2), unallocated: (12000 - allocated).toFixed(2), lines, left_out: [] }] : [] });
  }
  if (sub === '/recurring' && method === 'GET') {
    const found = owner.transactions.length && !s.decided.has(FOUND_KEY) ? [{ key: FOUND_KEY, name: 'NETFLIX.COM', amount: '39.00', currency: 'AED', cadence: 'monthly',
      next_due_on: day(20), category: 'subscriptions', account_id: 'fin_acct_current', occurrences: 6, kind: 'streaming', merchant: 'netflix', last_amount: '45.00',
      price_change: '6.00', transaction_ids: [] }] : [];
    const kept = owner.bills.map((b) => ({ id: b.id, name: b.name, amount: b.amount, currency: b.currency, cadence: b.cadence, next_due_on: b.next_due_on, category: b.category,
      account_id: b.account_id, active: b.active, source: b.source, merchant: b.merchant ?? null, recurring_kind: b.recurring_kind ?? null, auto_link: Boolean(b.auto_link),
      usage: b.usage ?? null, last_paid_on: day(-27), last_amount: b.amount, price_change: null, still_billing_rarely_used: b.usage === 'rarely' }));
    return ok({ today: day(0), found, kept, dismissed: s.decided.size });
  }
  if (sub === '/recurring/confirm' && method === 'POST') {
    const b = body as Record<string, unknown>;
    if (b.key) s.decided.add(String(b.key));
    const bill = { id: `fin_bill_${s.next++}`, name: b.name, amount: b.amount, currency: b.currency, cadence: b.cadence, next_due_on: b.next_due_on, category: b.category,
      account_id: b.account_id ?? null, source: 'detected', active: true, notes: null, created_at: stamp, updated_at: stamp, merchant: b.merchant ?? null,
      recurring_kind: b.recurring_kind ?? null, auto_link: Boolean(b.auto_link), usage: b.usage ?? null };
    owner.bills.push(bill);
    return ok({ ...bill, last_paid_on: null, last_amount: null, price_change: null, still_billing_rarely_used: false }, 201);
  }
  if (sub === '/recurring/reject' && method === 'POST') {
    s.decided.add(String((body as { key: string }).key));
    return ok({ key: (body as { key: string }).key, status: 'rejected' });
  }
  const settings = /^\/recurring\/bills\/([^/]+)$/.exec(sub);
  if (settings && method === 'PATCH') {
    const bill = owner.bills.find((b) => b.id === settings[1]);
    if (!bill) return ok({ detail: 'Bill not found.' }, 404);
    const b = body as { auto_link?: boolean; usage?: string };
    if (b.auto_link !== undefined) bill.auto_link = b.auto_link;
    if (b.usage !== undefined) bill.usage = b.usage === 'unknown' ? null : b.usage;
    return ok(bill);
  }
  if (sub === '/recurring/link-now' && method === 'POST') return ok({ linked: 1, left: 0 });
  return null;
}
