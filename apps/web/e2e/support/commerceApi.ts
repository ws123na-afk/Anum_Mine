// Mocked /api/v1/commerce answers (docs/commerce.md) shared by commerce.spec.ts and the
// workspace mock (accessibility and localization specs). Synthetic values only.
const now = new Date();
const day = (offset: number) => { const d = new Date(now); d.setDate(d.getDate() - offset); return d.toISOString().slice(0, 10); };
const iso = (hoursAgo: number) => new Date(now.getTime() - hoursAgo * 3600_000).toISOString();

export const commerceHash = '5e'.repeat(32);

export function commerceState(role: 'owner' | 'member' | 'viewer' = 'owner', { connected = true }: { connected?: boolean } = {}) {
  const permissions = { read: true, operate: role !== 'viewer', manage: role === 'owner', pii: role === 'owner' };
  const connector = {
    id: 'conn_amazon', channel: 'amazon', name: 'Amazon KSA + UAE', marketplaces: ['A17E79C6D8DWNP', 'A2VIGQ35RCS4UG'], marketplace_names: ['Amazon.sa', 'Amazon.ae'],
    settings: { seller_id: 'AEXAMPLESELLER1', fetch_buyer_details: false }, enabled: true, credentials_configured: true, credential_hint: 'app …mple', health: 'ok',
    last_error: null, last_sync_at: iso(1), last_success_at: iso(1), next_sync_at: iso(-0.5), sync_counts: { orders: 42, listings: 18 },
    capabilities: ['buyer_details', 'competition', 'fba_inventory', 'finances', 'listings', 'orders', 'update_price', 'update_stock'], webhook_path: null,
    created_at: iso(200), updated_at: iso(1),
  };
  const store = {
    ...connector, id: 'conn_store', channel: 'own_store', name: 'ANUM 360', marketplaces: ['own'], marketplace_names: ['Own store'],
    settings: { mode: 'anum360', base_url: 'https://store.example.test', currency: 'AED' }, credential_hint: 'an…@store.example.test', health: 'auth_failed',
    last_error: 'The store asks this staff account for a two-step code. Connect ANUM with a staff account that holds only view keys.', capabilities: ['listings', 'orders', 'returns', 'update_price', 'update_stock'],
  };
  const listingSa = { id: 'clist_sa', connector_id: 'conn_amazon', channel: 'amazon', marketplace_id: 'A17E79C6D8DWNP', marketplace: 'Amazon.sa', sku: 'MX-2055516', external_id: 'B0EXAMPLE1', title: 'Michelin Primacy 4 205/55R16 91V', price: 450, currency: 'SAR', quantity: 3, status: 'active', fulfillment: 'merchant', buy_box_price: 430, buy_box_winner: false, lowest_competitor_price: 430, updated_at: iso(1) };
  const listingAe = { ...listingSa, id: 'clist_ae', marketplace_id: 'A2VIGQ35RCS4UG', marketplace: 'Amazon.ae', price: 420, currency: 'AED', quantity: 10, fulfillment: 'amazon', buy_box_winner: true, buy_box_price: 420, lowest_competitor_price: null };
  const product = {
    id: 'cprod_mx', sku: 'MX-2055516', title: 'Michelin Primacy 4 205/55R16 91V', title_ar: 'ميشلان برايمسي 4 مقاس 205/55R16', brand: 'Michelin',
    tyre: { size: '205/55R16', width_mm: 205, aspect_ratio: 55, construction: 'R', rim_inches: 16, load_index: '91', speed_index: 'V', dot_week: 23, dot_year: 2025 },
    cost_price: null, currency: 'SAR', low_stock_threshold: null, listings: [listingSa, listingAe], total_stock: 13, created_at: iso(300), updated_at: iso(1),
  };
  const products = [product, ...Array.from({ length: 24 }, (_, i) => ({ ...product, id: `cprod_${i}`, sku: `BR-${String(i).padStart(3, '0')}`, title: `Bridgestone Turanza ${i}`, title_ar: null, tyre: { size: '225/60R17' }, listings: [], total_stock: null }))];
  const order = (i: number, status: string, late = false) => ({
    id: `cord_${i}`, connector_id: 'conn_amazon', channel: i % 3 === 2 ? 'noon' : 'amazon', marketplace_id: 'A17E79C6D8DWNP', marketplace: i % 3 === 2 ? 'noon Saudi' : 'Amazon.sa',
    external_id: `402-0000${String(i).padStart(3, '0')}-0000001`, status, purchased_at: iso(3 + i), ship_by: late ? iso(1) : iso(-20), total_amount: 900 + i, currency: 'SAR', item_count: 2,
    fulfillment: 'merchant', ship_city: 'Riyadh', ship_country: 'SA', lines: [{ sku: 'MX-2055516', external_line_id: 'l1', title: 'Michelin Primacy 4', quantity: 2, unit_price: 450, total: 900 }], late,
  });
  const orders = [order(1, 'unshipped', true), ...Array.from({ length: 29 }, (_, i) => order(i + 2, i % 4 === 0 ? 'unshipped' : 'shipped'))];
  const alerts = [
    { id: 'calert_low', kind: 'low_stock', severity: 'warning', title: 'Low stock: MX-2055516 on Amazon.sa', detail: 'Michelin Primacy 4: 3 left (alert at 4 or fewer).', subject: 'MX-2055516', channel: 'amazon', connector_id: 'conn_amazon', status: 'open', acknowledged_by: null, created_at: iso(1), updated_at: iso(1) },
    { id: 'calert_late', kind: 'late_shipment', severity: 'critical', title: 'Not shipped in time: order 402-0000001-0000001 (Amazon)', detail: 'Ship-by passed 1 h ago.', subject: 'cord_1', channel: 'amazon', connector_id: 'conn_amazon', status: 'open', acknowledged_by: null, created_at: iso(1), updated_at: iso(1) },
  ];
  const by_day = Array.from({ length: 30 }, (_, i) => [
    { date: day(i), channel: 'amazon', revenue: 800 + ((i * 137) % 900), orders: 2 + (i % 3), units: 4 + (i % 5) },
    ...(i % 2 ? [{ date: day(i), channel: 'noon', revenue: 300 + ((i * 71) % 400), orders: 1, units: 2 }] : []),
  ]).flat();
  const dashboard = {
    start: day(29), end: day(0), tz: 'Asia/Riyadh', currency: 'SAR',
    currencies: [{ currency: 'SAR', revenue: 41250.5, orders: 96, units: 210 }, { currency: 'AED', revenue: 4100, orders: 9, units: 14 }],
    by_channel: [
      { channel: 'amazon', label: 'Amazon', marketplace_id: 'A17E79C6D8DWNP', marketplace: 'Amazon.sa', currency: 'SAR', revenue: 35250.5, orders: 80, units: 180 },
      { channel: 'noon', label: 'noon', marketplace_id: 'SA', marketplace: 'noon Saudi', currency: 'SAR', revenue: 6000, orders: 16, units: 30 },
      { channel: 'amazon', label: 'Amazon', marketplace_id: 'A2VIGQ35RCS4UG', marketplace: 'Amazon.ae', currency: 'AED', revenue: 4100, orders: 9, units: 14 },
    ],
    by_day,
    top_products: [{ sku: 'MX-2055516', title: 'Michelin Primacy 4 205/55R16 91V', title_ar: 'ميشلان برايمسي 4 مقاس 205/55R16', tyre_size: '205/55R16', units: 64, revenue: 14400, currency: 'SAR' }],
    pending_orders: [{ id: 'cord_1', external_id: '402-0000001-0000001', channel: 'amazon', marketplace: 'Amazon.sa', status: 'unshipped', purchased_at: iso(4), ship_by: iso(1), late: true, total_amount: 901, currency: 'SAR', item_count: 2 }],
    pending_total: 8, late_total: 1,
    low_stock: [{ listing_id: 'clist_sa', sku: 'MX-2055516', title: 'Michelin Primacy 4', channel: 'amazon', marketplace: 'Amazon.sa', quantity: 3, threshold: 4 }],
    returns: { count: 3, units: 3, amount: { SAR: 1350 }, rate: 0.0143 },
    price_alerts: [{ listing_id: 'clist_sa', sku: 'MX-2055516', marketplace: 'Amazon.sa', price: 450, buy_box_price: 430, buy_box_winner: false, lowest_competitor_price: 430, currency: 'SAR' }],
    open_alerts: 2, fees: { SAR: -3980.25 }, truncated: false, connectors: connected ? 2 : 0,
  };
  return {
    permissions, setup: { permissions, connectors: connected ? 2 : 0, channels: [], own_store_modes: ['anum360', 'rest', 'supabase', 'webhook'], sync_scheduler: true },
    connectors: connected ? [connector, store] : [], products, orders, alerts, dashboard,
    settings: { low_stock_threshold: 4, handling_hours: 48, return_spike_count: 3, return_spike_ratio: 0.1, price_alert_percent: 2, sync_interval_minutes: 30, updated_at: null },
    approval: {
      id: 'approval_price', task_id: 'task_price', action: 'commerce.amazon.update_price', risk_level: 'high', status: 'pending', reason: 'External or high-impact actions require explicit approval.',
      created_at: iso(0), run_id: 'run_price', step_id: 'step_price', payload_hash: commerceHash, expires_at: iso(-6), target: 'sellingpartnerapi-eu.amazon.com',
      arguments: { connector_id: 'conn_amazon', listing_id: 'clist_sa', sku: 'MX-2055516', marketplace_id: 'A17E79C6D8DWNP', marketplace: 'Amazon.sa', price: 435, currency: 'SAR', previous_price: 450, target_host: 'sellingpartnerapi-eu.amazon.com' },
    },
  };
}

