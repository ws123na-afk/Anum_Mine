import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../data/readable_error.dart';

/// IBKR trading on the phone (docs/finance-live-trading.md#on-the-phone):
/// the connection, the paper period, the live gates, IBKR's own figures,
/// the orders and the approvals that wait. The phone approves an order the
/// same way the web does (`POST /intents/{id}/approve` with the payload hash
/// the owner was shown, single use, expiring, checked again by the server),
/// rejects it, and can always stop everything. The order ticket makes a
/// request that waits for that same approval (nothing is sent by making it),
/// limits are read and changed by the owner, alerts are read and marked
/// read, and an order is checked, looked up and sent, or cancelled, as on
/// the web. Connecting, buy plans, the allowed list and the live switch stay
/// on the web.

/// One gate or paper proof check: the code names it, the message is the
/// server's own English sentence with the figures.
class IbkrCheck {
  const IbkrCheck(
      {required this.code, required this.ok, required this.message});
  final String code, message;
  final bool ok;

  factory IbkrCheck.fromJson(JsonReader j) => IbkrCheck(
      code: j.string('code'),
      ok: j.boolean('ok'),
      message: j.optString('message') ?? '');

  /// "5 of 20 ..." in the message, as done and needed; null without one.
  (int, int)? get progress {
    final m = RegExp(r'(\d+) of (\d+)').firstMatch(message);
    if (m == null) return null;
    return (int.parse(m.group(1)!), int.parse(m.group(2)!));
  }
}

class IbkrConnection {
  const IbkrConnection(
      {required this.id,
      required this.environment,
      required this.hint,
      required this.session,
      this.sessionMessage,
      this.verifiedAt,
      this.lastSyncAt,
      this.lastError});
  final String id, environment, hint, session;
  final String? sessionMessage, lastError;
  final DateTime? verifiedAt, lastSyncAt;

  factory IbkrConnection.fromJson(JsonReader j) => IbkrConnection(
        id: j.string('id'),
        environment: j.string('environment'),
        hint: j.optString('hint') ?? '-',
        session: j.optString('session') ?? 'unknown',
        sessionMessage: j.optString('session_message'),
        verifiedAt: j.optDate('verified_at'),
        lastSyncAt: j.optDate('last_sync_at'),
        lastError: j.optString('last_error'),
      );
}

class IbkrPosition {
  const IbkrPosition(
      {required this.symbol,
      required this.quantity,
      this.marketValue,
      this.unrealized});
  final String symbol, quantity;
  final String? marketValue, unrealized;
}

/// IBKR's own figures from the last read (never a forecast).
class IbkrSnapshot {
  const IbkrSnapshot(
      {this.syncedAt,
      this.cash,
      this.net,
      this.today,
      this.unrealized,
      this.realized,
      this.positions = const []});
  final DateTime? syncedAt;
  final String? cash, net, today, unrealized, realized;
  final List<IbkrPosition> positions;

  factory IbkrSnapshot.fromJson(JsonReader? j) {
    if (j == null) return const IbkrSnapshot();
    String? usd(String key) {
      final m = j.optMap(key);
      final v = m?['USD'];
      return v is String ? v : null;
    }

    String? text(String key) {
      final v = j.json[key];
      return v is String ? v : (v is num ? '$v' : null);
    }

    return IbkrSnapshot(
      syncedAt: j.optDate('synced_at'),
      cash: usd('settled_cash') ?? usd('cash'),
      net: text('net_liquidation'),
      today: text('daily_pnl'),
      unrealized: text('unrealized_pnl'),
      realized: text('realized_pnl'),
      positions: j.optObjects('positions', (p) {
        String? s(String k) {
          final v = p.json[k];
          return v is String ? v : (v is num ? '$v' : null);
        }

        return IbkrPosition(
            symbol: s('symbol') ?? '-',
            quantity: s('quantity') ?? '-',
            marketValue: s('market_value'),
            unrealized: s('unrealized_pnl'));
      }),
    );
  }
}

