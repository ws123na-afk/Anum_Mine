// The shared record-module plumbing: decimals typed in either language,
// day and time to an instant, query strings, offset and cursor pages,
// refusals with their module codes, and the paged list's states.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/attendance/attendance_screen.dart';
import 'package:anum_mobile/features/books/books_screen.dart';
import 'package:anum_mobile/features/purchases/purchases_screen.dart';
import 'package:anum_mobile/features/records_kit/record_module_routes.dart';
import 'package:anum_mobile/features/records_kit/records_form.dart';
import 'package:anum_mobile/features/records_kit/records_kit.dart';
import 'package:anum_mobile/features/records_kit/records_widgets.dart';
import 'package:anum_mobile/features/vault/vault_screen.dart';
import 'package:anum_mobile/features/vehicles/vehicles_screen.dart';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'records_harness.dart';

void main() {
  test('kit catalogs match', () => expect(KitStrings.catalogsMatch, isTrue));

  test('decimals typed with Arabic digits or a comma', () {
    expect(decimalText('12.5'), '12.5');
    expect(decimalText(' 30,5 '), '30.5');
    expect(decimalText('١٢٫٥'), '12.5');
    expect(decimalText('-3'), isNull);
    expect(decimalText('12a'), isNull);
    expect(decimalText(''), isNull);
  });

  test('a day and a time become a UTC instant; today without a time is now',
      () {
    final now = DateTime(2026, 11, 5, 9, 30);
    expect(
        instantOf('2026-11-05', null, now: now), now.toUtc().toIso8601String());
    expect(instantOf('2026-11-01', null, now: now),
        DateTime(2026, 11, 1, 12).toUtc().toIso8601String());
    expect(instantOf('2026-11-01', '08:15', now: now),
        DateTime(2026, 11, 1, 8, 15).toUtc().toIso8601String());
    expect(parseTime('24:00'), isNull);
    expect(isoDay(DateTime(2026, 1, 2)), '2026-01-02');
  });

  test('query strings leave out empty values and encode the rest', () {
    expect(recordsQuery({'q': '', 'status': null}), '');
    expect(recordsQuery({'q': 'a b', 'offset': 0}), '?q=a+b&offset=0');
  });

  test('offset pages know their next offset, even when not echoed', () {
    final page = JsonReader({
      'items': [
        {'id': 'a'},
        {'id': 'b'}
      ],
      'total': 5,
    });
    final first = RecordsChunk.offsetPage(page, (j) => j.string('id'));
    expect(first.next, '2');
    final later =
        RecordsChunk.offsetPage(page, (j) => j.string('id'), offset: 3);
    expect(later.next, isNull);
  });

  test('a module refusal keeps its top-level code', () {
    final error = ApiException.fromResponse(const ApiResponse(
        statusCode: 409,
        body: {
          'detail': 'The odometer cannot go down.',
          'code': 'odometer_decrease'
        }));
    expect(error.code, 'odometer_decrease');
    expect(error.message, 'The odometer cannot go down.');
    final envelope =
        ApiException.fromResponse(const ApiResponse(statusCode: 403, body: {
      'error': {'code': 'forbidden', 'message': 'Owners only.'}
    }));
    expect(envelope.code, 'forbidden');
    final failure = RecordsFailure.from(envelope);
    expect(failure.denied, isTrue);
    expect(RecordsFailure.from(const ApiException(0, 'No network')).offline,
        isTrue);
  });

  test('a paged list loads, pages, keeps a failed "more" and resets', () async {
    var fail = false;
    final list = RecordsList<int>((query, filter, token) async {
      if (fail) throw const ApiException(503, 'Busy');
      final start = int.parse(token ?? '0');
      return RecordsChunk([start, start + 1],
          next: start == 0 ? '2' : null, total: 4);
    });
    await list.load(query: ' x ');
    expect(list.query, 'x');
    expect(list.items, [0, 1]);
    expect(list.narrowed, isTrue);
    fail = true;
    await list.more();
    expect(list.moreError, 'Busy');
    expect(list.items, [0, 1]);
    fail = false;
    await list.more();
    expect(list.items, [0, 1, 2, 3]);
    expect(list.hasMore, isFalse);
    list.upsert(9, (i) => i == 9);
    expect(list.items.first, 9);
    expect(list.total, 5);
    list.remove((i) => i == 9);
    expect(list.total, 4);
    list.dispose();
  });

  test('a 403 makes the list denied', () async {
    final list = RecordsList<int>((_, __, ___) async =>
        throw const ApiException(403, 'Ask a workspace owner.'));
    await list.load();
    expect(list.phase, RecordsPhase.denied);
    expect(list.failure!.message, 'Ask a workspace owner.');
  });

  test('idempotency keys are fresh and fit the API (8 to 128 characters)', () {
    final a = newIdempotencyKey('att'), b = newIdempotencyKey('att');
    expect(a, isNot(b));
    expect(a.length, inInclusiveRange(8, 128));
    expect(RegExp(r'^[A-Za-z0-9_.:-]+$').hasMatch(a), isTrue);
  });

  testWidgets('each module route opens its screen behind the guard',
      (tester) async {
    final api = FakeApi((_) => null)..failWith = 403;
    final routes = RecordModuleRoutes(api: api.client());
    final guarded = <String>[];
    await pumpModule(
        tester,
        Builder(
            builder: (context) => Navigator(
                  onGenerateRoute: (settings) =>
                      routes.route(settings, (child) {
                        guarded.add(settings.name!);
                        return child;
                      }) ??
                      MaterialPageRoute<void>(
                          builder: (_) => const SizedBox.shrink()),
                )));
    final navigator = tester.state<NavigatorState>(find.byType(Navigator).last);
    final screens = <String, Type>{
      RecordModuleRoutes.vault: VaultScreen,
      RecordModuleRoutes.attendance: AttendanceScreen,
      RecordModuleRoutes.vehicles: VehiclesScreen,
      RecordModuleRoutes.purchases: PurchasesScreen,
      RecordModuleRoutes.books: BooksScreen,
    };
    for (final entry in screens.entries) {
      navigator.pushNamed(entry.key);
      await tester.pumpAndSettle();
      expect(find.byType(entry.value), findsOneWidget, reason: entry.key);
      // A viewer sees who may open it, not a broken page.
      expect(find.byKey(const Key('records-denied')), findsOneWidget,
          reason: entry.key);
      navigator.pop();
      await tester.pumpAndSettle();
    }
    expect(guarded, RecordModuleRoutes.all);
    expect(routes.route(const RouteSettings(name: '/elsewhere'), (c) => c),
        isNull);
  });

  test('the app wires the routes and the Menu lists every module', () {
    final app = File('lib/src/anum_app.dart').readAsStringSync();
    expect(app, contains('records.route('));
    expect(app, contains('RecordModuleRoutes('));
    final shell =
        File('lib/features/workspace/workspace_home.dart').readAsStringSync();
    for (final name in [
      'vault',
      'attendance',
      'vehicles',
      'purchases',
      'books'
    ]) {
      expect(shell, contains('RecordModuleRoutes.$name'), reason: name);
    }
  });
}
