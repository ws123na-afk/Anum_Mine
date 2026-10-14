import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/finance/finance_controller.dart';
import 'package:anum_mobile/features/finance/finance_models.dart';
import 'package:anum_mobile/features/finance/finance_repository.dart';
import 'package:anum_mobile/features/finance/finance_screen.dart';
import 'package:anum_mobile/features/finance/finance_strings.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

// Finance on the phone (docs/finance.md): the month, transactions, accounts,
// bills, debts and goals. Fixtures are plainly fake (example merchants).

Map<String, Object?> summaryJson({bool empty = false}) => {
      'month': '2026-10',
      'base_currency': 'AED',
      'accounts': empty
          ? []
          : [
              {
                'id': 'fin_acct_1',
                'name': 'Everyday account',
                'kind': 'bank',
                'currency': 'AED',
                'balance': '15349.50',
                'last4': '1234',
                'archived': false,
              },
            ],
      'balances': empty
          ? []
          : [
              {'currency': 'AED', 'amount': '15349.50'},
              {'currency': 'USD', 'amount': '-60.25'},
            ],
      'spend_by_category': [
        {
          'category': 'utilities',
          'currency': 'AED',
          'amount': '450.00',
          'count': 1
        },
        {
          'category': 'groceries',
          'currency': 'AED',
          'amount': '120.50',
          'count': 2
        },
      ],
      'income': [
        {'currency': 'AED', 'amount': '15000.00'}
      ],
      'spend': [
        {'currency': 'AED', 'amount': '570.50'}
      ],
      'net': [
        {'currency': 'AED', 'amount': '14429.50'}
      ],
      'upcoming_bills': [
        {
          'id': 'fin_bill_rent',
          'name': 'Rent',
          'amount': '6500.00',
          'currency': 'AED',
          'due_on': '2026-10-17',
          'days_until': 3,
          'category': 'housing'
        },
      ],
      'bills_due': [
        {'currency': 'AED', 'amount': '6500.00'}
      ],
      'debts': [],
      'minimum_payments': [],
      'debt_count': 0,
      'goals': [],
    };

Map<String, Object?> transactionJson(int i) => {
      'id': 'fin_txn_$i',
      'account_id': 'fin_acct_1',
      'posted_on': '2026-10-0${1 + i % 9}',
      'amount': i.isEven ? '-35.00' : '15000.00',
      'currency': 'AED',
      'description': i.isEven ? 'TALABAT ORDER $i' : 'SALARY $i',
      'category': i.isEven ? 'dining' : 'income',
    };

class FakeFinance implements FinanceRepository {
  FakeFinance(
      {this.deny = false, this.failSummary = false, this.empty = false});
  bool deny, failSummary;
  final bool empty;
  final requests = <String>[];
  final total = 30;

  @override
  Future<FinanceSummary> summary({String? month}) async {
    requests.add('summary');
    if (deny) throw const ApiException(403, 'Workspace permission denied');
    if (failSummary) {
      throw const ApiException(503, 'The database is not reachable.');
    }
    return FinanceSummary.fromJson(JsonReader.of(summaryJson(empty: empty)));
  }

  @override
  Future<List<FinanceAccount>> accounts() async => empty
      ? []
      : [
          const FinanceAccount(
              id: 'fin_acct_1',
              name: 'Everyday account',
              kind: 'bank',
              currency: 'AED',
              balance: '15349.50',
              last4: '1234')
        ];

  @override
  Future<FinanceAccount> createAccount(
      {required String name,
      required String kind,
      String? last4,
      String openingBalance = '0'}) async {
    requests.add('createAccount $name $last4');
    return FinanceAccount(
        id: 'fin_acct_2',
        name: name,
        kind: kind,
        currency: 'AED',
        balance: openingBalance);
  }

  @override
  Future<void> deleteAccount(String id) async => requests.add('delete $id');

  @override
  Future<TransactionPage> transactions(
      {String? query, required int offset, int limit = 25}) async {
    requests.add('transactions ${query ?? ''} $offset');
    final count = (total - offset).clamp(0, limit);
    return TransactionPage([
      for (var i = 0; i < count; i++)
        FinanceTransaction.fromJson(JsonReader.of(transactionJson(offset + i)))
    ], total);
  }

  @override
  Future<void> createTransaction(
          {required String accountId,
          required String postedOn,
          required String amount,
          required String description}) async =>
      requests.add('createTransaction $amount');

  @override
  Future<void> deleteTransaction(String id) async => requests.add('delete $id');

  @override
  Future<List<FinanceItem>> items(String kind) async {
    requests.add('items $kind');
    if (kind != 'bills') return [];
    return [
      FinanceItem.bill(JsonReader.of({
        'id': 'fin_bill_rent',
        'name': 'Rent',
        'amount': '6500.00',
        'currency': 'AED',
        'cadence': 'monthly',
        'next_due_on': '2026-10-17',
      })),
    ];
  }

  @override
  Future<void> createBill(
          {required String name,
          required String amount,
          required String dueOn}) async =>
      requests.add('createBill $name $amount');

  @override
  Future<void> deleteItem(String kind, String id) async =>
      requests.add('delete $kind $id');
}

