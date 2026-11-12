// Vehicles on the phone (docs/vehicles.md#mobile) against a fake API that
// answers with the API's JSON shapes (packages/contracts/src/vehicles.ts):
// the list with reminders, a vehicle's fuel, trips and service, adding a
// fuel entry with an idempotency key, the "odometer cannot go down" refusal
// and its correction, linking a Finance payment, and every state, from a
// 320 px phone at 200 percent text in Arabic to a 1024 px tablet.
import 'dart:convert';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/vehicles/vehicles_controller.dart';
import 'package:anum_mobile/features/vehicles/vehicles_data.dart';
import 'package:anum_mobile/features/vehicles/vehicles_screen.dart';
import 'package:anum_mobile/features/vehicles/vehicles_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

Map<String, Object?> vehicleJson(String id,
        {String registration = 'DXB A 12345',
        bool canEdit = true,
        String ownership = 'personal',
        List<Map<String, Object?>> due = const []}) =>
    {
      'id': id,
      'owner_user_id': 'user_me',
      'ownership': ownership,
      'registration': registration,
      'vin': '••••1234',
      'vin_masked': true,
      'nickname': 'Family car',
      'make': 'Toyota',
      'model': 'Camry',
      'year': 2022,
      'vehicle_type': 'car',
      'country': 'AE',
      'currency': 'AED',
      'fuel_type': 'petrol_95',
      'tank_litres': '60.00',
      'acquisition_date': null,
      'purchase_price': null,
      'residual_value': null,
      'useful_life_months': null,
      'purchase_payment': null,
      'insurance_expires_on': '2026-12-01',
      'registration_expires_on': null,
      'inspection_expires_on': null,
      'notes': null,
      'archived_at': null,
      'version': 1,
      'created_at': '2026-11-01T08:00:00Z',
      'updated_at': '2026-11-01T08:00:00Z',
      'can_edit': canEdit,
      'mine': true,
      'odometer_km': '42000.0',
      'due': due,
    };

Map<String, Object?> dueJson(String vehicleId, {int days = 12}) => {
      'vehicle_id': vehicleId,
      'registration': 'DXB A 12345',
      'kind': 'insurance',
      'source_id': vehicleId,
      'due_on': '2026-11-17',
      'days_left': days,
      'due_km': null,
      'km_left': null,
    };

Map<String, Object?> fuelJson(String id, {Map<String, Object?>? payment}) => {
      'id': id,
      'owner_user_id': 'user_me',
      'vehicle_id': 'veh_1',
      'visibility': 'personal',
      'vehicle_owner_id': 'user_me',
      'version': 1,
      'created_at': '2026-11-02T08:00:00Z',
      'updated_at': '2026-11-02T08:00:00Z',
      'can_edit': true,
      'mine': true,
      'filled_at': '2026-11-02T07:30:00Z',
      'station': 'ENOC Al Barsha',
      'fuel_type': 'petrol_95',
      'litres': '45.000',
      'price_per_litre': '2.890',
      'total': '130.05',
      'currency': 'AED',
      'odometer_km': '42000.0',
      'full_tank': true,
      'payment_method': 'card',
      'payment': payment,
      'document_id': null,
      'notes': null,
    };

Map<String, Object?> page(List<Map<String, Object?>> items,
        {int? total, int offset = 0}) =>
    {
      'items': items,
      'total': total ?? items.length,
      'limit': 20,
      'offset': offset
    };

