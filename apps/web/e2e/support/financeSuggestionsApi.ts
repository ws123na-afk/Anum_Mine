// The mocked Finance, Suggestions API (docs/finance.md#suggestions, #rules-and-learning, #automation-policies): a queue,
// versioned rules with preview, apply and undo, merchant spellings, subcategories and automation. SYNTHETIC data only.

export interface SuggestionsState {
  items: Record<string, any>[];
  decided: Record<string, string>;
  rules: { rule_id: string; versions: Record<string, any>[] }[];
  batches: Record<string, any>[];
  aliases: Record<string, any>[];
  subcategories: Record<string, any>[];
  automation: Record<string, any>;
  fail: number;
  empty: boolean;
  rowCategory: Record<string, string>;
}

const stamp = '2026-11-01T00:00:00Z';
const hash = (n: number) => String(n).padStart(64, 'a');

function suggestion(key: string, kind: string, extra: Record<string, any> = {}): Record<string, any> {
  return {
    key, producer: 'test', kind, detected: 'SYNTHETIC', why: 'SYNTHETIC reason', params: {}, evidence: [], confidence: null, effect: [],
    action: { kind: 'open', transaction_ids: [], category: null, subcategory_id: null, transaction_type: null }, href: null, occurred_on: '2026-11-05',
    payload_hash: hash(key.length), ...extra,
  };
}

export function suggestionsState({ empty = false, fail = 0 }: { empty?: boolean; fail?: number } = {}): SuggestionsState {
  return {
    empty, fail, decided: {}, batches: [], aliases: [], subcategories: [], rowCategory: {},
    automation: { auto_classify: false, min_confidence: '0.90', sources: ['rule'], notify_unresolved: false, notify_min_items: 1 },
    rules: [],
    items: empty ? [] : [
      suggestion('history:fin_txn_7:groceries:-', 'classification', {
        params: { merchant: 'zebramart testcity', category: 'groceries', agree: '3', seen: '3', current: 'other' }, confidence: '0.90',
        evidence: [{ label: 'POS 3381 ZEBRAMART TESTCITY', ref_type: 'transaction', ref_id: 'fin_txn_7', value: '2026-11-05 AED -27.00' }],
        effect: [{ amount: '27.00', currency: 'AED', direction: 'out', note: null }],
        action: { kind: 'classify', transaction_ids: ['fin_txn_7'], category: 'groceries', subcategory_id: null, transaction_type: null }, href: '#finance-transactions',
      }),
      suggestion('bill_due:fin_bill_rent:2026-11-10', 'bill_due', {
        params: { name: 'Rent', days: '3', due_on: '2026-11-10' }, effect: [{ amount: '6500.00', currency: 'AED', direction: 'out', note: null }],
        action: { kind: 'acknowledge', transaction_ids: [], category: null, subcategory_id: null, transaction_type: null }, href: '#finance-bills',
      }),
      suggestion('uncategorized:fin_txn_9', 'uncategorized', { params: { description: 'QUOKKA UNKNOWN' }, confidence: null, href: '#finance-transactions',
        effect: [{ amount: '9.00', currency: 'AED', direction: 'out', note: null }] }),
      suggestion('statement_hold:fin_imp_1:2', 'statement_hold', { params: { count: '2', file: 'october.csv' }, href: '#finance-import' }),
    ],
  };
}

const err = (status: number, message: string) => ({ status, body: { error: { code: 'refused', message, correlation_id: 'c', details: [] } } });

function preview(state: SuggestionsState, content: Record<string, any>) {
  const word = String(content.match?.words?.[0] ?? content.match?.merchant ?? '').toLowerCase();
  const rows = word.includes('zebra') && !state.rowCategory.fin_txn_7
    ? [{ transaction_id: 'fin_txn_7', account_id: 'fin_acct_current', posted_on: '2026-11-05', description: 'POS 3381 ZEBRAMART TESTCITY', amount: '-27.00', currency: 'AED',
      before_category: 'other', before_subcategory_id: null, before_type: 'uncategorized', after_category: content.category, after_subcategory_id: null, after_type: 'expense' }]
    : [];
  return { matched: rows.length + 1, changes: rows.length, unchanged: 0, manual_kept: 1, sms_kept: 0, truncated: false, preview_hash: hash(rows.length + 7), rows };
}

