import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/commerce/commerce_controller.dart';
import 'package:anum_mobile/features/commerce/commerce_models.dart';
import 'package:anum_mobile/features/commerce/commerce_repository.dart';
import 'package:anum_mobile/features/commerce/commerce_screen.dart';
import 'package:anum_mobile/features/commerce/commerce_strings.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

// Commerce on the phone (docs/commerce.md): summary dashboard and order list.

Map<String, Object?> dashboardJson({int connectors = 2, int late = 1}) => {
      'currencies': [
        {'currency': 'SAR', 'revenue': 41250.5, 'orders': 96, 'units': 210},
        {'currency': 'AED', 'revenue': 4100.0, 'orders': 9, 'units': 14},
      ],
      'by_channel': [
        {
          'channel': 'amazon',
          'marketplace': 'Amazon.sa',
          'currency': 'SAR',
          'revenue': 35250.5,
          'orders': 80
        },
      ],
      'top_products': [
        {
          'sku': 'MX-2055516',
          'title': 'Michelin Primacy 4',
          'title_ar': 'ميشلان برايمسي 4',
          'tyre_size': '205/55R16',
          'units': 64
        },
      ],
      'low_stock': [
        {'sku': 'MX-2055516', 'marketplace': 'Amazon.sa', 'quantity': 3},
      ],
      'pending_total': 8,
      'late_total': late,
      'returns': {'count': 3},
      'open_alerts': 2,
      'connectors': connectors,
    };

Map<String, Object?> orderJson(int i, {bool late = false}) => {
      'id': 'cord_$i',
      'external_id': '402-000000$i-0000001',
      'channel': 'amazon',
      'marketplace': 'Amazon.sa',
      'status': 'unshipped',
      'purchased_at': '2026-10-11T08:00:00Z',
      'ship_by': '2026-10-11T20:00:00Z',
      'total_amount': 900.0 + i,
      'currency': 'SAR',
      'item_count': 2,
      'late': late,
      'lines': [
        {
          'sku': 'MX-2055516',
          'title': 'Michelin Primacy 4',
          'quantity': 2,
          'total': 900.0
        },
      ],
    };

class FakeCommerce implements CommerceRepository {
  FakeCommerce(
      {this.connectors = 2,
      this.failSummary = false,
      this.deny = false,
      this.totalOrders = 25});
  final int connectors, totalOrders;
  bool failSummary, deny;
  final requests = <String>[];

  @override
  Future<CommerceDashboard> dashboard(String from, String to) async {
    requests.add('dashboard $from $to');
    if (deny) throw const ApiException(403, 'Workspace permission denied');
    if (failSummary) {
      throw const ApiException(503, 'The database is not reachable.');
    }
    return CommerceDashboard.fromJson(
        JsonReader.of(dashboardJson(connectors: connectors)));
  }

  @override
  Future<SalesSummary> summary(String period, String lang) async {
    requests.add('summary $period $lang');
    return SalesSummary(lang == 'ar'
        ? 'مبيعات اليوم: 2 طلب'
        : 'Sales across all channels today: 2 orders.');
  }

  @override
  Future<OrderPage> orders(
      {String? status, required int offset, int limit = 20}) async {
    requests.add('orders $status $offset');
    final count = (totalOrders - offset).clamp(0, limit);
    return OrderPage(
      items: [
        for (var i = 0; i < count; i++)
          CommerceOrder.fromJson(
              JsonReader.of(orderJson(offset + i, late: offset + i == 0)))
      ],
      total: totalOrders,
      offset: offset,
      limit: limit,
    );
  }

  @override
  Future<CommerceOrder> order(String id) async =>
      CommerceOrder.fromJson(JsonReader.of(orderJson(0)));
}

Widget app(CommerceController controller,
        {Locale locale = const Locale('en'), double textScale = 1}) =>
    MaterialApp(
      theme: AnumTheme.dark(),
      locale: locale,
      supportedLocales: const [Locale('en'), Locale('ar')],
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context)
              .copyWith(textScaler: TextScaler.linear(textScale)),
          child: child!),
      home: CommerceScreen(controller: controller),
    );