type State = ReturnType<typeof commerceState>;
type Reply = { status: number; body: unknown };

/** The answer for a commerce request, or null when the path is not a commerce route. */
export function commerceReply(state: State, method: string, url: URL, body: unknown): Reply | null {
  const path = url.pathname;
  if (!path.startsWith('/api/v1/commerce')) return null;
  const ok = (value: unknown, status = 200): Reply => ({ status, body: value });
  const denied: Reply = { status: 403, body: { error: { code: 'forbidden', message: 'Only workspace owners can connect or change sales channels.', correlation_id: 'c' } } };
  const page = <T>(items: T[]) => {
    const limit = Number(url.searchParams.get('limit') ?? 25), offset = Number(url.searchParams.get('offset') ?? 0);
    return ok({ items: items.slice(offset, offset + limit), total: items.length, limit, offset });
  };
  const rest = path.slice('/api/v1/commerce'.length);
  if (rest === '/setup') return ok(state.setup);
  if (rest === '/dashboard') {
    const currency = url.searchParams.get('currency');
    if (currency === 'AED') return ok({ ...state.dashboard, currency: 'AED', by_day: state.dashboard.by_day.slice(0, 3).map((row) => ({ ...row, revenue: row.revenue / 10 })) });
    return ok(state.dashboard);
  }
  if (rest === '/connectors' && method === 'GET') return ok(state.connectors);
  if (rest === '/connectors' && method === 'POST') {
    if (!state.permissions.manage) return denied;
    const input = body as { channel: string; name?: string };
    const created = { ...state.connectors[0] ?? commerceState('owner').connectors[0], id: 'conn_new', channel: input.channel, name: input.name || 'noon Saudi', health: 'pending', last_sync_at: null, sync_counts: {} };
    state.connectors.push(created);
    state.setup.connectors = state.connectors.length;
    return ok({ connector: created, webhook_secret: null }, 201);
  }
  const sync = rest.match(/^\/connectors\/([^/]+)\/sync$/);
  if (sync) return state.permissions.operate ? ok({ connector_id: sync[1], status: 'ok', message: 'Up to date.', counts: { orders: 2, listings: 3 }, alerts_raised: 1 }) : denied;
  const test = rest.match(/^\/connectors\/([^/]+)\/test$/);
  if (test) return state.permissions.manage ? ok({ ok: true, message: 'Connected to Amazon.sa, Amazon.ae', connector: state.connectors[0] }) : denied;
  if (rest === '/products' && method === 'GET') {
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    return page(state.products.filter((p) => !q || p.sku.toLowerCase().includes(q) || p.title.toLowerCase().includes(q) || (p.tyre.size ?? '').replace(/\D/g, '').includes(q.replace(/\D/g, '') || '§')));
  }
  if (rest === '/products' && method === 'POST') return state.permissions.operate ? ok({ ...state.products[1], ...(body as object), id: 'cprod_new', listings: [], total_stock: null }, 201) : denied;
  const priceChange = rest.match(/^\/listings\/([^/]+)\/(price|stock)-change$/);
  if (priceChange) return state.permissions.operate ? ok({ listing: state.products[0].listings[0], approval: state.approval, task_id: 'task_price' }) : denied;
  if (rest === '/orders') {
    const status = url.searchParams.get('status');
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    return page(state.orders.filter((o) => (!status || (status === 'open' ? o.status === 'unshipped' : o.status === status)) && (!q || o.external_id.includes(q))));
  }
  const orderDetail = rest.match(/^\/orders\/([^/]+)$/);
  if (orderDetail) {
    const found = state.orders.find((o) => o.id === orderDetail[1]);
    return found ? ok({ ...found, customer: state.permissions.pii ? { id: 'ccust_1', city: 'Riyadh', country: 'SA', orders_count: 1, pii_visible: true, name: 'Example Buyer', email: 'buyer@example.test', phone: '+966500000000', address: '1 Example Street, Riyadh' } : { id: 'ccust_1', city: 'Riyadh', country: 'SA', orders_count: 1, pii_visible: false, name: null, email: null, phone: null, address: null }, returns: [] }) : ok({ error: { code: 'not_found', message: 'Order not found' } }, 404);
  }
  if (rest === '/returns') {
    const days = Number(url.searchParams.get('days') ?? 30);
    const at = (d: number) => new Date(Date.now() - d * 86_400_000).toISOString();
    const rows = [
      { id: 'cret_1', channel: 'amazon', order_external_id: '402-0000001-0000001', sku: 'MX-2055516', quantity: 1, reason: 'Wrong size ordered', status: 'refunded', amount: 450, currency: 'SAR', returned_at: at(2) },
      { id: 'cret_2', channel: 'noon', order_external_id: 'NSA-1001', sku: 'MX-2055516', quantity: 1, reason: null, status: 'requested', amount: null, currency: 'SAR', returned_at: at(9) },
      { id: 'cret_3', channel: 'amazon', order_external_id: '402-0000002-0000002', sku: 'BR-2156517', quantity: 1, reason: 'Damaged in transit', status: 'refunded', amount: 900, currency: 'SAR', returned_at: at(45) },
    ].filter((row) => Date.parse(row.returned_at) >= Date.now() - days * 86_400_000);
    return page(rows);
  }
  if (rest === '/summary') {
    const period = url.searchParams.get('period');
    const arabic = url.searchParams.get('lang') === 'ar';
    const data = period === 'today'
      ? { period, channel: null, start: '2026-10-12', end: '2026-10-12', orders: 4, units: 9, revenue: { SAR: 1980, AED: 410 }, top_skus: [['MX-2055516', 4]] }
      : { period, channel: null, start: '2026-10-06', end: '2026-10-12', orders: 31, units: 70, revenue: { SAR: 12600 }, top_skus: [['MX-2055516', 22], ['BR-2156517', 9]] };
    const text = arabic ? `مبيعاتك: ${data.orders} طلب، ${data.units} قطعة.` : `Sales across all channels: ${data.orders} orders, ${data.units} units.`;
    return ok({ text, data });
  }
  if (rest === '/alerts') {
    const status = url.searchParams.get('status') ?? 'open';
    return page(state.alerts.filter((a) => a.status === status));
  }
  const ack = rest.match(/^\/alerts\/([^/]+)\/acknowledge$/);
  if (ack) {
    if (!state.permissions.operate) return denied;
    const alert = state.alerts.find((a) => a.id === ack[1]);
    if (alert) { alert.status = 'acknowledged'; alert.acknowledged_by = 'user_local'; }
    return ok(alert);
  }
  if (rest === '/settings' && method === 'GET') return ok(state.settings);
  if (rest === '/settings' && method === 'PUT') return state.permissions.manage ? ok(body) : denied;
  return null;
}
