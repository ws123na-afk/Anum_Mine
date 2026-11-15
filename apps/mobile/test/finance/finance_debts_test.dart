// The debt overview, one obligation with its promises, the payment
// calendar, credit cards, financial alerts and the advisor's answers on
// the phone (docs/finance.md#debt-overview). Against a fake API with the
// API's JSON shapes, in English and Arabic, at 320 to 1024 px. Fixtures are
// plainly fake.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/finance/finance_debts_data.dart';
import 'package:anum_mobile/features/finance/finance_debts_screens.dart';
import 'package:anum_mobile/features/finance/finance_debts_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

const fin = '/api/v1/finance';
final _dash = RegExp('[–—]');

Map<String, Object?> obligation(String id,
        {String type = 'personal_loan',
        String direction = 'owe',
        String status = 'open',
        String refType = 'debt',
        bool discrepancy = false,
        String unverified = '0.00',
        List<Map<String, Object?>> history = const [],
        List<Map<String, Object?>> promises = const []}) =>
    {
      'id': '$refType:$id',
      'ref_type': refType,
      'ref_id': id,
      'type': type,
      'direction': direction,
      'name': 'Example $id',
      'counterparty': null,
      'currency': 'AED',
      'original_amount': '12000.00',
      'start_on': '2026-01-15',
      'schedule': 'monthly',
      'interest': 'unknown',
      'rate': null,
      'expected_installment': '1000.00',
      'paid': '1000.00',
      'remaining': '11000.00',
      'interest_paid': null,
      'fees': null,
      'next_due_on': '2026-12-27',
      'next_due_amount': '1000.00',
      'overdue_amount': status == 'overdue' ? '1000.00' : '0.00',
      'status': status,
      'verified_balance': discrepancy ? '11500.00' : null,
      'verified_on': discrepancy ? '2026-11-01' : null,
      'difference': discrepancy ? '500.00' : null,
      'discrepancy': discrepancy,
      'disputed_amount': null,
      'unverified_amount': unverified,
      'payoff_on': '2027-01-27',
      'payoff_basis': 'schedule',
      'assumptions': ['Each instalment paid on its date.'],
      'history': history,
      'promises': promises,
      'sources': [
        {'figure': 'remaining', 'source': 'schedule_and_matched_payments'}
      ],
      'missing': ['rate'],
      'contact_id': null,
    };

Map<String, Object?> overviewJson({bool empty = false}) => {
      'today': '2026-11-15',
      'items': empty
          ? <Object?>[]
          : [
              obligation('loan_1',
                  status: 'overdue',
                  discrepancy: true,
                  history: [
                    {
                      'on': '2026-10-27',
                      'amount': '1000.00',
                      'currency': 'AED',
                      'kind': 'payment',
                      'evidence': 'verified',
                      'ref_type': 'transaction',
                      'ref_id': 'c1',
                      'note': null
                    }
                  ]),
              obligation('bnpl_1',
                  type: 'bnpl',
                  refType: 'installment_order',
                  unverified: '125.00',
                  history: [
                    {
                      'on': '2026-11-14',
                      'amount': '125.00',
                      'currency': 'AED',
                      'kind': 'installment',
                      'evidence': 'reported',
                      'ref_type': 'installment',
                      'ref_id': 'o:1',
                      'note': 'reported paid by the provider'
                    }
                  ]),
              obligation('ploan_1',
                  type: 'lent_person',
                  direction: 'owed_to_me',
                  refType: 'person_loan',
                  promises: [
                    {
                      'id': 'prom_1',
                      'made_on': '2026-11-10',
                      'due_by': '2026-11-20',
                      'amount': '300.00',
                      'note': null,
                      'status': 'pending',
                      'paid': '0.00'
                    }
                  ]),
            ],
      'totals': [
        {
          'currency': 'AED',
          'owe': '22000.00',
          'owed_to_me': '11000.00',
          'overdue': '1000.00',
          'due_30_days': '2125.00'
        }
      ],
      'calendar': [
        {
          'on': '2026-11-15',
          'obligation_id': 'installment_order:bnpl_1',
          'name': 'Tamara: Example',
          'amount': '125.00',
          'currency': 'AED',
          'direction': 'owe',
          'status': 'reported_paid'
        }
      ],
      'plan': [
        {
          'rank': 1,
          'obligation_id': 'debt:loan_1',
          'name': 'Example loan_1',
          'currency': 'AED',
          'remaining': '11000.00',
          'rate': null,
          'reason': 'overdue'
        }
      ],
      'discrepancies': 1,
      'note': '',
    };

