// Purchases on the phone (docs/purchases.md#mobile) against a fake API that
// answers with the API's JSON shapes (packages/contracts/src/purchases.ts):
// status groups and paging, a purchase with its timeline, shipments and
// returns, a status move with its version, starting a return with an
// idempotency key, linking the payment and a refund from Finance, a stale
// version, and every state from 320 px Arabic at 200 percent to a tablet.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/purchases/purchases_controller.dart';
import 'package:anum_mobile/features/purchases/purchases_data.dart';
import 'package:anum_mobile/features/purchases/purchases_screen.dart';
import 'package:anum_mobile/features/purchases/purchases_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

Map<String, Object?> purchaseJson(String id,
        {String merchant = 'Noon',
        String status = 'shipped',
        int version = 3,
        Map<String, Object?>? payment,
        bool canEdit = true}) =>
    {
      'id': id,
      'owner_user_id': 'user_me',
      'ownership': 'personal',
      'order_ref': 'NOON-1001',
      'merchant': merchant,
      'ordered_on': '2026-11-01',
      'currency': 'AED',
      'tax_amount': '9.52',
      'shipping_amount': null,
      'total': '199.99',
      'payment_method': 'card',
      'payment': payment,
      'document_id': null,
      'expected_delivery_on': '2026-11-06',
      'delivered_on': null,
      'address_ref': 'Home',
      'warranty_until': null,
      'return_window_until': '2026-11-15',
      'status': status,
      'source': 'web',
      'needs_review': false,
      'notes': null,
      'archived_at': null,
      'version': version,
      'created_at': '2026-11-01T08:00:00Z',
      'updated_at': '2026-11-01T08:00:00Z',
      'can_edit': canEdit,
      'can_pay': true,
      'mine': true,
      'allowed_moves': ['out_for_delivery', 'delivered', 'cancelled'],
      'due': [
        {
          'purchase_id': id,
          'merchant': merchant,
          'order_ref': 'NOON-1001',
          'kind': 'delivery_expected',
          'source_id': id,
          'due_on': '2026-11-06',
          'days_left': 1,
        }
      ],
      'refunded': <Object?>[],
      'items_total': '199.99',
    };

Map<String, Object?> eventJson(String id, String from, String to) => {
      'id': id,
      'owner_user_id': 'user_me',
      'purchase_id': 'pur_1',
      'kind': 'status',
      'from_status': from,
      'to_status': to,
      'subject_id': null,
      'note': null,
      'source': 'web',
      'occurred_at': '2026-11-02T08:00:00Z',
      'created_at': '2026-11-02T08:00:00Z',
    };

Map<String, Object?> returnJson(String id) => {
      'id': id,
      'owner_user_id': 'user_me',
      'purchase_id': 'pur_1',
      'status': 'requested',
      'requested_on': '2026-11-05',
      'reason': 'damaged',
      'reason_note': 'Screen cracked',
      'items': <Object?>[],
      'amount': '199.99',
      'currency': 'AED',
      'deadline_on': null,
      'pickup_on': null,
      'courier': null,
      'tracking_number': null,
      'proof_document_id': null,
      'confirmation_ref': null,
      'refund_ref': null,
      'returned_on': null,
      'expected_refund_on': null,
      'refunded_on': null,
      'dispute_note': null,
      'refunds': <Object?>[],
      'refunded': '0.00',
      'refunded_all': <Object?>[],
      'version': 1,
      'can_edit': true,
      'can_refund': true,
      'allowed_moves': ['approved', 'cancelled'],
    };

class PurchasesApi {
  PurchasesApi({this.empty = false});
  bool empty;
  bool staleOnce = false;
  String status = 'shipped';
  int version = 3;
  final returns = <Map<String, Object?>>[];
  final shipments = <Map<String, Object?>>[];
  Map<String, Object?>? payment;
  late final api = FakeApi(route);

