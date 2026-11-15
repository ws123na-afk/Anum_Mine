// The mocked automatic-matching API (docs/finance-matching.md) for the Finance specs: the policy, the match records,
// installment status, history runs and capture health. Every order, card and amount is SYNTHETIC and low entropy.

const stamp = '2026-11-15T09:00:00Z';

export interface MatchingState {
  policy: Record<string, unknown>;
  approved: boolean;
  records: Record<string, unknown>[];
  runs: Record<string, unknown>[];
  installments: boolean;
}

export function matchingState({ empty = false }: { empty?: boolean } = {}): MatchingState {
  const record = (id: string, outcome: string, over: Record<string, unknown> = {}) => ({
    id, canonical_id: `fin_je_${id}`, kind: 'installment', dimension: 'bill', target_type: 'finance_bill_due', target_id: `fin_bill_${id}:2026-11-15`,
    label: 'Example Shop 2/4', due_on: '2026-11-15', posted_on: '2026-11-15', amount: '125.00', currency: 'SAR', outcome, evidence_state: 'verified',
    reason_code: 'provider_amount_date', confidence: '0.900', evidence: [{ code: 'provider', value: 'tamara', weight: '0.30' }, { code: 'amount_exact', value: 'SAR 125', weight: '0.45' }],
    flags: [], competitors: 0, policy_version: 'pfm-1.r1', run_id: null, link_id: outcome === 'auto_linked' ? `fin_link_${id}` : null, decided_by: null,
    decided_at: null, created_at: stamp, updated_at: stamp, version: 1, ...over,
  });
  return {
    approved: false,
    policy: {
      match_transactions: false, reconcile_installments: false, link_recurring_bills: false, link_card_repayments: false, link_bank_loan_repayments: false,
      prevent_duplicates: true, reprocess_history: false, notify_exceptions: false, min_confidence: '0.9', review_confidence: '0.5',
      settled_sources: ['statement', 'sms'], source_account_ids: [], revision: 0, approved_at: null, approved_by: null,
    },
    records: empty ? [] : [
      record('a', 'review', { reason_code: 'ambiguous_obligation', competitors: 1, evidence_state: 'pending', label: 'Example Shop 2/4' }),
      record('b', 'review', { reason_code: 'ambiguous_obligation', competitors: 1, evidence_state: 'pending', label: 'Sample Store 1/3', target_id: 'fin_bill_b:2026-11-15' }),
      record('c', 'auto_linked', { label: 'Demo Market 3/4' }),
      // Across currencies (docs/finance-matching.md#across-currencies): one waits without the bank's original amount, one was linked on it.
      record('d', 'review', { reason_code: 'fx_evidence_missing', evidence_state: 'pending', label: 'Sample Outlet 2/4', amount: '100.00', currency: 'USD',
        target_id: 'fin_bill_d:2026-11-15', evidence: [{ code: 'provider', value: 'tamara', weight: '0.30' }, { code: 'fx_evidence_missing', value: 'SAR 375.00 for USD 100.00', weight: '0' }],
        fx: { basis: 'missing', bank_amount: '375.00', bank_currency: 'SAR', original_amount: null, original_currency: null, rate: null, rate_kind: 'not_stated' } }),
      record('e', 'auto_linked', { reason_code: 'fx_original_amount', label: 'Sample Outlet 1/4', amount: '100.00', currency: 'USD', target_id: 'fin_bill_e:2026-10-15',
        fx: { original_amount: '100.00', original_currency: 'USD', billed_amount: '384.38', purchase_amount: '375.00', bank_amount: '375.00', bank_currency: 'SAR',
          rate: '3.750000', rate_kind: 'implied', rate_source: 'sms', rate_date: '2026-10-15', fee_amount: '9.38', fee_currency: 'SAR', fee_source: 'markup_stated',
          fee_transaction_id: null } }),
    ],
    runs: [],
    installments: !empty,
  };
}

const NEVER = ['post_adjustment', 'delete_record', 'change_bank_mapping', 'approve_payment', 'change_period', 'link_loan_payments', 'link_person_loan_payments',
  'change_policy'];

function view(state: MatchingState) {
  return {
    policy: { ...state.policy, approved_at: state.approved ? stamp : null }, version: `pfm-1.r${state.policy.revision as number}`, approved: state.approved,
    never_automated: NEVER, spending_policy: 'installments_when_paid',
    providers: [{ key: 'postpay', name: 'Postpay', reads: [], connected: false }, { key: 'spotii', name: 'Spotii', reads: [], connected: false },
      { key: 'tabby', name: 'Tabby', reads: [], connected: false }, { key: 'tamara', name: 'Tamara', reads: ['gmail', 'statement_csv'], connected: true }],
  };
}

const counts = (over: Record<string, number> = {}) => ({ transactions: 4, matched: 3, auto_linked: 0, verified: 0, provisional: 0, ambiguous: 1, review: 2,
  missing_source: 1, duplicates: 0, corrected: 0, differences: 0, already_linked: 1, kept_owner_choice: 0, errors: 0, ...over });