Widget app(FinanceController controller,
        {Locale locale = const Locale('en'), double textScale = 1}) =>
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
      home: Builder(
        builder: (context) => Scaffold(
          body: Center(
            child: FilledButton(
              onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => FinanceScreen(controller: controller))),
              child: const Text('Open Finance'),
            ),
          ),
        ),
      ),
    );

Future<void> open(WidgetTester tester) async {
  await tester.tap(find.text('Open Finance'));
  await tester.pumpAndSettle();
}

void main() {
  test('money stays a string per currency, and typed amounts are checked', () {
    expect(formatAmount('15349.5', 'AED'), 'AED 15,349.5');
    expect(formatAmount('-1234.50', 'AED'), 'AED -1,234.50');
    expect(formatAmount('abc', 'AED'), '-');
    expect(
        formatAmounts(const [
          CurrencyAmount('AED', '10.00'),
          CurrencyAmount('USD', '5.00')
        ]),
        'AED 10.00 · USD 5.00');
    expect(parseAmount('1,234.50'), '1234.50');
    expect(parseAmount('-45'), '-45');
    expect(parseAmount('1.234'), isNull);
    expect(parseAmount('-5', signed: false), isNull);
    expect(parseAmount('ten'), isNull);
    expect(FinanceStrings.catalogsMatch, isTrue);
  });

  test('the controller pages and searches transactions; a 403 is denied',
      () async {
    final repo = FakeFinance();
    final c = FinanceController(repo);
    await c.load();
    expect(c.summaryState, FinanceLoad.ready);
    expect(c.summary!.pick(c.summary!.balances).amount, '15349.50');
    await c.select(FinanceSection.transactions);
    expect(c.transactions, hasLength(25));
    await c.loadMoreTransactions();
    expect(c.transactions, hasLength(30));
    expect(c.hasMoreTransactions, isFalse);
    await c.search('talabat');
    expect(repo.requests, contains('transactions talabat 0'));
    final denied = FinanceController(FakeFinance(deny: true));
    await denied.loadSummary();
    expect(denied.summaryState, FinanceLoad.denied);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
    testWidgets('Finance lays out at $width wide with 200% text',
        (tester) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final c = FinanceController(FakeFinance());
      await tester.pumpWidget(app(c, textScale: 2));
      await open(tester);
      expect(tester.takeException(), isNull);
      expect(find.text('AED 15,349.50'), findsWidgets);
      await c.select(FinanceSection.transactions);
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('TALABAT ORDER 0'), findsOneWidget);
    });
  }

  testWidgets('the AppBar back button returns to where Finance was opened',
      (tester) async {
    final c = FinanceController(FakeFinance());
    await tester.pumpWidget(app(c));
    await open(tester);
    expect(find.byType(FinanceScreen), findsOneWidget);
    await tester.tap(find.byType(BackButton));
    await tester.pumpAndSettle();
    expect(find.text('Open Finance'), findsOneWidget);
  });

  testWidgets('Arabic reads right to left; empty leads to adding an account',
      (tester) async {
    final repo = FakeFinance(empty: true);
    final c = FinanceController(repo);
    await tester.pumpWidget(app(c, locale: const Locale('ar')));
    await tester.tap(find.text('Open Finance'));
    await tester.pumpAndSettle();
    expect(Directionality.of(tester.element(find.byType(FinanceScreen))),
        TextDirection.rtl);
    expect(find.text('ابدأ بحساب'), findsOneWidget);
    await tester.tap(find.text('أضف حسابًا').first);
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, 'Salary account');
    await tester.enterText(find.byType(TextField).last, '12345678');
    await tester.tap(find.text('حفظ'));
    await tester.pumpAndSettle();
    expect(find.text('أدخل آخر أربعة أرقام فقط، أو اتركه فارغًا.'),
        findsOneWidget);
    await tester.enterText(find.byType(TextField).last, '1234');
    await tester.tap(find.text('حفظ'));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('createAccount Salary account 1234'));
  });

  testWidgets('a failed summary says what failed and retries', (tester) async {
    final repo = FakeFinance(failSummary: true);
    final c = FinanceController(repo);
    await tester.pumpWidget(app(c));
    await open(tester);
    expect(find.textContaining('Finance could not be loaded'), findsOneWidget);
    repo.failSummary = false;
    await tester.tap(find.text('Retry').first);
    await tester.pumpAndSettle();
    expect(find.text('AED 15,349.50'), findsWidgets);
  });

  testWidgets('a viewer is told who can use Finance', (tester) async {
    final c = FinanceController(FakeFinance(deny: true));
    await tester.pumpWidget(app(c));
    await open(tester);
    expect(find.textContaining('Ask a workspace owner to change your role'),
        findsOneWidget);
  });

  testWidgets('bills are listed, added and deleted', (tester) async {
    tester.view.physicalSize = const Size(390, 2000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final repo = FakeFinance();
    final c = FinanceController(repo);
    await tester.pumpWidget(app(c));
    await open(tester);
    await tester.tap(find.text('Bills'));
    await tester.pumpAndSettle();
    expect(find.text('Rent'), findsWidgets);
    await tester.tap(find.byTooltip('Delete Rent'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('delete bills fin_bill_rent'));
  });
}
