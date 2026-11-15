// The Personal Finance dashboard, account freshness, the transaction register
// and a transaction's evidence (docs/finance.md#dashboard,
// docs/finance.md#transaction-detail). The summary fixture is shared with the
// web (apps/web/test/financeDashboard.test.ts parses the same file and
// expects the same totals), so the phone and the web show the same figures.
// Every fixture is plainly fake.
import 'dart:convert';
import 'dart:io';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/finance/finance_core_models.dart';
import 'package:anum_mobile/features/finance/finance_core_repository.dart';
import 'package:anum_mobile/features/finance/finance_core_strings.dart';
import 'package:anum_mobile/features/finance/finance_core_view.dart';
import 'package:anum_mobile/features/finance/finance_models.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/fake_export_saver.dart';
import '../records/records_harness.dart';

Map<String, Object?> fixtureJson() =>
    jsonDecode(File('../../packages/contracts/fixtures/finance-summary.json')
        .readAsStringSync()) as Map<String, Object?>;

FinanceSummary fixture() =>
    FinanceSummary.fromJson(JsonReader.of(fixtureJson()));

Map<String, Object?> rowJson(int i,
        {String status = 'recorded', String origin = 'statement'}) =>
    {
      'id': 'txn_$i',
      'account_id': 'acc_current',
      'posted_on': '2026-11-${(15 - i % 10).toString().padLeft(2, '0')}',
      'amount': i.isEven ? '-${10 + i}.00' : '${100 + i}.00',
      'currency': 'SAR',
      'description': 'EXAMPLE MERCHANT $i',
      'category': 'groceries',
      'category_source': 'rule',
      'notes': null,
      'transaction_type': 'expense',
      'canonical_id': 'je_$i',
      'origin': origin,
      'sources': [origin],
      'status': status,
      'reconciled': origin == 'statement',
      'receipts': 0,
      'links_need_review': status == 'needs_review' ? 1 : 0,
    };

class CoreApi {
  CoreApi() {
    fake = FakeApi(route);
  }
  late final FakeApi fake;
  int total = 30;
  String? note;

  FinanceCoreRepository repo() => FinanceCoreRepository(fake.client());

  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path.replaceFirst('/api/v1/finance', '');
    if (r.method == 'GET' && path == '/register') {
      final q = r.uri.queryParameters;
      final offset = int.parse(q['offset'] ?? '0');
      final limit = int.parse(q['limit'] ?? '25');
      final review = q['status'] == 'needs_review';
      final count = review ? 1 : total;
      final one = q['account_id'] != null;
      return ApiResponse(statusCode: 200, body: {
        'items': [
          for (var i = offset; i < count && i < offset + limit; i++)
            {
              ...rowJson(i, status: review ? 'needs_review' : 'recorded'),
              'running_balance': one ? '${900 - i}.00' : null,
            }
        ],
        'total': count,
        'balances': [
          {'currency': 'SAR', 'beginning': '1000.00', 'ending': '1250.50'}
        ],
      });
    }
    if (r.method == 'GET' && path == '/transactions/export.csv') {
      return ok({'csv': 'date,account,description\n2026-11-15,Example,X\n'});
    }
    if (r.method == 'GET' && path == '/register/txn_0') {
      return ApiResponse(statusCode: 200, body: {
        'row': {...rowJson(0, origin: 'manual'), 'notes': note},
        'account_name': 'Example Current',
        'category_by': 'owner',
        'suggested_category': 'groceries',
        'sources': [],
        'receipt_document_ids': [],
        'duplicate': null,
        'links': [
          {
            'id': 'lnk_1',
            'dimension': 'bill',
            'role': 'payment',
            'target_type': 'finance_bill_due',
            'target_id': 'bill_1:2026-11-20',
            'target_name': 'Example Internet',
            'amount': '10.00',
            'currency': 'SAR',
            'status': 'active',
            'evidence': 'provisional',
            'reason': null,
            'created_at': '2026-11-15T08:00:00Z',
          }
        ],
        'related': [
          {
            'id': 'txn_9',
            'relation': 'refunded_by',
            'posted_on': '2026-11-14',
            'amount': '10.00',
            'currency': 'SAR',
            'description': 'EXAMPLE REFUND',
          }
        ],
        'split_count': 0,
      });
    }
    if (r.method == 'PATCH' && path == '/transactions/txn_0') {
      note = (r.body as Map?)?['notes'] as String?;
      return const ApiResponse(statusCode: 200, body: {});
    }
    return null;
  }
}

Future<void> show(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
}

