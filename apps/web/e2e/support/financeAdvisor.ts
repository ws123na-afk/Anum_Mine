// The mocked Money advisor API (docs/finance.md#money-advisor) for the e2e specs and the ui-gate sweep.
// Figures are SYNTHETIC and shaped like the API's weekly note; nothing here moves money.
import type { FinanceAdvisorNote, FinanceAdvisorNoteView } from '@anum/contracts';

export interface AdvisorState {
  notes: FinanceAdvisorNoteView[];
  settings: { enabled: boolean; weekday: number; updated_at: string | null };
  /** Fail the next N overview reads with a 503. */
  failOverview?: number;
  denied?: boolean;
  noAccounts?: boolean;
  shortHistory?: boolean;
  generated?: number;
}

const FSI = '⁨', PDI = '⁩';
const iso = (s: string) => `${FSI}${s}${PDI}`;

export function advisorNote(weekStart = '2026-11-08', o: { noAccounts?: boolean; shortHistory?: boolean } = {}): FinanceAdvisorNote {
  const scenarios = o.shortHistory
    ? [{ kind: 'bare' as const, monthly_outflow: '1650.00', days: 279, runs_out_on: '2027-08-14' }]
    : [
      { kind: 'bare' as const, monthly_outflow: '1650.00', days: 279, runs_out_on: '2027-08-14' },
      { kind: 'essential' as const, monthly_outflow: '9150.00', days: 50, runs_out_on: '2026-12-28' },
      { kind: 'full' as const, monthly_outflow: '12340.00', days: 37, runs_out_on: '2026-12-15' },
    ];
  return {
    version: 1, today: weekStart, timezone: 'Asia/Dubai', base_currency: 'AED', week_start: '2026-11-01', week_end: '2026-11-07', month_start: '2026-11-01',
    newest_record_on: '2026-11-07', history_days: o.shortHistory ? 19 : 128, has_accounts: !o.noAccounts,
    currencies: o.noAccounts ? [] : [
      {
        currency: 'AED',
        runway: {
          status: o.shortHistory ? 'not_enough_history' : 'ok', history_days: o.shortHistory ? 19 : 128, first_record_on: '2026-07-01',
          basis: o.shortHistory ? 'none' : 'months', months: o.shortHistory ? [] : ['2026-08', '2026-09', '2026-10'], liquid: '15045.50', accounts_counted: 2,
          bills_monthly: '1500.00', minimums_monthly: '150.00', essential_spending_monthly: o.shortHistory ? null : '7500.00',
          total_spending_monthly: o.shortHistory ? null : '12190.00',
          essential_categories: o.shortHistory ? [] : [
            { category: 'housing', monthly_average: '5000.00', bills_monthly: '0.00', counted: '5000.00' },
            { category: 'groceries', monthly_average: '2500.00', bills_monthly: '0.00', counted: '2500.00' },
          ],
          scenarios,
        },
        card_available: '7550.00', income_month: '18000.00', spend_month: '7420.75',
        spending: {
          week: { start: '2026-11-01', end: '2026-11-07', amount: '2310.25', previous: '1780.00', previous_start: '2026-10-25', previous_end: '2026-10-31', change_percent: 30, average: o.shortHistory ? null : '2803.45', vs_average_percent: o.shortHistory ? null : -18 },
          month: { start: '2026-11-01', end: '2026-11-08', amount: '7420.75', previous: '6100.00', previous_start: '2026-10-01', previous_end: '2026-10-08', change_percent: 22, average: o.shortHistory ? null : '12190.00', vs_average_percent: o.shortHistory ? null : -39 },
          categories: [
            { category: 'housing', week: '0.00', previous_week: '0.00', month: '5000.00', previous_month: '5000.00', average_month: '5000.00' },
            { category: 'dining', week: '1180.25', previous_week: '420.00', month: '1400.75', previous_month: '600.00', average_month: '950.00' },
            { category: 'groceries', week: '640.00', previous_week: '700.00', month: '640.00', previous_month: '500.00', average_month: '2500.00' },
            { category: 'subscriptions', week: '55.00', previous_week: '0.00', month: '55.00', previous_month: '55.00', average_month: '55.00' },
          ],
          payees_week: [{ name: 'NOBU RESTAURANT DUBAI MALL', amount: '980.25', count: 1 }, { name: 'CARREFOUR CITY CENTRE', amount: '640.00', count: 3 }],
          payees_month: [{ name: 'RENT EJARI 2026 UNIT 1204 MARINA GATE TOWER', amount: '5000.00', count: 1 }, { name: 'NOBU RESTAURANT DUBAI MALL', amount: '980.25', count: 1 }],
          spikes: [{ category: 'dining', period: 'month', amount: '1400.75', expected: '950.00', percent: 47 }, { category: 'travel', period: 'new', amount: '900.00', expected: '0.00', percent: null }],
        },
        coming: {
          items: [
            { kind: 'bill', name: 'DEWA', ref_id: 'bill_dewa', amount: '600.00', due_on: '2026-11-10', days_until: 2, category: 'utilities', detected: false },
            { kind: 'card_minimum', name: 'Rewards card', ref_id: 'debt_card', amount: '150.00', due_on: '2026-11-12', days_until: 4, category: null, detected: false },
            { kind: 'subscription', name: 'NETFLIX.COM', ref_id: null, amount: '55.00', due_on: '2026-11-20', days_until: 12, category: 'subscriptions', detected: true },
            { kind: 'minimum', name: 'Car loan', ref_id: 'debt_car', amount: '1500.00', due_on: null, days_until: null, category: null, detected: false },
          ],
          total: '2305.00', saved_total: '2250.00', detected_total: '55.00', within_7_days: '750.00',
        },
      },
      {
        currency: 'USD',
        runway: { status: 'ok', history_days: 120, first_record_on: '2026-07-10', basis: 'months', months: ['2026-08', '2026-09', '2026-10'], liquid: '1250.00', accounts_counted: 1,
          bills_monthly: '0.00', minimums_monthly: '0.00', essential_spending_monthly: '0.00', total_spending_monthly: '40.00', essential_categories: [],
          scenarios: [{ kind: 'bare', monthly_outflow: '0.00', days: null, runs_out_on: null }, { kind: 'essential', monthly_outflow: '0.01', days: 3805468, runs_out_on: null },
            { kind: 'full', monthly_outflow: '40.00', days: 951, runs_out_on: '2029-06-16' }] },
        card_available: null, income_month: '0.00', spend_month: '0.00',
        spending: {
          week: { start: '2026-11-01', end: '2026-11-07', amount: '0.00', previous: '0.00', previous_start: '2026-10-25', previous_end: '2026-10-31', change_percent: null, average: '9.20', vs_average_percent: -100 },
          month: { start: '2026-11-01', end: '2026-11-08', amount: '0.00', previous: '40.00', previous_start: '2026-10-01', previous_end: '2026-10-08', change_percent: -100, average: '40.00', vs_average_percent: -100 },
          categories: [], payees_week: [], payees_month: [], spikes: [],
        },
        coming: { items: [], total: '0.00', saved_total: '0.00', detected_total: '0.00', within_7_days: '0.00' },
      },
    ],
    cards: o.noAccounts ? [] : [{
      name: 'Rewards card', account_id: 'fin_acct_card', debt_id: 'debt_card', currency: 'AED', last4: '9876', owed: '2450.00', owed_source: 'ledger',
      observed_balance: '2450.00', observed_at: '2026-11-07T09:00:00Z', credit_limit: '10000.00', available: '7550.00', utilization_percent: 25,
      minimum_payment: '150.00', due_on: '2026-11-12', days_until: 4, apr_percent: '36', interest_if_minimum: '69.00', months_to_clear_at_minimum: 24,
      total_interest_at_minimum: '1080.40', never_clears_at_minimum: false, missing: [],
    }, {
      name: 'Store card', account_id: null, debt_id: 'debt_store', currency: 'AED', last4: null, owed: '900.00', owed_source: 'debt', observed_balance: null, observed_at: null,
      credit_limit: null, available: null, utilization_percent: null, minimum_payment: '90.00', due_on: null, days_until: null, apr_percent: null,
      interest_if_minimum: null, months_to_clear_at_minimum: null, total_interest_at_minimum: null, never_clears_at_minimum: false, missing: ['rate', 'due_day'],
    }],
    income: { salary_found: true, last_salary_on: '2026-11-01' },
    actions: o.noAccounts
      ? [{ code: 'no_accounts', severity: 'high', currency: null, params: {}, tab: 'accounts', text_en: 'Add your bank account under Accounts and import a statement to get advice.', text_ar: 'أضف حسابك البنكي في الحسابات واستورد كشفًا للحصول على النصيحة.' }]
      : [
        { code: 'card_pay_full_rate', severity: 'urgent', currency: 'AED', params: {}, tab: 'debts',
          text_en: 'Pay Rewards card (9876) in full (AED 2,450.00) by 2026-11-12. Paying only the minimum (AED 150.00) would cost about AED 69.00 in interest next month.',
          text_ar: `سدّد ${iso('Rewards card (9876)')} بالكامل (${iso('AED 2,450.00')}) قبل ${iso('2026-11-12')}. دفع الحد الأدنى فقط (${iso('AED 150.00')}) سيكلفك نحو ${iso('AED 69.00')} فوائد في الشهر القادم.` },
        { code: 'bill_due', severity: 'urgent', currency: 'AED', params: {}, tab: 'bills', text_en: 'DEWA (AED 600.00) is due on 2026-11-10, in 2 days.',
          text_ar: `يستحق ${iso('DEWA')} (${iso('AED 600.00')}) في ${iso('2026-11-10')}، بعد ${iso('2')} أيام.` },
        { code: 'runway_low', severity: 'high', currency: 'AED', params: {}, tab: null,
          text_en: 'AED: if your income stopped, your money would cover essentials for 50 days, until 2026-12-28. Aim for at least 90 days before new spending.',
          text_ar: `${iso('AED')}: إذا توقف دخلك فسيغطي مالك الأساسيات لمدة ${iso('50')} يومًا حتى ${iso('2026-12-28')}. اجعل هدفك 90 يومًا على الأقل قبل أي إنفاق جديد.` },
        { code: 'spending_up', severity: 'medium', currency: 'AED', params: {}, tab: 'transactions',
          text_en: 'Dining is up 47% on your 3 month average: AED 1,400.75 so far this month against about AED 950.00 in a usual month.',
          text_ar: `ارتفع إنفاق المطاعم بنسبة ${iso('47')}% عن متوسط 3 أشهر: ${iso('AED 1,400.75')} حتى الآن هذا الشهر مقابل نحو ${iso('AED 950.00')} في شهر عادي.` },
        { code: 'card_add_rate', severity: 'medium', currency: 'AED', params: {}, tab: 'debts', text_en: 'Add the interest rate of Store card under Debts to see what paying only the minimum would cost.',
          text_ar: `أضف نسبة الفائدة لـ ${iso('Store card')} في الديون لترى كلفة دفع الحد الأدنى فقط.` },
        { code: 'subscriptions_review', severity: 'info', currency: 'AED', params: {}, tab: 'bills', text_en: 'Review 1 subscriptions (AED 55.00 in the next 30 days) and cancel the ones you do not use.',
          text_ar: `راجع ${iso('1')} اشتراكات (${iso('AED 55.00')} خلال 30 يومًا القادمة) وألغِ ما لا تستخدمه.` },
      ],
    notice_en: 'ANUM only advises. It never moves money, pays a bill or touches a bank or card.',
    notice_ar: 'ANUM يقدّم النصيحة فقط. لا ينقل المال أبدًا ولا يدفع فاتورة ولا يتعامل مع بنك أو بطاقة.',
  };
}

