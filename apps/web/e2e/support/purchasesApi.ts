// Mocked /api/v1/purchases answers (docs/purchases.md) shared by purchases.spec.ts and the workspace mock (accessibility,
// layout and journey gates). Synthetic values only: example shops, order numbers and round figures.
const now = new Date();
const stamp = now.toISOString();
const day = (offset: number) => new Date(now.getTime() + offset * 86_400_000).toISOString().slice(0, 10);

type Row = Record<string, unknown>;
export interface PurchasesState {
  purchases: Row[];
  preferences: Row;
  candidates: Row[];
  refundCandidates: Row[];
  accounts: Row[];
  denied: boolean;
  failOverview: boolean;
  links: number;
  cashTransactions: Set<string>;
  sequence: number;
}

// The purchase state machine without the refund statuses (they follow the refunds linked from Finance).
const MOVES: Record<string, string[]> = {
  ordered: ['confirmed', 'shipped', 'out_for_delivery', 'partially_delivered', 'delivered', 'cancelled', 'disputed'],
  confirmed: ['shipped', 'out_for_delivery', 'partially_delivered', 'delivered', 'cancelled', 'disputed'],
  shipped: ['out_for_delivery', 'partially_delivered', 'delivered', 'cancelled', 'disputed'],
  out_for_delivery: ['shipped', 'partially_delivered', 'delivered', 'disputed'],
  partially_delivered: ['shipped', 'out_for_delivery', 'delivered', 'return_requested', 'disputed'],
  delivered: ['return_requested', 'refund_pending', 'disputed'],
  cancelled: ['refund_pending', 'disputed'],
  return_requested: ['return_approved', 'pickup_scheduled', 'returned', 'delivered', 'refund_pending', 'disputed'],
  return_approved: ['pickup_scheduled', 'returned', 'delivered', 'refund_pending', 'disputed'],
  pickup_scheduled: ['returned', 'return_approved', 'disputed'],
  returned: ['refund_pending', 'disputed'],
  refund_pending: ['disputed'],
  partially_refunded: ['refund_pending', 'return_requested', 'disputed'],
  refunded: ['disputed'],
  disputed: ['delivered', 'returned', 'refund_pending', 'cancelled'],
};
const RETURN_MOVES: Record<string, string[]> = {
  requested: ['approved', 'pickup_scheduled', 'returned', 'refund_pending', 'rejected', 'cancelled', 'disputed'],
  approved: ['pickup_scheduled', 'returned', 'refund_pending', 'cancelled', 'disputed'],
  pickup_scheduled: ['returned', 'approved', 'cancelled', 'disputed'],
  returned: ['refund_pending', 'disputed'],
  refund_pending: ['disputed'],
  partially_refunded: ['refund_pending', 'disputed'],
  refunded: ['disputed'],
  rejected: ['disputed', 'requested'],
  disputed: ['approved', 'returned', 'refund_pending', 'rejected', 'cancelled'],
  cancelled: [],
};

const money = (amount: string, extra: Row = {}): Row => ({ link_id: 'fin_link_1', canonical_id: 'fin_je_old', status: 'active', status_reason: null, amount,
  currency: 'SAR', paid_by: 'user_local', recorded_as_cash: false, linked_at: stamp, original_canonical_id: null, ...extra });

const item = (id: string, purchaseId: string, description: string, quantity: string, price: string, position: number): Row => ({
  id, owner_user_id: 'user_local', purchase_id: purchaseId, position, description, sku: null, quantity, unit_price: price, warranty_until: null, created_at: stamp,
});

const event = (id: string, purchaseId: string, kind: string, from: string | null, to: string | null, note: string | null, hoursAgo: number): Row => ({
  id, owner_user_id: 'user_local', purchase_id: purchaseId, kind, from_status: from, to_status: to, subject_id: null, note, source: 'web',
  occurred_at: new Date(now.getTime() - hoursAgo * 3_600_000).toISOString(), created_at: stamp,
});

