// What a statement import did with each row, on the phone as on the web
// (docs/finance.md#import-history): filters with the stored counts, the
// reason per row, and Undo the match on a row that stood for several
// recorded payments. Every fixture is plainly fake.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/finance/finance_import_rows.dart';
import 'package:anum_mobile/features/finance/finance_more_data.dart';
import 'package:anum_mobile/features/finance/finance_tools_data.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

const item = StatementImport(
    id: 'imp_1',
    accountId: 'acc_1',
    filename: 'example.csv',
    createdAt: '2026-11-14T08:00:00Z',
    rowCount: 4,
    importedCount: 2,
    duplicateCount: 0,
    errorCount: 0,
    reconciled: 1,
    heldForReview: 1,
    notInLedger: 1,
    check: 'tied');

class Api {
  Api() {
    fake = FakeApi(route);
  }
  late final FakeApi fake;
  bool undone = false;

  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path.replaceFirst('/api/v1/finance', '');
    if (r.method == 'GET' && path == '/imports/imp_1') {
      final d = r.uri.queryParameters['disposition'];
      final rows = [
        {
          'line': 2,
          'posted_on': '2026-11-02',
          'amount': '-250.00',
          'description': 'EXAMPLE TAMARA',
          'disposition': undone ? 'held_for_review' : 'attached_to_existing',
          'reason': undone ? 'split_undone' : 'split_match',
          'message': null,
          'record_id': null,
          'split_group': undone ? null : 'grp_1',
          'split_records': undone ? null : ['txn_a', 'txn_b'],
        },
        {
          'line': 3,
          'posted_on': '2026-11-03',
          'amount': '-12.00',
          'description': 'EXAMPLE SHOP',
          'disposition': 'held_for_review',
          'reason': 'two_candidates',
          'message': null,
          'record_id': null,
        },
      ];
      final shown = d == null
          ? rows
          : d == 'not_in_ledger'
              ? rows.where((x) => x['disposition'] == 'held_for_review')
              : rows.where((x) => x['disposition'] == d);
      return ok({
        'id': 'imp_1',
        'rows': shown.toList(),
        'rows_total': shown.length,
        'limit': 100,
        'offset': 0,
      });
    }
    if (r.method == 'POST' && path == '/imports/imp_1/splits/grp_1/undo') {
      undone = true;
      return ok({'id': 'imp_1'});
    }
    return null;
  }
}

void main() {
  test('the catalogs have the same keys and no long dashes', () {
    expect(FinanceImportRowsStrings.catalogsMatch, isTrue);
    for (final text in FinanceImportRowsStrings.allTexts) {
      expect(text.contains('—') || text.contains('–'), isFalse, reason: text);
    }
  });

  test('filters keep all and not in the ledger, and drop empty groups', () {
    expect([
      for (final (id, _) in importRowFilters(item)) id
    ], [
      'all',
      'not_in_ledger',
      'held_for_review',
      'attached_to_existing',
      'imported'
    ]);
  });

  testWidgets('opens on the rows not in the ledger, filters and unmatches',
      (tester) async {
    final api = Api();
    await pumpModule(
        tester,
        ImportRowsScreen(
            repository: FinanceMoreRepository(api.fake.client()),
            item: item,
            currency: 'SAR'));
    await tester.pumpAndSettle();
    expect(api.fake.requests.first.uri.queryParameters['disposition'],
        'not_in_ledger');
    expect(find.byKey(const Key('rows-tied-missing')), findsOneWidget);
    expect(find.textContaining('more than one recorded payment fits'),
        findsOneWidget);
    await tester.tap(find.byKey(const Key('rows-filter')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('All rows').last);
    await tester.pumpAndSettle();
    expect(find.text('Attached to 2 recorded payments'), findsOneWidget);
    await tester.tap(find.byKey(const Key('rows-undo-grp_1')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('records-confirm')));
    await tester.pumpAndSettle();
    expect(api.undone, isTrue);
    expect(find.text('Undone. The row waits in Review.'), findsOneWidget);
    expect(find.byKey(const Key('rows-undo-grp_1')), findsNothing);
  });

  testWidgets('Arabic, right to left, and a failed load retries',
      (tester) async {
    final api = Api()..fake.failWith = 500;
    await pumpModule(
        tester,
        ImportRowsScreen(
            repository: FinanceMoreRepository(api.fake.client()),
            item: item,
            currency: 'SAR'),
        locale: const Locale('ar'),
        size: narrow);
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.textContaining('تعذر تحميل الصفوف'), findsOneWidget);
    api.fake.failWith = null;
    await tester.ensureVisible(find.byIcon(Icons.refresh).first);
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.refresh).first);
    await tester.pumpAndSettle();
    expect(find.textContaining('أكثر من دفعة مسجلة تناسبه'), findsOneWidget);
  });
}