/// The vehicles API over an in-memory list.
class VehiclesApi {
  VehiclesApi(
      {this.empty = false, this.business = false, this.proposal = false});
  bool empty, business, proposal;
  bool decreaseOnce = false;
  final fuel = <Map<String, Object?>>[fuelJson('vfuel_1')];
  late final api = FakeApi(route);

  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path;
    if (r.method == 'GET' && path == '/api/v1/vehicles') {
      if (empty) return ok(page([]));
      if (r.uri.queryParameters['offset'] != '0') {
        return ok(page([vehicleJson('veh_21', registration: 'AUH 9')],
            total: 3, offset: 2));
      }
      return ok(page([
        vehicleJson('veh_1', due: [dueJson('veh_1')]),
        vehicleJson('veh_2', registration: 'SHJ 3 777', canEdit: false),
      ], total: 3));
    }
    if (r.method == 'GET' && path == '/api/v1/vehicles/reminders') {
      return ok({
        'items': empty ? [] : [dueJson('veh_1')],
        'window_days': 30
      });
    }
    if (r.method == 'GET' && path == '/api/v1/vehicles/veh_1') {
      return ok(vehicleJson('veh_1',
          due: [dueJson('veh_1')],
          ownership: business ? 'business' : 'personal'));
    }
    // A business vehicle's cost from a business allocation
    // (docs/vehicles.md#business-allocations).
    if (r.method == 'GET' && path == '/api/v1/vehicles/allocations') {
      return ok({
        'items': [
          {
            'allocation_id': 'alloc_1',
            'posted_on': '2026-11-02',
            'amount': '130.05',
            'currency': 'AED',
            'category': 'fuel',
            'memo': 'Van diesel',
            'source_ref': 'a1b2c3',
          }
        ]
      });
    }
    if (r.method == 'POST' &&
        path == '/api/v1/vehicles/costs/fuel_entry/vfuel_1/allocation') {
      final payment = {
        'link_id': '',
        'canonical_id': '',
        'status': 'active',
        'status_reason': null,
        'amount': '130.05',
        'currency': 'AED',
        'paid_by': 'user_me',
        'recorded_as_cash': false,
        'linked_at': '2026-11-02T09:00:00Z',
        'source': 'allocation',
        'allocation_id': r.body!['allocation_id'],
        'source_ref': 'a1b2c3',
      };
      fuel[fuel.length - 1] = fuelJson('vfuel_1', payment: payment);
      return ok({'payment': payment, 'replayed': false}, 201);
    }
    if (r.method == 'GET' && path == '/api/v1/vehicles/veh_1/fuel') {
      // A business allocation proposed for a record with no cost yet.
      return ok(page([
        for (final f in fuel)
          {
            ...f,
            if (proposal && f['payment'] == null)
              'allocation_proposal': {
                'allocation_id': 'alloc_1',
                'posted_on': '2026-11-02',
                'amount': '130.05',
                'currency': 'AED',
                'category': 'fuel',
                'memo': 'Van diesel',
                'source_ref': 'a1b2c3',
                'days_apart': 0,
              },
          }
      ]));
    }
    if (r.method == 'GET' &&
        (path == '/api/v1/vehicles/veh_1/trips' ||
            path == '/api/v1/vehicles/veh_1/service')) {
      return ok(page([]));
    }
    if (r.method == 'POST' && path == '/api/v1/vehicles/veh_1/fuel') {
      if (decreaseOnce) {
        decreaseOnce = false;
        return refuse(
            409,
            'The odometer cannot go down: 42000.0 km was recorded on 2026-11-02. '
                'If the odometer was replaced or a reading was wrong, add a correction with a reason.',
            'odometer_decrease');
      }
      final body = r.body!;
      final created = {
        ...fuelJson('vfuel_${fuel.length + 1}'),
        'litres': body['litres'],
        'odometer_km': body['odometer_km'],
        'replayed': false,
      };
      fuel.insert(0, created);
      return ok(created, 201);
    }
    if (r.method == 'POST' && path == '/api/v1/vehicles/veh_1/odometer') {
      return ok({
        'id': 'vodo_9',
        'reading_km': r.body!['reading_km'],
        'correction': r.body!['correction'],
      }, 201);
    }
    if (r.method == 'GET' && path == '/api/v1/vehicles/payments') {
      return ok({
        'items': [
          {
            'canonical_id': 'canon_1',
            'posted_on': '2026-11-02',
            'merchant': 'ENOC',
            'category': 'fuel',
            'amount': '130.05',
            'currency': 'AED',
            'remaining': '130.05',
            'linked_to_vehicle': false,
          }
        ]
      });
    }
    if (r.method == 'POST' &&
        path == '/api/v1/vehicles/costs/fuel_entry/vfuel_1/payment') {
      final payment = {
        'link_id': 'flink_1',
        'canonical_id': 'canon_1',
        'status': 'active',
        'status_reason': null,
        'amount': '130.05',
        'currency': 'AED',
        'paid_by': 'user_me',
        'recorded_as_cash': false,
        'linked_at': '2026-11-02T09:00:00Z',
      };
      fuel[fuel.length - 1] = fuelJson('vfuel_1', payment: payment);
      return ok({'payment': payment, 'replayed': false});
    }
    return null;
  }
}