const purchase = (id: string, extra: Row = {}): Row => ({
  id, owner_user_id: 'user_local', ownership: 'personal', order_ref: null, merchant: 'Example Shop', ordered_on: day(-5), currency: 'SAR', tax_amount: null,
  shipping_amount: null, total: null, payment_method: 'card', payment: null, document_id: null, expected_delivery_on: null, delivered_on: null, address_ref: null,
  warranty_until: null, return_window_until: null, status: 'ordered', source: 'web', needs_review: false, notes: null, archived_at: null, version: 1,
  created_at: stamp, updated_at: stamp, can_edit: true, can_pay: true, mine: true, due: [], items: [], shipments: [], returns: [], events: [], ...extra,
});

export function purchasesState({ empty = false, denied = false, failOverview = false, many = false }: { empty?: boolean; denied?: boolean; failOverview?: boolean; many?: boolean } = {}): PurchasesState {
  const purchases: Row[] = empty ? [] : [
    purchase('pur_amz', {
      merchant: 'Example Store', order_ref: 'EX-1001', ordered_on: day(-9), total: '450.00', status: 'delivered', delivered_on: day(-3), address_ref: 'Home',
      return_window_until: day(2), warranty_until: day(360), items: [item('puri_head', 'pur_amz', 'Headphones', '1.000', '300.00', 0), item('puri_cable', 'pur_amz', 'Cable', '2.000', '75.00', 1)],
      events: [event('pure_2', 'pur_amz', 'status', 'shipped', 'delivered', null, 72), event('pure_1', 'pur_amz', 'status', null, 'ordered', null, 200)],
      due: [{ purchase_id: 'pur_amz', merchant: 'Example Store', order_ref: 'EX-1001', kind: 'return_window', source_id: 'pur_amz', due_on: day(2), days_left: 2 }],
    }),
    purchase('pur_noon', {
      merchant: 'Example Market', order_ref: 'N-77', ordered_on: day(-4), total: '120.00', status: 'shipped', expected_delivery_on: day(-1), ownership: 'business',
      owner_user_id: 'user_manager', mine: false, can_edit: false, can_pay: false, payment: money('120.00', { paid_by: 'user_manager', link_id: null, canonical_id: null }),
      shipments: [{ id: 'purs_1', owner_user_id: 'user_manager', purchase_id: 'pur_noon', carrier: 'Example Courier', tracking_number: 'TRK12345678', status: 'in_transit',
        shipped_on: day(-3), expected_on: day(-1), delivered_on: null, note: null, version: 1, can_edit: true }],
      events: [event('pure_3', 'pur_noon', 'status', 'ordered', 'shipped', null, 70)],
      due: [{ purchase_id: 'pur_noon', merchant: 'Example Market', order_ref: 'N-77', kind: 'delivery_late', source_id: 'pur_noon', due_on: day(-1), days_left: -1 }],
    }),
  ];
  if (many) for (let i = 0; i < 23; i += 1) purchases.push(purchase(`pur_${i}`, { merchant: `Example Seller ${i + 1}`, order_ref: `ORD-${100 + i}`, ordered_on: day(-20 - i) }));
  return {
    purchases,
    preferences: { owner_user_id: 'user_local', reminders: true, lead_days: 3, warranty_days: 30, default_currency: 'SAR', version: 0, updated_at: null },
    candidates: [
      { canonical_id: 'fin_je_store', posted_on: day(-9), merchant: 'EXAMPLE STORE ONLINE', category: 'shopping', amount: '450.00', currency: 'SAR', remaining: '450.00', linked_to_purchase: false },
      { canonical_id: 'fin_je_cafe', posted_on: day(-8), merchant: 'EXAMPLE CAFE', category: 'dining', amount: '24.00', currency: 'SAR', remaining: '24.00', linked_to_purchase: false },
    ],
    refundCandidates: [
      { canonical_id: 'fin_je_refund', posted_on: day(-1), merchant: 'EXAMPLE STORE REFUND', category: 'shopping', amount: '150.00', currency: 'SAR', remaining: '150.00', linked_to_purchase: false },
    ],
    accounts: [{ id: 'fin_acc_cash', name: 'Wallet', kind: 'cash', currency: 'SAR' }],
    denied, failOverview, links: 1, cashTransactions: new Set(), sequence: 10,
  };
}

