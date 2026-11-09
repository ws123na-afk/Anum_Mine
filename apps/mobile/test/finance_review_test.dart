// Finance's review side on the phone (docs/finance.md): transaction types
// and filing an uncategorized row, the suggestion queue, the last statement
// import and reconciliation status, Safe to spend's next pay day and overdue
// minimums. Fixtures are plainly fake and shaped like the API's answers.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/finance/finance_controller.dart';
import 'package:anum_mobile/features/finance/finance_models.dart';
import 'package:anum_mobile/features/finance/finance_personal_strings.dart';
import 'package:anum_mobile/features/finance/finance_plan_models.dart';
import 'package:anum_mobile/features/finance/finance_repository.dart';
import 'package:anum_mobile/features/finance/finance_review_models.dart';
import 'package:anum_mobile/features/finance/finance_review_strings.dart';
import 'package:anum_mobile/features/finance/finance_review_view.dart';
import 'package:anum_mobile/features/finance/finance_screen.dart';
import 'package:anum_mobile/features/finance/finance_strings.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'chat/chat_fakes.dart';
import 'finance_personal_test.dart' show FakePersonal, FakePlanner;
import 'finance_test.dart'
    show
        FakeFinance,
        importJson,
        reconHealthJson,
        reconOpenJson,
        suggestionsJson,
        transactionJson;

/// Answers the review routes like the API does and keeps every request.
class ReviewTransport implements ApiTransport {
  final requests = <ApiRequest>[];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path.replaceFirst('/api/v1/finance', '');
    final Map<String, Object?> body = switch (path) {
      '/register' => {
          'items': [transactionJson(2, uncategorized: true)],
          'total': 4,
          'limit': 25,
          'offset': 0,
        },
      '/transactions' when request.method == 'GET' => {
          'items': [transactionJson(1)],
          'total': 1,
          'limit': 25,
          'offset': 0,
        },
      '/imports' => {
          'items': [importJson()],
          'total': 1,
          'limit': 1,
          'offset': 0,
        },
      '/reconciliations/health' => reconHealthJson(),
      '/reconciliations' => reconOpenJson(),
      '/suggestions' => {
          'items': suggestionsJson(),
          'total': 2,
          'limit': 25,
          'offset': 0,
          'counts': <Object?>[],
          'unavailable': ['bank_messages'],
          'dismissed': 0,
        },
      '/suggestions/accept' => {
          'key': 'classification:fin_txn_2',
          'status': 'accepted',
          'batch': {'id': 'fin_batch_1', 'changed': 1},
        },
      '/suggestions/reject' => {
          'key': 'classification:fin_txn_2',
          'status': 'rejected',
          'batch': null,
        },
      _ => transactionJson(2),
    };
    return ApiResponse(statusCode: 200, body: body);
  }
}

FinanceController review(
        {FakeFinance? finance,
        FakePersonal? personal,
        bool withPersonal = true}) =>
    FinanceController(
        finance ?? (FakeFinance()..uncategorizedRows.addAll({2, 4})),
        personalRepository: withPersonal ? (personal ?? FakePersonal()) : null,
        planner: withPersonal ? FakePlanner() : null);

Widget app(FinanceController controller,
        {Locale locale = const Locale('en'),
        double textScale = 1,
        String webAppUrl = 'https://app.example/',
        List<Uri>? opened}) =>
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
      home: FinanceScreen(
          controller: controller,
          webAppUrl: webAppUrl,
          openLink: (uri) async {
            opened?.add(uri);
            return true;
          }),
    );

Future<void> show(
    WidgetTester tester, FinanceController c, FinanceSection section) async {
  await c.select(section);
  await tester.pumpAndSettle();
}