  Map<String, Object?> detail() => {
        ...purchaseJson('pur_1',
            status: status, version: version, payment: payment),
        'items': [
          {
            'id': 'pitem_1',
            'owner_user_id': 'user_me',
            'purchase_id': 'pur_1',
            'position': 0,
            'description': 'Phone case',
            'sku': null,
            'quantity': '2',
            'unit_price': '99.99',
            'warranty_until': null,
            'created_at': '2026-11-01T08:00:00Z',
          }
        ],
        'shipments': shipments,
        'returns': returns,
        'events': [eventJson('pev_1', 'ordered', 'shipped')],
        'events_total': 1,
      };

  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path;
    final q = r.uri.queryParameters;
    if (r.method == 'GET' && path == '/api/v1/purchases') {
      if (q['status'] == 'awaiting') {
        return ok({
          'items': [
            if (status != 'delivered')
              purchaseJson('pur_1', status: status, version: version),
          ],
          'total': status != 'delivered' ? 1 : 0,
          'limit': 20,
          'offset': 0,
        });
      }
      if (empty || q['status'] == 'returns') {
        return ok({'items': [], 'total': 0, 'limit': 20, 'offset': 0});
      }
      return ok({
        'items': [
          purchaseJson('pur_1', status: status, version: version),
          purchaseJson('pur_2', merchant: 'Amazon', status: 'delivered'),
        ],
        'total': 2,
        'limit': 20,
        'offset': 0,
      });
    }
    if (r.method == 'GET' && path == '/api/v1/purchases/pur_1') {
      return ok(detail());
    }
    if (r.method == 'GET' && path == '/api/v1/purchases/pur_1/events') {
      return ok({
        'items': [
          if (status != 'shipped') eventJson('pev_2', 'shipped', status),
          eventJson('pev_1', 'ordered', 'shipped'),
        ],
        'total': status != 'shipped' ? 2 : 1,
        'limit': 20,
        'offset': 0,
      });
    }
    if (r.method == 'POST' && path == '/api/v1/purchases/pur_1/status') {
      if (staleOnce) {
        staleOnce = false;
        version += 1;
        return refuse(
            409,
            'This purchase changed since you opened it. Reload and try again.',
            'stale');
      }
      if (r.body!['version'] != version) {
        return refuse(409, 'stale version', 'stale');
      }
      status = r.body!['to']! as String;
      version += 1;
      return ok(detail());
    }
    if (r.method == 'POST' && path == '/api/v1/purchases/pur_1/shipments') {
      final shipment = {
        'id': 'pship_1',
        'owner_user_id': 'user_me',
        'purchase_id': 'pur_1',
        'carrier': r.body!['carrier'],
        'tracking_number': r.body!['tracking_number'],
        'status': r.body!['status'],
        'shipped_on': null,
        'expected_on': null,
        'delivered_on': null,
        'note': null,
        'version': 1,
        'can_edit': true,
      };
      shipments.add(shipment);
      return ok(shipment, 201);
    }
    if (r.method == 'POST' && path == '/api/v1/purchases/pur_1/returns') {
      final created = returnJson('pret_1');
      returns.add(created);
      return ok(created, 201);
    }
    if (r.method == 'GET' && path == '/api/v1/purchases/payments') {
      return ok({
        'items': [
          {
            'canonical_id':
                q['kind'] == 'refund' ? 'canon_refund' : 'canon_pay',
            'posted_on': '2026-11-01',
            'merchant': 'NOON',
            'category': 'shopping',
            'amount': '199.99',
            'currency': 'AED',
            'remaining': '199.99',
            'linked_to_purchase': false,
          }
        ]
      });
    }
    if (r.method == 'POST' && path == '/api/v1/purchases/pur_1/payment') {
      payment = {
        'link_id': 'flink_1',
        'canonical_id': 'canon_pay',
        'status': 'active',
        'status_reason': null,
        'amount': '199.99',
        'currency': 'AED',
        'paid_by': 'user_me',
        'recorded_as_cash': false,
        'linked_at': '2026-11-02T09:00:00Z',
        'original_canonical_id': null,
      };
      return ok({'payment': payment, 'replayed': false}, 201);
    }
    if (r.method == 'POST' &&
        path == '/api/v1/purchases/returns/pret_1/refunds') {
      return ok({
        'refund': {
          'link_id': 'flink_2',
          'canonical_id': 'canon_refund',
          'status': 'active',
          'status_reason': null,
          'amount': '199.99',
          'currency': 'AED',
          'paid_by': 'user_me',
          'recorded_as_cash': false,
          'linked_at': '2026-11-06T09:00:00Z',
          'original_canonical_id': 'canon_pay',
        },
        'replayed': false,
      }, 201);
    }
    return null;
  }
}