const refused = (status: number, detail: string, code: string, extra: Row = {}) => ({ status, body: { detail, code, ...extra } });
const refundedOf = (returns: Row[]) => returns.flatMap((r) => r.refunds as Row[]).filter((l) => l.status === 'active').reduce((s, l) => s + Number(l.amount), 0);

function view(p: Row): Row {
  const returns = p.returns as Row[];
  const refunded = refundedOf(returns);
  const items = p.items as Row[];
  const priced = items.filter((i) => i.unit_price);
  return {
    ...p, allowed_moves: MOVES[String(p.status)] ?? [], refunded: refunded ? [{ currency: 'SAR', amount: refunded.toFixed(2) }] : [],
    items_total: priced.length ? priced.reduce((s, i) => s + Number(i.unit_price) * Number(i.quantity), 0).toFixed(2) : null,
    returns: returns.map((r) => ({ ...r, allowed_moves: RETURN_MOVES[String(r.status)] ?? [], refunded: refundedOf([r]).toFixed(2),
      refunded_all: refundedOf([r]) ? [{ currency: 'SAR', amount: refundedOf([r]).toFixed(2) }] : [], merchant: p.merchant, order_ref: p.order_ref })),
    events_total: (p.events as Row[]).length,
  };
}

function addEvent(state: PurchasesState, p: Row, kind: string, from: string | null, to: string | null, note: string | null): void {
  (p.events as Row[]).unshift({ ...event(`pure_${++state.sequence}`, String(p.id), kind, from, to, note, 0), occurred_at: new Date().toISOString() });
}

function settle(state: PurchasesState, p: Row): void {
  const returns = p.returns as Row[];
  for (const r of returns) {
    const got = refundedOf([r]);
    if (got > 0) r.status = r.amount === null || got >= Number(r.amount) ? 'refunded' : 'partially_refunded';
  }
  const total = refundedOf(returns);
  const paid = Number((p.payment as Row | null)?.amount ?? p.total ?? 0);
  if (total > 0 && p.status !== 'disputed') {
    const next = paid && total >= paid ? 'refunded' : 'partially_refunded';
    if (next !== p.status) { addEvent(state, p, 'status', String(p.status), next, null); p.status = next; }
  }
}

