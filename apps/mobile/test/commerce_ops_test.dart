import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/commerce/commerce_controller.dart';
import 'package:anum_mobile/features/commerce/commerce_ops_data.dart';
import 'package:anum_mobile/features/commerce/commerce_ops_screen.dart';
import 'package:anum_mobile/features/commerce/commerce_screen.dart';
import 'package:anum_mobile/features/commerce/commerce_strings.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_test.dart' show FakeCommerce;

// Commerce working sections on the phone (docs/commerce.md): catalogue,
// prices and stock (proposed, approved elsewhere), returns, alerts and
// channels, with the server's own permission answers.

Map<String, Object?> listingJson(int i) => {
      'id': 'clist_$i',
      'connector_id': 'cconn_1',
      'channel': 'amazon',
      'marketplace': 'Amazon.sa',
      'marketplace_id': 'A17E79C6D8DWNP',
      'sku': 'MX-$i',
      'title': 'Michelin Primacy 4',
      'price': 399.0,
      'currency': 'SAR',
      'quantity': 12,
      'status': 'active',
      'buy_box_winner': i.isOdd ? false : true,
      'fulfillment': i == 7 ? 'amazon' : 'merchant',
    };

Map<String, Object?> productJson(int i) => {
      'id': 'cprod_$i',
      'sku': 'MX-$i',
      'title': 'Michelin Primacy 4 $i',
      'title_ar': 'ميشلان برايمسي 4',
      'brand': 'Michelin',
      'cost_price': 280.0,
      'currency': 'SAR',
      'low_stock_threshold': 4,
      'total_stock': 12,
      'tyre': {'size': '205/55R16'},
      'listings': [listingJson(i)],
    };

class FakeOps implements CommerceOpsRepository {
  FakeOps({
    this.operate = true,
    this.manage = false,
    this.total = 25,
    this.deny = false,
    this.fail = false,
  });
  final bool operate, manage, deny, fail;
  final int total;
  final calls = <String>[];
  final bodies = <Map<String, Object?>>[];
  String? refuse;
  bool failNextProducts = false;
  var channelList = <Map<String, Object?>>[channelJson()];
  var alertOpen = true;

  void need() {
    if (!operate) {
      throw const ApiException(
          403, 'Only workspace owners and members can do this.');
    }
    if (refuse != null) throw ApiException(409, refuse!);
  }

  void gate() {
    if (deny) throw const ApiException(403, 'No Commerce access.');
    if (fail) throw const ApiException(503, 'The database is not reachable.');
  }

  CommercePage<T> page<T>(int offset, int limit, T Function(int) make) {
    final count = (total - offset).clamp(0, limit);
    return CommercePage(
        [for (var i = 0; i < count; i++) make(offset + i)], total);
  }

  @override
  Future<CommerceRights> rights() async =>
      CommerceRights(operate: operate, manage: manage);

  @override
  Future<CommercePage<CommerceProduct>> products(
      {String? query, required int offset, int limit = 20}) async {
    calls.add('products $query $offset');
    gate();
    if (failNextProducts && offset > 0) {
      await Future<void>.delayed(Duration.zero);
      throw const ApiException(503, 'Old page failed.');
    }
    return page(offset, limit,
        (i) => CommerceProduct.fromJson(JsonReader.of(productJson(i))));
  }

  @override
  Future<CommerceProduct> createProduct(Map<String, Object?> fields) async {
    need();
    calls.add('createProduct');
    bodies.add(fields);
    return CommerceProduct.fromJson(JsonReader.of(productJson(0)));
  }

  @override
  Future<CommerceProduct> updateProduct(
      String id, Map<String, Object?> fields) async {
    need();
    calls.add('updateProduct $id');
    bodies.add(fields);
    return CommerceProduct.fromJson(JsonReader.of(productJson(0)));
  }

  @override
  Future<void> deleteProduct(String id) async {
    need();
    calls.add('deleteProduct $id');
  }

  @override
  Future<CommercePage<CommerceListing>> listings(
      {String? query, required int offset, int limit = 20}) async {
    calls.add('listings $query $offset');
    gate();
    return page(offset, limit,
        (i) => CommerceListing.fromJson(JsonReader.of(listingJson(i))));
  }