void main() {
  group('the summary the web and the phone share', () {
    test('every SAR figure is the server\'s, the same as the web reads', () {
      final t = dashboardTotals(fixture(), 'SAR');
      // The same numbers are written in apps/web/test/financeDashboard.test.ts.
      expect(t.balance, '7429.75');
      expect(t.cash, '8750.25');
      expect(t.cardOwed, '1320.50');
      expect(t.cardAvailable, '13679.50');
      expect(t.income, '15250.00');
      expect(t.spend, '1130.50');
      expect(t.net, '14176.90');
      expect(t.paymentsDue, '2750.00');
      expect(t.upcomingCount, 3);
      expect(t.reviewTotal, 4);
      expect(t.staleAccounts, 1);
      expect(t.salaryReceived, '15000.00');
      expect(t.excluded, {
        'own_transfer': ('2000.00', '2000.00', 2),
        'card_payment': ('1500.00', '1500.00', 2),
        'loan_principal': ('0.00', '1800.00', 1),
      });
    });

    test('each currency stays on its own and nothing is converted', () {
      final t = dashboardTotals(fixture(), 'USD');
      expect(t.balance, '1200.00');
      expect(t.spend, '15.99');
      expect(t.cardAvailable, isNull);
      expect(t.paymentsDue, '0.00');
      expect(t.salaryReceived, isNull);
      expect(t.excluded, isEmpty);
    });

    test('an older answer without the dashboard reads as none', () {
      final older = Map<String, Object?>.of(fixtureJson())
        ..removeWhere((k, _) => const {
              'positions',
              'excluded',
              'upcoming_payments',
              'freshness',
              'review',
              'salary'
            }.contains(k));
      final s = FinanceSummary.fromJson(JsonReader.of(older));
      expect(s.dashboard.hasDashboard, isFalse);
      expect(dashboardTotals(s, 'SAR').cash, '0.00');
    });

    test('freshness says verified or estimated, the captures and staleness',
        () {
      const s = FinanceCoreStrings(false);
      final d = fixture().dashboard;
      expect(freshnessText(s, d.freshnessFor('acc_current'), 'SAR'),
          contains('Balance confirmed by the bank 2026-11-14'));
      expect(freshnessText(s, d.freshnessFor('acc_cash'), 'SAR'),
          'Estimated from your records · Typed by hand only');
      expect(
          freshnessText(s, d.freshnessFor('acc_usd'), 'USD',
              now: DateTime.utc(2026, 11, 15)),
          startsWith('Nothing new for 44 days'));
      expect(d.freshnessFor('acc_card')!.lastCapture,
          ('sms', '2026-11-15T09:05:00Z'));
      expect(freshnessText(s, null, 'SAR'), isNull);
    });

    test('the catalogs have the same keys and no long dashes', () {
      expect(FinanceCoreStrings.catalogsMatch, isTrue);
      for (final text in FinanceCoreStrings.allTexts) {
        expect(text.contains('—') || text.contains('–'), isFalse, reason: text);
      }
    });
  });

  group('the dashboard cards', () {
    for (final (name, locale, size) in [
      ('English phone', const Locale('en'), phone),
      ('Arabic tablet', const Locale('ar'), tablet),
    ]) {
      testWidgets('cash, left out, money in, review and payments: $name',
          (tester) async {
        var opened = '';
        await pumpModule(
            tester,
            Scaffold(
                body: ListView(children: [
              FinanceDashboardCards(
                  summary: fixture(),
                  onOpenTransactions: () => opened = 'transactions',
                  onOpenSms: () => opened = 'sms',
                  onOpenBills: () => opened = 'bills',
                  onOpenSalary: () => opened = 'salary'),
            ])),
            locale: locale,
            size: size);
        expect(tester.takeException(), isNull);
        expect(find.byKey(const Key('dash-cash')), findsOneWidget);
        expect(find.byKey(const Key('dash-excluded-card_payment')),
            findsOneWidget);
        expect(find.byKey(const Key('dash-salary-status')), findsOneWidget);
        if (locale.languageCode == 'en') {
          expect(find.text('Transfers between your accounts'), findsOneWidget);
          expect(find.text('Salary'), findsOneWidget);
          await show(
              tester, find.text('SMS and Apple Pay messages to check: 1'));
          await tester.tap(find.text('SMS and Apple Pay messages to check: 1'));
          expect(opened, 'sms');
          await show(tester, find.text('Shop Beta 2/4'));
          expect(
              find.textContaining('check the statement row'), findsOneWidget);
        }
      });
    }
  });

  group('the register and a transaction', () {
    testWidgets('filters, pages and opens a transaction', (tester) async {
      final api = CoreApi();
      final opened = <String>[];
      await pumpModule(
          tester,
          RegisterScreen(
              repository: api.repo(),
              accounts: const [
                FinanceAccount(
                    id: 'acc_current',
                    name: 'Example Current',
                    kind: 'bank',
                    currency: 'SAR',
                    balance: '1.00')
              ],
              openTransaction: (_, row) async => opened.add(row.id)));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('reg-txn_0')), findsOneWidget);
      await show(tester, find.byKey(const Key('register-more')));
      await tester.tap(find.byKey(const Key('register-more')));
      await tester.pumpAndSettle();
      expect(api.fake.requests.last.uri.queryParameters['offset'], '25');
      await tester.drag(find.byType(ListView).first, const Offset(0, 3000));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('register-needs-review')));
      await tester.pumpAndSettle();
      expect(
          api.fake.requests.last.uri.queryParameters['status'], 'needs_review');
      expect(find.byKey(const Key('register-more')), findsNothing);
      await tester.tap(find.byKey(const Key('reg-txn_0')));
      await tester.pumpAndSettle();
      expect(opened, ['txn_0']);
    });

    testWidgets(
        'balances for the view, the balance after each row for one account and Download CSV',
        (tester) async {
      final api = CoreApi();
      final saver = FakeExportSaver();
      await pumpModule(
          tester,
          RegisterScreen(
              repository: api.repo(),
              accounts: const [
                FinanceAccount(
                    id: 'acc_current',
                    name: 'Example Current',
                    kind: 'bank',
                    currency: 'SAR',
                    balance: '1.00')
              ],
              initial: const RegisterFilters(
                  accountId: 'acc_current', from: '2026-11-01'),
              saver: saver,
              openTransaction: (_, __) async {}));
      await tester.pumpAndSettle();
      expect(find.text('Balances (Example Current)'), findsOneWidget);
      expect(find.text('Beginning balance'), findsOneWidget);
      expect(find.textContaining('1,250.50'), findsOneWidget);
      expect(find.textContaining('Balance after: '), findsWidgets);
      await tester.tap(find.byKey(const Key('register-download')));
      await tester.pumpAndSettle();
      final sent = api.fake
          .sent('GET', '/api/v1/finance/transactions/export.csv')
          .single;
      expect(sent.uri.queryParameters,
          {'account_id': 'acc_current', 'from': '2026-11-01'});
      expect(saver.saved['anum-transactions.csv'], contains('2026-11-15'));
      expect(find.text('Saved anum-transactions.csv.'), findsOneWidget);
    });

    testWidgets('Download CSV is off when the view is empty', (tester) async {
      final api = CoreApi()..total = 0;
      await pumpModule(
          tester,
          RegisterScreen(
              repository: api.repo(),
              accounts: const [],
              openTransaction: (_, __) async {}));
      await tester.pumpAndSettle();
      expect(
          tester
              .widget<IconButton>(find.byKey(const Key('register-download')))
              .onPressed,
          isNull);
    });

    testWidgets('the evidence: who set the category, links, refunds and note',
        (tester) async {
      final api = CoreApi();
      await pumpModule(
          tester,
          Scaffold(
              body: ListView(children: [
            TransactionEvidenceSection(
                repository: api.repo(), transactionId: 'txn_0')
          ])));
      await tester.pumpAndSettle();
      expect(find.text('You'), findsOneWidget);
      expect(find.text('Example Internet'), findsOneWidget);
      expect(find.byKey(const Key('link-evidence-lnk_1')), findsOneWidget);
      expect(find.text('Provisionally matched'), findsOneWidget);
      expect(
          find.textContaining('Refunded by: EXAMPLE REFUND'), findsOneWidget);
      await show(tester, find.byKey(const Key('tx-note')));
      await tester.enterText(find.byKey(const Key('tx-note')), 'Example note');
      await tester.tap(find.byKey(const Key('tx-note-save')));
      await tester.pumpAndSettle();
      expect(api.note, 'Example note');
      expect(find.text('Note saved.'), findsOneWidget);
    });

    testWidgets('a failed load says what failed and retries', (tester) async {
      final api = CoreApi()..fake.failWith = 500;
      await pumpModule(
          tester,
          RegisterScreen(
              repository: api.repo(),
              accounts: const [],
              openTransaction: (_, __) async {}));
      await tester.pumpAndSettle();
      expect(find.textContaining('Transactions could not be loaded'),
          findsOneWidget);
      api.fake.failWith = null;
      await tester.tap(find.text('Retry').first);
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('reg-txn_0')), findsOneWidget);
    });
  });
}
