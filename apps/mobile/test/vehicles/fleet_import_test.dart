// A client fleet imported on the phone (docs/vehicles.md#fleet-import)
// against a fake API with the API's JSON shapes: the file read and previewed
// with the kind, country and date order, a column chosen and checked again,
// the rows with their problems in the reader's language, a row's action
// changed, Import after a confirmation with the fingerprint and only the
// changed decisions, the result, a refused import in the API's words, a
// file of the wrong type, a viewer, the template, and Arabic at 320 px and
// 200% text. Fixtures are plainly fake.
import 'dart:convert';
import 'dart:typed_data';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/vehicles/fleet_import.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/fake_export_saver.dart';
import '../records/records_harness.dart';

const csv = 'Plate,Make\nA 1001,Example\nA 1002,Example\n';

Map<String, Object?> previewJson({String? plateColumn = 'Plate'}) => {
      'kind': 'vehicles',
      'columns': ['Plate', 'Make'],
      'fields': [
        {'field': 'registration', 'required': true},
        {'field': 'make', 'required': false},
      ],
      'mapping': {
        if (plateColumn != null) 'registration': plateColumn,
        'make': 'Make'
      },
      'suggested': true,
      'rows': plateColumn == null
          ? <Object?>[]
          : [
              {
                'row': 2,
                'registration': 'A 1001',
                'summary': {'make': 'Example'},
                'status': 'new',
                'action': 'create',
                'actions': ['create', 'skip'],
                'vehicle_id': null,
                'duplicate_of': null,
                'issues': <Object?>[],
              },
              {
                'row': 3,
                'registration': 'A 1002',
                'summary': {'make': 'Example'},
                'status': 'invalid',
                'action': 'skip',
                'actions': ['skip'],
                'vehicle_id': null,
                'duplicate_of': null,
                'issues': [
                  {
                    'code': 'vin',
                    'field': 'vin',
                    'value': 'X',
                    'text': 'VIN is not valid.'
                  }
                ],
              },
            ],
      'counts': {
        'rows': plateColumn == null ? 0 : 2,
        'new': plateColumn == null ? 0 : 1,
        'exists': 0,
        'duplicate_in_file': 0,
        'imported': 0,
        'invalid': plateColumn == null ? 0 : 1,
      },
      'fingerprint': 'fp_example',
    };

class FleetFake {
  bool viewer = false, refuse = false, noMapping = false;
  late final FakeApi api = FakeApi(route);

  ApiResponse? route(ApiRequest r) {
    if (viewer) {
      return refuseResponse(
          403, 'Only workspace owners can import a fleet.', 'forbidden');
    }
    switch ((r.method, r.uri.path)) {
      case ('POST', '/api/v1/vehicles/fleet/import/preview'):
        final chosen = r.body!['mapping'] as Map?;
        return ok(previewJson(
            plateColumn: noMapping && chosen == null
                ? null
                : (chosen?['registration'] as String? ?? 'Plate')));
      case ('POST', '/api/v1/vehicles/fleet/import/apply'):
        if (refuse) {
          return refuseResponse(
              409,
              'The file changed since the preview. Check it again.',
              'stale_preview');
        }
        return ok({
          'kind': 'vehicles',
          'rows': 2,
          'created': 0,
          'updated': 0,
          'skipped': 2,
          'readings': 0
        });
    }
    return null;
  }
}

ApiResponse refuseResponse(int status, String detail, String code) =>
    refuse(status, detail, code);