Map<String, Object?> cardJson({String? debtId = 'debt_card'}) => {
      'account_id': 'acc_card',
      'debt_id': debtId,
      'name': 'Example Card',
      'last4': '0000',
      'currency': 'AED',
      'outstanding': '1025.00',
      'limit': '5000.00',
      'limit_source': 'entered',
      'available': '3975.00',
      'utilization_percent': 21,
      'observed_balance': null,
      'apr_percent': null,
      'statement_day': 5,
      'statement_on': '2026-11-05',
      'statement_balance': '1100.00',
      'statement_minimum': '55.00',
      'next_closing_on': '2026-12-05',
      'due_on': '2026-11-25',
      'days_until_due': 10,
      'paid_since_statement': '0.00',
      'paid_provisional': '200.00',
      'payment_state': 'due',
      'cycle': {
        'start': '2026-11-06',
        'end': '2026-12-05',
        'purchases': '0.00',
        'refunds': '0.00',
        'cashback': '0.00',
        'fees': '0.00',
        'interest': '0.00',
        'cash_advances': '0.00',
        'installment_conversions': '0.00',
        'payments': '200.00'
      },
      'check': {
        'status': 'differs',
        'statement_on': '2026-11-05',
        'statement_balance': '1100.00',
        'ledger_balance': '1025.00',
        'difference': '75.00',
        'unverified_rows': 2
      },
      'pay_options': [
        {'code': 'full', 'amount': '1100.00', 'leaves': '0'},
        {
          'code': 'minimum',
          'amount': '55.00',
          'leaves': '1045.00',
          'interest_estimate': null,
          'months_to_clear': null,
          'total_interest': null,
          'never_clears': false
        }
      ],
      'payments': [
        {
          'transaction_id': 'txn_pay',
          'posted_on': '2026-11-10',
          'amount': '200.00',
          'description': 'Payment received',
          'kind': 'payment',
          'evidence': 'unverified',
          'linked': false,
          'from_account_id': null,
          'from_account_name': null
        }
      ],
      'records': <Object?>[],
      'evidence': {'posted': 0, 'pending': 0, 'unverified': 2},
      'unverified_amount': '1225.00',
      'missing': ['rate'],
    };

class DebtsApi {
  DebtsApi() {
    fake = FakeApi(route);
  }
  late final FakeApi fake;
  bool empty = false;
  final alertKinds = <String, bool>{
    'installment_due_tomorrow': true,
    'refund_received': true
  };
  final alertPush = <String, bool>{
    'installment_due_tomorrow': true,
    'refund_received': true
  };
  Map<String, Object?>? lastAlertBody;
  bool emailConfigured = false;

  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path;
    if (path == '$fin/debt-overview') return ok(overviewJson(empty: empty));
    if (path == '$fin/cards') {
      return ok({
        'today': '2026-11-15',
        'items': [cardJson()],
        'note': ''
      });
    }
    if (path.endsWith('/profile') && r.method == 'PUT') return ok({});
    if (path.endsWith('/promises') && r.method == 'POST') {
      return ok({'id': 'prom_2'}, 201);
    }
    if (path == '$fin/alerts/settings') {
      if (r.method == 'PUT') {
        lastAlertBody = r.body;
        final body = r.body?['kinds'] as Map<String, Object?>? ?? const {};
        body.forEach((k, v) => alertKinds[k] = v == true);
        final channels =
            r.body?['channels'] as Map<String, Object?>? ?? const {};
        channels.forEach((k, v) =>
            alertPush[k] = (v as Map<String, Object?>)['push'] == true);
      }
      return ok({
        'kinds': [
          for (final e in alertKinds.entries)
            {
              'kind': e.key,
              'on': e.value,
              'title': e.key,
              'in_app': e.value,
              'push': alertPush[e.key] ?? false,
              'email': false,
            }
        ],
        'channels': {
          'in_app': {'available': true, 'reason': null, 'message': null},
          'push': {'available': true, 'reason': null, 'message': null},
          'email': {
            'available': emailConfigured,
            'reason': emailConfigured ? null : 'not_configured',
            'message': null
          },
        },
      });
    }
    if (path == '$fin/alerts/preview') {
      return ok({
        'today': '2026-11-15',
        'items': [
          {
            'kind': 'unverified_payment',
            'title': 'x',
            'body':
                'Tamara: Example: AED 125.00 is reported paid by the provider, with no bank payment matched yet. It is not counted as paid.',
            'on': '2026-11-10',
            'evidence': 'provisional',
            'on_for_you': true
          }
        ]
      });
    }
    if (path == '$fin/advisor/answer') {
      return ok({
        'question': r.uri.queryParameters['question'],
        'as_of': '2026-11-15',
        'answer': 'You owe AED 22,000.00 across 2 obligation(s).',
        'figures': [
          {'label': 'owed', 'amount': '22000.00', 'currency': 'AED'}
        ],
        'calculation': ['AED: Example loan_1 AED 11,000.00 = AED 11,000.00'],
        'records': [
          {
            'ref_type': 'obligation',
            'ref_id': 'debt:loan_1',
            'on': '2026-12-27',
            'label': 'Example loan_1 (personal_loan)',
            'amount': '11000.00',
            'currency': 'AED',
            'evidence': 'schedule_and_matched_payments'
          }
        ],
        'missing': ['Tamara: AED 125.00 is reported but not verified.'],
        'complete': false,
        'note': ''
      });
    }
    return null;
  }

  FinanceDebtsRepository repo() => FinanceDebtsRepository(fake.client());
}