Future<void> pumpPurchases(WidgetTester tester, PurchasesApi fake,
    {Size size = phone,
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  final controller =
      PurchasesController(ApiPurchasesRepository(fake.api.client()));
  addTearDown(controller.dispose);
  await pumpModule(tester, PurchasesScreen(controller: controller),
      size: size, textScale: textScale, locale: locale);
}

void main() {
  test('catalogs match in English and Arabic', () {
    expect(PurchasesStrings.catalogsMatch, isTrue);
  });

  group('repository', () {
    test('reads a list page and the status filter', () async {
      final fake = PurchasesApi();
      final repo = ApiPurchasesRepository(fake.api.client());
      final page = await repo.list(status: 'open');
      expect(page.items.first.merchant, 'Noon');
      expect(page.items.first.due.single.kind, 'delivery_expected');
      expect(page.next, isNull);
      expect(fake.api.requests.last.uri.queryParameters['status'], 'open');
    });

    test('reads a detail with items, shipments, returns and events', () async {
      final fake = PurchasesApi()..returns.add(returnJson('pret_1'));
      final detail =
          await ApiPurchasesRepository(fake.api.client()).get('pur_1');
      expect(detail.items.single.description, 'Phone case');
      expect(detail.returns.single.canRefund, isTrue);
      expect(detail.events.single.toStatus, 'shipped');
      expect(detail.purchase.allowedMoves, contains('delivered'));
    });

    test('a move sends the version; a stale one keeps its code', () async {
      final fake = PurchasesApi()..staleOnce = true;
      final repo = ApiPurchasesRepository(fake.api.client());
      await expectLater(
          repo.move('pur_1', to: 'delivered', version: 3),
          throwsA(
              isA<ApiException>().having((e) => isStale(e), 'stale', isTrue)));
      final moved = await repo.move('pur_1', to: 'delivered', version: 4);
      expect(moved.purchase.status, 'delivered');
      expect(fake.api.requests.last.body, {'to': 'delivered', 'version': 4});
    });

    test('a return and a refund link carry idempotency keys', () async {
      final fake = PurchasesApi();
      final repo = ApiPurchasesRepository(fake.api.client());
      await repo.startReturn('pur_1', {'reason': 'damaged'},
          idempotencyKey: 'key_return');
      expect(fake.api.requests.last.headers['Idempotency-Key'], 'key_return');
      final refund = await repo.linkRefund('pret_1',
          canonicalId: 'canon_refund', idempotencyKey: 'key_refund');
      expect(refund.amount, '199.99');
      expect(fake.api.requests.last.body, {'canonical_id': 'canon_refund'});
    });
  });

  group('screen', () {
    testWidgets('list, detail with timeline, then a status move',
        (tester) async {
      final fake = PurchasesApi();
      await pumpPurchases(tester, fake);
      expect(find.text('Noon'), findsOneWidget);
      expect(find.textContaining('Delivery expected in 1 day'), findsWidgets);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      expect(find.text('2 × Phone case'), findsOneWidget);
      expect(find.text('Ordered → Shipped'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('move-delivered')));
      expect(
          fake.api.sent('POST', '/api/v1/purchases/pur_1/status').single.body,
          {'to': 'delivered', 'version': 3});
      expect(find.text('Shipped → Delivered'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('a move refreshes a filtered list', (tester) async {
      final fake = PurchasesApi();
      await pumpPurchases(tester, fake);
      await tapVisible(tester, find.byKey(const Key('filter-awaiting')));
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      await tapVisible(tester, find.byKey(const Key('move-delivered')));
      await tester.pageBack();
      await tester.pumpAndSettle();
      // Delivered is no longer awaiting delivery.
      expect(find.byKey(const Key('purchase-pur_1')), findsNothing);
    });

    testWidgets('a stale move reloads and says so', (tester) async {
      final fake = PurchasesApi()..staleOnce = true;
      await pumpPurchases(tester, fake);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      await tapVisible(tester, find.byKey(const Key('move-delivered')));
      expect(find.textContaining('Someone changed this'), findsOneWidget);
      expect(fake.api.sent('GET', '/api/v1/purchases/pur_1'), hasLength(2));
    });

    testWidgets('adds a shipment with tracking and starts a return',
        (tester) async {
      final fake = PurchasesApi();
      await pumpPurchases(tester, fake);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      await tapVisible(tester, find.byKey(const Key('purchase-add-shipment')));
      await tester.enterText(find.byKey(const Key('field-carrier')), 'Aramex');
      await tester.enterText(
          find.byKey(const Key('field-tracking_number')), 'TRACK-EXAMPLE');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(find.text('In transit · Aramex'), findsOneWidget);
      expect(find.text('Tracking number: TRACK-EXAMPLE'), findsOneWidget);

      await tapVisible(tester, find.byKey(const Key('purchase-start-return')));
      await tester.enterText(
          find.byKey(const Key('field-reason_note')), 'Screen cracked');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      final sent =
          fake.api.sent('POST', '/api/v1/purchases/pur_1/returns').single;
      expect(sent.body!['reason'], 'damaged');
      expect(sent.headers['Idempotency-Key'], startsWith('ret'));
      expect(find.text('Damaged · Requested'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('a shipment offers its carrier tracking pages', (tester) async {
      final fake = PurchasesApi();
      fake.shipments
        ..add({
          'id': 'purs_ups',
          'status': 'in_transit',
          'carrier': null,
          'tracking_number': '1Z999AA10123456784',
          'tracking': {
            'carrier': 'ups',
            'carrier_name': 'UPS',
            'url': 'https://www.ups.com/track?tracknum=1Z999AA10123456784',
            'detected_from': 'number',
            'candidates': [],
            'status_provider': 'off',
          },
        })
        ..add({
          'id': 'purs_ten',
          'status': 'in_transit',
          'carrier': null,
          'tracking_number': '1234567891',
          'tracking': {
            'carrier': null,
            'carrier_name': null,
            'url': null,
            'detected_from': null,
            'candidates': [
              {
                'carrier': 'dhl',
                'carrier_name': 'DHL Express',
                'url': 'https://www.dhl.com/x?tracking-id=1234567891',
                'confidence': 'likely'
              },
              {
                'carrier': 'aramex',
                'carrier_name': 'Aramex',
                'url': 'https://www.aramex.com/x?ShipmentNumber=1234567891',
                'confidence': 'possible'
              },
              {
                'carrier': 'smsa',
                'carrier_name': 'SMSA Express',
                'url': 'javascript:alert(1)',
                'confidence': 'possible'
              },
            ],
            'status_provider': 'off',
          },
        });
      final opened = <Uri>[];
      final original = purchasesOpenUrl;
      addTearDown(() => purchasesOpenUrl = original);
      purchasesOpenUrl = (uri) async {
        opened.add(uri);
        return uri.host != 'www.dhl.com';
      };
      await pumpPurchases(tester, fake);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      expect(find.text('In transit · UPS (from the tracking number)'),
          findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('track-purs_ups-UPS')));
      expect(opened.single.toString(),
          'https://www.ups.com/track?tracknum=1Z999AA10123456784');
      // Shared format: one button per carrier, and never a non-https link.
      expect(find.textContaining('fits more than one carrier'), findsOneWidget);
      expect(find.byKey(const Key('track-purs_ten-Aramex')), findsOneWidget);
      expect(
          find.byKey(const Key('track-purs_ten-SMSA Express')), findsNothing);
      await tapVisible(
          tester, find.byKey(const Key('track-purs_ten-DHL Express')));
      expect(find.textContaining('could not be opened'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('links the payment and the refund from Finance',
        (tester) async {
      final fake = PurchasesApi()..returns.add(returnJson('pret_1'));
      await pumpPurchases(tester, fake);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      await tapVisible(tester, find.byKey(const Key('purchase-link-payment')));
      await tapVisible(tester, find.byKey(const Key('candidate-canon_pay')));
      expect(fake.api.sent('POST', '/api/v1/purchases/pur_1/payment'),
          hasLength(1));
      expect(find.text('AED 199.99, linked'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('return-refund-pret_1')));
      expect(fake.api.requests.last.uri.queryParameters['kind'], 'refund');
      await tapVisible(tester, find.byKey(const Key('candidate-canon_refund')));
      expect(fake.api.sent('POST', '/api/v1/purchases/returns/pret_1/refunds'),
          hasLength(1));
      expectClean(tester);
    });

    testWidgets('a filter with no match offers to clear it', (tester) async {
      final fake = PurchasesApi();
      await pumpPurchases(tester, fake);
      await tester.tap(find.byKey(const Key('filter-returns')));
      await tester.pumpAndSettle();
      expect(find.text('Nothing matches'), findsOneWidget);
      await tester.tap(find.text('Clear filters'));
      await tester.pumpAndSettle();
      expect(find.text('Noon'), findsOneWidget);
    });

    testWidgets('empty list offers to add a purchase', (tester) async {
      await pumpPurchases(tester, PurchasesApi(empty: true));
      expect(find.byKey(const Key('purchases-empty')), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('a failed load says what failed', (tester) async {
      final fake = PurchasesApi();
      fake.api.failWith = 503;
      fake.api.failMessage = 'Purchases are temporarily unavailable.';
      await pumpPurchases(tester, fake);
      expect(find.byKey(const Key('records-error')), findsOneWidget);
      expect(find.textContaining('temporarily unavailable'), findsOneWidget);
    });

    testWidgets('a viewer is told who may see purchases', (tester) async {
      final fake = PurchasesApi();
      fake.api.failWith = 403;
      fake.api.failMessage =
          'Purchases are for workspace owners and members. Ask a workspace owner to change your role from viewer to member.';
      await pumpPurchases(tester, fake);
      expect(find.byKey(const Key('records-denied')), findsOneWidget);
      expect(find.byKey(const Key('purchases-add')), findsNothing);
    });

    testWidgets('Arabic, 320 px, 200 percent text: no overflow',
        (tester) async {
      final fake = PurchasesApi()..returns.add(returnJson('pret_1'));
      await pumpPurchases(tester, fake,
          size: narrow, textScale: 2, locale: const Locale('ar'));
      expect(find.text('المشتريات'), findsWidgets);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      expect(find.text('السجل الزمني'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('tablet: list and purchase side by side', (tester) async {
      final fake = PurchasesApi();
      await pumpPurchases(tester, fake, size: tablet);
      await tester.tap(find.byKey(const Key('purchase-pur_1')));
      await tester.pumpAndSettle();
      expect(find.byType(PurchaseDetailHost), findsOneWidget);
      expect(find.text('2 × Phone case'), findsOneWidget);
      expectClean(tester);
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
    });
  });
}