/// An order request waiting for the owner's approval, exactly as the API
/// shows it; [payloadHash] binds the stored values.
class IbkrIntent {
  const IbkrIntent(
      {required this.id,
      required this.environment,
      required this.symbol,
      required this.side,
      required this.quantity,
      required this.limitPrice,
      required this.amount,
      required this.payloadHash,
      required this.expiresAt,
      this.requestedBy = 'person',
      this.reason});
  final String id, environment, symbol, side, quantity, limitPrice, amount;
  final String payloadHash, requestedBy;
  final String? reason;
  final DateTime expiresAt;

  bool get live => environment == 'live';

  factory IbkrIntent.fromJson(JsonReader j) {
    String s(String k) {
      final v = j.json[k];
      return v is String ? v : (v is num ? '$v' : '-');
    }

    return IbkrIntent(
      id: j.string('id'),
      environment: j.optString('environment') ??
          (j.optString('mode') == 'live' ? 'live' : 'paper'),
      symbol: s('symbol'),
      side: j.optString('side') ?? 'buy',
      quantity: s('quantity'),
      limitPrice: s('limit_price'),
      amount: s('amount'),
      payloadHash: j.string('payload_hash'),
      expiresAt: j.date('expires_at'),
      requestedBy: j.optString('requested_by') ?? 'person',
      reason: j.optString('reason'),
    );
  }

  /// Whole seconds left before the approval lapses (0 once it has).
  int secondsLeft(DateTime now) {
    final left = expiresAt.difference(now).inSeconds;
    return left < 0 ? 0 : left;
  }
}

class IbkrOrder {
  const IbkrOrder(
      {required this.id,
      required this.environment,
      required this.symbol,
      required this.side,
      required this.quantity,
      required this.limitPrice,
      required this.state,
      required this.filledQuantity,
      this.averagePrice,
      this.message,
      this.createdAt});
  final String id, environment, symbol, side, quantity, limitPrice, state;
  final String filledQuantity;
  final String? averagePrice, message;
  final DateTime? createdAt;

  factory IbkrOrder.fromJson(JsonReader j) {
    String s(String k, [String fallback = '-']) {
      final v = j.json[k];
      return v is String ? v : (v is num ? '$v' : fallback);
    }

    return IbkrOrder(
      id: j.string('id'),
      environment: j.optString('environment') ?? 'paper',
      symbol: s('symbol'),
      side: j.optString('side') ?? 'buy',
      quantity: s('quantity'),
      limitPrice: s('limit_price'),
      state: j.optString('state') ?? 'unknown',
      filledQuantity: s('filled_quantity', '0'),
      averagePrice: j.json['average_price'] == null ? null : s('average_price'),
      message: j.optString('message'),
      createdAt: j.optDate('created_at'),
    );
  }

  bool get filled =>
      double.tryParse(filledQuantity) != null &&
      double.parse(filledQuantity) > 0;
}

class IbkrOverview {
  const IbkrOverview(
      {required this.mode,
      required this.deploymentLiveEnabled,
      required this.killSwitch,
      required this.connections,
      required this.snapshots,
      required this.liveGates,
      required this.paperProof,
      required this.marketOpen,
      required this.marketMessage,
      required this.pending,
      required this.reconciliation,
      this.unreadAlerts = 0,
      this.approvalMinutes = 5,
      this.paperMinOrders = 20,
      this.paperMinDays = 14,
      this.riskNote = '',
      this.limits = const {},
      this.settingsVersion = 0,
      this.liveOn = false,
      this.allowed = const [],
      this.fractionalOk = false});
  final String mode;
  final bool deploymentLiveEnabled, killSwitch, marketOpen;
  final String marketMessage, riskNote;
  final List<IbkrConnection> connections;
  final Map<String, IbkrSnapshot> snapshots;
  final Map<String, String> reconciliation;
  final List<IbkrCheck> liveGates, paperProof;
  final List<IbkrIntent> pending;
  final int unreadAlerts, approvalMinutes, paperMinOrders, paperMinDays;

  /// The owner's hard limits as the API sent them (field name to text).
  final Map<String, String> limits;
  final int settingsVersion;

  /// Live orders are switched on: limits may then only be made stricter.
  final bool liveOn;
  final List<IbkrAllowed> allowed;
  final bool fractionalOk;