Future<void> tapVisible(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void phone(WidgetTester tester, [Size size = const Size(390, 844)]) {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

void main() {
  group('models and catalogs', () {
    test('the catalogs match in English and Arabic, with no long dashes', () {
      expect(FinanceReviewStrings.catalogsMatch, isTrue);
      expect(FinancePersonalStrings.catalogsMatch, isTrue);
      expect(FinanceStrings.catalogsMatch, isTrue);
      for (final text in FinanceReviewStrings.allTexts) {
        expect(text.contains('—') || text.contains('–'), isFalse, reason: text);
      }
      const en = FinanceReviewStrings(false), ar = FinanceReviewStrings(true);
      for (final type in [...fileableTypes, 'uncategorized']) {
        expect(en.maybe('type.$type'), isNotNull, reason: type);
        expect(ar.maybe('type.$type'), isNotNull, reason: type);
      }
      const f = FinanceStrings(false);
      for (final category in fileableCategories) {
        expect(f.maybe('category.$category'), isNotNull, reason: category);
      }
    });

    test('a transaction reads its type; older rows have none', () {
      final row = FinanceTransaction.fromJson(
          JsonReader.of(transactionJson(2, uncategorized: true)));
      expect(row.transactionType, 'uncategorized');
      expect(row.uncategorized, isTrue);
      final old = FinanceTransaction.fromJson(JsonReader.of({
        'id': 'fin_txn_old',
        'account_id': 'fin_acct_1',
        'posted_on': '2026-01-01',
        'amount': '-1.00',
        'currency': 'AED',
        'description': 'OLD',
        'category': 'other',
      }));
      expect(old.transactionType, isNull);
      expect(old.uncategorized, isFalse);
    });

    test('the import, reconciliation and suggestions read', () {
      final last = ImportRecord.fromJson(JsonReader.of(importJson()));
      expect((last.imported, last.held, last.attached, last.notInLedger),
          (31, 2, 5, 2));
      expect(last.status, 'tied');
      final status = ReconStatus(
        accounts: JsonReader.of(reconHealthJson())
            .optObjects('accounts', ReconHealth.fromJson),
        open: JsonReader.of(reconOpenJson())
            .optObjects('items', ReconSession.fromJson),
      );
      expect(status.reconcilable.map((a) => a.accountName),
          ['Everyday account', 'Rewards card']);
      final open = status.openFor(status.reconcilable.first)!;
      expect((open.unexplained, open.blocking, open.canComplete),
          ('135.00', 1, false));
      expect(status.openFor(status.reconcilable.last), isNull);
      final items = JsonReader.of({'items': suggestionsJson()})
          .optObjects('items', Suggestion.fromJson);
      expect(items.first.acceptMode, 'apply');
      expect(items.first.percent, 80);
      expect(items.first.effects.single.signed, '-35.00');
      expect(items.last.acceptMode, 'none');
      expect(items.last.percent, isNull);
      expect(items.last.href, '#finance-reconciliation');
    });

    test('safe to spend reads the next pay day and overdue minimums', () {
      final row = CurrencySafe.fromJson(JsonReader.of({
        'currency': 'AED',
        'safe_to_spend': '10.00',
        'per_day': '1.00',
        'short': false,
        'next_pay_on': '2026-11-27',
        'expected_pay': 15000,
        'days_to_payday': 18,
        'lines': [
          {
            'kind': 'minimum_payment',
            'label': 'Car loan',
            'currency': 'AED',
            'amount': '-1200.00',
            'note': 'minimum_overdue',
            'params': {'date': '2026-11-05'},
          },
        ],
      }));
      expect((row.nextPayOn, row.expectedPay, row.daysToPayday),
          ('2026-11-27', '15000', 18));
      expect(row.overdueMinimums.single.label, 'Car loan');
    });
  });

  group('repository', () {
    test('the routes and bodies match the API', () async {
      final transport = ReviewTransport();
      final repo = ApiFinanceRepository(AnumApiClient(
          baseUri: Uri.parse('https://anum.test'),
          transport: transport,
          sessions: await signedInSessions()));
      final filtered =
          await repo.transactions(offset: 0, type: 'uncategorized', query: 'x');
      expect(transport.requests.last.uri.path, '/api/v1/finance/register');
      expect(transport.requests.last.uri.queryParameters,
          {'q': 'x', 'type': 'uncategorized', 'offset': '0', 'limit': '25'});
      expect(filtered.items.single.uncategorized, isTrue);
      await repo.transactions(offset: 25);
      expect(transport.requests.last.uri.path, '/api/v1/finance/transactions');
      expect(await repo.uncategorizedCount(), 4);
      expect(transport.requests.last.uri.queryParameters,
          {'type': 'uncategorized', 'limit': '1'});
      await repo.fileTransaction('fin_txn_2', type: 'expense');
      expect(transport.requests.last.method, 'PATCH');
      expect(transport.requests.last.uri.path,
          '/api/v1/finance/transactions/fin_txn_2');
      expect(transport.requests.last.body, {'transaction_type': 'expense'});
      await repo.fileTransaction('fin_txn_2',
          type: 'expense', category: 'groceries');
      expect(transport.requests.last.body,
          {'transaction_type': 'expense', 'category': 'groceries'});
      expect((await repo.lastImport())!.filename, 'statement-october.pdf');
      expect(transport.requests.last.uri.queryParameters, {'limit': '1'});
      final recon = await repo.reconciliation();
      expect(recon.open.single.id, 'fin_rec_2');
      expect(
          transport.requests.reversed.take(2).map((r) => r.uri.toString()),
          containsAll([
            'https://anum.test/api/v1/finance/reconciliations/health',
            'https://anum.test/api/v1/finance/reconciliations?state=open&limit=50',
          ]));
      final queue = await repo.suggestions(offset: 25);
      expect(transport.requests.last.uri.queryParameters,
          {'limit': '25', 'offset': '25'});
      expect(queue.unavailable, ['bank_messages']);
      final done =
          await repo.acceptSuggestion('classification:fin_txn_2', 'ef56' * 16);
      expect(done.changed, 1);
      expect(transport.requests.last.body,
          {'key': 'classification:fin_txn_2', 'payload_hash': 'ef56' * 16});
      await repo.dismissSuggestion('classification:fin_txn_2');
      expect(transport.requests.last.uri.path,
          '/api/v1/finance/suggestions/reject');
      expect(transport.requests.last.body, {'key': 'classification:fin_txn_2'});
    });
  });

  group('controller', () {
    test('uncategorized rows are counted, shown alone and filed', () async {
      final repo = FakeFinance()..uncategorizedRows.addAll({2, 4});
      final c = FinanceController(repo);
      await c.select(FinanceSection.transactions);
      expect(c.uncategorized, 2);
      await c.showUncategorized(true);
      expect(repo.requests, contains('transactions  0 uncategorized'));
      expect(c.transactions.map((t) => t.id), ['fin_txn_2', 'fin_txn_4']);
      expect(
          await c.fileTransaction(c.transactions.first,
              type: 'expense', category: 'groceries'),
          isNull);
      expect(repo.requests, contains('file fin_txn_2 expense groceries'));
      expect(c.uncategorized, 1);
      expect(c.transactions.single.id, 'fin_txn_4');
      repo.fileFailure = const ApiException(
          409, 'An iPhone SMS or Apple Pay recorded this transaction.');
      expect(await c.fileTransaction(c.transactions.single, type: 'expense'),
          'An iPhone SMS or Apple Pay recorded this transaction.');
    });

    test('suggestions page, accept and dismiss; review reads fail on their own',
        () async {
      final repo = FakeFinance();
      final c = FinanceController(repo);
      await c.select(FinanceSection.suggestions);
      expect(c.suggestions, hasLength(2));
      final accepted =
          await c.decideSuggestion(c.suggestions.first, accept: true);
      expect(accepted.changed, 1);
      expect(c.suggestions, hasLength(1));
      await c.decideSuggestion(c.suggestions.first, accept: false);
      expect(repo.requests, contains('dismiss reconciliation:fin_rec_2:x1'));
      expect(c.suggestions, isEmpty);
      final denied = FinanceController(FakeFinance(deny: true));
      await denied.select(FinanceSection.connections);
      expect(denied.importState, FinanceLoad.denied);
      expect(denied.reconState, FinanceLoad.denied);
      final failing = FinanceController(FakeFinance()
        ..reviewFailure = const ApiException(503, 'Database down'));
      await failing.loadSuggestions();
      expect(failing.suggestionsState, FinanceLoad.error);
      expect(failing.suggestionsError, 'Database down');
      failing.reset();
      expect(failing.suggestionsState, FinanceLoad.idle);
    });
  });

  group('screens', () {
    testWidgets('transactions: the notice, the filter and filing a row',
        (tester) async {
      phone(tester);
      final repo = FakeFinance()..uncategorizedRows.addAll({2, 4});
      final c = review(finance: repo);
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.transactions);
      expect(
          find.textContaining('2 uncategorized, not counted in income or '
              'spending'),
          findsOneWidget);
      expect(find.text('2026-10-02 · Income · Income'), findsWidgets);
      await tapVisible(tester, find.byKey(const Key('tx-show-uncategorized')));
      expect(
          find.text('Showing only the rows not counted yet.'), findsOneWidget);
      expect(find.text('TALABAT ORDER 2'), findsOneWidget);
      expect(find.text('SALARY 1'), findsNothing);
      expect(find.text('2026-10-03 · Other · Uncategorized'), findsOneWidget);

      await tapVisible(tester, find.byKey(const Key('tx-file-fin_txn_2')));
      expect(find.text('What is this?'), findsOneWidget);
      await tester.tap(find.byKey(const Key('file-category')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Groceries').last);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('file-save')));
      await tester.pumpAndSettle();
      expect(repo.requests, contains('file fin_txn_2 expense groceries'));
      expect(find.text('Saved as Spending.'), findsOneWidget);
      expect(find.text('TALABAT ORDER 2'), findsNothing);

      repo.fileFailure = const ApiException(
          409, 'An iPhone SMS or Apple Pay recorded this transaction.');
      await tapVisible(tester, find.byKey(const Key('tx-file-fin_txn_4')));
      await tester.tap(find.byKey(const Key('file-save')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('file-error')), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();

      repo.fileFailure = null;
      repo.uncategorizedRows.clear();
      await c.loadTransactions();
      await tester.pumpAndSettle();
      expect(find.text('Nothing left to file'), findsOneWidget);
      await tapVisible(
          tester, find.widgetWithText(FilledButton, 'Show all rows'));
      expect(c.typeFilter, isNull);
      expect(find.text('SALARY 1'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('suggestions: what, why, money, accept and dismiss',
        (tester) async {
      phone(tester);
      final repo = FakeFinance();
      final opened = <Uri>[];
      final c = review(finance: repo);
      await tester.pumpWidget(app(c, opened: opened));
      await tester.pumpAndSettle();
      await tapVisible(tester, find.widgetWithText(ChoiceChip, 'Suggestions'));
      expect(find.text('File as Groceries?'), findsOneWidget);
      expect(find.text('You chose Groceries for zebramart 4 of 5 times.'),
          findsOneWidget);
      expect(find.text('80% sure'), findsOneWidget);
      expect(find.text('AED -35.00'), findsOneWidget);
      expect(find.text('Everyday account: Statement row not in your books'),
          findsOneWidget);
      expect(
          find.textContaining(
              'The reconciliation for 2026-10-01 to 2026-10-31 cannot be '
              'completed'),
          findsOneWidget);
      expect(find.byKey(const Key('sug-accept-reconciliation:fin_rec_2:x1')),
          findsNothing,
          reason: 'resolved on its page on the web');
      await tapVisible(
          tester, find.byKey(const Key('sug-accept-classification:fin_txn_2')));
      expect(repo.requests,
          contains('accept classification:fin_txn_2 ${'ef56' * 16}'));
      expect(find.text('Done. Rows changed: 1.'), findsOneWidget);
      expect(find.text('File as Groceries?'), findsNothing);

      await tapVisible(
          tester, find.widgetWithText(TextButton, 'Finish on the web').first);
      expect(opened.first.fragment, 'finance-reconciliation');
      await tapVisible(tester,
          find.byKey(const Key('sug-dismiss-reconciliation:fin_rec_2:x1')));
      expect(find.text('Dismissed. It will not come back.'), findsOneWidget);
      expect(find.text('Nothing waits for you'), findsOneWidget);
      await tapVisible(tester, find.text('Open Transactions'));
      expect(c.section, FinanceSection.transactions);
      expect(tester.takeException(), isNull);
    });

    testWidgets('suggestions: failed retries, denied explains who',
        (tester) async {
      final repo = FakeFinance()
        ..reviewFailure = const ApiException(503, 'The database is down.');
      final c = review(finance: repo);
      await tester.pumpWidget(app(c, webAppUrl: ''));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.suggestions);
      expect(
          find.text('Suggestions could not be loaded: The database is down.'),
          findsOneWidget);
      repo.reviewFailure = null;
      await tapVisible(tester, find.text('Retry'));
      expect(find.text('File as Groceries?'), findsOneWidget);
      expect(find.textContaining('Rules, merchant spellings'), findsOneWidget);
      repo.deny = true;
      await c.loadSuggestions();
      await tester.pumpAndSettle();
      expect(find.textContaining('Ask a workspace owner'), findsOneWidget);
    });

    testWidgets('connections: the last import and reconciliation, read only',
        (tester) async {
      phone(tester);
      final opened = <Uri>[];
      final c = review();
      await tester.pumpWidget(app(c, opened: opened));
      await tester.pumpAndSettle();
      await c.loadAccounts();
      await show(tester, c, FinanceSection.connections);
      expect(find.text('statement-october.pdf'), findsOneWidget);
      expect(find.text('2026-11-02 · Everyday account'), findsOneWidget);
      expect(find.text('Imported'), findsOneWidget);
      expect(find.text('31'), findsOneWidget);
      expect(find.text('Not in the ledger'), findsOneWidget);
      expect(find.textContaining('Some statement rows are not in your books'),
          findsOneWidget);
      expect(find.text('Balances match the statement.'), findsOneWidget);
      expect(find.text('Reconciled through 2026-09-30'), findsOneWidget);
      expect(find.text('Open: 2026-10-01 to 2026-10-31'), findsOneWidget);
      expect(find.text('Unexplained AED 135.00'), findsOneWidget);
      expect(find.text('Items that need you: 3'), findsOneWidget);
      expect(find.text('Months with activity but no statement: 2026-08'),
          findsOneWidget);
      expect(find.text('Next: finish the open reconciliation for 2026-10.'),
          findsOneWidget);
      expect(find.text('Rewards card'), findsOneWidget);
      expect(find.text('Not reconciled yet'), findsOneWidget);
      expect(find.text('Next: import a statement for this account.'),
          findsOneWidget);
      expect(find.text('Wallet cash'), findsNothing);
      await tapVisible(
          tester, find.widgetWithText(TextButton, 'Finish on the web').first);
      expect(opened.single.fragment, 'finance-connections');
      await tapVisible(
          tester, find.widgetWithText(TextButton, 'Finish on the web').last);
      expect(opened.last.fragment, 'finance-reconciliation');
      expect(tester.takeException(), isNull);
    });

    testWidgets('connections: no import yet, and each card fails on its own',
        (tester) async {
      final repo = FakeFinance()..noImport = true;
      final c = review(finance: repo);
      await tester.pumpWidget(app(c, webAppUrl: ''));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.connections);
      expect(find.text('No statement imported yet'), findsOneWidget);
      expect(find.textContaining('Statements are imported on the web'),
          findsOneWidget);
      expect(
          find.textContaining('completing a reconciliation are done on the '
              'web'),
          findsOneWidget);
      repo.reviewFailure = const ApiException(503, 'Database down');
      await c.loadReconciliation();
      await tester.pumpAndSettle();
      expect(
          find.text('Reconciliation status could not be loaded: Database down'),
          findsOneWidget);
      expect(find.text('ECB rates'), findsOneWidget);
      repo.reviewFailure = null;
      await tapVisible(tester, find.text('Retry'));
      expect(find.text('Everyday account'), findsWidgets);
    });

    testWidgets('safe to spend shows the next pay day and overdue minimums',
        (tester) async {
      phone(tester, const Size(390, 2400));
      final c = review();
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.safe);
      expect(
          find.text('Next pay day 2026-10-27, in 13 day(s): AED 15,000.00 '
              'expected'),
          findsOneWidget);
      expect(
          find.text('Minimum payments overdue: 1. Still counted until a '
              'payment is matched.'),
          findsOneWidget);
      expect(
          find.textContaining('Overdue since 2026-10-05: no payment matched'),
          findsOneWidget);
      expect(find.text('Salary'), findsOneWidget);
    });

    testWidgets('Arabic reads right to left on the review screens',
        (tester) async {
      phone(tester);
      final c = review();
      await tester.pumpWidget(app(c, locale: const Locale('ar')));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.transactions);
      expect(find.textContaining('2 غير مصنفة'), findsOneWidget);
      await show(tester, c, FinanceSection.suggestions);
      expect(Directionality.of(tester.element(find.byType(SuggestionsView))),
          TextDirection.rtl);
      expect(find.text('هل تصنفها البقالة؟'), findsOneWidget);
      expect(find.text('متأكد بنسبة 80%'), findsOneWidget);
      await show(tester, c, FinanceSection.connections);
      expect(find.text('آخر استيراد لكشف'), findsOneWidget);
      expect(find.text('مطابَق حتى 2026-09-30'), findsOneWidget);
      await show(tester, c, FinanceSection.safe);
      expect(find.text('الراتب'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    for (final (width, height) in [
      (360.0, 640.0),
      (390.0, 844.0),
      (768.0, 1024.0)
    ]) {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        testWidgets(
            'review screens lay out at ${width.toInt()}x${height.toInt()} '
            'with 200% text (${locale.languageCode})', (tester) async {
          phone(tester, Size(width, height));
          final c = review();
          await tester.pumpWidget(app(c, locale: locale, textScale: 2));
          await tester.pumpAndSettle();
          for (final section in const [
            FinanceSection.transactions,
            FinanceSection.suggestions,
            FinanceSection.connections,
            FinanceSection.safe,
            FinanceSection.bills,
            FinanceSection.debts,
          ]) {
            await show(tester, c, section);
            expect(tester.takeException(), isNull, reason: section.name);
          }
          await show(tester, c, FinanceSection.transactions);
          await c.showUncategorized(true);
          await tester.pumpAndSettle();
          await tapVisible(tester, find.byKey(const Key('tx-file-fin_txn_2')));
          expect(tester.takeException(), isNull);
          expect(find.byKey(const Key('file-save')), findsOneWidget);
        });
      }
    }
  });
}