void main() {
  test('money, periods and the catalogs', () {
    expect(formatMoney(41250.5, 'SAR'), 'SAR 41,250.50');
    expect(formatMoney(-79.5, 'AED'), 'AED -79.50');
    final now = DateTime(2026, 10, 11, 9);
    expect(periodRange('today', now), (from: '2026-10-11', to: '2026-10-11'));
    expect(
        periodRange('yesterday', now), (from: '2026-10-10', to: '2026-10-10'));
    expect(periodRange('last_7_days', now),
        (from: '2026-10-05', to: '2026-10-11'));
    expect(CommerceStrings.catalogsMatch, isTrue);
  });

  test('the controller pages orders and keeps the summary when orders fail',
      () async {
    final repo = FakeCommerce();
    final c = CommerceController(repo, clock: () => DateTime(2026, 10, 11));
    await c.load();
    expect(c.summaryState, CommerceLoad.ready);
    expect(c.dashboard!.orders, 105);
    expect(c.orders, hasLength(20));
    expect(c.hasMoreOrders, isTrue);
    await c.loadMoreOrders();
    expect(c.orders, hasLength(25));
    expect(c.hasMoreOrders, isFalse);
    await c.setStatus('shipped');
    expect(repo.requests.last, 'orders shipped 0');
    await c.setPeriod('last_7_days');
    expect(repo.requests, contains('dashboard 2026-10-05 2026-10-11'));
  });

  test('a 403 is permission denied, other failures are errors', () async {
    final denied = CommerceController(FakeCommerce(deny: true));
    await denied.loadSummary();
    expect(denied.summaryState, CommerceLoad.denied);
    final failing = CommerceController(FakeCommerce(failSummary: true));
    await failing.loadSummary();
    expect(failing.summaryState, CommerceLoad.error);
    expect(failing.summaryError, contains('not reachable'));
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
    testWidgets('summary and orders lay out at $width wide with 200% text',
        (tester) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final c = CommerceController(FakeCommerce());
      await tester.pumpWidget(app(c, textScale: 2));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('Sales across all channels today: 2 orders.'),
          findsOneWidget);
      expect(find.text('SAR 41,250.50'), findsOneWidget);
    });
  }

  testWidgets('orders open in a sheet and load more', (tester) async {
    tester.view.physicalSize = const Size(390, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final c = CommerceController(FakeCommerce());
    await tester.pumpWidget(app(c));
    await tester.pumpAndSettle();
    final more = find.text('Load more (20 of 25)');
    await tester.ensureVisible(more);
    await tester.pumpAndSettle();
    await tester.tap(more);
    await tester.pumpAndSettle();
    expect(c.orders, hasLength(25));
    final first = find.text('402-0000000-0000001');
    await tester.ensureVisible(first);
    await tester.pumpAndSettle();
    await tester.tap(first);
    await tester.pumpAndSettle();
    expect(find.text('2 × MX-2055516'), findsOneWidget);
  });

  testWidgets(
      'no channel connected leads to the owner setup; Arabic reads right to left',
      (tester) async {
    final c = CommerceController(FakeCommerce(connectors: 0));
    await tester.pumpWidget(app(c, locale: const Locale('ar')));
    await tester.pumpAndSettle();
    expect(find.text('لا توجد قناة بيع مربوطة'), findsOneWidget);
    expect(Directionality.of(tester.element(find.byType(CommerceScreen))),
        TextDirection.rtl);
    expect(c.lang, 'ar');
  });

  testWidgets('a failed summary says what failed and retries', (tester) async {
    final repo = FakeCommerce(failSummary: true);
    final c = CommerceController(repo);
    await tester.pumpWidget(app(c));
    await tester.pumpAndSettle();
    expect(find.textContaining('Sales could not be loaded'), findsOneWidget);
    repo.failSummary = false;
    await tester.tap(find.text('Retry').first);
    await tester.pumpAndSettle();
    expect(find.text('SAR 41,250.50'), findsOneWidget);
  });
}
