// IBKR trading on the phone (docs/finance-live-trading.md#on-the-phone): the
// connection, the paper period with its progress, the live gates, IBKR's own
// figures, orders paged, an order approved with the exact payload hash it
// showed (and refused once expired), rejected, the kill switch and its
// typed release, every state, Arabic, and 320 to 1024 px at 200 percent.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/ibkr/ibkr_data.dart';
import 'package:anum_mobile/features/ibkr/ibkr_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'pump.dart';

final _now = DateTime.utc(2026, 11, 10, 15, 0, 0);
final _hash = 'c4' * 32;

class FakeIbkr implements IbkrRepository {
  int status = 0;
  bool stopped = false, pending = true, connected = true, live = false;
  int orderTotal = 30;
  final calls = <String>[];

  Map<String, Object?> _intent() => {
        'id': 'intent_1',
        'mode': live ? 'live' : 'paper',
        'environment': live ? 'live' : 'paper',
        'symbol': 'SPUS',
        'side': 'buy',
        'order_type': 'limit',
        'quantity': '1',
        'limit_price': '9.80',
        'amount': '9.80',
        'currency': 'USD',
        'status': 'pending',
        'payload_hash': _hash,
        'created_at': _now.toIso8601String(),
        'expires_at': _now.add(const Duration(minutes: 4)).toIso8601String(),
        'requested_by': 'person',
      };

  @override
  Future<IbkrOverview> overview() async {
    if (status != 0) throw ApiException(status, 'Refused');
    return IbkrOverview.fromJson(JsonReader({
      'mode': 'paper',
      'deployment_live_enabled': false,
      'settings': {
        'kill_switch': stopped,
        'version': 3,
        'fractional_ok': false,
        'live': {'on': live},
        'allowed': [
          {'symbol': 'SPUS', 'conid': 1, 'shariah_screened': true},
          {'symbol': 'VOO', 'conid': 2, 'shariah_screened': false},
        ],
        'limits': {
          'max_capital': '50',
          'max_order': '10',
          'max_daily_loss': '5',
          'max_orders_per_day': 3,
          'price_band_percent': '2',
          'session_buffer_minutes': 15,
          'fee_buffer': '1',
        },
        'reconciliation': {
          'paper': {'state': 'ok'}
        },
      },
      'connections': connected
          ? [
              {
                'id': 'conn_1',
                'environment': 'paper',
                'gateway_host': '127.0.0.1',
                'hint': 'DU...567',
                'status': 'active',
                'session': 'authenticated',
                'session_message': null,
                'verified_at': '2026-11-01T10:00:00Z',
                'last_sync_at': '2026-11-10T14:00:00Z',
                'last_error': null,
                'statements_connected': false,
                'created_at': '2026-11-01T10:00:00Z',
              }
            ]
          : <Object?>[],
      'snapshots': {
        'paper': {
          'synced_at': '2026-11-10T14:00:00Z',
          'settled_cash': {'USD': '40.20'},
          'net_liquidation': '49.90',
          'daily_pnl': '-0.10',
          'unrealized_pnl': '0.30',
          'realized_pnl': '0',
          'positions': [
            {
              'conid': 1,
              'symbol': 'SPUS',
              'quantity': '1',
              'market_value': '9.70',
              'unrealized_pnl': '0.30',
              'currency': 'USD'
            }
          ],
        }
      },
      'statements': <String, Object?>{},
      'live_gates': [
        {
          'code': 'deployment_flag',
          'ok': false,
          'message': 'The operator setting ANUM_FINANCE_LIVE_ENABLED is off.'
        },
        {
          'code': 'limits',
          'ok': true,
          'message': 'Your allowed ETF list is set.'
        },
      ],
      'switch_gates': <Object?>[],
      'paper_proof': [
        {
          'code': 'paper_orders',
          'ok': false,
          'message': '7 of 20 paper orders reached IBKR.'
        },
        {
          'code': 'paper_days',
          'ok': false,
          'message': '5 of 14 days since the first paper order.'
        },
        {
          'code': 'paper_mismatches',
          'ok': true,
          'message': 'Every difference is explained.'
        },
      ],
      'market': {'open': true, 'message': 'Open', 'closes_at': null},
      'pending': pending ? [_intent()] : <Object?>[],
      'unread_alerts': 2,
      'gateway_hosts': ['127.0.0.1'],
      'etfs': <String, Object?>{},
      'shariah_etfs': ['SPUS'],
      'approval_minutes': 5,
      'paper_min_orders': 20,
      'paper_min_days': 14,
      'risk_note': 'Real money can be lost.',
    }));
  }