function ruleView(rule: SuggestionsState['rules'][number]) {
  const approved = rule.versions.filter((v) => v.status === 'approved');
  const current = approved.length ? approved[approved.length - 1] : null;
  const newest = rule.versions[rule.versions.length - 1];
  return { rule_id: rule.rule_id, current, pending: newest.status === 'proposed' ? newest : null, versions: rule.versions.length };
}

function propose(state: SuggestionsState, content: Record<string, any>, ruleId?: string, extra: Record<string, any> = {}) {
  let rule = ruleId ? state.rules.find((r) => r.rule_id === ruleId) : undefined;
  if (!rule) { rule = { rule_id: `fin_rule_${state.rules.length + 1}`, versions: [] }; state.rules.push(rule); }
  const version = { rule_id: rule.rule_id, version: rule.versions.length + 1, status: 'proposed', content, provenance: extra.source ? 'learned' : 'owner',
    source_transaction_id: extra.source ?? null, confidence: '0.90', payload_hash: hash(rule.versions.length + 1), created_by: 'user_local', created_at: stamp,
    decided_by: null, decided_at: null, reverted_from: extra.reverted ?? null };
  rule.versions.push(version);
  return version;
}

export function suggestionsReply(state: SuggestionsState, method: string, sub: string, url: URL, body: any): { status: number; body: unknown } | null {
  const ok = (value: unknown, status = 200) => ({ status, body: value });
  if (sub === '/suggestions' && method === 'GET') {
    if (state.fail > 0) { state.fail -= 1; return err(503, 'The database is not reachable.'); }
    const open = state.items.filter((s) => !state.decided[s.key]);
    const kind = url.searchParams.get('kind');
    const chosen = open.filter((s) => !kind || s.kind === kind);
    const counts = [...new Set(open.map((s) => s.kind))].map((k) => ({ kind: k, count: open.filter((s) => s.kind === k).length }));
    const limit = Number(url.searchParams.get('limit') ?? 20), offset = Number(url.searchParams.get('offset') ?? 0);
    return ok({ items: chosen.slice(offset, offset + limit), total: chosen.length, limit, offset, counts, unavailable: [], dismissed: Object.keys(state.decided).length });
  }
  if (sub === '/suggestions/accept' || sub === '/suggestions/reject') {
    const item = state.items.find((s) => s.key === body.key);
    if (!item || state.decided[item.key]) return err(409, 'This suggestion was already decided.');
    state.decided[item.key] = sub.endsWith('accept') ? 'accepted' : 'rejected';
    if (sub.endsWith('accept') && item.action.kind === 'classify') {
      for (const id of item.action.transaction_ids) state.rowCategory[id] = item.action.category;
      const batch = { id: `fin_batch_${state.batches.length + 1}`, origin: 'owner', rule_id: null, rule_version: null, suggestion_key: item.key, changed: item.action.transaction_ids.length,
        status: 'applied', created_by: 'user_local', created_at: stamp, undone_at: null, ids: item.action.transaction_ids };
      state.batches.unshift(batch);
      return ok({ key: item.key, status: 'accepted', batch });
    }
    return ok({ key: item.key, status: state.decided[item.key], batch: null });
  }
  if (sub === '/suggestions/restore') { delete state.decided[body.key]; return ok({ key: body.key, status: 'open', batch: null }); }
  if (sub === '/rules' && method === 'GET') {
    const views = state.rules.map(ruleView);
    return ok({ items: views, total: views.length, limit: 20, offset: 0, legacy_rules: 0, pending: views.filter((v) => v.pending).length });
  }
  if (sub === '/rules' && method === 'POST') return ok(propose(state, body.content, undefined, { source: body.source_transaction_id }), 201);
  if (sub === '/rules/preview') return ok(preview(state, body.content));
  if (sub === '/rule-batches') return ok({ items: state.batches, total: state.batches.length, limit: 10, offset: 0 });
  const undo = /^\/rule-batches\/([^/]+)\/undo$/.exec(sub);
  if (undo) {
    const batch = state.batches.find((b) => b.id === undo[1]);
    if (!batch || batch.status !== 'applied') return err(409, 'This change was already undone.');
    batch.status = 'undone'; batch.undone_at = stamp;
    for (const id of batch.ids ?? []) delete state.rowCategory[id];
    for (const [key, value] of Object.entries(state.decided)) if (value === 'accepted' && key === batch.suggestion_key) delete state.decided[key];
    return ok({ batch, restored: batch.changed, kept: 0 });
  }
  const rule = /^\/rules\/([^/]+)(?:\/(versions|approve|reject|revert|preview|apply))?$/.exec(sub);
  if (rule) {
    const found = state.rules.find((r) => r.rule_id === rule[1]);
    if (!found) return err(404, 'Rule not found. It may have been removed.');
    const action = rule[2];
    if (!action) return ok({ rule_id: found.rule_id, versions: [...found.versions].reverse() });
    if (action === 'versions') return ok(propose(state, body.content, found.rule_id), 201);
    if (action === 'approve' || action === 'reject') {
      const v = found.versions.find((x) => x.version === body.version);
      if (!v || v.status !== 'proposed') return err(409, 'This version was already decided.');
      if (action === 'approve' && body.payload_hash !== v.payload_hash) return err(409, 'The rule changed since you looked at it.');
      v.status = action === 'approve' ? 'approved' : 'rejected';
      return ok(v);
    }
    if (action === 'revert') {
      const old = found.versions.find((x) => x.version === body.to_version);
      return ok(propose(state, old!.content, found.rule_id, { reverted: body.to_version }), 201);
    }
    const current = ruleView(found).current;
    if (!current) return err(409, 'Approve the rule before you apply it to earlier rows.');
    if (action === 'preview') return ok(preview(state, current.content));
    if (action === 'apply') {
      const p = preview(state, current.content);
      for (const r of p.rows) state.rowCategory[r.transaction_id] = r.after_category;
      const batch = { id: `fin_batch_${state.batches.length + 1}`, origin: 'owner', rule_id: found.rule_id, rule_version: current.version, suggestion_key: null,
        changed: p.rows.length, status: 'applied', created_by: 'user_local', created_at: stamp, undone_at: null, ids: p.rows.map((r) => r.transaction_id) };
      state.batches.unshift(batch);
      return ok(batch, 201);
    }
  }
  const similar = /^\/transactions\/([^/]+)\/similar$/.exec(sub);
  if (similar) {
    const content = { match: { merchant: null, words: ['zebramart testcity'], reference: null, direction: 'out', min_amount: null, max_amount: null, currency: null,
      account_ids: body.scope === 'account' ? ['fin_acct_current'] : [], senders: [] }, category: 'groceries', subcategory_id: null, transaction_type: null, priority: 100,
      enabled: true, auto_apply: false, note: null };
    return ok({ content, preview: preview(state, content), source_transaction_id: decodeURIComponent(similar[1]) });
  }
  if (/^\/transactions\/[^/]+\/ai-suggestion$/.test(sub)) return err(409, 'AI help is off. Turn it on under Finance, iPhone SMS, Settings if you want it.');
  if (sub === '/merchants/aliases' && method === 'GET') return ok(state.aliases);
  if (sub === '/merchants/aliases' && method === 'POST') {
    if (state.aliases.some((a) => a.alias.toLowerCase() === String(body.alias).toLowerCase())) return err(409, 'You already have this spelling. Edit it instead.');
    const made = { id: `fin_alias_${state.aliases.length + 1}`, alias: body.alias, merchant: body.merchant, created_at: stamp, updated_at: stamp };
    state.aliases.push(made);
    return ok(made, 201);
  }
  const alias = /^\/merchants\/aliases\/([^/]+)$/.exec(sub);
  if (alias && method === 'DELETE') { state.aliases = state.aliases.filter((a) => a.id !== alias[1]); return ok({}); }
  if (sub === '/subcategories' && method === 'GET') return ok(state.subcategories);
  if (sub === '/subcategories' && method === 'POST') {
    const made = { id: `fin_sub_${state.subcategories.length + 1}`, parent: body.parent, name: body.name, archived: false };
    state.subcategories.push(made);
    return ok(made, 201);
  }
  const subcat = /^\/subcategories\/([^/]+)$/.exec(sub);
  if (subcat && method === 'PATCH') {
    const found = state.subcategories.find((s) => s.id === subcat[1]);
    if (found) Object.assign(found, body);
    return ok(found);
  }
  if (sub === '/automation') {
    if (method === 'PUT') state.automation = { ...body };
    return ok({ policy: state.automation, updated_at: method === 'PUT' ? stamp : null,
      never_automated: ['post_adjustment', 'delete_record', 'change_bank_mapping', 'approve_payment', 'change_period'] });
  }
  return null;
}
