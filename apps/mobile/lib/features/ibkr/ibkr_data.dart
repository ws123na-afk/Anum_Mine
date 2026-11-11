import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../data/readable_error.dart';

/// IBKR trading on the phone (docs/finance-live-trading.md#on-the-phone):
/// the connection, the paper period, the live gates, IBKR's own figures,
/// the orders and the approvals that wait. The phone approves an order the
/// same way the web does (`POST /intents/{id}/approve` with the payload hash
/// the owner was shown, single use, expiring, checked again by the server),
/// rejects it, and can always stop everything. Connecting, limits, the order
/// ticket, buy plans and the live switch stay on the web.

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
      this.riskNote = ''});
  final String mode;
  final bool deploymentLiveEnabled, killSwitch, marketOpen;
  final String marketMessage, riskNote;
  final List<IbkrConnection> connections;
  final Map<String, IbkrSnapshot> snapshots;
  final Map<String, String> reconciliation;
  final List<IbkrCheck> liveGates, paperProof;
  final List<IbkrIntent> pending;
  final int unreadAlerts, approvalMinutes, paperMinOrders, paperMinDays;

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
    if (phase == IbkrPhase.ready) await loadOrders();
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