  @override
  Future<ChangeProposal> proposePrice(String id, double price) async {
    need();
    calls.add('price $id $price');
    return ChangeProposal(
        CommerceListing.fromJson(JsonReader.of(listingJson(0))), 'appr_1');
  }

  @override
  Future<ChangeProposal> proposeStock(String id, int quantity) async {
    need();
    calls.add('stock $id $quantity');
    return ChangeProposal(
        CommerceListing.fromJson(JsonReader.of(listingJson(0))), 'appr_2');
  }

  @override
  Future<CommercePage<CommerceReturn>> returns(
      {required int days, required int offset, int limit = 20}) async {
    calls.add('returns $days $offset');
    gate();
    return page(
        offset,
        limit,
        (i) => CommerceReturn.fromJson(JsonReader.of({
              'id': 'cret_$i',
              'order_external_id': '404-$i',
              'sku': 'MX-$i',
              'quantity': 1,
              'reason': 'Wrong size',
              'status': 'refunded',
              'amount': 399.0,
              'currency': 'SAR',
              'returned_at': '2026-10-17T08:00:00Z',
            })));
  }

  @override
  Future<CommercePage<CommerceAlert>> alerts(
      {String? status, required int offset, int limit = 20}) async {
    calls.add('alerts $status $offset');
    gate();
    final shown = status == 'open' ? alertOpen : !alertOpen;
    if (!shown || status == 'resolved') return const CommercePage([], 0);
    return CommercePage([
      CommerceAlert.fromJson(JsonReader.of({
        'id': 'calert_1',
        'kind': 'low_stock',
        'severity': 'warning',
        'title': 'MX-1 is low on stock',
        'detail': '3 left on Amazon.sa',
        'status': status ?? 'open',
        'created_at': '2026-10-18T08:00:00Z',
      }))
    ], 1);
  }

  @override
  Future<CommerceAlert> acknowledge(String id) async {
    need();
    calls.add('ack $id');
    alertOpen = false;
    return CommerceAlert.fromJson(JsonReader.of({
      'id': id,
      'kind': 'low_stock',
      'severity': 'warning',
      'title': 'x',
      'status': 'acknowledged',
      'created_at': '2026-10-18T08:00:00Z',
    }));
  }

  @override
  Future<List<CommerceChannel>> channels() async {
    calls.add('channels');
    gate();
    return [
      for (final c in channelList) CommerceChannel.fromJson(JsonReader.of(c))
    ];
  }

  @override
  Future<String> testChannel(String id) async {
    if (!manage) {
      throw const ApiException(
          403, 'Only workspace owners can connect or change sales channels.');
    }
    calls.add('test $id');
    return 'Connected as seller A1.';
  }

  @override
  Future<String> syncChannel(String id) async {
    need();
    calls.add('sync $id');
    return 'Synced 12 orders.';
  }

  @override
  Future<CommerceChannel> setEnabled(String id, bool enabled) async {
    calls.add('enabled $id $enabled');
    channelList = [channelJson(enabled: enabled)];
    return CommerceChannel.fromJson(JsonReader.of(channelList.first));
  }

  @override
  Future<void> deleteChannel(String id) async {
    if (refuse != null) throw ApiException(409, refuse!);
    calls.add('deleteChannel $id');
    channelList = [];
  }
}

Map<String, Object?> channelJson({bool enabled = true}) => {
      'id': 'cconn_1',
      'channel': 'amazon',
      'name': 'Amazon Saudi',
      'marketplace_names': ['Amazon.sa'],
      'enabled': enabled,
      'health': enabled ? 'ok' : 'disabled',
      'last_error': null,
      'last_sync_at': '2026-10-19T07:00:00Z',
    };

Future<void> phone(WidgetTester tester, {double width = 390}) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = Size(width, 844);
  addTearDown(tester.view.reset);
}

Widget shell(CommerceController c,
        {Locale locale = const Locale('en'), VoidCallback? onApprovals}) =>
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
      home: CommerceScreen(
          key: UniqueKey(), controller: c, onOpenApprovals: onApprovals),
    );