export function matchingReply(state: MatchingState, method: string, sub: string, url: URL, body: unknown): { status: number; body: unknown } | null {
  const ok = (value: unknown, status = 200) => ({ status, body: value });
  if (sub === '/automation/matching') {
    if (method === 'GET') return ok(view(state));
    if (method === 'PUT') {
      const input = body as Record<string, unknown>;
      const { approve, expected_revision: _expected, ...rest } = input;
      state.policy = { ...state.policy, ...rest, revision: (state.policy.revision as number) + 1 };
      state.approved = Boolean(approve);
      return ok(view(state));
    }
  }
  if (sub === '/matching/run' && method === 'POST') return ok({ counts: counts(), policy_version: view(state).version });
  if (sub === '/matching/records' && method === 'GET') {
    const outcome = url.searchParams.get('outcome');
    const items = state.records.filter((r) => !outcome || r.outcome === outcome);
    const tally = (o: string) => state.records.filter((r) => r.outcome === o).length;
    return ok({ items, total: items.length, limit: 25, offset: 0, counts: { review: tally('review'), auto_linked: tally('auto_linked'), stale: tally('stale') } });
  }
  const decided = /^\/matching\/records\/([^/]+)\/(accept|reject|undo)$/.exec(sub);
  if (decided && method === 'POST') {
    const record = state.records.find((r) => r.id === decided[1]);
    if (!record) return ok({ detail: 'Match not found.', code: 'match_not_found' }, 404);
    record.outcome = { accept: 'accepted', reject: 'rejected', undo: 'undone' }[decided[2]];
    record.version = (record.version as number) + 1;
    return ok(record);
  }
  if (sub === '/matching/installments' && method === 'GET') {
    if (!state.installments) return ok({ orders: [], spending_policy: 'installments_when_paid', today: '2026-11-15' });
    const row = (n: number, status: string, due: string) => ({ installment: n, total_installments: 4, due_on: due, amount: '125.00', still_due: status === 'verified_paid' ? '0.00' : '125.00',
      currency: 'SAR', status, bill_id: `fin_bill_${n}`, paid_amount: status === 'verified_paid' ? '125.00' : '0', canonical_ids: [], record_id: null, record_version: null,
      record_outcome: null, confidence: null, reason_code: null });
    return ok({ today: '2026-11-15', spending_policy: 'installments_when_paid', orders: [{
      provider: 'Tamara', order_id: 'example-order-1', merchant: 'Example Shop', purchase_on: '2026-09-15', currency: 'SAR', total: '500.00', paid: '250.00',
      still_due: '250.00', next_due_on: '2026-11-15',
      installments: [row(1, 'verified_paid', '2026-09-15'), row(2, 'review', '2026-11-15'), row(3, 'provisional', '2026-12-15'), row(4, 'upcoming', '2027-01-15')],
    }] });
  }
  if (sub.startsWith('/matching/backfill')) {
    if (sub === '/matching/backfill' && method === 'GET') return ok({ items: [...state.runs].reverse(), total: state.runs.length });
    if (sub === '/matching/backfill' && method === 'POST') {
      const mode = (body as { mode: string }).mode;
      const run = { id: `fin_backfill_${state.runs.length + 1}`, mode, status: 'done', start_on: '2025-01-03', end_on: '2026-11-15', cursor_on: '2026-11-30', batches: 23,
        counts: counts(mode === 'apply' ? { auto_linked: 2, verified: 2 } : {}), policy_version: view(state).version, error_code: null, attempts: 0, created_by: 'user_local',
        started_at: stamp, updated_at: stamp, finished_at: stamp, undone_at: null, version: 3 };
      state.runs.push(run);
      return ok(run, 201);
    }
    const undo = /^\/matching\/backfill\/([^/]+)\/undo$/.exec(sub);
    if (undo && method === 'POST') {
      const run = state.runs.find((r) => r.id === undo[1])!;
      run.status = 'undone';
      return ok({ run, links_removed: 2 });
    }
  }
  if (sub === '/capture-health' && method === 'GET') {
    return ok({
      state: 'attention', enabled: true, self_test_passed: true, checked_at: stamp,
      devices: [{ id: 'dev_1', name: 'My iPhone', status: 'active', scopes: ['finance.sms', 'finance.wallet'], token_hint: 'abcd', last_seen_at: stamp, last_ok_at: stamp,
        last_error: null, last_error_at: null }],
      channels: [
        { channel: 'sms', last_captured_at: stamp, last_failed_at: null, last_failed_code: null, refused_7d: 0, awaiting_review: 1, review_reasons: { unknown_sender: 1 } },
        { channel: 'wallet', last_captured_at: null, last_failed_at: null, last_failed_code: null, refused_7d: 0, awaiting_review: 0, review_reasons: {} },
      ],
      unknown_senders: [{ sender: 'EXAMPLEBANK', messages: 1, last_at: stamp }], unmapped_wallet_cards: 0,
      mappings: [{ kind: 'card_number', label: '1234', account_id: 'fin_acct_current', account_name: 'Everyday account' }], accounts_without_mapping: ['Rewards card'],
      gaps: [], problems: ['unknown_senders', 'messages_waiting'],
    });
  }
  return null;
}