  factory IbkrOverview.fromJson(JsonReader j) {
    final settings = j.object('settings');
    final snaps = j.optObject('snapshots');
    final market = j.optObject('market');
    final recon = settings.optObject('reconciliation');
    return IbkrOverview(
      mode: j.optString('mode') ?? 'paper',
      deploymentLiveEnabled: j.optBool('deployment_live_enabled') ?? false,
      killSwitch: settings.optBool('kill_switch') ?? false,
      connections: j.optObjects('connections', IbkrConnection.fromJson),
      snapshots: {
        for (final env in const ['paper', 'live'])
          if (snaps?.optObject(env) case final s?) env: IbkrSnapshot.fromJson(s)
      },
      reconciliation: {
        for (final env in const ['paper', 'live'])
          if (recon?.optObject(env) case final r?)
            env: r.optString('state') ?? 'not_checked'
      },
      liveGates: j.optObjects('live_gates', IbkrCheck.fromJson),
      paperProof: j.optObjects('paper_proof', IbkrCheck.fromJson),
      marketOpen: market?.optBool('open') ?? false,
      marketMessage: market?.optString('message') ?? '',
      pending: j.optObjects('pending', IbkrIntent.fromJson),
      unreadAlerts: j.optInt('unread_alerts') ?? 0,
      approvalMinutes: j.optInt('approval_minutes') ?? 5,
      paperMinOrders: j.optInt('paper_min_orders') ?? 20,
      paperMinDays: j.optInt('paper_min_days') ?? 14,
      riskNote: j.optString('risk_note') ?? '',
      limits: {
        for (final e
            in (settings.optMap('limits') ?? const <String, Object?>{}).entries)
          if (e.value is String || e.value is num) e.key: '${e.value}'
      },
      settingsVersion: settings.optInt('version') ?? 0,
      liveOn: settings.optObject('live')?.optBool('on') ?? false,
      allowed: settings.optObjects(
          'allowed',
          (a) => IbkrAllowed(
              symbol: a.string('symbol'),
              shariah: a.optBool('shariah_screened') ?? false)),
      fractionalOk: settings.optBool('fractional_ok') ?? false,
    );
  }

  IbkrConnection? connection(String environment) {
    for (final c in connections) {
      if (c.environment == environment) return c;
    }
    return null;
  }

  bool get paperDone => paperProof.isNotEmpty && paperProof.every((c) => c.ok);
}

/// An ETF on the owner's allowed list (the order ticket offers only these).
class IbkrAllowed {
  const IbkrAllowed({required this.symbol, this.shariah = false});
  final String symbol;
  final bool shariah;
}

/// The limit fields in the web's order (docs/finance-live-trading.md#limits).
const ibkrLimitFields = [
  'max_capital',
  'max_order',
  'max_daily_loss',
  'max_orders_per_day',
  'price_band_percent',
  'session_buffer_minutes',
  'fee_buffer',
];

/// IBKR's price for an allowed ETF, for the ticket.
class IbkrQuote {
  const IbkrQuote(
      {required this.symbol,
      this.last,
      this.delayed = false,
      this.bandPercent = ''});
  final String symbol;
  final String? last;
  final bool delayed;
  final String bandPercent;

  factory IbkrQuote.fromJson(JsonReader j) => IbkrQuote(
      symbol: j.optString('symbol') ?? '',
      last: j.optString('last'),
      delayed: j.optBool('delayed') ?? false,
      bandPercent: j.optString('band_percent') ?? '');
}

/// One IBKR alert (a refused order, a session expiry, a mismatch), newest first.
class IbkrAlert {
  const IbkrAlert(
      {required this.id,
      required this.severity,
      required this.message,
      this.environment,
      this.createdAt,
      this.read = false});
  final String id, severity, message;
  final String? environment;
  final DateTime? createdAt;
  final bool read;

  factory IbkrAlert.fromJson(JsonReader j) => IbkrAlert(
      id: j.string('id'),
      severity: j.optString('severity') ?? 'info',
      message: j.optString('message') ?? '',
      environment: j.optString('environment'),
      createdAt: j.optDate('created_at'),
      read: j.optString('read_at') != null);
}