Future<void> tap(WidgetTester tester, String key) async {
  await tester.ensureVisible(find.byKey(Key(key)));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(Key(key)));
  await tester.pumpAndSettle();
}

Future<void> enter(WidgetTester tester, String key, String text) async {
  await tester.ensureVisible(find.byKey(Key(key)));
  await tester.pumpAndSettle();
  await tester.enterText(find.byKey(Key(key)), text);
}

Future<CommerceController> open(
  WidgetTester tester,
  FakeOps ops, {
  String tab = 'catalogue',
  Locale locale = const Locale('en'),
  VoidCallback? onApprovals,
}) async {
  final c = CommerceController(FakeCommerce(), ops: CommerceOps(ops));
  await tester.pumpWidget(shell(c, locale: locale, onApprovals: onApprovals));
  await tester.pumpAndSettle();
  await tap(tester, 'commerce-tab-$tab');
  return c;
}

void main() {
  testWidgets('an Amazon-fulfilled listing offers no stock change',
      (tester) async {
    await phone(tester);
    final ops = FakeOps(total: 10);
    await open(tester, ops, tab: 'listings');
    expect(find.byKey(const Key('ops-stock-clist_0')), findsOneWidget);
    await tester.drag(find.byType(Scrollable).first, const Offset(0, -2000));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('ops-price-clist_7')), findsOneWidget);
    expect(find.byKey(const Key('ops-stock-clist_7')), findsNothing);
  });

  testWidgets('pull to refresh reloads the selected section', (tester) async {
    await phone(tester);
    final ops = FakeOps();
    final c = await open(tester, ops, tab: 'returns');
    final before = ops.calls.where((x) => x.startsWith('returns')).length;
    await refreshCommerceSection(c.ops!, CommerceSection.returns);
    expect(ops.calls.where((x) => x.startsWith('returns')).length, before + 1);
  });

  testWidgets('a search survives switching tabs and stays visible',
      (tester) async {
    await phone(tester);
    final ops = FakeOps();
    await open(tester, ops);
    await tester.enterText(find.byKey(const Key('ops-product-search')), 'MX-3');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    await tap(tester, 'commerce-tab-returns');
    await tap(tester, 'commerce-tab-catalogue');
    expect(
        tester
            .widget<TextField>(find.byKey(const Key('ops-product-search')))
            .controller!
            .text,
        'MX-3');
  });

  testWidgets('a product cost is sent with its currency; none is invented',
      (tester) async {
    await phone(tester);
    final ops = FakeOps();
    await open(tester, ops);
    await tap(tester, 'ops-product-new');
    await enter(tester, 'ops-sku', 'MX-9');
    await tap(tester, 'ops-save');
    expect(ops.bodies.last.containsKey('currency'), isFalse);
    await tap(tester, 'ops-product-new');
    await enter(tester, 'ops-sku', 'MX-10');
    await enter(tester, 'ops-cost', '310');
    await tap(tester, 'ops-save');
    expect(ops.bodies.last['currency'], 'SAR');
  });

  test('a stale failed page does not set an error', () async {
    final repo = FakeOps();
    final ops = CommerceOps(repo);
    await ops.first(ops.products);
    repo.failNextProducts = true;
    final more = ops.more(ops.products);
    await ops.first(ops.products);
    await more;
    expect(ops.products.error, isNull);
  });

  test('the catalogs have the same keys', () {
    expect(CommerceStrings.catalogsMatch, isTrue);
  });

  testWidgets('without operations wired the screen is unchanged',
      (tester) async {
    await phone(tester);
    final c = CommerceController(FakeCommerce());
    await tester.pumpWidget(shell(c));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('commerce-tab-catalogue')), findsNothing);
  });

  group('catalogue', () {
    testWidgets('lists, searches and pages', (tester) async {
      await phone(tester);
      final ops = FakeOps();
      await open(tester, ops);
      expect(find.text('Michelin Primacy 4 0'), findsOneWidget);
      expect(find.text('Load more (20 of 25)'), findsOneWidget);
      await tap(tester, 'ops-more');
      expect(ops.calls, contains('products null 20'));
      await tester.enterText(
          find.byKey(const Key('ops-product-search')), 'MX-3');
      await tester.testTextInput.receiveAction(TextInputAction.search);
      await tester.pumpAndSettle();
      expect(ops.calls.last, 'products MX-3 0');
    });

    testWidgets('shows empty, error with retry and permission states',
        (tester) async {
      await phone(tester);
      await open(tester, FakeOps(total: 0));
      expect(find.text('The catalogue is empty'), findsOneWidget);
      await open(tester, FakeOps(deny: true));
      expect(find.textContaining('أو'), findsNothing);
      expect(find.byIcon(Icons.lock_outline), findsOneWidget);
      final failing = FakeOps(fail: true);
      await open(tester, failing);
      expect(find.textContaining('The database is not reachable.'),
          findsOneWidget);
    });

    testWidgets('an operator adds, edits and deletes a product',
        (tester) async {
      await phone(tester);
      final ops = FakeOps();
      await open(tester, ops);
      await tap(tester, 'ops-product-new');
      await tap(tester, 'ops-save');
      expect(find.text('Enter the SKU.'), findsOneWidget);
      await enter(tester, 'ops-sku', 'bad sku!');
      await tap(tester, 'ops-save');
      expect(
          find.text('Use letters, digits and . _ / - only.'), findsOneWidget);
      await enter(tester, 'ops-sku', 'MX-9');
      await enter(tester, 'ops-title', 'Pirelli P Zero 245/40R19');
      await enter(tester, 'ops-cost', '310.5');
      await tap(tester, 'ops-save');
      expect(ops.bodies.single['sku'], 'MX-9');
      expect(ops.bodies.single['cost_price'], 310.5);
      expect(ops.bodies.single['title_ar'], isNull);

      await tester.tap(find.byKey(const Key('ops-product-cprod_0')));
      await tester.pumpAndSettle();
      await tap(tester, 'ops-product-edit');
      await enter(tester, 'ops-brand', 'Pirelli');
      await tap(tester, 'ops-save');
      expect(ops.calls, contains('updateProduct cprod_0'));
      expect(ops.bodies.last.containsKey('sku'), isFalse);
      expect(ops.bodies.last['brand'], 'Pirelli');

      ops.refuse = 'This SKU is still listed on a connected channel.';
      await tap(tester, 'ops-product-delete');
      await tap(tester, 'ops-confirm');
      expect(find.byKey(const Key('ops-product-error')), findsOneWidget);
      ops.refuse = null;
      await tap(tester, 'ops-product-delete');
      await tap(tester, 'ops-confirm');
      expect(ops.calls, contains('deleteProduct cprod_0'));
    });

    testWidgets('a viewer reads but cannot change anything', (tester) async {
      await phone(tester);
      await open(tester, FakeOps(operate: false));
      expect(find.byKey(const Key('ops-readonly')), findsOneWidget);
      expect(find.byKey(const Key('ops-product-new')), findsNothing);
      await tester.tap(find.byKey(const Key('ops-product-cprod_0')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('ops-product-edit')), findsNothing);
      expect(find.byKey(const Key('ops-product-delete')), findsNothing);
      expect(find.byKey(const Key('ops-price-clist_0')), findsNothing);
    });
  });

  group('prices and stock', () {
    testWidgets('a price change is proposed, validated and sent for approval',
        (tester) async {
      await phone(tester);
      final ops = FakeOps();
      var opened = 0;
      await open(tester, ops, tab: 'listings', onApprovals: () => opened++);
      await tap(tester, 'ops-price-clist_0');
      await enter(tester, 'ops-number', '0');
      await tap(tester, 'ops-number-ok');
      expect(find.text('Enter an amount above zero.'), findsOneWidget);
      await enter(tester, 'ops-number', '389.5');
      await tap(tester, 'ops-number-ok');
      expect(ops.calls, contains('price clist_0 389.5'));
      expect(find.byKey(const Key('ops-proposed')), findsOneWidget);
      await tester.tap(find.text('Open Approvals'));
      await tester.pumpAndSettle();
      expect(opened, 1);
    });

    testWidgets('a stock change needs a whole number', (tester) async {
      await phone(tester);
      final ops = FakeOps();
      await open(tester, ops, tab: 'listings');
      await tap(tester, 'ops-stock-clist_0');
      await enter(tester, 'ops-number', '2.5');
      await tap(tester, 'ops-number-ok');
      expect(
          find.text('Enter a whole number of zero or more.'), findsOneWidget);
      await enter(tester, 'ops-number', '0');
      await tap(tester, 'ops-number-ok');
      expect(ops.calls, contains('stock clist_0 0'));
    });

    testWidgets('the server refusing a change is shown', (tester) async {
      await phone(tester);
      final ops = FakeOps()..refuse = 'This connection is read-only.';
      await open(tester, ops, tab: 'listings');
      await tap(tester, 'ops-price-clist_0');
      await enter(tester, 'ops-number', '300');
      await tap(tester, 'ops-number-ok');
      expect(find.text('This connection is read-only.'), findsOneWidget);
    });
  });

  group('returns, alerts and channels', () {
    testWidgets('returns follow the chosen window', (tester) async {
      await phone(tester);
      final ops = FakeOps();
      await open(tester, ops, tab: 'returns');
      expect(ops.calls, contains('returns 30 0'));
      await tap(tester, 'ops-returns-7');
      expect(ops.calls.last, 'returns 7 0');
      expect(find.text('404-0'), findsOneWidget);
    });

    testWidgets('an operator acknowledges an alert; others cannot',
        (tester) async {
      await phone(tester);
      final ops = FakeOps();
      await open(tester, ops, tab: 'alerts');
      expect(find.text('MX-1 is low on stock'), findsOneWidget);
      await tap(tester, 'ops-ack-calert_1');
      expect(ops.calls, contains('ack calert_1'));
      expect(find.text('No alerts here'), findsOneWidget);

      await open(tester, FakeOps(operate: false), tab: 'alerts');
      expect(find.byKey(const Key('ops-ack-calert_1')), findsNothing);
    });

    testWidgets('channels: an operator can only sync', (tester) async {
      await phone(tester);
      final ops = FakeOps();
      await open(tester, ops, tab: 'channels');
      expect(find.text('Amazon Saudi'), findsOneWidget);
      expect(find.byKey(const Key('ops-test-cconn_1')), findsNothing);
      expect(find.byKey(const Key('ops-delete-cconn_1')), findsNothing);
      await tap(tester, 'ops-sync-cconn_1');
      expect(ops.calls, contains('sync cconn_1'));
      expect(find.text('Synced 12 orders.'), findsOneWidget);
    });

    testWidgets('channels: an owner tests, turns off and removes',
        (tester) async {
      await phone(tester);
      final ops = FakeOps(manage: true);
      await open(tester, ops, tab: 'channels');
      await tap(tester, 'ops-test-cconn_1');
      expect(find.text('Connected as seller A1.'), findsOneWidget);
      await tap(tester, 'ops-toggle-cconn_1');
      expect(ops.calls, contains('enabled cconn_1 false'));
      expect(find.text('Turned off'), findsOneWidget);
      ops.refuse = 'Nope';
      await tap(tester, 'ops-delete-cconn_1');
      await tap(tester, 'ops-confirm');
      expect(find.text('Nope'), findsOneWidget);
      ops.refuse = null;
      await tester.pump(const Duration(seconds: 5));
      await tap(tester, 'ops-delete-cconn_1');
      await tap(tester, 'ops-confirm');
      expect(ops.calls, contains('deleteChannel cconn_1'));
      expect(find.text('No sales channel connected'), findsOneWidget);
    });
  });

  for (final tab in [
    'catalogue',
    'listings',
    'returns',
    'alerts',
    'channels'
  ]) {
    for (final width in [320.0, 390.0]) {
      testWidgets('arabic $tab fits at $width', (tester) async {
        await phone(tester, width: width);
        await open(tester, FakeOps(manage: true),
            tab: tab, locale: const Locale('ar'));
        expect(find.text('الكتالوج'), findsWidgets);
        expect(tester.takeException(), isNull);
      });
    }
  }
}