void main() {
  test('every alert kind the server sends has a name, failed capture included',
      () {
    // The server's list (services/api/anum_api/finance/alerts.py KINDS).
    const kinds = [
      'installment_due_tomorrow',
      'installment_paid',
      'unverified_payment',
      'card_payment_due',
      'loan_due_soon',
      'loan_installment_missed',
      'loan_payment_linked',
      'source_stopped',
      'capture_failed',
      'budget_nearly_exhausted',
      'unexpected_bank_charge',
      'suspected_duplicate',
      'refund_received',
      'new_recurring_charge',
      'salary_received',
      'salary_differs',
      'lender_balance_differs',
    ];
    for (final arabic in [false, true]) {
      final s = FinanceDebtsStrings(arabic);
      for (final kind in kinds) {
        expect(s.t('alert.$kind'), isNot('alert.$kind'), reason: kind);
      }
    }
    expect(const FinanceDebtsStrings(true).t('alert.capture_failed'),
        'تعذرت قراءة رسالة بنك أو كشف حساب');
  });

  test('the catalogs match and use no long dashes', () {
    expect(FinanceDebtsStrings.catalogsMatch, isTrue);
    for (final text in FinanceDebtsStrings.allTexts) {
      expect(text.contains(_dash), isFalse, reason: text);
    }
  });

  for (final size in [narrow, phone, tablet]) {
    testWidgets('the overview shows sources, evidence and the plan at $size',
        (tester) async {
      final api = DebtsApi();
      await pumpModule(tester, DebtOverviewScreen(repository: api.repo()),
          size: size);
      expect(find.text('You owe (AED)'), findsOneWidget);
      expect(find.textContaining('Lender differs'), findsWidgets);
      expect(find.textContaining('AED 125.00 not verified'), findsOneWidget);
      expect(find.text('Owed to you'), findsOneWidget);
      expectClean(tester);
    });
  }

  testWidgets(
      'an obligation shows its source and a reported payment as reported',
      (tester) async {
    final api = DebtsApi();
    final view = DebtOverview.fromJson(JsonReader.of(overviewJson()));
    await pumpModule(
        tester,
        ObligationScreen(
            repository: api.repo(),
            item: view.items.firstWhere((o) => o.type == 'bnpl')));
    expect(find.textContaining('Reported by the provider'), findsOneWidget);
    expect(find.textContaining('Matched payment'), findsNothing);
    expect(find.textContaining('the schedule and the payments matched'),
        findsOneWidget);
    expectClean(tester);
  });

  testWidgets('a promise is added to a loan with a person', (tester) async {
    final api = DebtsApi();
    final view = DebtOverview.fromJson(JsonReader.of(overviewJson()));
    await pumpModule(
        tester,
        ObligationScreen(
            repository: api.repo(),
            item: view.items.firstWhere((o) => o.refType == 'person_loan')));
    expect(find.textContaining('Waiting'), findsOneWidget);
    await tapVisible(tester, find.byKey(const Key('promise-add')));
    // The form asks for the amount in the loan's currency and the date.
    expect(find.text('Amount (AED)'), findsOneWidget);
    expect(find.text('Promised by'), findsOneWidget);
    // Nothing is sent until the form is submitted with both.
    expect(
        api.fake.sent('POST', '$fin/people-loans/ploan_1/promises'), isEmpty);
    expectClean(tester);
  });

  testWidgets('a card says a payment not matched to its bank debit is not paid',
      (tester) async {
    final api = DebtsApi();
    await pumpModule(tester, CreditCardsScreen(repository: api.repo()),
        size: narrow);
    expect(
        find.textContaining('not matched to a bank debit yet'), findsOneWidget);
    expect(find.textContaining('Bank side not matched'), findsOneWidget);
    expect(
        find.textContaining('Enter the card\'s yearly rate'), findsOneWidget);
    expect(find.textContaining('differs from your records'), findsOneWidget);
    expectClean(tester);
  });

  testWidgets(
      'alerts are turned off and saved, and the preview never says paid',
      (tester) async {
    final api = DebtsApi();
    await pumpModule(tester, FinancialAlertsScreen(repository: api.repo()));
    await tapVisible(
        tester, find.byKey(const Key('alert-refund_received-in_app')));
    await tapVisible(tester, find.byKey(const Key('alerts-save')));
    expect(api.alertKinds['refund_received'], isFalse);
    // The saved toast covers the bottom of the page for a moment.
    await tester.pump(const Duration(seconds: 6));
    await tester.pumpAndSettle();
    await tapVisible(tester, find.byKey(const Key('alerts-preview')));
    expect(find.textContaining('not counted as paid'), findsOneWidget);
    expectClean(tester);
  });

  testWidgets(
      'alerts choose push per kind; email says why it cannot be used, in Arabic too',
      (tester) async {
    final api = DebtsApi();
    await pumpModule(tester, FinancialAlertsScreen(repository: api.repo()));
    expect(find.byKey(const Key('alerts-why-email')), findsOneWidget);
    expect(find.textContaining('no mail server'), findsOneWidget);
    final email = tester.widget<FilterChip>(
        find.byKey(const Key('alert-refund_received-email')));
    expect(email.onSelected, isNull);
    await tapVisible(
        tester, find.byKey(const Key('alert-refund_received-push')));
    await tapVisible(tester, find.byKey(const Key('alerts-save')));
    expect(api.alertPush['refund_received'], isFalse);
    expect(api.alertPush['installment_due_tomorrow'], isTrue);
    final channels = api.lastAlertBody?['channels'] as Map<String, Object?>;
    expect(channels['refund_received'],
        {'in_app': true, 'push': false, 'email': false});
    expectClean(tester);

    final arabic = DebtsApi()..emailConfigured = true;
    await pumpModule(tester, FinancialAlertsScreen(repository: arabic.repo()),
        locale: const Locale('ar'));
    expect(find.byKey(const Key('alerts-why-email')), findsNothing);
    expect(find.text('إشعار الهاتف'), findsWidgets);
    final enabled = tester.widget<FilterChip>(
        find.byKey(const Key('alert-refund_received-email')));
    expect(enabled.onSelected, isNotNull);
    expectClean(tester);
  });

  testWidgets('an answer shows its records and admits it may be incomplete',
      (tester) async {
    final api = DebtsApi();
    await pumpModule(tester, AdvisorAskScreen(repository: api.repo()),
        locale: const Locale('ar'));
    await tapVisible(tester, find.byKey(const Key('ask-go')));
    expect(
        api.fake.sent('GET', '$fin/advisor/answer').single.uri.queryParameters,
        {'question': 'owe'});
    expect(find.byKey(const Key('ask-answer')), findsOneWidget);
    expect(find.text('قد تكون ناقصة'), findsOneWidget);
    expect(find.textContaining('الجدول والدفعات المطابقة له'), findsOneWidget);
    expectClean(tester);
  });

  testWidgets('an empty overview says what to add', (tester) async {
    final api = DebtsApi()..empty = true;
    await pumpModule(tester, DebtOverviewScreen(repository: api.repo()));
    expect(find.text('No debts recorded'), findsOneWidget);
    expectClean(tester);
  });

  testWidgets('a refused overview says who can see it', (tester) async {
    final api = DebtsApi();
    api.fake.failWith = 403;
    await pumpModule(tester, DebtOverviewScreen(repository: api.repo()));
    expect(find.textContaining('person\'s own'), findsOneWidget);
    expectClean(tester);
  });
}