/// The order ticket's own checks before anything is sent (the server checks
/// everything again), as `checkTicket` on the web: null when it may go.
String? ibkrTicketProblem(
    {required String symbol,
    required String quantity,
    required String limitPrice,
    required List<String> allowed,
    required bool fractionalOk,
    required String maxOrder}) {
  if (!allowed.contains(symbol)) return 'symbol';
  final q = _scaled(quantity);
  if (q == null || q <= BigInt.zero) return 'quantity';
  if (!fractionalOk && q % BigInt.from(10000) != BigInt.zero) {
    return 'wholeShares';
  }
  final p = _scaled(limitPrice);
  if (p == null || p <= BigInt.zero) return 'price';
  final total = ibkrNotional(quantity, limitPrice);
  final cap = _scaled(maxOrder);
  if (total != null && cap != null && (_scaled(total) ?? BigInt.zero) > cap) {
    return 'overOrder';
  }
  return null;
}

final _decimal = RegExp(r'^\d{1,10}(\.\d{1,4})?$');

/// A decimal text as a count of 1/10000, or null.
BigInt? _scaled(String text) {
  final value = text.trim();
  if (!_decimal.hasMatch(value)) return null;
  final parts = value.split('.');
  final fraction = (parts.length > 1 ? parts[1] : '').padRight(4, '0');
  return BigInt.parse(parts[0]) * BigInt.from(10000) +
      BigInt.parse(fraction.substring(0, 4));
}

/// quantity times price, rounded half up to cents ("12.34"); exact.
String? ibkrNotional(String quantity, String price) {
  final q = _scaled(quantity), p = _scaled(price);
  if (q == null || p == null) return null;
  final cents = (q * p + BigInt.from(500000)) ~/ BigInt.from(1000000);
  final whole = cents ~/ BigInt.from(100);
  final rest = (cents % BigInt.from(100)).toString().padLeft(2, '0');
  return '$whole.$rest';
}

/// Order states where Check at IBKR applies (`OPEN_ORDER_STATES` on the web).
const ibkrOpenStates = {
  'submitting',
  'submitted',
  'partially_filled',
  'cancel_requested',
  'unknown'
};

class IbkrOrderPage {
  const IbkrOrderPage({required this.items, required this.total});
  final List<IbkrOrder> items;
  final int total;
}

/// What the kill switch did: how many open orders it cancelled and whether
/// every cancel was confirmed at IBKR.
class IbkrKillReport {
  const IbkrKillReport({required this.cancelled, required this.allConfirmed});
  final int cancelled;
  final bool allConfirmed;

  factory IbkrKillReport.fromJson(JsonReader j) {
    var cancelled = 0;
    var all = true;
    for (final env in j.optObjects('report', (r) => r)) {
      if (env.optString('error') != null) all = false;
      for (final o in env.optObjects('orders', (o) => o)) {
        if (o.optBool('cancelled') ?? false) {
          cancelled++;
        } else {
          all = false;
        }
      }
    }
    return IbkrKillReport(cancelled: cancelled, allConfirmed: all);
  }
}

abstract interface class IbkrRepository {
  Future<IbkrOverview> overview();
  Future<IbkrOrderPage> orders({int offset = 0, int limit = 25});

  /// The order IBKR answered with after the approval.
  Future<IbkrOrder> approve(IbkrIntent intent);
  Future<void> reject(IbkrIntent intent);
  Future<IbkrKillReport> kill(String reason);
  Future<void> release();

  /// IBKR's price for an allowed ETF.
  Future<IbkrQuote> quote(String symbol);

  /// The order ticket: a limit order request that waits for approval.
  Future<IbkrIntent> createOrder(
      {required String symbol,
      required String side,
      required String quantity,
      required String limitPrice,
      String? reason});
  Future<void> saveLimits(Map<String, Object> limits, int version);
  Future<(List<IbkrAlert>, int)> alerts({int limit = 10});
  Future<void> markAlertsRead();

  /// Check at IBKR (refresh), Look up and send, Cancel.
  Future<IbkrOrder> refreshOrder(String orderId);
  Future<IbkrOrder> resendOrder(String orderId);
  Future<IbkrOrder> cancelOrder(String orderId);
}

