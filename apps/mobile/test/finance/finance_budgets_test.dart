// Budgets and the salary plan, recurring bills and subscriptions, and
// spending categories on the phone (docs/finance.md#budgets-and-planning,
// #recurring-payments, #category-ontology). Against a fake API with the
// API's JSON shapes, in English and Arabic, at phone and narrow sizes.
// Fixtures are plainly fake.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/finance/finance_budget_data.dart';
import 'package:anum_mobile/features/finance/finance_budget_screens.dart';
import 'package:anum_mobile/features/finance/finance_budget_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

const fin = '/api/v1/finance';
final _dash = RegExp('[–—]');

Map<String, Object?> _budget({String risk = 'watch', bool active = true}) => {
      'id': 'fin_bud_1',
      'name': 'Food',
      'kind': 'spending',
      'period': 'monthly',
      'currency': 'AED',
      'amount': '500.00',
      'period_start': '2026-11-01',
      'period_end': '2026-11-30',
      'days_left': 16,
      'categories': ['groceries'],
      'subcategory_ids': <String>[],
      'fixed': false,
      'essential': true,
      'allocation': 'food',
      'alert_percent': 80,
      'reserve_in_safe_to_spend': false,
      'active': active,
      'notes': null,
      'goal_id': null,
      'debt_ids': <String>[],
      'spent': '420.00',
      'remaining': '80.00',
      'over': '0.00',
      'percent': 84,
      'coming': '0.00',
      'coming_items': <Object?>[],
      'forecast': '588.00',
      'risk': risk,
      'rows': 3,
      'basis': 'spending',
      'goal_saved': null,
      'goal_target': null,
    };

// A budget that carries what is left, at most AED 150.00, and carries an
// overspend: October left 150.00 and November started from 650.00
// (docs/finance.md#budget-rollover).
Map<String, Object?> _rolled() => {
      ..._budget(risk: 'ok'),
      'id': 'fin_bud_roll',
      'name': 'Rolled food',
      'remaining': '230.00',
      'percent': 64,
      'rollover': 'capped',
      'rollover_cap': '150.00',
      'overspend': 'carry',
      'carried_in': '150.00',
      'available': '650.00',
      'chain_periods': 2,
      'starts_on': null,
      'ends_on': null,
      'chain': [
        for (final p in [
          ['2026-10-01', '2026-10-31', '0.00', '500.00', '350.00', '150.00'],
          ['2026-11-01', '2026-11-30', '150.00', '650.00', '420.00', null],
        ])
          {
            'start': p[0],
            'end': p[1],
            'currency': 'AED',
            'amount': '500.00',
            'carried_in': p[2],
            'available': p[3],
            'spent': p[4],
            'rows': 3,
            'left': '0.00',
            'carried_out': p[5],
            'rollover': 'capped',
            'cap': '150.00',
            'overspend': 'carry',
            'restarted': false,
          }
      ],
    };

const _tree = {
  'month': null,
  'spend': <Object?>[],
  'group_totals': {
    'food_drinks': {'AED': '420.00'}
  },
  'groups': [
    {
      'id': 'food_drinks',
      'name': 'Food and drinks',
      'name_ar': 'الطعام والمشروبات',
      'debt_types': <Object?>[],
      'categories': [
        {
          'id': 'groceries',
          'name': 'Groceries',
          'name_ar': 'البقالة',
          'spending': true,
          'essential': true,
          'subcategories': [
            {
              'id': 'sys:vegetables',
              'name': 'Vegetables',
              'name_ar': 'خضار',
              'built_in': true,
              'archived': false,
              'essential': true
            }
          ]
        }
      ]
    },
    {
      'id': 'charity',
      'name': 'Religious and charitable',
      'name_ar': 'الديني والخيري',
      'debt_types': <Object?>[],
      'categories': [
        {
          'id': 'charity',
          'name': 'Religious and charitable',
          'name_ar': 'الديني والخيري',
          'spending': true,
          'essential': false,
          'subcategories': [
            {
              'id': 'sys:zakat',
              'name': 'Zakat',
              'name_ar': 'الزكاة',
              'built_in': true,
              'archived': false,
              'essential': true
            }
          ]
        }
      ]
    },
  ],
};