Future<FleetFake> open(WidgetTester tester,
    {FleetFake? fake,
    String name = 'fleet.csv',
    String content = csv,
    FakeExportSaver? saver,
    Size size = const Size(800, 3000),
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  final f = fake ?? FleetFake();
  await pumpModule(
      tester,
      FleetImportScreen(
          api: FleetImportApi(f.api.client()),
          saver: saver ?? FakeExportSaver(),
          picker: () async =>
              (name: name, bytes: Uint8List.fromList(utf8.encode(content)))),
      size: size,
      textScale: textScale,
      locale: locale);
  return f;
}

Future<void> tap(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void main() {
  test('the strings match in English and Arabic and have no long dashes', () {
    expect(FleetImportStrings.catalogsMatch, isTrue);
    for (final text in FleetImportStrings.allTexts) {
      expect(text.contains('–') || text.contains('—'), isFalse, reason: text);
    }
  });

  test('a file is read by its type and size', () {
    expect(
        readFleetFile('a.csv', Uint8List.fromList(utf8.encode('x\n'))).body(),
        {'format': 'csv', 'csv': 'x\n', 'file_name': 'a.csv'});
    expect(
        readFleetFile('a.xlsx', Uint8List.fromList([1, 2, 3])).body()['format'],
        'xlsx');
    expect(() => readFleetFile('a.pdf', Uint8List.fromList([1])),
        throwsA(isA<FormatException>()));
    expect(() => readFleetFile('a.csv', Uint8List(0)),
        throwsA(isA<FormatException>()));
  });

  testWidgets(
      'preview, a row action changed, Import with the fingerprint and the result',
      (tester) async {
    final fake = await open(tester);
    await tap(tester, find.byKey(const Key('fleet-import-choose')));
    final sent =
        fake.api.sent('POST', '/api/v1/vehicles/fleet/import/preview').single;
    expect(sent.body, {
      'kind': 'vehicles',
      'format': 'csv',
      'csv': csv,
      'file_name': 'fleet.csv',
      'mapping': null,
      'country': 'AE',
      'date_order': 'dmy',
    });
    expect(find.text('File: fleet.csv'), findsOneWidget);
    expect(find.text('What the file would do: 2 rows'), findsOneWidget);
    expect(find.text('VIN: 11 to 17 letters and digits, without I, O or Q.'),
        findsOneWidget);
    expect(find.text('1 to add, 0 to update, 1 to skip'), findsOneWidget);
    // The only addable row is skipped: nothing to import.
    await tap(tester, find.byKey(const Key('fleet-action-2')));
    await tester.tap(find.text('Skip').last);
    await tester.pumpAndSettle();
    expect(
        find.text('Nothing to import: every row is skipped.'), findsOneWidget);
    expect(
        tester
            .widget<FilledButton>(find.byKey(const Key('fleet-import-apply')))
            .onPressed,
        isNull);
    // Filtered to the rows with a problem.
    await tap(tester, find.byKey(const Key('fleet-filter-invalid')));
    expect(find.byKey(const Key('fleet-row-2')), findsNothing);
    expect(find.byKey(const Key('fleet-row-3')), findsOneWidget);
    await tap(tester, find.byKey(const Key('fleet-filter-all')));
    await tap(tester, find.byKey(const Key('fleet-action-2')));
    await tester.tap(find.text('Add').last);
    await tester.pumpAndSettle();
    await tap(tester, find.byKey(const Key('fleet-import-apply')));
    expect(find.text('Import this file?'), findsOneWidget);
    await tap(tester, find.byKey(const Key('records-confirm')));
    final applied =
        fake.api.sent('POST', '/api/v1/vehicles/fleet/import/apply').single;
    expect(applied.body!['fingerprint'], 'fp_example');
    expect(applied.body!['mapping'], {'registration': 'Plate', 'make': 'Make'});
    expect(applied.body!['decisions'], isEmpty);
    expect(find.byKey(const Key('fleet-import-done')), findsOneWidget);
    expect(find.byKey(const Key('fleet-import-choose')), findsOneWidget);
  });

  testWidgets('a column is chosen and the file checked again', (tester) async {
    final fake = FleetFake()..noMapping = true;
    await open(tester, fake: fake);
    await tap(tester, find.byKey(const Key('fleet-import-choose')));
    expect(find.textContaining('could not tell which column'), findsOneWidget);
    await tap(tester, find.byKey(const Key('fleet-map-registration-')));
    await tester.tap(find.text('Plate').last);
    await tester.pumpAndSettle();
    await tap(tester, find.byKey(const Key('fleet-import-check')));
    expect(
        fake.api
            .sent('POST', '/api/v1/vehicles/fleet/import/preview')
            .last
            .body!['mapping'],
        {'registration': 'Plate', 'make': 'Make'});
    expect(find.text('What the file would do: 2 rows'), findsOneWidget);
  });

  testWidgets('a refused import says so in the API words', (tester) async {
    final fake = FleetFake()..refuse = true;
    await open(tester, fake: fake);
    await tap(tester, find.byKey(const Key('fleet-import-choose')));
    await tap(tester, find.byKey(const Key('fleet-import-apply')));
    await tap(tester, find.byKey(const Key('records-confirm')));
    expect(
        find.text(
            'Nothing was imported: The file changed since the preview. Check it again.'),
        findsOneWidget);
    expect(find.byKey(const Key('fleet-import-apply')), findsOneWidget);
  });

  testWidgets('a file of the wrong type is refused before sending',
      (tester) async {
    final fake = await open(tester, name: 'fleet.pdf');
    await tap(tester, find.byKey(const Key('fleet-import-choose')));
    expect(find.text('Choose an Excel (.xlsx) or CSV file.'), findsOneWidget);
    expect(fake.api.requests, isEmpty);
  });

  testWidgets('a viewer is told who may import', (tester) async {
    await open(tester, fake: FleetFake()..viewer = true);
    await tap(tester, find.byKey(const Key('fleet-import-choose')));
    expect(find.byKey(const Key('fleet-import-denied')), findsOneWidget);
    expect(find.textContaining('workspace owners'), findsWidgets);
  });

  testWidgets('the template is saved through the save dialog', (tester) async {
    final saver = FakeExportSaver();
    await open(tester, saver: saver);
    await tap(tester, find.byKey(const Key('fleet-import-template')));
    expect(saver.saved['anum-fleet-vehicles-template.csv'],
        startsWith('Registration,Country,Fleet number'));
  });

  testWidgets('Arabic at 320 px and 200% text lays out without overflow',
      (tester) async {
    await open(tester,
        size: const Size(320, 8000), textScale: 2, locale: const Locale('ar'));
    await tap(tester, find.byKey(const Key('fleet-import-choose')));
    expect(tester.takeException(), isNull);
    expect(find.text('استيراد أسطول عميل'), findsOneWidget);
    expect(find.text('ما سيفعله الملف: 2 صف'), findsOneWidget);
  });
}