class ApiIbkrRepository implements IbkrRepository {
  const ApiIbkrRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/finance/ibkr';
  static final _hash = RegExp(r'^[0-9a-f]{64}$');

  @override
  Future<IbkrOverview> overview() async => IbkrOverview.fromJson(
      JsonReader(await api.request('GET', '$_base/overview')));

  @override
  Future<IbkrOrderPage> orders({int offset = 0, int limit = 25}) async {
    final j = JsonReader(
        await api.request('GET', '$_base/orders?limit=$limit&offset=$offset'));
    return IbkrOrderPage(
        items: j.optObjects('items', IbkrOrder.fromJson),
        total: j.optInt('total') ?? 0);
  }

  @override
  Future<IbkrOrder> approve(IbkrIntent intent) async {
    // The hash the owner was shown; the server refuses any other.
    if (!_hash.hasMatch(intent.payloadHash)) {
      throw const ApiException(0, 'This order has no valid approval code.');
    }
    final j = JsonReader(await api.request(
        'POST', '$_base/intents/${Uri.encodeComponent(intent.id)}/approve',
        body: {'payload_hash': intent.payloadHash}));
    return IbkrOrder.fromJson(j.object('order'));
  }

  @override
  Future<void> reject(IbkrIntent intent) async {
    await api.request(
        'POST', '$_base/intents/${Uri.encodeComponent(intent.id)}/reject');
  }

  @override
  Future<IbkrKillReport> kill(String reason) async => IbkrKillReport.fromJson(
      JsonReader(await api.request('POST', '$_base/kill-switch',
          body: {'reason': reason.trim().isEmpty ? null : reason.trim()})));

  @override
  Future<void> release() async {
    await api.request('POST', '$_base/kill-switch/release',
        body: {'confirm': 'RESUME'});
  }

  String _order(String id) => '$_base/orders/${Uri.encodeComponent(id)}';

  @override
  Future<IbkrQuote> quote(String symbol) async =>
      IbkrQuote.fromJson(JsonReader(await api.request(
          'GET', '$_base/quote?symbol=${Uri.encodeQueryComponent(symbol)}')));

  @override
  Future<IbkrIntent> createOrder(
          {required String symbol,
          required String side,
          required String quantity,
          required String limitPrice,
          String? reason}) async =>
      IbkrIntent.fromJson(
          JsonReader(await api.request('POST', '$_base/orders', body: {
        'symbol': symbol,
        'side': side,
        'quantity': quantity.trim(),
        'limit_price': limitPrice.trim(),
        if (reason != null && reason.trim().isNotEmpty) 'reason': reason.trim(),
      })));

  @override
  Future<void> saveLimits(Map<String, Object> limits, int version) async {
    await api.request('PUT', '$_base/limits',
        body: {'limits': limits, 'version': version});
  }

  @override
  Future<(List<IbkrAlert>, int)> alerts({int limit = 10}) async {
    final j = JsonReader(await api.request(
        'GET', '$_base/events?alerts=true&limit=$limit&offset=0'));
    return (j.optObjects('items', IbkrAlert.fromJson), j.optInt('unread') ?? 0);
  }

  @override
  Future<void> markAlertsRead() async {
    await api.request('POST', '$_base/events/read');
  }

  @override
  Future<IbkrOrder> refreshOrder(String orderId) async => IbkrOrder.fromJson(
      JsonReader(await api.request('POST', '${_order(orderId)}/refresh')));

  @override
  Future<IbkrOrder> resendOrder(String orderId) async {
    final j = JsonReader(await api.request('POST', '${_order(orderId)}/send'));
    return IbkrOrder.fromJson(j.optObject('order') ?? j);
  }

  @override
  Future<IbkrOrder> cancelOrder(String orderId) async => IbkrOrder.fromJson(
      JsonReader(await api.request('POST', '${_order(orderId)}/cancel')));
}

enum IbkrPhase { loading, ready, denied, error }

/// A message for the screen: a catalog key with values, or the API's own
/// sentence ([raw]).
class IbkrNote {
  const IbkrNote(
      {this.key, this.values = const {}, this.raw, this.failed = false});
  final String? key, raw;
  final Map<String, Object> values;
  final bool failed;
}