/** `count` past notes, newest week first, each a week apart. */
export function advisorState(count: number, o: Partial<AdvisorState> = {}): AdvisorState {
  const notes: FinanceAdvisorNoteView[] = Array.from({ length: count }, (_, i) => {
    const start = new Date(Date.UTC(2026, 10, 8 - 7 * i)).toISOString().slice(0, 10);
    return { id: `fin_adv_${i + 1}`, week_start: start, trigger: i === 0 ? 'manual' : 'schedule', generated_at: `${start}T06:00:00Z`, delivered_at: i === 0 ? null : `${start}T06:00:00Z`,
      note: advisorNote(start, { noAccounts: o.noAccounts, shortHistory: o.shortHistory }) };
  });
  return { notes, settings: { enabled: true, weekday: 6, updated_at: null }, ...o };
}

const error = (status: number, message: string) => ({ status, body: { error: { code: status === 403 ? 'forbidden' : 'internal_error', message, correlation_id: 'c', details: [] } } });

/** The reply for `/api/v1/finance/advisor...`, or null for any other path. */
export function advisorReply(state: AdvisorState, method: string, url: URL, body: unknown): { status: number; body: unknown } | null {
  const sub = url.pathname.replace('/api/v1/finance', '');
  if (!sub.startsWith('/advisor')) return null;
  if (state.denied) return error(403, 'Finance is for workspace owners and members. Ask a workspace owner to change your role from viewer to member.');
  const ok = (value: unknown, status = 200) => ({ status, body: value });
  const summary = (n: FinanceAdvisorNoteView) => ({
    id: n.id, week_start: n.week_start, trigger: n.trigger, generated_at: n.generated_at, delivered_at: n.delivered_at,
    survival: n.note.currencies.map((c) => ({ currency: c.currency, status: c.runway.status, days: (c.runway.scenarios.find((s) => s.kind === 'essential') ?? c.runway.scenarios[0])?.days ?? null })),
    coming: n.note.currencies.map((c) => ({ currency: c.currency, amount: c.coming.total })), actions: n.note.actions.length,
    urgent: n.note.actions.filter((a) => a.severity === 'urgent').length,
  });
  if (sub === '/advisor' && method === 'GET') {
    if (state.failOverview) { state.failOverview -= 1; return error(503, 'The database is not reachable.'); }
    return ok({ settings: state.settings, timezone: 'Asia/Dubai', today: '2026-11-08', week_start: '2026-11-08', next_note_on: state.settings.enabled ? '2026-11-08' : null, latest: state.notes[0] ?? null });
  }
  if (sub === '/advisor/notes') {
    const limit = Number(url.searchParams.get('limit') ?? 10), offset = Number(url.searchParams.get('offset') ?? 0);
    return ok({ items: state.notes.slice(offset, offset + limit).map(summary), total: state.notes.length, limit, offset });
  }
  const one = /^\/advisor\/notes\/([^/]+)$/.exec(sub);
  if (one) { const found = state.notes.find((n) => n.id === decodeURIComponent(one[1])); return found ? ok(found) : error(404, 'Note not found. It may have been deleted.'); }
  if (sub === '/advisor/generate' && method === 'POST') {
    state.generated = (state.generated ?? 0) + 1;
    const made: FinanceAdvisorNoteView = { id: 'fin_adv_1', week_start: '2026-11-08', trigger: 'manual', generated_at: '2026-11-08T09:30:00Z', delivered_at: null,
      note: advisorNote('2026-11-08', { noAccounts: state.noAccounts, shortHistory: state.shortHistory }) };
    state.notes = [made, ...state.notes.filter((n) => n.week_start !== made.week_start)];
    return ok(made, 201);
  }
  if (sub === '/advisor/settings' && method === 'PUT') {
    const input = body as { enabled: boolean; weekday: number };
    state.settings = { enabled: input.enabled, weekday: input.weekday, updated_at: '2026-11-08T09:31:00Z' };
    return ok(state.settings);
  }
  return null;
}