  @override
  Future<IbkrOrderPage> orders({int offset = 0, int limit = 25}) async {
    calls.add('orders $offset');
    return IbkrOrderPage(total: orderTotal, items: [
      for (var i = offset; i < orderTotal && i < offset + limit; i++)
        IbkrOrder.fromJson(JsonReader({
          'id': 'order_$i',
          'environment': 'paper',
          'symbol': 'SPUS',
          'side': 'buy',
          'quantity': '1',
          'limit_price': '9.80',
          'state': switch (i) {
            0 => 'filled',
            1 => 'unknown',
            2 => 'submitted',
            _ => 'cancelled'
          },
          'filled_quantity': i == 0 ? '1' : '0',
          'average_price': i == 0 ? '9.79' : null,
          'message': null,
          'created_at': '2026-11-0${1 + i % 9}T15:00:00Z',
        }))
    ]);
  }

  @override
  Future<IbkrOrder> approve(IbkrIntent intent) async {
    calls.add('approve ${intent.id} ${intent.payloadHash}');
    pending = false;
    return IbkrOrder.fromJson(JsonReader({
      'id': 'order_new',
      'environment': 'paper',
      'symbol': 'SPUS',
      'side': 'buy',
      'quantity': '1',
      'limit_price': '9.80',
      'state': 'submitted',
      'filled_quantity': '0',
      'message': 'Order 123 at IBKR.',
    }));
  }

  @override
  Future<void> reject(IbkrIntent intent) async {
    calls.add('reject ${intent.id}');
    pending = false;
  }

  @override
  Future<IbkrKillReport> kill(String reason) async {
    calls.add('kill $reason');
    stopped = true;
    return IbkrKillReport.fromJson(JsonReader({
      'kill_switch': true,
      'report': [
        {
          'environment': 'paper',
          'orders': [
            {'order_id': 'o1', 'cancelled': true}
          ]
        }
      ]
    }));
  }

  @override
  Future<void> release() async {
    calls.add('release');
    stopped = false;
  }

  String? refuseOrder;

  @override
  Future<IbkrQuote> quote(String symbol) async {
    calls.add('quote $symbol');
    return IbkrQuote(
        symbol: symbol, last: '9.75', delayed: true, bandPercent: '2');
  }

  @override
  Future<IbkrIntent> createOrder(
      {required String symbol,
      required String side,
      required String quantity,
      required String limitPrice,
      String? reason}) async {
    if (refuseOrder != null) throw ApiException(422, refuseOrder!);
    calls.add('order $symbol $side $quantity $limitPrice');
    pending = true;
    return IbkrIntent.fromJson(JsonReader(_intent()));
  }

  String? refuseLimits;

  @override
  Future<void> saveLimits(Map<String, Object> limits, int version) async {
    if (refuseLimits != null) throw ApiException(422, refuseLimits!);
    calls.add(
        'limits ${limits['max_order']} ${limits['max_orders_per_day']} v$version');
  }

  bool alertsRead = false;

  @override
  Future<(List<IbkrAlert>, int)> alerts({int limit = 10}) async => (
        [
          IbkrAlert.fromJson(JsonReader({
            'id': 'ev_1',
            'severity': 'critical',
            'message': 'IBKR refused order 7: outside the price band.',
            'environment': 'paper',
            'created_at': '2026-11-10T14:30:00Z',
            'read_at': alertsRead ? '2026-11-10T14:40:00Z' : null,
          }))
        ],
        alertsRead ? 0 : 2
      );

  @override
  Future<void> markAlertsRead() async {
    calls.add('alerts read');
    alertsRead = true;
  }

  IbkrOrder _done(String id, String state) => IbkrOrder.fromJson(JsonReader({
        'id': id,
        'environment': 'paper',
        'symbol': 'SPUS',
        'side': 'buy',
        'quantity': '1',
        'limit_price': '9.80',
        'state': state,
        'filled_quantity': '0',
      }));

  @override
  Future<IbkrOrder> refreshOrder(String orderId) async {
    calls.add('refresh $orderId');
    return _done(orderId, 'submitted');
  }

  @override
  Future<IbkrOrder> resendOrder(String orderId) async {
    calls.add('resend $orderId');
    return _done(orderId, 'submitted');
  }

  @override
  Future<IbkrOrder> cancelOrder(String orderId) async {
    calls.add('cancel $orderId');
    return _done(orderId, 'cancelled');
  }
}