Map<String, Object?>? _body(ApiRequest request) => request.body;

FakeApi _api({bool empty = false, bool rolled = false}) {
  final budgets = <Map<String, Object?>>[
    if (!empty && !rolled) _budget(),
    if (rolled) _rolled()
  ];
  var found = true;
  var autoLink = false;
  var mappingOn = true;
  return FakeApi((r) {
    final path = r.uri.path;
    if (path == '$fin/budgets/plans' && r.method == 'GET') {
      return ok({
        'on': '2026-11-15',
        'today': '2026-11-15',
        'budgets': budgets,
        'totals': budgets.isEmpty
            ? <Object?>[]
            : [
                {
                  'currency': 'AED',
                  'budget': '500.00',
                  'spent': '420.00',
                  'remaining': '80.00',
                  'coming': '0.00',
                  'forecast': '588.00'
                }
              ]
      });
    }
    if (path == '$fin/budgets/plans' && r.method == 'POST') {
      budgets.add({
        ..._budget(),
        'id': 'fin_bud_${budgets.length + 1}',
        'name': _body(r)?['name'] ?? 'x'
      });
      return ok(budgets.last, 201);
    }
    if (path.startsWith('$fin/budgets/plans/') && r.method == 'PATCH') {
      return ok(budgets.first);
    }
    if (path == '$fin/budgets/allocation') {
      return ok({
        'month': '2026-11',
        'currencies': [
          {
            'currency': 'AED',
            'income': '12000.00',
            'income_source': 'salary_profile',
            'buckets': {'food': '500.00'},
            'allocated': '500.00',
            'unallocated': '11500.00',
            'lines': <Object?>[],
            'left_out': <Object?>[]
          }
        ]
      });
    }
    if (path == '$fin/categories') return ok(_tree);
    if (path == '$fin/goals') return okList(<Object?>[]);
    if (path == '$fin/recurring' && r.method == 'GET') {
      return ok({
        'today': '2026-11-15',
        'dismissed': found ? 0 : 1,
        'found': found
            ? [
                {
                  'key': 'subscription:acc:NETFLIX.COM:AED:monthly',
                  'name': 'NETFLIX.COM',
                  'amount': '39.00',
                  'currency': 'AED',
                  'cadence': 'monthly',
                  'next_due_on': '2026-12-05',
                  'category': 'subscriptions',
                  'account_id': 'acc',
                  'occurrences': 6,
                  'kind': 'streaming',
                  'merchant': 'netflix',
                  'last_amount': '45.00',
                  'price_change': '6.00',
                  'transaction_ids': <Object?>[]
                }
              ]
            : <Object?>[],
        'kept': [
          {
            'id': 'bill_rent',
            'name': 'Rent',
            'amount': '6500.00',
            'currency': 'AED',
            'cadence': 'monthly',
            'next_due_on': '2026-11-18',
            'category': 'housing',
            'account_id': 'acc',
            'active': true,
            'source': 'manual',
            'merchant': null,
            'recurring_kind': 'rent',
            'auto_link': autoLink,
            'usage': 'rarely',
            'last_paid_on': '2026-10-19',
            'last_amount': '6500.00',
            'price_change': null,
            'still_billing_rarely_used': true
          }
        ]
      });
    }
    if (path == '$fin/recurring/reject') {
      found = false;
      return ok({'key': 'k', 'status': 'rejected'});
    }
    if (path == '$fin/recurring/bills/bill_rent') {
      autoLink = _body(r)?['auto_link'] == true || autoLink;
      return ok({});
    }
    if (path == '$fin/category-mappings' && r.method == 'GET') {
      return ok({
        'items': [
          {
            'merchant': 'quokka kitchen',
            'direction': 'out',
            'category': 'dining',
            'subcategory_id': null,
            'transaction_type': null,
            'confirmations': 2,
            'enabled': mappingOn,
            'example': 'POS QUOKKA KITCHEN',
            'source_transaction_id': 't',
            'created_at': '2026-11-01T00:00:00Z',
            'updated_at': '2026-11-01T00:00:00Z'
          }
        ],
        'total': 1,
        'limit': 100,
        'offset': 0
      });
    }
    if (path == '$fin/category-mappings' && r.method == 'PATCH') {
      mappingOn = _body(r)?['enabled'] == true;
      return ok({});
    }
    return null;
  });
}

