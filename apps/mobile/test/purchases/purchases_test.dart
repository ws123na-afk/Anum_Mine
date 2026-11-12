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
import 'package:anum_mobile/features/purchases/vault_receipts.dart';
import 'package:anum_mobile/features/records_kit/records_kit.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/vault/vault_data.dart';
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
  Map<String, Object?> patched = {};
  bool archived = false;
  bool deleted = false;
  List<Map<String, Object?>> accounts = [
    {'id': 'acct_cash', 'name': 'Wallet', 'currency': 'AED', 'kind': 'cash'},
    {'id': 'acct_card', 'name': 'Card', 'currency': 'AED', 'kind': 'card'},
  ];
  late final api = FakeApi(route);

  Map<String, Object?> _money(String link, String canonical, String amount,
          {bool cash = false}) =>
      {
        'link_id': link,
        'canonical_id': canonical,
        'status': 'active',
        'status_reason': null,
        'amount': amount,
        'currency': 'AED',
        'paid_by': 'user_me',
        'recorded_as_cash': cash,
        'linked_at': '2026-11-06T09:00:00Z',
        'original_canonical_id': null,
      };

  Map<String, Object?> detail() => {
        ...purchaseJson('pur_1',
            status: status, version: version, payment: payment),
        ...patched,
        if (archived) 'archived_at': '2026-11-07T09:00:00Z',
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
      return deleted
          ? refuse(404, 'Purchase not found.', 'not_found')
          : ok(detail());
    }
    if (r.method == 'PATCH' && path == '/api/v1/purchases/pur_1') {
      if (r.body!['version'] != version) {
        return refuse(
            409,
            'This purchase changed since you opened it. Reload and try again.',
            'stale');
      }
      patched.addAll({...r.body!}..remove('version'));
      version += 1;
      return ok(detail());
    }
    if (r.method == 'POST' && path == '/api/v1/purchases/pur_1/archive') {
      archived = r.body!['archived'] == true;
      version += 1;
      return ok(detail());
    }
    if (r.method == 'DELETE' && path == '/api/v1/purchases/pur_1') {
      if (payment != null) {
        return refuse(
            409,
            'This purchase has a payment or refunds linked from Finance. Remove those links, or archive it to keep its history.',
            'has_payments');
      }
      deleted = true;
      return const ApiResponse(statusCode: 204, body: null);
    }
    if (r.method == 'POST' && path == '/api/v1/purchases/pur_1/notes') {
      return ok({
        'id': 'pev_note',
        'owner_user_id': 'user_me',
        'purchase_id': 'pur_1',
        'kind': 'note',
        'note': r.body!['note'],
        'occurred_at': '2026-11-07T08:00:00Z',
      }, 201);
    }
    if (path == '/api/v1/purchases/shipments/pship_1') {
      if (r.method == 'DELETE') {
        shipments.clear();
        return const ApiResponse(statusCode: 204, body: null);
      }
      final changed = {...shipments.single, ...r.body!}..remove('version');
      changed['version'] = (shipments.single['version']! as int) + 1;
      shipments[0] = changed;
      return ok(changed);
    }
    if (path == '/api/v1/purchases/returns/pret_1') {
      if (r.method == 'DELETE') {
        returns.clear();
        return const ApiResponse(statusCode: 204, body: null);
      }
      returns[0] = {...returns.single, ...r.body!, 'version': 2};
      return ok(returns.single);
    }
    if (r.method == 'POST' &&
        path == '/api/v1/purchases/returns/pret_1/status') {
      returns[0] = {
        ...returns.single,
        'status': r.body!['to'],
        'version': 2,
        'allowed_moves': ['cancelled'],
      };
      return ok(returns.single);
    }
    if (r.method == 'GET' && path == '/api/v1/finance/accounts') {
      return ok({'data': accounts});
    }
    if (r.method == 'POST' && path == '/api/v1/purchases/pur_1/payment/cash') {
      payment = _money('flink_cash', 'canon_cash', r.body!['amount']! as String,
          cash: true);
      return ok({'payment': payment, 'replayed': false}, 201);
    }
    if (r.method == 'DELETE' && path == '/api/v1/purchases/pur_1/payment') {
      payment = null;
      return const ApiResponse(statusCode: 204, body: null);
    }
    if (r.method == 'POST' &&
        path == '/api/v1/purchases/returns/pret_1/refunds/cash') {
      final refund = _money(
          'flink_rcash', 'canon_rcash', r.body!['amount']! as String,
          cash: true);
      returns[0] = {
        ...returns.single,
        'refunds': [refund],
        'refunded': refund['amount']
      };
      return ok({'refund': refund, 'replayed': false}, 201);
    }
    if (r.method == 'DELETE' &&
        path == '/api/v1/purchases/returns/pret_1/refunds/flink_rcash') {
      returns[0] = {
        ...returns.single,
        'refunds': <Object?>[],
        'refunded': '0.00'
      };
      return const ApiResponse(statusCode: 204, body: null);
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

class FakeReceipts implements PurchaseReceipts {
  final uploads = <String>[];
  bool failSearch = false;

  @override
  Future<List<ReceiptChoice>> search(String query) async {
    if (failSearch) throw const ApiException(503, 'The vault is unavailable.');
    return [
      if ('noon receipt'.contains(query.toLowerCase()))
        const ReceiptChoice(
            id: 'doc_noon', title: 'Noon receipt', category: 'receipt'),
    ];
  }

  @override
  Future<ReceiptChoice?> upload() async {
    uploads.add('receipt.pdf');
    return const ReceiptChoice(
        id: 'doc_new', title: 'receipt', category: 'receipt');
  }
}

Future<void> pumpPurchases(WidgetTester tester, PurchasesApi fake,
    {Size size = phone,
    double textScale = 1,
    Locale locale = const Locale('en'),
    PurchaseReceipts? receipts}) async {
  final controller =
      PurchasesController(ApiPurchasesRepository(fake.api.client()));
  addTearDown(controller.dispose);
  await pumpModule(
      tester, PurchasesScreen(controller: controller, receipts: receipts),
      size: size, textScale: textScale, locale: locale);
}

void main() {
  test('catalogs match in English and Arabic', () {
    expect(PurchasesStrings.catalogsMatch, isTrue);
  });

  test('receipts search the vault and upload a private receipt', () async {
    final vault = _FakeVault();
    final receipts = VaultPurchaseReceipts(vault,
        picker: (_) async => ('/tmp/noon.pdf', 'noon.pdf', 2048));
    expect((await receipts.search('noon')).single.id, 'doc_1');
    expect(vault.queries, ['noon']);
    final uploaded = await receipts.upload();
    expect(uploaded!.id, 'doc_2');
    expect(vault.uploads, ['/tmp/noon.pdf noon.pdf noon receipt private']);
    expect(
        await VaultPurchaseReceipts(vault, picker: (_) async => null).upload(),
        isNull);
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
            'candidates': <Object?>[],
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
      await tapVisible(tester, find.byKey(const Key('return-actions-pret_1')));
      await tapVisible(tester, find.byKey(const Key('action-link')));
      expect(fake.api.requests.last.uri.queryParameters['kind'], 'refund');
      await tapVisible(tester, find.byKey(const Key('candidate-canon_refund')));
      expect(fake.api.sent('POST', '/api/v1/purchases/returns/pret_1/refunds'),
          hasLength(1));
      expectClean(tester);
    });

    testWidgets('edits details with the version on screen, adds a note',
        (tester) async {
      final fake = PurchasesApi();
      await pumpPurchases(tester, fake);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      await tapVisible(tester, find.byKey(const Key('purchase-edit')));
      await tester.enterText(
          find.byKey(const Key('field-order_ref')), 'NOON-2002');
      await tester.enterText(
          find.byKey(const Key('field-warranty_until')), '2027-11-01');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      final sent = fake.api.sent('PATCH', '/api/v1/purchases/pur_1').single;
      expect(sent.body, {
        'order_ref': 'NOON-2002',
        'warranty_until': '2027-11-01',
        'version': 3
      });
      expect(find.text('NOON-2002'), findsOneWidget);

      await tapVisible(tester, find.byKey(const Key('purchase-note')));
      await tester.enterText(
          find.byKey(const Key('field-note')), 'Called the shop');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(fake.api.sent('POST', '/api/v1/purchases/pur_1/notes').single.body,
          {'note': 'Called the shop'});
      expectClean(tester);
    });

    testWidgets('a stale edit says so and keeps the form open', (tester) async {
      final fake = PurchasesApi();
      await pumpPurchases(tester, fake);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      await tapVisible(tester, find.byKey(const Key('purchase-edit')));
      fake.version = 9;
      await tester.enterText(
          find.byKey(const Key('field-order_ref')), 'NOON-3');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(find.textContaining('changed since you opened it'), findsWidgets);
    });

    testWidgets('attaches a receipt from the vault, uploads one, removes it',
        (tester) async {
      final fake = PurchasesApi();
      final receipts = FakeReceipts();
      await pumpPurchases(tester, fake, receipts: receipts);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      await tapVisible(tester, find.byKey(const Key('purchase-receipt')));
      await tapVisible(tester, find.byKey(const Key('receipt-doc_noon')));
      expect(
          fake.api
              .sent('PATCH', '/api/v1/purchases/pur_1')
              .last
              .body!['document_id'],
          'doc_noon');
      expect(find.text('Attached from the document vault'), findsOneWidget);

      await tapVisible(tester, find.byKey(const Key('purchase-receipt')));
      await tapVisible(tester, find.byKey(const Key('receipt-upload')));
      expect(receipts.uploads, ['receipt.pdf']);
      expect(
          fake.api
              .sent('PATCH', '/api/v1/purchases/pur_1')
              .last
              .body!['document_id'],
          'doc_new');

      await tapVisible(tester, find.byKey(const Key('purchase-receipt')));
      await tapVisible(tester, find.byKey(const Key('receipt-remove')));
      expect(
          fake.api
              .sent('PATCH', '/api/v1/purchases/pur_1')
              .last
              .body!
              .containsKey('document_id'),
          isTrue);
      expect(
          fake.api
              .sent('PATCH', '/api/v1/purchases/pur_1')
              .last
              .body!['document_id'],
          isNull);
      expectClean(tester);
    });

    testWidgets('a vault that cannot be searched says so', (tester) async {
      final fake = PurchasesApi();
      final receipts = FakeReceipts()..failSearch = true;
      await pumpPurchases(tester, fake, receipts: receipts);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      await tapVisible(tester, find.byKey(const Key('purchase-receipt')));
      expect(find.byKey(const Key('receipt-error')), findsOneWidget);
    });

    testWidgets('edits and removes a shipment', (tester) async {
      final fake = PurchasesApi()
        ..shipments.add({
          'id': 'pship_1',
          'purchase_id': 'pur_1',
          'carrier': 'Aramex',
          'tracking_number': 'TRACK-EXAMPLE',
          'status': 'in_transit',
          'version': 1,
          'can_edit': true,
        });
      await pumpPurchases(tester, fake);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      await tapVisible(tester, find.byKey(const Key('shipment-pship_1')));
      await tapVisible(tester, find.byKey(const Key('action-edit')));
      await tester.enterText(find.byKey(const Key('field-carrier')), 'DHL');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(
          fake.api
              .sent('PATCH', '/api/v1/purchases/shipments/pship_1')
              .single
              .body,
          {'carrier': 'DHL', 'version': 1});
      expect(find.text('In transit · DHL'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('shipment-pship_1')));
      await tapVisible(tester, find.byKey(const Key('action-delete')));
      await tapVisible(tester, find.byKey(const Key('records-confirm')));
      expect(fake.api.sent('DELETE', '/api/v1/purchases/shipments/pship_1'),
          hasLength(1));
      expect(find.byKey(const Key('shipment-pship_1')), findsNothing);
    });

    testWidgets('a return: edit, move, cash refund, unlink, then remove',
        (tester) async {
      final fake = PurchasesApi()..returns.add(returnJson('pret_1'));
      await pumpPurchases(tester, fake);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      Future<void> act(String key) async {
        await tapVisible(
            tester, find.byKey(const Key('return-actions-pret_1')));
        await tapVisible(tester, find.byKey(Key('action-$key')));
      }

      await act('edit');
      await tester.enterText(find.byKey(const Key('field-courier')), 'Aramex');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(
          fake.api
              .sent('PATCH', '/api/v1/purchases/returns/pret_1')
              .single
              .body,
          {'courier': 'Aramex', 'version': 1});

      await act('move:approved');
      expect(
          fake.api
              .sent('POST', '/api/v1/purchases/returns/pret_1/status')
              .single
              .body,
          {'to': 'approved', 'version': 2});
      expect(find.text('Damaged · Approved'), findsOneWidget);

      await act('cash');
      expect(find.text('Wallet (AED)'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      final cash = fake.api
          .sent('POST', '/api/v1/purchases/returns/pret_1/refunds/cash')
          .single;
      expect(cash.body, {
        'account_id': 'acct_cash',
        'amount': '199.99',
        'posted_on': isA<String>()
      });
      expect(cash.headers['Idempotency-Key'], startsWith('cashref'));

      await act('unlink:flink_rcash');
      await tapVisible(tester, find.byKey(const Key('records-confirm')));
      expect(
          fake.api.sent(
              'DELETE', '/api/v1/purchases/returns/pret_1/refunds/flink_rcash'),
          hasLength(1));

      await act('delete');
      await tapVisible(tester, find.byKey(const Key('records-confirm')));
      expect(fake.api.sent('DELETE', '/api/v1/purchases/returns/pret_1'),
          hasLength(1));
      expect(find.byKey(const Key('return-pret_1')), findsNothing);
      expectClean(tester);
    });

    testWidgets('records a cash payment, unlinks it, archives and deletes',
        (tester) async {
      final fake = PurchasesApi();
      await pumpPurchases(tester, fake);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      await tapVisible(tester, find.byKey(const Key('purchase-cash-payment')));
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      final cash =
          fake.api.sent('POST', '/api/v1/purchases/pur_1/payment/cash').single;
      expect(cash.body, {
        'account_id': 'acct_cash',
        'amount': '199.99',
        'posted_on': '2026-11-01'
      });
      expect(find.byKey(const Key('purchase-unlink-payment')), findsOneWidget);

      // A purchase with money linked cannot be deleted: the API says why.
      await tapVisible(tester, find.byKey(const Key('purchase-delete')));
      await tapVisible(tester, find.byKey(const Key('records-confirm')));
      expect(find.textContaining('payment or refunds linked'), findsOneWidget);

      await tapVisible(
          tester, find.byKey(const Key('purchase-unlink-payment')));
      await tapVisible(tester, find.byKey(const Key('records-confirm')));
      expect(find.byKey(const Key('purchase-cash-payment')), findsOneWidget);

      await tapVisible(tester, find.byKey(const Key('purchase-archive')));
      expect(
          fake.api.sent('POST', '/api/v1/purchases/pur_1/archive').single.body,
          {'version': 3, 'archived': true});
      expect(find.text('Restore'), findsOneWidget);
      expect(find.byKey(const Key('purchase-edit')), findsNothing);

      await tapVisible(tester, find.byKey(const Key('purchase-delete')));
      await tapVisible(tester, find.byKey(const Key('records-confirm')));
      expect(fake.api.sent('DELETE', '/api/v1/purchases/pur_1'), hasLength(2));
      expect(find.byKey(const Key('purchase-pur_1')), findsNothing);
    });

    testWidgets(
        'without a Finance account a cash payment says where to add one',
        (tester) async {
      final fake = PurchasesApi()..accounts = [];
      await pumpPurchases(tester, fake);
      await tapVisible(tester, find.byKey(const Key('purchase-pur_1')));
      await tapVisible(tester, find.byKey(const Key('purchase-cash-payment')));
      expect(find.textContaining('Add one under Finance, Accounts'),
          findsOneWidget);
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

/// Only what receipts use; anything else fails the test loudly.
class _FakeVault implements VaultRepository {
  final queries = <String>[];
  final uploads = <String>[];

  VaultDocument _doc(String id, String title) => VaultDocument.fromJson(
      JsonReader({'id': id, 'title': title, 'category': 'receipt'}));

  @override
  Future<RecordsChunk<VaultDocument>> list(
      {String query = '',
      String category = '',
      String expiry = '',
      bool trash = false,
      String? token}) async {
    queries.add(query);
    return RecordsChunk([_doc('doc_1', 'Noon receipt')]);
  }

  @override
  Future<VaultDocument> upload(String path,
      {required String name,
      required String title,
      required String category,
      required String accessClass,
      bool allowDuplicate = false,
      void Function(int sent, int total)? onProgress}) async {
    uploads.add('$path $name $title $category $accessClass');
    return _doc('doc_2', title);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName}');
}
