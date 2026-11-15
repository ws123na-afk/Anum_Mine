// Fleet costs and the monthly client report on the phone
// (docs/vehicles.md#fleet) against a fake API with the API's JSON shapes:
// the month's figures, the report saved through the system save dialog with
// the company, language and notes, a missing company, a cancelled save, the
// empty fleet, a failed load and Arabic at a narrow width.
import 'dart:typed_data';

import 'package:anum_mobile/data/api_downloads.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/vehicles/fleet_costs.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

Map<String, Object?> summary({bool empty = false}) => {
      'month': '2026-10',
      'vehicles': empty ? 0 : 1,
      'distance_km': empty ? '0.0' : '1050.0',
      'totals': empty
          ? []
          : [
              {
                'month': '2026-10',
                'currency': 'AED',
                'fuel': '900.00',
                'service': '450.00',
                'tyres': '2400.00',
                'other': '50.00',
                'total': '3800.00',
                'distance_km': '1000.0',
                'cost_per_km': '3.800',
                'previous_total': '600.00',
                'linked': '0.00',
                'recorded': '3800.00',
              }
            ],
      'rows': empty
          ? []
          : [
              {
                'vehicle_id': 'veh_1',
                'registration': 'A 1001',
                'nickname': 'T-01',
                'distance_km': '1000.0',
                'costs': [
                  {
                    'currency': 'AED',
                    'total': '3800.00',
                    'cost_per_km': '3.800',
                    'previous_total': '600.00',
                  }
                ],
              }
            ],
      'worst': empty
          ? []
          : [
              {
                'vehicle_id': 'veh_1',
                'registration': 'A 1001',
                'nickname': 'T-01',
                'currency': 'AED',
                'cost_per_km': '3.800',
                'total': '3800.00',
                'distance_km': '1000.0',
              }
            ],
      'renewals': empty
          ? []
          : [
              {
                'vehicle_id': 'veh_1',
                'registration': 'A 1001',
                'kind': 'insurance',
                'due_on': '2026-11-20',
                'days_left': 8,
              }
            ],
      'tyres': {
        'status': 'not_connected',
        'matched': 0,
        'vehicles': <Object?>[]
      },
    };

class _Downloads implements ApiDownloads {
  final bodies = <JsonMap?>[];
  final names = <String>[];
  bool saved = true;

  @override
  Future<Uint8List> fetch(String path, {int maxBytes = 0}) async =>
      throw UnimplementedError();

  @override
  Future<bool> save(String method, String path,
      {JsonMap? body,
      required String fileName,
      required String dialogTitle}) async {
    expect('$method $path', 'POST /api/v1/vehicles/fleet/report');
    bodies.add(body);
    names.add(fileName);
    return saved;
  }
}

Future<(FakeApi, _Downloads)> pumpFleet(WidgetTester tester,
    {bool empty = false,
    Size size = phone,
    Locale locale = const Locale('en')}) async {
  final api = FakeApi((request) =>
      request.uri.path == '/api/v1/vehicles/fleet/costs'
          ? ok(summary(empty: empty))
          : null);
  final downloads = _Downloads();
  final client = api.client()..downloads = downloads;
  await pumpModule(tester,
      FleetCostsScreen(fleet: FleetApi(client), now: DateTime(2026, 10, 15)),
      size: size, locale: locale);
  return (api, downloads);
}

void main() {
  test('months move by one and never past this month', () {
    expect(shiftMonth('2026-01', -1), '2025-12');
    expect(shiftMonth('2026-12', 1), '2027-01');
    expect(monthOf(DateTime(2026, 3, 9)), '2026-03');
  });

  testWidgets('the month figures, then the report saved as an Arabic PDF',
      (tester) async {
    final (api, downloads) = await pumpFleet(tester);
    expect(api.requests.first.uri.queryParameters['month'], '2026-10');
    expect(find.byKey(const Key('fleet-total-AED')), findsOneWidget);
    expect(find.textContaining('A 1001 · T-01'), findsOneWidget);
    expect(find.textContaining('Insurance · A 1001'), findsOneWidget);
    expect(
        tester
            .widget<IconButton>(find.byKey(const Key('fleet-next')))
            .onPressed,
        isNull);

    await tester.ensureVisible(find.byKey(const Key('fleet-pdf')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('fleet-pdf')));
    await tester.pumpAndSettle();
    expect(
        find.text('Type the company name the report is for.'), findsOneWidget);
    expect(downloads.bodies, isEmpty);

    await tester.enterText(
        find.byKey(const Key('fleet-company')), 'Gulf Mixers LLC');
    await tester.ensureVisible(find.text('Arabic'));
    await tester.tap(find.text('Arabic'));
    await tester.enterText(find.byKey(const Key('fleet-notes')), 'Two idle.');
    await tester.ensureVisible(find.byKey(const Key('fleet-pdf')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('fleet-pdf')));
    await tester.pumpAndSettle();
    expect(downloads.bodies.single, {
      'month': '2026-10',
      'language': 'ar',
      'format': 'pdf',
      'company_name': 'Gulf Mixers LLC',
      'notes': 'Two idle.',
    });
    expect(downloads.names.single, 'fleet-report-2026-10-ar.pdf');
    expect(find.text('Report saved.'), findsOneWidget);

    downloads.saved = false;
    await tester.ensureVisible(find.byKey(const Key('fleet-xlsx')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('fleet-xlsx')));
    await tester.pumpAndSettle();
    expect(downloads.names.last, 'fleet-report-2026-10-ar.xlsx');
    expect(find.text('Nothing was saved.'), findsOneWidget);
  });

  testWidgets('the previous month is asked for', (tester) async {
    final (api, _) = await pumpFleet(tester);
    await tester.tap(find.byKey(const Key('fleet-previous')));
    await tester.pumpAndSettle();
    expect(api.requests.last.uri.queryParameters['month'], '2026-09');
  });

  testWidgets('an empty fleet points to the import', (tester) async {
    await pumpFleet(tester, empty: true);
    expect(find.byKey(const Key('fleet-empty')), findsOneWidget);
  });

  testWidgets('a failed load says so and retries', (tester) async {
    final api = FakeApi((request) => null)
      ..failWith = 503
      ..failMessage = 'The database is not answering.';
    await pumpModule(
        tester,
        FleetCostsScreen(
            fleet: FleetApi(api.client()), now: DateTime(2026, 10, 15)));
    expect(find.byKey(const Key('records-error')), findsOneWidget);
    api
      ..failWith = null
      ..route = (request) => ok(summary());
    await tester.tap(find.text('Try again').first);
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('fleet-total-AED')), findsOneWidget);
  });

  testWidgets('Arabic at 320 px has no overflow', (tester) async {
    await pumpFleet(tester, size: narrow, locale: const Locale('ar'));
    expect(find.text('تكاليف الأسطول'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