class IbkrController extends ChangeNotifier {
  IbkrController(this.repository, {DateTime Function()? clock})
      : now = clock ?? DateTime.now;
  final IbkrRepository repository;
  final DateTime Function() now;
  static const pageSize = 25;

  IbkrPhase phase = IbkrPhase.loading;
  String? error;
  IbkrOverview? overview;

  bool ordersLoading = false;
  String? ordersError;
  List<IbkrOrder> orders = const [];
  int ordersTotal = 0;

  String? busy;
  IbkrNote? note;

  Future<void> load() async {
    phase = IbkrPhase.loading;
    error = null;
    notifyListeners();
    try {
      overview = await repository.overview();
      phase = IbkrPhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? IbkrPhase.denied : IbkrPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = IbkrPhase.error;
      error = readableError(e);
    }
    notifyListeners();
    if (phase == IbkrPhase.ready) {
      await loadOrders();
      if (overview!.unreadAlerts > 0 || alerts.isNotEmpty) await loadAlerts();
    }
  }

  Future<void> loadOrders({bool more = false}) async {
    ordersLoading = true;
    ordersError = null;
    notifyListeners();
    try {
      final page = await repository.orders(
          offset: more ? orders.length : 0, limit: pageSize);
      orders = more ? [...orders, ...page.items] : page.items;
      ordersTotal = page.total;
    } on Object catch (e) {
      ordersError = e is ApiException ? e.readable : readableError(e);
    }
    ordersLoading = false;
    notifyListeners();
  }

  bool get hasMoreOrders => orders.length < ordersTotal;

  Future<void> _act(String id, Future<IbkrNote> Function() work) async {
    if (busy != null) return;
    busy = id;
    note = null;
    notifyListeners();
    try {
      note = await work();
    } on ApiException catch (e) {
      note = IbkrNote(raw: e.readable, failed: true);
    } on Object catch (e) {
      note = IbkrNote(raw: readableError(e), failed: true);
    }
    busy = null;
    notifyListeners();
    await load();
  }

  /// Approves exactly [intent] as shown. The server reads IBKR again, checks
  /// every limit, then sends; an expired request is not sent.
  Future<void> approve(IbkrIntent intent) {
    if (intent.secondsLeft(now()) == 0) {
      note = const IbkrNote(key: 'ibkr.review.expired', failed: true);
      notifyListeners();
      return Future.value();
    }
    return _act(intent.id, () async {
      final order = await repository.approve(intent);
      final rejected = order.state == 'rejected';
      return IbkrNote(
          key: rejected
              ? 'ibkr.approvals.rejectedByIbkr'
              : 'ibkr.approvals.sent',
          values: {'message': order.message ?? ''},
          failed: rejected);
    });
  }

  Future<void> reject(IbkrIntent intent) => _act(intent.id, () async {
        await repository.reject(intent);
        return const IbkrNote(key: 'ibkr.approvals.declined');
      });

  Future<void> kill(String reason) => _act('kill', () async {
        final report = await repository.kill(reason);
        return report.allConfirmed
            ? IbkrNote(
                key: 'ibkr.kill.done', values: {'count': report.cancelled})
            : const IbkrNote(key: 'ibkr.kill.stuck', failed: true);
      });

  // -- alerts ---------------------------------------------------------------

  List<IbkrAlert> alerts = const [];
  bool alertsLoading = false;
  String? alertsError;

  Future<void> loadAlerts() async {
    alertsLoading = true;
    alertsError = null;
    notifyListeners();
    try {
      alerts = (await repository.alerts()).$1;
    } on Object catch (e) {
      alertsError = e is ApiException ? e.readable : readableError(e);
    }
    alertsLoading = false;
    notifyListeners();
  }

  Future<void> markAlertsRead() => _act('alerts', () async {
        await repository.markAlertsRead();
        return const IbkrNote(key: 'od.ib.alertsRead');
      });

  // -- the order ticket -----------------------------------------------------

  IbkrQuote? quote;
  bool quoting = false;

  /// The ticket's own problem as a catalog key, or the API's sentence.
  IbkrNote? ticketNote;