Widget _screen(IbkrController c) => IbkrScreen(controller: c, countdown: false);

void main() {
  test('progress, kill reports and countdowns read like the API', () {
    const check = IbkrCheck(
        code: 'paper_orders',
        ok: false,
        message: '7 of 20 paper orders reached IBKR.');
    expect(check.progress, (7, 20));
    expect(const IbkrCheck(code: 'x', ok: true, message: 'Done.').progress,
        isNull);
    final stuck = IbkrKillReport.fromJson(JsonReader({
      'report': [
        {
          'environment': 'paper',
          'orders': [
            {'order_id': 'a', 'cancelled': true},
            {'order_id': 'b', 'cancelled': false}
          ]
        }
      ]
    }));
    expect((stuck.cancelled, stuck.allConfirmed), (1, false));
    final intent = IbkrIntent(
        id: 'i',
        environment: 'paper',
        symbol: 'SPUS',
        side: 'buy',
        quantity: '1',
        limitPrice: '9',
        amount: '9',
        payloadHash: _hash,
        expiresAt: _now.add(const Duration(seconds: 90)));
    expect(intent.secondsLeft(_now), 90);
    expect(intent.secondsLeft(_now.add(const Duration(hours: 1))), 0);
  });

  testWidgets(
      'the owner sees the paper period and approves the order with its hash',
      (tester) async {
    final fake = FakeIbkr();
    final controller = IbkrController(fake, clock: () => _now);
    await pumpDesk(tester, _screen(controller));
    expect(find.text('PAPER'), findsWidgets);
    expect(find.text('DU...567'), findsWidgets);
    expect(find.text('7 of 20 paper orders reached IBKR.'), findsOneWidget);
    expect(find.text('Enough paper orders reached IBKR'), findsOneWidget);
    expect(find.text('1 of 2'), findsOneWidget); // live gates met
    expect(find.text('USD 40.20'), findsOneWidget);
    expect(find.text('New alerts: 2. They are listed under Alerts.'),
        findsOneWidget);
    expect(find.text('240 seconds'), findsOneWidget);
    expect(find.text(_hash), findsOneWidget);
    expect(find.textContaining('1 SPUS @ USD 9.80'), findsWidgets);

    // Orders paged.
    expect(find.byKey(const Key('ib-order-order_0')), findsOneWidget);
    expect(find.text('1 filled at USD 9.79'), findsOneWidget);
    await tapDesk(tester, find.byKey(const Key('ib-orders-more')));
    expect(fake.calls, contains('orders 25'));
    expect(find.byKey(const Key('ib-orders-more')), findsNothing);

    await tapDesk(tester, find.byKey(const Key('ib-approve-intent_1')));
    expect(find.text('Approve this paper order?'), findsOneWidget);
    await tapDesk(tester, find.byKey(const Key('ib-approve-confirm')));
    expect(fake.calls, contains('approve intent_1 $_hash'));
    expect(find.text('Approved and sent to IBKR. Order 123 at IBKR.'),
        findsOneWidget);
    expect(find.text('Nothing is waiting for your approval.'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('an expired request is not sent; reject; stop and resume',
      (tester) async {
    final fake = FakeIbkr();
    var now = _now;
    final controller = IbkrController(fake, clock: () => now);
    await pumpDesk(tester, _screen(controller));
    now = _now.add(const Duration(minutes: 10));
    await controller.approve(controller.overview!.pending.single);
    await tester.pumpAndSettle();
    expect(find.text('Expired: make a new order'), findsWidgets);
    expect(fake.calls.where((c) => c.startsWith('approve')), isEmpty);

    now = _now;
    await controller.load();
    await tester.pumpAndSettle();
    await tapDesk(tester, find.byKey(const Key('ib-reject-intent_1')));
    expect(fake.calls, contains('reject intent_1'));
    expect(find.text('Order rejected. Nothing was sent.'), findsOneWidget);

    await tapDesk(tester, find.byKey(const Key('ib-kill')));
    await tester.enterText(
        find.byKey(const Key('ib-kill-reason')), 'Travelling');
    await tapDesk(tester, find.byKey(const Key('ib-kill-confirm')));
    expect(fake.calls, contains('kill Travelling'));
    expect(
        find.text(
            'Stopped. 1 open orders at IBKR were cancelled and confirmed.'),
        findsOneWidget);
    expect(find.text('On: no new orders'), findsOneWidget);

    await tapDesk(tester, find.byKey(const Key('ib-release')));
    await tester.enterText(find.byKey(const Key('ib-release-word')), 'resume');
    await tapDesk(tester, find.byKey(const Key('ib-release-confirm')));
    expect(find.text('Type RESUME exactly to allow orders again.'),
        findsOneWidget);
    expect(fake.calls, isNot(contains('release')));
    await tapDesk(tester, find.byKey(const Key('ib-release')));
    await tester.enterText(find.byKey(const Key('ib-release-word')), 'RESUME');
    await tapDesk(tester, find.byKey(const Key('ib-release-confirm')));
    expect(fake.calls, contains('release'));
    expect(find.text('Orders are allowed again.'), findsOneWidget);
  });

  testWidgets('a live order says real money; not connected, denied, error',
      (tester) async {
    final fake = FakeIbkr()..live = true;
    final controller = IbkrController(fake, clock: () => _now);
    await pumpDesk(tester, _screen(controller));
    expect(find.text('LIVE: this uses real money in your IBKR account.'),
        findsOneWidget);
    expect(find.text('Approve and send LIVE'), findsOneWidget);

    fake
      ..connected = false
      ..pending = false
      ..orderTotal = 0;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('ib-not-connected')), findsOneWidget);
    expect(find.text('No orders yet.'), findsOneWidget);

    fake.status = 403;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('ib-denied')), findsOneWidget);
    fake.status = 502;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('ib-error')), findsOneWidget);
    fake.status = 0;
    await tapDesk(tester, find.text('Try again'));
    expect(find.byKey(const Key('ib-status')), findsOneWidget);
  });

  test('the ticket checks like the web before anything is sent', () {
    String? check(String q, String p, {bool fractional = false}) =>
        ibkrTicketProblem(
            symbol: 'SPUS',
            quantity: q,
            limitPrice: p,
            allowed: const ['SPUS'],
            fractionalOk: fractional,
            maxOrder: '10');
    expect(check('1', '9.80'), isNull);
    expect(check('1.5', '5'), 'wholeShares');
    expect(check('1.5', '5', fractional: true), isNull);
    expect(check('0', '5'), 'quantity');
    expect(check('1', 'abc'), 'price');
    expect(check('2', '9.80'), 'overOrder');
    expect(ibkrNotional('3', '3.335'), '10.01');
    expect(
        ibkrTicketProblem(
            symbol: 'QQQ',
            quantity: '1',
            limitPrice: '1',
            allowed: const ['SPUS'],
            fractionalOk: false,
            maxOrder: '10'),
        'symbol');
  });

  testWidgets(
      'the ticket makes a request that waits for approval; a refusal is shown beside it',
      (tester) async {
    final fake = FakeIbkr()..pending = false;
    final controller = IbkrController(fake, clock: () => _now);
    await pumpDesk(tester, _screen(controller));
    await tapDesk(tester, find.byKey(const Key('ib-ticket-quote')));
    expect(fake.calls, contains('quote SPUS'));
    expect(find.textContaining('IBKR price USD 9.75, Delayed'), findsOneWidget);
    expect(
        find.text(
            'About USD 9.75 before fees. Your limit per order is USD 10.'),
        findsOneWidget);
    // Two shares are over the 10 USD limit: refused before anything is sent.
    await tester.enterText(find.byKey(const Key('ib-ticket-quantity')), '2');
    await tapDesk(tester, find.byKey(const Key('ib-ticket-create')));
    expect(find.text('This order is over your limit of USD 10 per order.'),
        findsOneWidget);
    expect(fake.calls.where((c) => c.startsWith('order SPUS')), isEmpty);
    await tester.enterText(find.byKey(const Key('ib-ticket-quantity')), '1');
    fake.refuseOrder =
        'The US market is closed. Orders are made in regular hours.';
    await tapDesk(tester, find.byKey(const Key('ib-ticket-create')));
    expect(
        find.text('The US market is closed. Orders are made in regular hours.'),
        findsOneWidget);
    fake.refuseOrder = null;
    await tapDesk(tester, find.byKey(const Key('ib-ticket-create')));
    expect(fake.calls, contains('order SPUS buy 1 9.75'));
    // It waits in the approvals with its code; nothing was approved.
    expect(find.byKey(const Key('ib-approve-intent_1')), findsOneWidget);
    expect(fake.calls.where((c) => c.startsWith('approve')), isEmpty);
    expect(tester.takeException(), isNull);
  });

  testWidgets('limits are changed by the owner; a refusal stays in the form',
      (tester) async {
    final fake = FakeIbkr();
    final controller = IbkrController(fake, clock: () => _now);
    await pumpDesk(tester, _screen(controller));
    expect(find.text('Most per order'), findsOneWidget);
    await tapDesk(tester, find.byKey(const Key('ib-limits-edit')));
    await tester.enterText(
        find.byKey(const Key('ib-limit-max_orders_per_day')), 'two');
    await tapDesk(tester, find.byKey(const Key('ib-limits-save')));
    expect(find.text('Orders per day and the minutes are whole numbers.'),
        findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('ib-limit-max_orders_per_day')), '2');
    fake.refuseLimits = 'While live is on, limits can only be made stricter.';
    await tapDesk(tester, find.byKey(const Key('ib-limits-save')));
    expect(find.byKey(const Key('ib-limits-error')), findsOneWidget);
    fake.refuseLimits = null;
    await tester.enterText(find.byKey(const Key('ib-limit-max_order')), '8');
    await tapDesk(tester, find.byKey(const Key('ib-limits-save')));
    expect(fake.calls, contains('limits 8 2 v3'));
    expect(find.byKey(const Key('ib-limits-save')), findsNothing);
    expect(find.text('Saved.'), findsOneWidget);
  });

  testWidgets(
      'alerts are read here; orders are checked, looked up and cancelled',
      (tester) async {
    final fake = FakeIbkr()..pending = false;
    final controller = IbkrController(fake, clock: () => _now);
    await pumpDesk(tester, _screen(controller));
    expect(find.text('IBKR refused order 7: outside the price band.'),
        findsOneWidget);
    expect(find.text('Urgent'), findsOneWidget);
    await tapDesk(tester, find.byKey(const Key('ib-alerts-read')));
    expect(fake.calls, contains('alerts read'));
    expect(find.text('Alerts marked as read.'), findsOneWidget);

    expect(find.byKey(const Key('ib-check-order-order_0')),
        findsNothing); // filled
    await tapDesk(tester, find.byKey(const Key('ib-resend-order_1')));
    expect(fake.calls, contains('resend order_1'));
    expect(find.text('Looked up at IBKR first. Nothing is sent twice.'),
        findsOneWidget);
    await tapDesk(tester, find.byKey(const Key('ib-check-order-order_2')));
    expect(fake.calls, contains('refresh order_2'));
    await tapDesk(tester, find.byKey(const Key('ib-cancel-order_2')));
    expect(find.text('Cancel this order at IBKR?'), findsOneWidget);
    await tapDesk(tester, find.byKey(const Key('ib-cancel-confirm')));
    expect(fake.calls, contains('cancel order_2'));
    expect(find.text('Cancel sent to IBKR and checked.'), findsOneWidget);
  });

  testWidgets('no allowed ETF: the ticket says what to add', (tester) async {
    final controller = IbkrController(_NoEtfs(), clock: () => _now);
    await pumpDesk(tester, _screen(controller));
    expect(find.byKey(const Key('ib-ticket-empty')), findsOneWidget);
    expect(find.text('No ETF allowed yet'), findsOneWidget);
  });

  testWidgets('reads in Arabic, right to left', (tester) async {
    await pumpDesk(
        tester, _screen(IbkrController(FakeIbkr(), clock: () => _now)),
        locale: const Locale('ar'));
    expect(find.text('تداول IBKR'), findsWidgets);
    expect(find.text('الفترة التجريبية'), findsOneWidget);
    expect(Directionality.of(tester.element(find.byType(IbkrScreen))),
        TextDirection.rtl);
  });

  for (final (size, scale, locale) in deskSizes) {
    testWidgets(
        'fits at ${size.width} px, text x$scale, ${locale.languageCode}',
        (tester) async {
      await pumpDesk(tester,
          _screen(IbkrController(FakeIbkr()..live = true, clock: () => _now)),
          size: size, textScale: scale, locale: locale);
      await tester.drag(find.byType(Scrollable).first, const Offset(0, -60000));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }
}

class _NoEtfs extends FakeIbkr {
  @override
  Future<IbkrOverview> overview() async {
    final o = await super.overview();
    return IbkrOverview(
        mode: o.mode,
        deploymentLiveEnabled: false,
        killSwitch: false,
        connections: o.connections,
        snapshots: o.snapshots,
        liveGates: o.liveGates,
        paperProof: o.paperProof,
        marketOpen: true,
        marketMessage: '',
        pending: const [],
        reconciliation: o.reconciliation,
        limits: o.limits);
  }
}