void main() {
  test('the English and Arabic catalogs match and use no long dashes', () {
    expect(FinanceBudgetStrings.catalogsMatch, isTrue);
    expect(FinanceBudgetStrings.allTexts.where(_dash.hasMatch), isEmpty);
  });

  test('the budget form says what is missing, in order', () {
    const m = FinanceBudgetStrings(false);
    String? problem(
            {String name = 'Food',
            String amount = '500',
            String kind = 'spending',
            Set<String> cats = const {'groceries'},
            String period = 'monthly',
            String? goal,
            String start = '',
            String end = ''}) =>
        budgetFormProblem(m,
            name: name,
            amount: amount,
            currency: 'AED',
            kind: kind,
            period: period,
            categories: cats,
            goalId: goal,
            startsOn: start,
            endsOn: end);
    expect(problem(name: ' '), m.t('form.needName'));
    expect(problem(amount: '0'), m.t('form.needAmount'));
    expect(problem(cats: {}), m.t('form.needCategory'));
    expect(problem(kind: 'emergency', cats: {}), m.t('form.needGoal'));
    expect(problem(period: 'custom', start: '2026-12-10', end: '2026-12-01'),
        m.t('form.needDates'));
    expect(problem(), isNull);
  });

  testWidgets(
      'budgets list spent, risk and the salary split, and a new budget is saved',
      (tester) async {
    final api = _api();
    await pumpModule(tester,
        BudgetsScreen(repository: FinanceBudgetRepository(api.client())));
    expect(find.text('Food'), findsWidgets);
    expect(find.textContaining('Close to the limit'), findsOneWidget);
    expect(find.textContaining('Income this month'), findsOneWidget);
    expect(find.textContaining('Not planned yet'), findsOneWidget);
    expectClean(tester);
    await tester.tap(find.byKey(const Key('budget-new')));
    await tester.pumpAndSettle();
    await tapVisible(tester, find.byKey(const Key('budget-save')));
    expect(find.text('Enter a name.'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('budget-name')), 'Groceries plan');
    await tester.enterText(find.byKey(const Key('budget-amount')), '800');
    await tapVisible(tester, find.byKey(const Key('budget-cat-groceries')));
    await tapVisible(tester, find.byKey(const Key('budget-save')));
    expect(api.sent('POST', '$fin/budgets/plans'), hasLength(1));
    expect(find.text('Groceries plan was saved.'), findsOneWidget);
  });

  test('a capped rollover needs its cap', () {
    const m = FinanceBudgetStrings(false);
    String? problem(String rollover, String cap) => budgetFormProblem(m,
        name: 'Food',
        amount: '500',
        currency: 'AED',
        kind: 'spending',
        period: 'monthly',
        categories: const {'groceries'},
        rollover: rollover,
        rolloverCap: cap);
    expect(problem('capped', ''), m.t('form.needCap'));
    expect(problem('capped', '150'), isNull);
    expect(problem('surplus', ''), isNull);
    final line = PlanBudget.fromJson(JsonReader(_rolled()));
    expect(line.showsRollover, isTrue);
    expect(line.body()['rollover'], 'capped');
    expect(line.body()['rollover_cap'], '150.00');
    expect(line.body()['overspend'], 'carry');
    expect(PlanBudget.fromJson(JsonReader(_budget())).showsRollover, isFalse);
  });

  testWidgets(
      'a carrying budget shows what came in, what is available and the chain, and an edit keeps it',
      (tester) async {
    final api = _api(rolled: true);
    await pumpModule(tester,
        BudgetsScreen(repository: FinanceBudgetRepository(api.client())));
    expect(find.textContaining('Carried in'), findsOneWidget);
    expect(find.textContaining('Available'), findsOneWidget);
    expect(find.textContaining('at most'), findsOneWidget);
    await tapVisible(
        tester, find.byKey(const Key('budget-chain-fin_bud_roll')));
    expect(find.text('Still open'), findsOneWidget);
    expect(find.textContaining('Carries up to'), findsNWidgets(2));
    expectClean(tester);
    await tapVisible(tester, find.byKey(const Key('budget-menu-fin_bud_roll')));
    await tester.tap(find.text('Pause').last);
    await tester.pumpAndSettle();
    final sent = api.sent('PATCH', '$fin/budgets/plans/fin_bud_roll');
    expect(sent, hasLength(1));
    expect(sent.single.body?['rollover'], 'capped');
    expect(sent.single.body?['rollover_cap'], '150.00');
  });

  testWidgets('the rollover history reads in Arabic at 320 px', (tester) async {
    await pumpModule(
        tester,
        BudgetsScreen(
            repository: FinanceBudgetRepository(_api(rolled: true).client())),
        size: narrow,
        locale: const Locale('ar'));
    expect(find.textContaining('مُرحَّل من الفترة السابقة'), findsOneWidget);
    await tapVisible(
        tester, find.byKey(const Key('budget-chain-fin_bud_roll')));
    expect(find.text('ما زالت مفتوحة'), findsOneWidget);
    expectClean(tester);
  });

  testWidgets(
      'budgets say how to start when there are none, in Arabic, at 320 px',
      (tester) async {
    await pumpModule(
        tester,
        BudgetsScreen(
            repository: FinanceBudgetRepository(_api(empty: true).client())),
        size: narrow,
        locale: const Locale('ar'));
    expect(find.text('لا توجد ميزانيات بعد'), findsOneWidget);
    expectClean(tester);
  });

  testWidgets(
      'a found payment is dismissed and a kept bill turns on self-linking',
      (tester) async {
    final api = _api();
    await pumpModule(tester,
        RecurringScreen(repository: FinanceBudgetRepository(api.client())));
    expect(find.textContaining('Price changed by'), findsOneWidget);
    expect(find.text('Still billing while you rarely use it'), findsOneWidget);
    await tapVisible(tester, find.byKey(const Key('found-reject-NETFLIX.COM')));
    expect(api.sent('POST', '$fin/recurring/reject'), hasLength(1));
    expect(
        find.text('NETFLIX.COM will not be suggested again.'), findsOneWidget);
    await tapVisible(tester, find.byKey(const Key('kept-auto-bill_rent')));
    expect(api.sent('PATCH', '$fin/recurring/bills/bill_rent'), hasLength(1));
    expect(find.text('Turn off self-linking'), findsOneWidget);
    expectClean(tester);
  });

  testWidgets(
      'categories show groups, search narrows them, and a learned merchant is switched off',
      (tester) async {
    final api = _api();
    await pumpModule(
        tester,
        SpendingCategoriesScreen(
            repository: FinanceBudgetRepository(api.client())));
    expect(find.text('Food and drinks'), findsOneWidget);
    expect(find.text('Vegetables'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('categories-search')), 'zakat');
    await tester.pumpAndSettle();
    expect(find.text('Food and drinks'), findsNothing);
    expect(find.text('Religious and charitable'), findsWidgets);
    await tapVisible(
        tester, find.byKey(const Key('learned-switch-quokka kitchen')));
    expect(api.sent('PATCH', '$fin/category-mappings'), hasLength(1));
    expect(find.text('Switch on'), findsOneWidget);
    expectClean(tester);
  });

  testWidgets('a refusal shows who can use Finance', (tester) async {
    final api = _api()..failWith = 403;
    await pumpModule(tester,
        RecurringScreen(repository: FinanceBudgetRepository(api.client())));
    expect(
        find.textContaining('only they can see and change it'), findsOneWidget);
  });
}