Future<VehiclesController> pumpVehicles(WidgetTester tester, VehiclesApi fake,
    {Size size = phone,
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  final controller =
      VehiclesController(ApiVehiclesRepository(fake.api.client()));
  addTearDown(controller.dispose);
  await pumpModule(tester, VehiclesScreen(controller: controller),
      size: size, textScale: textScale, locale: locale);
  return controller;
}

void main() {
  test('catalogs match in English and Arabic', () {
    expect(VehiclesStrings.catalogsMatch, isTrue);
  });

  group('repository', () {
    test('reads a page and the next offset', () async {
      final fake = VehiclesApi();
      final repo = ApiVehiclesRepository(fake.api.client());
      final first = await repo.list();
      expect(first.items.map((v) => v.id), ['veh_1', 'veh_2']);
      expect(first.items.first.due.single.kind, 'insurance');
      expect(first.items.first.odometerKm, '42000.0');
      expect(first.next, '2');
      expect(fake.api.requests.last.uri.queryParameters,
          containsPair('limit', '20'));
    });

    test('a record is added with its idempotency key', () async {
      final fake = VehiclesApi();
      final repo = ApiVehiclesRepository(fake.api.client());
      final record = await repo.addRecord(VehicleRecordKind.fuel, 'veh_1',
          {'filled_at': '2026-11-02T07:30:00Z', 'litres': '30'},
          idempotencyKey: 'key_example');
      expect(record, isA<VehicleFuel>());
      final sent = fake.api.sent('POST', '/api/v1/vehicles/veh_1/fuel').single;
      expect(sent.headers['Idempotency-Key'], 'key_example');
      expect(sent.body!['litres'], '30');
    });

    test('the odometer refusal keeps its code', () async {
      final fake = VehiclesApi()..decreaseOnce = true;
      final repo = ApiVehiclesRepository(fake.api.client());
      await expectLater(
        repo.addRecord(VehicleRecordKind.fuel, 'veh_1', {'litres': '1'},
            idempotencyKey: 'k'),
        throwsA(isA<ApiException>()
            .having((e) => e.code, 'code', odometerDecrease)
            .having((e) => e.statusCode, 'status', 409)),
      );
    });

    test('a correction sends its reason', () async {
      final fake = VehiclesApi();
      final repo = ApiVehiclesRepository(fake.api.client());
      await repo.addReading('veh_1',
          readingKm: '100', correction: true, reason: 'New odometer');
      final body =
          fake.api.sent('POST', '/api/v1/vehicles/veh_1/odometer').single.body!;
      expect(body,
          {'reading_km': '100', 'correction': true, 'reason': 'New odometer'});
    });

    test('linking a payment posts the canonical id', () async {
      final fake = VehiclesApi();
      final repo = ApiVehiclesRepository(fake.api.client());
      final payment = await repo.linkPayment('fuel_entry', 'vfuel_1',
          canonicalId: 'canon_1', idempotencyKey: 'k1');
      expect(payment.amount, '130.05');
      final sent = fake.api
          .sent('POST', '/api/v1/vehicles/costs/fuel_entry/vfuel_1/payment')
          .single;
      expect(jsonEncode(sent.body), '{"canonical_id":"canon_1"}');
      expect(sent.headers['Idempotency-Key'], 'k1');
    });
  });

  group('screen', () {
    testWidgets('list with reminders, paging and a vehicle with its fuel',
        (tester) async {
      final fake = VehiclesApi();
      await pumpVehicles(tester, fake);
      expect(find.text('Family car (DXB A 12345)'), findsWidgets);
      expect(find.textContaining('Insurance in 12 days'), findsWidgets);
      await tapVisible(tester, find.byKey(const Key('records-more')));
      expect(find.text('Family car (AUH 9)'), findsOneWidget);
      expect(find.text('3 of 3'), findsOneWidget);
      expect(find.byKey(const Key('records-more')), findsNothing);
      await tapVisible(tester, find.byKey(const Key('vehicle-veh_1')));
      expect(find.text('45.000 L'), findsOneWidget);
      expect(find.text('No payment linked'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('adds a fuel entry once with one key', (tester) async {
      final fake = VehiclesApi();
      await pumpVehicles(tester, fake);
      await tapVisible(tester, find.byKey(const Key('vehicle-veh_1')));
      await tapVisible(tester, find.byKey(const Key('vehicle-add-fuel')));
      await tester.enterText(find.byKey(const Key('field-litres')), '30,5');
      await tester.enterText(
          find.byKey(const Key('field-odometer_km')), '42100');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      final sent = fake.api.sent('POST', '/api/v1/vehicles/veh_1/fuel').single;
      expect(sent.body!['litres'], '30.5');
      expect(sent.body!['odometer_km'], '42100');
      expect(sent.headers['Idempotency-Key'], startsWith('veh'));
      expect(find.text('30.5 L'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('a new record refreshes the list row and reminders',
        (tester) async {
      final fake = VehiclesApi();
      await pumpVehicles(tester, fake);
      final before = fake.api.sent('GET', '/api/v1/vehicles/reminders').length;
      await tapVisible(tester, find.byKey(const Key('vehicle-veh_1')));
      await tapVisible(tester, find.byKey(const Key('vehicle-add-fuel')));
      await tester.enterText(find.byKey(const Key('field-litres')), '20');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(fake.api.sent('GET', '/api/v1/vehicles/reminders').length,
          greaterThan(before));
    });

    testWidgets('the odometer refusal says why and offers a correction',
        (tester) async {
      final fake = VehiclesApi()..decreaseOnce = true;
      await pumpVehicles(tester, fake);
      await tapVisible(tester, find.byKey(const Key('vehicle-veh_1')));
      await tapVisible(tester, find.byKey(const Key('vehicle-add-fuel')));
      await tester.enterText(find.byKey(const Key('field-litres')), '20');
      await tester.enterText(
          find.byKey(const Key('field-odometer_km')), '41000');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(
          find.textContaining('The odometer cannot go down'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('odometer-correction')));
      // The correction form opens with the typed kilometres and the switch on.
      final field = tester
          .widget<TextFormField>(find.byKey(const Key('field-reading_km')));
      expect(field.controller!.text, '41000');
      await tester.enterText(
          find.byKey(const Key('field-reason')), 'Odometer replaced');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      final body =
          fake.api.sent('POST', '/api/v1/vehicles/veh_1/odometer').single.body!;
      expect(body['correction'], true);
      expect(body['reason'], 'Odometer replaced');
      expectClean(tester);
    });

    testWidgets('links one of the person\'s Finance payments', (tester) async {
      final fake = VehiclesApi();
      await pumpVehicles(tester, fake);
      await tapVisible(tester, find.byKey(const Key('vehicle-veh_1')));
      await tapVisible(tester, find.byKey(const Key('record-menu-vfuel_1')));
      await tester.tap(find.byKey(const Key('menu-link')));
      await tester.pumpAndSettle();
      await tapVisible(tester, find.byKey(const Key('candidate-canon_1')));
      expect(
          fake.api.sent(
              'POST', '/api/v1/vehicles/costs/fuel_entry/vfuel_1/payment'),
          hasLength(1));
      expect(find.textContaining('linked'), findsWidgets);
      expectClean(tester);
    });

    testWidgets('a business vehicle uses a business allocation as its cost',
        (tester) async {
      final fake = VehiclesApi(business: true);
      await pumpVehicles(tester, fake);
      await tapVisible(tester, find.byKey(const Key('vehicle-veh_1')));
      await tapVisible(tester, find.byKey(const Key('record-menu-vfuel_1')));
      await tester.tap(find.byKey(const Key('menu-allocate')));
      await tester.pumpAndSettle();
      expect(find.textContaining('ref a1b2c3'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('candidate-alloc_1')));
      expect(
          fake.api
              .sent('POST',
                  '/api/v1/vehicles/costs/fuel_entry/vfuel_1/allocation')
              .single
              .body,
          {'allocation_id': 'alloc_1'});
      expect(find.textContaining('business allocation'), findsWidgets);
      expectClean(tester);
    });

    testWidgets('a fitting business allocation is proposed and confirmed',
        (tester) async {
      final fake = VehiclesApi(business: true, proposal: true);
      await pumpVehicles(tester, fake);
      await tapVisible(tester, find.byKey(const Key('vehicle-veh_1')));
      expect(find.byKey(const Key('alloc-proposal-vfuel_1')), findsOneWidget);
      expect(find.textContaining('ref a1b2c3'), findsOneWidget);
      await tapVisible(
          tester, find.byKey(const Key('alloc-proposal-use-vfuel_1')));
      expect(
          fake.api
              .sent('POST',
                  '/api/v1/vehicles/costs/fuel_entry/vfuel_1/allocation')
              .single
              .body,
          {'allocation_id': 'alloc_1'});
      expect(find.byKey(const Key('alloc-proposal-vfuel_1')), findsNothing);
      expectClean(tester);
    });

    testWidgets('a personal vehicle offers no business allocation',
        (tester) async {
      final fake = VehiclesApi();
      await pumpVehicles(tester, fake);
      await tapVisible(tester, find.byKey(const Key('vehicle-veh_1')));
      await tapVisible(tester, find.byKey(const Key('record-menu-vfuel_1')));
      expect(find.byKey(const Key('menu-allocate')), findsNothing);
    });

    testWidgets('empty list offers to add a vehicle', (tester) async {
      await pumpVehicles(tester, VehiclesApi(empty: true));
      expect(find.byKey(const Key('vehicles-empty')), findsOneWidget);
      expect(find.text('Add vehicle'), findsWidgets);
      expectClean(tester);
    });

    testWidgets('a failed load says what failed and retries', (tester) async {
      final fake = VehiclesApi();
      fake.api.failWith = 500;
      fake.api.failMessage = 'The vehicles service is unavailable.';
      await pumpVehicles(tester, fake);
      expect(find.byKey(const Key('records-error')), findsOneWidget);
      expect(find.textContaining('The vehicles service is unavailable.'),
          findsOneWidget);
      fake.api.failWith = null;
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Family car (DXB A 12345)'), findsWidgets);
    });

    testWidgets('a viewer is told who can see vehicles', (tester) async {
      final fake = VehiclesApi();
      fake.api.failWith = 403;
      fake.api.failMessage =
          'Vehicles are for workspace owners and members. Ask a workspace owner to change your role from viewer to member.';
      await pumpVehicles(tester, fake);
      expect(find.byKey(const Key('records-denied')), findsOneWidget);
      expect(find.textContaining('workspace owners and members'), findsWidgets);
      expect(find.byKey(const Key('vehicles-add')), findsNothing);
    });

    testWidgets('Arabic, 320 px, 200 percent text: no overflow',
        (tester) async {
      final fake = VehiclesApi();
      await pumpVehicles(tester, fake,
          size: narrow, textScale: 2, locale: const Locale('ar'));
      expect(find.text('المركبات'), findsWidgets);
      expect(Directionality.of(tester.element(find.byType(VehiclesScreen))),
          TextDirection.rtl);
      await tapVisible(tester, find.byKey(const Key('vehicle-veh_1')));
      expect(find.text('الوقود'), findsWidgets);
      expectClean(tester);
    });

    testWidgets('tablet shows the list and the vehicle side by side',
        (tester) async {
      final fake = VehiclesApi();
      await pumpVehicles(tester, fake, size: tablet);
      await tester.tap(find.byKey(const Key('vehicle-veh_1')));
      await tester.pumpAndSettle();
      expect(find.byType(VehicleDetailHost), findsOneWidget);
      expect(find.byType(VehicleDetailScreen), findsNothing);
      expect(find.text('45.000 L'), findsOneWidget);
      expectClean(tester);
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
    });
  });
}