  Future<void> getQuote(String symbol) async {
    quoting = true;
    ticketNote = null;
    quote = null;
    notifyListeners();
    try {
      quote = await repository.quote(symbol);
    } on ApiException catch (e) {
      ticketNote = IbkrNote(raw: e.readable, failed: true);
    } on Object catch (e) {
      ticketNote = IbkrNote(raw: readableError(e), failed: true);
    }
    quoting = false;
    notifyListeners();
  }

  /// Makes an order request for approval; true when it was made. Nothing
  /// reaches IBKR until the owner approves it with its payload hash.
  Future<bool> createOrder(
      {required String symbol,
      required String side,
      required String quantity,
      required String limitPrice}) async {
    final o = overview;
    if (o == null || busy != null) return false;
    final problem = ibkrTicketProblem(
        symbol: symbol,
        quantity: quantity,
        limitPrice: limitPrice,
        allowed: [for (final a in o.allowed) a.symbol],
        fractionalOk: o.fractionalOk,
        maxOrder: o.limits['max_order'] ?? '0');
    if (problem != null) {
      ticketNote = IbkrNote(
          key: 'ibkr.ticket.error.$problem',
          values: {'max': 'USD ${o.limits['max_order'] ?? '-'}'},
          failed: true);
      notifyListeners();
      return false;
    }
    busy = 'ticket';
    ticketNote = null;
    note = null;
    notifyListeners();
    var made = false;
    try {
      await repository.createOrder(
          symbol: symbol,
          side: side,
          quantity: quantity,
          limitPrice: limitPrice);
      made = true;
      quote = null;
      ticketNote = const IbkrNote(key: 'ibkr.ticket.created');
    } on ApiException catch (e) {
      // Shown beside the ticket: the server's own sentence (a limit, the
      // market hours, a missing connection).
      ticketNote = IbkrNote(raw: e.readable, failed: true);
    } on Object catch (e) {
      ticketNote = IbkrNote(raw: readableError(e), failed: true);
    }
    busy = null;
    notifyListeners();
    if (made) await load();
    return made;
  }

  // -- limits ---------------------------------------------------------------

  /// Saves the owner's limits; null when saved, else the sentence to show
  /// in the form (the server refuses loosening while live, and any value
  /// outside its bounds).
  Future<String?> saveLimits(Map<String, String> values) async {
    final o = overview;
    if (o == null) return null;
    final body = <String, Object>{};
    for (final field in ibkrLimitFields) {
      final text = (values[field] ?? '').trim();
      if (field == 'max_orders_per_day' || field == 'session_buffer_minutes') {
        final number = int.tryParse(text);
        if (number == null) return 'od.ib.limitWhole';
        body[field] = number;
      } else {
        if (_scaled(text) == null) return 'od.ib.limitNumber';
        body[field] = text;
      }
    }
    try {
      await repository.saveLimits(body, o.settingsVersion);
    } on ApiException catch (e) {
      return e.readable;
    } on Object catch (e) {
      return readableError(e);
    }
    note = const IbkrNote(key: 'ibkr.limits.saved');
    await load();
    return null;
  }

  // -- per-order actions ----------------------------------------------------

  Future<void> checkOrder(IbkrOrder order) => _act(order.id, () async {
        await repository.refreshOrder(order.id);
        return const IbkrNote(key: 'ibkr.orders.checked');
      });

  /// For an order IBKR never answered: the server looks it up by its own id
  /// first and sends only if IBKR has none, so nothing is sent twice.
  Future<void> resendOrder(IbkrOrder order) => _act(order.id, () async {
        await repository.resendOrder(order.id);
        return const IbkrNote(key: 'ibkr.orders.resent');
      });

  Future<void> cancelOrder(IbkrOrder order) => _act(order.id, () async {
        await repository.cancelOrder(order.id);
        return const IbkrNote(key: 'ibkr.orders.cancelled');
      });

  Future<void> release(String typed) {
    if (typed.trim() != 'RESUME') {
      note = const IbkrNote(key: 'od.ib.releaseBad', failed: true);
      notifyListeners();
      return Future.value();
    }
    return _act('release', () async {
      await repository.release();
      return const IbkrNote(key: 'ibkr.kill.released');
    });
  }
}