export function purchasesReply(state: PurchasesState, method: string, url: URL, body: unknown): { status: number; body: unknown } | null {
  const path = url.pathname;
  if (path === '/api/v1/finance/accounts' && method === 'GET') return { status: 200, body: state.accounts };
  if (!path.startsWith('/api/v1/purchases')) return null;
  if (state.denied) return { status: 403, body: { error: { code: 'forbidden', message: 'Purchases are for workspace owners and members. Ask a workspace owner to change your role from viewer to member.', details: [] } } };
  const sub = path.slice('/api/v1/purchases'.length);
  const b = (body ?? {}) as Row;
  const params = url.searchParams;
  const page = (rows: Row[]) => {
    const offset = Number(params.get('offset') ?? 0), limit = Number(params.get('limit') ?? 20);
    return { status: 200, body: { items: rows.slice(offset, offset + limit), total: rows.length, limit, offset } };
  };
  const find = (id: string) => state.purchases.find((p) => p.id === id);
  const returnOf = (id: string): [Row, Row] | null => {
    for (const p of state.purchases) { const r = (p.returns as Row[]).find((x) => x.id === id); if (r) return [p, r]; }
    return null;
  };

  if (method === 'GET' && sub === '') {
    const q = (params.get('q') ?? '').toLowerCase();
    const status = params.get('status') ?? '';
    const groups: Record<string, string[]> = { awaiting: ['ordered', 'confirmed', 'shipped', 'out_for_delivery', 'partially_delivered'],
      returns: ['return_requested', 'return_approved', 'pickup_scheduled', 'returned', 'refund_pending', 'partially_refunded', 'disputed'], closed: ['refunded', 'cancelled'] };
    const merchant = (params.get('merchant') ?? '').toLowerCase();
    const from = params.get('from'), to = params.get('to'), payment = params.get('payment'), source = params.get('source');
    const rows = state.purchases.filter((p) => (params.get('archived') === 'true') === Boolean(p.archived_at))
      .filter((p) => !q || `${p.merchant} ${p.order_ref ?? ''} ${p.notes ?? ''}`.toLowerCase().includes(q))
      .filter((p) => !status || status === 'open' ? true : (groups[status] ?? [status]).includes(String(p.status)))
      .filter((p) => !merchant || String(p.merchant).toLowerCase() === merchant)
      .filter((p) => (!from || String(p.ordered_on) >= from) && (!to || String(p.ordered_on) <= to))
      .filter((p) => !source || p.source === source)
      .filter((p) => !payment || (payment === 'linked' ? (p.payment as Row | null)?.status === 'active' : payment === 'unlinked' ? !p.payment : p.payment_method === payment))
      .filter((p) => params.get('warranty') !== 'true' || (Boolean(p.warranty_until) && String(p.warranty_until) >= day(0)));
    return page(rows.map((p) => {
      const items = p.items as Row[], shipments = p.shipments as Row[];
      const { items: _i, shipments: _s, returns: _r, events: _e, ...row } = view(p);
      return { ...row, first_item: items[0]?.description ?? null, item_count: items.length, latest_shipment: shipments.length ? shipments[shipments.length - 1] : null };
    }));
  }
  if (method === 'GET' && sub === '/merchants') return { status: 200, body: { items: [...new Set(state.purchases.filter((p) => !p.archived_at).map((p) => String(p.merchant)))].sort() } };
  if (method === 'POST' && sub === '/device/share') {
    const text = String(b.text ?? '');
    const ref = text.match(/order\s+#?([A-Z0-9-]{3,})/i)?.[1];
    if (!ref) return refused(422, 'No order number, tracking number or known shop was found in the shared text. Add the purchase by hand.', 'nothing_found');
    const existing = state.purchases.find((p) => p.order_ref === ref);
    if (existing) return { status: 200, body: { status: 'updated', purchase_id: existing.id, purchase_status: existing.status, message: 'Updated.' } };
    const id = `pur_imp_${++state.sequence}`;
    const created = purchase(id, { merchant: 'Example Import Shop', order_ref: ref, source: 'web', needs_review: true });
    addEvent(state, created, 'share', null, null, null);
    state.purchases.unshift(created);
    return { status: 200, body: { status: 'created', purchase_id: id, purchase_status: 'ordered', message: 'Saved.' } };
  }
  if (method === 'POST' && sub === '') {
    const id = `pur_new_${++state.sequence}`;
    const created = purchase(id, { ...b, items: ((b.items as Row[]) ?? []).map((line, i) => item(`puri_${id}_${i}`, id, String(line.description), String(line.quantity ?? '1'), String(line.unit_price ?? ''), i)),
      status: b.status ?? 'ordered', currency: b.currency ?? 'SAR', ownership: b.ownership ?? 'personal' });
    addEvent(state, created, 'status', null, 'ordered', null);
    state.purchases.unshift(created);
    return { status: 201, body: { ...view(created), replayed: false } };
  }
  if (method === 'GET' && sub === '/overview') {
    if (state.failOverview) return { status: 503, body: { error: { code: 'unavailable', message: 'The database is not answering.', details: [] } } };
    const active = state.purchases.filter((p) => !p.archived_at);
    const spend = active.filter((p) => (p.payment as Row | null)?.status === 'active').reduce((s, p) => s + Number((p.payment as Row).amount), 0);
    const refunded = active.reduce((s, p) => s + refundedOf(p.returns as Row[]), 0);
    const rows = (value: number) => (value ? [{ currency: 'SAR', amount: value.toFixed(2) }] : []);
    const thisMonth = active.filter((p) => String(p.ordered_on).slice(0, 7) === day(0).slice(0, 7) && p.status !== 'cancelled');
    return { status: 200, body: { purchases: active.length, awaiting_delivery: active.filter((p) => ['ordered', 'confirmed', 'shipped', 'out_for_delivery'].includes(String(p.status))).length,
      late: active.filter((p) => p.expected_delivery_on && String(p.expected_delivery_on) < day(0) && p.status === 'shipped').length,
      open_returns: active.reduce((s, p) => s + (p.returns as Row[]).filter((r) => !['refunded', 'rejected', 'cancelled'].includes(String(r.status))).length, 0),
      needs_review: active.filter((p) => p.needs_review).length, unlinked: active.filter((p) => p.mine && !p.payment).length,
      spend: rows(spend), refunded: rows(refunded), net: rows(spend - refunded), refunds_owed: [],
      monthly: spend ? [{ month: day(0).slice(0, 7), currency: 'SAR', amount: spend.toFixed(2) }] : [], due: active.flatMap((p) => p.due as Row[]),
      month: day(0).slice(0, 7), orders_this_month: thisMonth.length, untotalled_this_month: thisMonth.filter((p) => !p.total).length,
      committed_this_month: rows(thisMonth.reduce((s, p) => s + Number(p.total ?? 0), 0)),
      in_transit: active.reduce((s, p) => s + (p.shipments as Row[]).filter((x) => ['in_transit', 'out_for_delivery'].includes(String(x.status))).length, 0) } };
  }
  if (method === 'GET' && sub === '/reminders') return { status: 200, body: { items: state.purchases.flatMap((p) => p.due as Row[]), lead_days: 3, warranty_days: 30 } };
  if (method === 'GET' && sub === '/preferences') return { status: 200, body: state.preferences };
  if (method === 'PUT' && sub === '/preferences') {
    state.preferences = { ...state.preferences, ...b, version: Number(b.version) + 1, updated_at: stamp };
    return { status: 200, body: state.preferences };
  }
  if (method === 'GET' && sub === '/payments') return { status: 200, body: { items: params.get('kind') === 'refund' ? state.refundCandidates : state.candidates } };
  if (method === 'GET' && sub === '/returns') {
    const open = params.get('open') !== 'false';
    const rows = state.purchases.flatMap((p) => (view(p).returns as Row[])).filter((r) => !open || !['refunded', 'rejected', 'cancelled'].includes(String(r.status)));
    return page(rows);
  }

  const shipment = sub.match(/^\/shipments\/([^/]+)$/);
  if (shipment) {
    const p = state.purchases.find((x) => (x.shipments as Row[]).some((s) => s.id === shipment[1]));
    if (!p) return refused(404, 'Shipment not found.', 'not_found');
    const s = (p.shipments as Row[]).find((x) => x.id === shipment[1])!;
    if (method === 'DELETE') { p.shipments = (p.shipments as Row[]).filter((x) => x !== s); return { status: 204, body: null }; }
    Object.assign(s, b, { version: Number(s.version) + 1 });
    if (b.status === 'delivered' && (p.shipments as Row[]).every((x) => x.status === 'delivered')) { addEvent(state, p, 'status', String(p.status), 'delivered', null); p.status = 'delivered'; }
    return { status: 200, body: s };
  }
  const ret = sub.match(/^\/returns\/([^/]+)(?:\/(status|refunds)(?:\/(cash|[^/]+))?)?$/);
  if (ret) {
    const found = returnOf(ret[1]);
    if (!found) return refused(404, 'Return not found.', 'not_found');
    const [p, r] = found;
    const [, , part, extra] = ret;
    if (!part && method === 'DELETE') { p.returns = (p.returns as Row[]).filter((x) => x !== r); return { status: 204, body: null }; }
    if (!part && method === 'PATCH') { Object.assign(r, b, { version: Number(r.version) + 1 }); return { status: 200, body: r }; }
    if (part === 'status') {
      if (!(RETURN_MOVES[String(r.status)] ?? []).includes(String(b.to))) return refused(409, 'That move is not allowed.', 'invalid_transition');
      r.status = b.to; r.version = Number(r.version) + 1;
      const target: Record<string, string> = { approved: 'return_approved', pickup_scheduled: 'pickup_scheduled', returned: 'returned', refund_pending: 'refund_pending', disputed: 'disputed' };
      if (target[String(b.to)] && (MOVES[String(p.status)] ?? []).includes(target[String(b.to)])) { addEvent(state, p, 'status', String(p.status), target[String(b.to)], null); p.status = target[String(b.to)]; }
      return { status: 200, body: r };
    }
    if (part === 'refunds' && method === 'DELETE' && extra) {
      r.refunds = (r.refunds as Row[]).filter((l) => l.link_id !== extra);
      return { status: 204, body: null };
    }
    if (part === 'refunds' && method === 'POST') {
      const original = (p.payment as Row | null)?.canonical_id ?? null;
      if (extra === 'cash') {
        state.cashTransactions.add(String(r.id));
        const link = money(String(b.amount), { link_id: `fin_link_${++state.links}`, canonical_id: `fin_je_cashback_${r.id}`, recorded_as_cash: true, original_canonical_id: original });
        (r.refunds as Row[]).push(link);
        settle(state, p);
        return { status: 201, body: { refund: link, replayed: false } };
      }
      const candidate = state.refundCandidates.find((c) => c.canonical_id === b.canonical_id);
      if (!candidate) return refused(404, 'Refund not found in your Finance ledger.', 'transaction_not_found');
      const link = money(String(candidate.remaining), { link_id: `fin_link_${++state.links}`, canonical_id: candidate.canonical_id, original_canonical_id: original });
      (r.refunds as Row[]).push(link);
      candidate.remaining = '0.00';
      settle(state, p);
      return { status: 201, body: { refund: link, replayed: false } };
    }
  }

  const nested = sub.match(/^\/([^/]+)(?:\/(archive|status|events|notes|shipments|returns|payment)(?:\/(cash))?)?$/);
  if (!nested) return refused(404, 'Not found.', 'not_found');
  const [, purchaseId, part, cash] = nested;
  const p = find(purchaseId);
  if (!p) return refused(404, "Purchase not found. It may have been deleted, or it is someone's personal purchase.", 'not_found');
  if (!part && method === 'GET') return { status: 200, body: view(p) };
  if (!part && method === 'PATCH') {
    if (Number(b.version) !== Number(p.version)) return refused(409, 'This purchase changed since you opened it. Reload and try again.', 'stale');
    const { items, ...rest } = b;
    Object.assign(p, rest, { version: Number(p.version) + 1 });
    if (Array.isArray(items)) p.items = (items as Row[]).map((line, i) => item(String(line.id ?? `puri_${p.id}_${++state.sequence}`), String(p.id), String(line.description), String(line.quantity ?? '1'), String(line.unit_price ?? ''), i));
    addEvent(state, p, 'note', null, null, 'Details changed');
    return { status: 200, body: view(p) };
  }
  if (!part && method === 'DELETE') {
    if (p.payment) return refused(409, 'This purchase has a payment or refunds linked from Finance. Remove those links, or archive it to keep its history.', 'has_payments');
    state.purchases = state.purchases.filter((x) => x !== p);
    return { status: 204, body: null };
  }
  if (part === 'archive') { p.archived_at = b.archived ? stamp : null; p.version = Number(p.version) + 1; return { status: 200, body: view(p) }; }
  if (part === 'status') {
    if (Number(b.version) !== Number(p.version)) return refused(409, 'This purchase changed since you opened it. Reload and try again.', 'stale');
    if (!(MOVES[String(p.status)] ?? []).includes(String(b.to))) return refused(409, 'That move is not allowed.', 'invalid_transition', { allowed: MOVES[String(p.status)] ?? [] });
    addEvent(state, p, 'status', String(p.status), String(b.to), (b.note as string | null) ?? null);
    p.status = b.to; p.version = Number(p.version) + 1;
    return { status: 200, body: view(p) };
  }
  if (part === 'events') return page(p.events as Row[]);
  if (part === 'notes') { addEvent(state, p, 'note', null, null, String(b.note)); return { status: 201, body: (p.events as Row[])[0] }; }
  if (part === 'shipments') {
    const created = { id: `purs_${++state.sequence}`, owner_user_id: 'user_local', purchase_id: p.id, carrier: b.carrier ?? null, tracking_number: b.tracking_number ?? null,
      status: b.status ?? 'in_transit', shipped_on: b.shipped_on ?? null, expected_on: b.expected_on ?? null, delivered_on: b.delivered_on ?? null, note: b.note ?? null, version: 1, can_edit: true };
    (p.shipments as Row[]).push(created);
    if (['ordered', 'confirmed'].includes(String(p.status))) { addEvent(state, p, 'status', String(p.status), 'shipped', null); p.status = 'shipped'; }
    return { status: 201, body: created };
  }
  if (part === 'returns') {
    const lines = ((b.items as Row[]) ?? []);
    const priced = lines.map((l) => { const it = (p.items as Row[]).find((i) => i.id === l.item_id); return Number(it?.unit_price ?? 0) * Number(l.quantity); }).reduce((s, v) => s + v, 0);
    const created = { id: `purr_${++state.sequence}`, owner_user_id: 'user_local', purchase_id: p.id, status: 'requested', requested_on: day(0), reason: b.reason, reason_note: b.reason_note ?? null,
      items: lines, amount: b.amount ?? (priced ? priced.toFixed(2) : p.total), currency: 'SAR', deadline_on: b.deadline_on ?? null, pickup_on: b.pickup_on ?? null, courier: b.courier ?? null,
      tracking_number: b.tracking_number ?? null, proof_document_id: b.proof_document_id ?? null, confirmation_ref: b.confirmation_ref ?? null, refund_ref: null, returned_on: null,
      expected_refund_on: b.expected_refund_on ?? null, refunded_on: null, dispute_note: null, refunds: [], version: 1, can_edit: true, can_refund: Boolean(p.mine) };
    (p.returns as Row[]).push(created);
    if ((MOVES[String(p.status)] ?? []).includes('return_requested')) { addEvent(state, p, 'status', String(p.status), 'return_requested', null); p.status = 'return_requested'; }
    return { status: 201, body: { ...created, replayed: false } };
  }
  if (part === 'payment') {
    if (method === 'DELETE') { p.payment = null; return { status: 204, body: null }; }
    const current = p.payment as Row | null;
    if (cash) {
      if (current && current.recorded_as_cash) return { status: 200, body: { payment: current, replayed: true } };
      state.cashTransactions.add(String(p.id));
      p.payment = money(String(b.amount), { link_id: `fin_link_${++state.links}`, canonical_id: `fin_je_cash_${p.id}`, recorded_as_cash: true });
      return { status: 201, body: { payment: p.payment, replayed: false } };
    }
    if (current && current.canonical_id === b.canonical_id) return { status: 200, body: { payment: current, replayed: true } };
    const candidate = state.candidates.find((c) => c.canonical_id === b.canonical_id);
    if (!candidate) return refused(404, 'Payment not found in your Finance ledger.', 'transaction_not_found');
    p.payment = money(String(candidate.remaining), { link_id: `fin_link_${++state.links}`, canonical_id: candidate.canonical_id });
    candidate.remaining = '0.00';
    return { status: 201, body: { payment: p.payment, replayed: false } };
  }
  return refused(404, 'Not found.', 'not_found');
}
