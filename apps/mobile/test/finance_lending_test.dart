// Loan schedules with interest, repayment suggestions and expected one-off
// income on the phone (docs/finance.md#lent-and-borrowed, #forecast), with
// every state and the layouts at 390x844 and 768x1024 in English and
// Arabic. Fixtures are plainly fake.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/finance/finance_controller.dart';
import 'package:anum_mobile/features/finance/finance_lending_strings.dart';
import 'package:anum_mobile/features/finance/finance_lending_view.dart';
import 'package:anum_mobile/features/finance/finance_personal_models.dart';
import 'package:anum_mobile/features/finance/finance_personal_repository.dart';
import 'package:anum_mobile/features/finance/finance_personal_view.dart';
import 'package:anum_mobile/features/records_kit/records_kit.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'chat/chat_fakes.dart';
import 'finance_personal_test.dart'
    show FakePersonal, FakePlanner, app, show, tapVisible;
import 'finance_test.dart' show FakeFinance;

const hash = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

/// Two loans in two currencies, both with something overdue: never added
/// together.
Map<String, Object?> scheduledLoans() => {
      'items': [
        {
          'id': 'fin_ploan_1',
          'direction': 'lent',
          'person': 'Sara Example',
          'amount': '1400.00',
          'currency': 'AED',
          'lent_on': '2026-07-15',
          'due_on': '2027-09-01',
          'notes': null,
          'remind': true,
          'remind_days_before': 3,
          'closed_on': null,
          'interest_rate': '5.0000',
          'installments': 14,
          'first_due_on': '2026-08-01',
          'repaid': '100.00',
          'outstanding': '1335.00',
          'handed': '1400.00',
          'status': 'overdue',
          'days_until_due': -9,
          'interest': '35.00',
          'total_due': '1435.00',
          'next_due_on': '2026-08-01',
          'next_due_amount': '2.50',
          'overdue_amount': '207.50',
          'links': 2,
        },
        {
          'id': 'fin_ploan_2',
          'direction': 'borrowed',
          'person': 'Omar Example',
          'amount': '500.00',
          'currency': 'USD',
          'lent_on': '2026-09-01',
          'due_on': '2026-10-15',
          'notes': null,
          'remind': false,
          'remind_days_before': 5,
          'closed_on': null,
          'interest_rate': null,
          'installments': null,
          'first_due_on': null,
          'repaid': '0.00',
          'outstanding': '500.00',
          'handed': '500.00',
          'status': 'overdue',
          'days_until_due': -26,
          'interest': '0.00',
          'total_due': '500.00',
          'next_due_on': '2026-10-15',
          'next_due_amount': '500.00',
          'overdue_amount': '500.00',
          'links': 1,
        },
      ],
      'total': 2,
      'limit': 25,
      'offset': 0,
      'owed': [
        {
          'currency': 'AED',
          'they_owe_me': '1335.00',
          'i_owe': '0.00',
          'they_owe_me_overdue': '207.50',
          'i_owe_overdue': '0.00',
        },
        {
          'currency': 'USD',
          'they_owe_me': '0.00',
          'i_owe': '500.00',
          'they_owe_me_overdue': '0.00',
          'i_owe_overdue': '500.00',
        },
      ],
      'overdue': 2,
      'note': 'Money lent or borrowed with a person is not spending.',
    };

/// The detail of the first loan: 14 monthly repayments, the first part
/// repaid and overdue, the next one overdue, the rest due.
Map<String, Object?> detailFor(String id) {
  final loans = scheduledLoans()['items']! as List<Object?>;
  final loan = loans.cast<Map<String, Object?>>().firstWhere(
      (l) => l['id'] == id,
      orElse: () => loans.first! as Map<String, Object?>);
  final scheduled = loan['installments'] != null;
  return {
    ...loan,
    'movements': <Object?>[],
    'schedule': scheduled
        ? [
            for (var n = 1; n <= 14; n++)
              {
                'number': n,
                'due_on':
                    '${n < 6 ? 2026 : 2027}-${((n + 6) % 12 + 1).toString().padLeft(2, '0')}-01',
                'principal': '100.00',
                'interest': '2.50',
                'amount': '102.50',
                'state': n == 1
                    ? 'part_repaid'
                    : n == 2
                        ? 'overdue'
                        : 'due',
              },
          ]
        : <Object?>[],
  };
}

Map<String, Object?> proposal(String id, {String direction = 'lent'}) => {
      'key': 'loan_repayment:fin_tx_$id:fin_ploan_1',
      'producer': 'person_loans',
      'kind': 'loan_repayment',
      'detected': 'This money in may be Sara Example repaying you.',
      'why': 'The description names the person.',
      'params': {
        'person': 'Sara Example',
        'direction': direction,
        'currency': 'AED',
        'amount': '102.50',
        'outstanding': '1335.00',
        'next': '102.50',
      },
      'evidence': <Object?>[],
      'confidence': '0.85',
      'effect': [
        {'amount': '102.50', 'currency': 'AED', 'direction': 'in'}
      ],
      'action': {
        'kind': 'link',
        'transaction_ids': ['fin_tx_$id'],
        'link': {
          'canonical_id': 'canon_$id',
          'dimension': 'person_loan',
          'target_type': 'finance_person_loan',
          'target_id': 'fin_ploan_1',
          'amount': '102.50',
          'currency': 'AED',
          'role': 'repayment',
        },
      },
      'payload_hash': hash,
      'href': '#finance-debts-people',
      'occurred_on': '2026-11-02',
    };

Map<String, Object?> incomeRow(String id, String label,
        {String? receivedOn, String state = 'expected'}) =>
    {
      'id': id,
      'label': label,
      'amount': '3000.00',
      'currency': 'AED',
      'expected_on': '2026-12-20',
      'received_on': receivedOn,
      'notes': null,
      'state': state,
    };

/// Lent and borrowed with schedules, proposals and expected income.
class LendingFake extends FakePersonal {
  LendingFake() {
    detailJson = detailFor;
  }

  @override
  Future<PersonLoanPage> loans(
      {LoanDirection? direction, required int offset, int limit = 25}) async {
    requests.add('loans ${direction?.name ?? 'all'} $offset');
    final f = failure;
    if (f != null) throw f;
    return PersonLoanPage.fromJson(JsonReader.of(scheduledLoans()));
  }
}

FinanceController lending(LendingFake fake) => FinanceController(FakeFinance(),
    personalRepository: fake, planner: FakePlanner());

Future<void> pumpAt(WidgetTester tester, FinanceController c,
    {Size size = const Size(390, 844),
    Locale locale = const Locale('en')}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(app(c, locale: locale));
  await tester.pumpAndSettle();
}

/// Records each request and answers like the API.
class LendingTransport implements ApiTransport {
  final requests = <ApiRequest>[];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    final Map<String, Object?> body = switch ((request.method, path)) {
      ('GET', '/api/v1/finance/people-loans/fin_ploan_1') =>
        detailFor('fin_ploan_1'),
      ('PATCH', '/api/v1/finance/people-loans/fin_ploan_1') =>
        (scheduledLoans()['items']! as List<Object?>).first!
            as Map<String, Object?>,
      ('GET', '/api/v1/finance/suggestions') => {
          'items': [proposal('1')],
          'total': 1,
          'unavailable': <String>[],
        },
      ('POST', '/api/v1/finance/suggestions/accept') => {
          'key': 'k',
          'status': 'accepted',
          'link_id': 'l_1',
        },
      ('GET', '/api/v1/finance/expected-income') => {
          'items': [incomeRow('fin_xinc_1', 'Annual bonus')],
          'total': 1,
        },
      ('DELETE', _) => <String, Object?>{},
      _ => incomeRow('fin_xinc_1', 'Annual bonus'),
    };
    return ApiResponse(statusCode: 200, body: body);
  }
}

void main() {
  group('models and repository', () {
    test('the catalog matches in English and Arabic', () {
      expect(FinanceLendingStrings.catalogsMatch, isTrue);
    });

    test('a loan reads its schedule, rate and what is overdue', () {
      final page = PersonLoanPage.fromJson(JsonReader.of(scheduledLoans()));
      final sara = page.items.first;
      expect(sara.installments, 14);
      expect(sara.interestRate, '5.0000');
      expect(sara.overdueAmount, '207.50');
      expect(sara.hasOverdue, isTrue);
      expect(sara.nextDueOn, '2026-08-01');
      expect(page.owed.map((o) => (o.currency, o.theyOweMeOverdue)),
          [('AED', '207.50'), ('USD', '0.00')]);
      expect(page.owed.last.iOweOverdue, '500.00');
      final detail =
          PersonLoanDetail.fromJson(JsonReader.of(detailFor('fin_ploan_1')));
      expect(detail.schedule, hasLength(14));
      expect(detail.schedule.first.state, InstallmentState.partRepaid);
      expect(detail.schedule[1].state, InstallmentState.overdue);
    });

    test('a schedule sends its repayments and drops the due date', () {
      expect(
          const PersonLoanInput(
            direction: LoanDirection.lent,
            person: 'Sara Example',
            amount: '1200',
            lentOn: '2026-07-15',
            dueOn: '2026-12-01',
            interestRate: '5',
            installments: 6,
            firstDueOn: '2026-08-01',
            remind: true,
            remindDaysBefore: 3,
            closed: false,
          ).toJson(),
          {
            'direction': 'lent',
            'person': 'Sara Example',
            'amount': '1200',
            'lent_on': '2026-07-15',
            'remind': true,
            'remind_days_before': 3,
            'closed': false,
            'interest_rate': '5',
            'installments': 6,
            'first_due_on': '2026-08-01',
          });
    });

    test('the routes and bodies match the API', () async {
      final transport = LendingTransport();
      final repo = ApiFinancePersonalRepository(AnumApiClient(
          baseUri: Uri.parse('https://anum.test'),
          transport: transport,
          sessions: await signedInSessions()));
      final detail = await repo.loan('fin_ploan_1');
      expect(detail.schedule, hasLength(14));
      await repo.updateLoan(
          'fin_ploan_1',
          const PersonLoanInput(
              direction: LoanDirection.lent,
              person: 'Sara Example',
              amount: '1400',
              lentOn: '2026-07-15',
              remind: false,
              remindDaysBefore: 3,
              closed: false));
      expect(transport.requests.last.body, containsPair('remind', false));
      final queue = await repo.loanSuggestions(offset: 0);
      expect(transport.requests.last.uri.queryParameters,
          {'kind': 'loan_repayment', 'limit': '25', 'offset': '0'});
      expect(queue.items.single.acceptMode, 'link');
      await repo.acceptSuggestion(queue.items.single.key, hash);
      expect(transport.requests.last.body,
          {'key': queue.items.single.key, 'payload_hash': hash});
      final income = await repo.expectedIncome(state: 'all', offset: 0);
      expect(income.items.single.label, 'Annual bonus');
      expect(transport.requests.last.uri.queryParameters['state'], 'all');
      await repo.updateExpectedIncome(
          'fin_xinc_1', income.items.single.toInput(receivedOn: '2026-11-10'));
      expect(transport.requests.last.method, 'PATCH');
      expect(transport.requests.last.body, {
        'label': 'Annual bonus',
        'amount': '3000.00',
        'currency': 'AED',
        'expected_on': '2026-12-20',
        'received_on': '2026-11-10',
        'notes': null,
      });
      await repo.deleteExpectedIncome('fin_xinc_1');
      expect(transport.requests.last.method, 'DELETE');
    });
  });

  group('loans with people', () {
    testWidgets('overdue per person and per currency, never mixed',
        (tester) async {
      final c = lending(LendingFake());
      await pumpAt(tester, c);
      await show(tester, c, FinanceSection.debts);
      expect(find.text('Overdue: 2'), findsOneWidget);
      expect(find.byKey(const Key('owed-overdue-in-AED')), findsOneWidget);
      expect(find.byKey(const Key('owed-overdue-out-USD')), findsOneWidget);
      expect(find.byKey(const Key('owed-overdue-in-USD')), findsNothing);
      expect(find.byKey(const Key('owed-overdue-out-AED')), findsNothing);
      expect(find.text('AED 207.50 overdue'), findsOneWidget);
      expect(find.text('USD 500.00 overdue'), findsOneWidget);
      expect(find.textContaining('14 monthly repayments'), findsOneWidget);
      expect(find.textContaining('5% a year'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('the schedule lists every repayment with its state',
        (tester) async {
      final fake = LendingFake();
      final c = lending(fake);
      await pumpAt(tester, c);
      await show(tester, c, FinanceSection.debts);
      await tapVisible(tester, find.byKey(const Key('loan-open-fin_ploan_1')));
      expect(fake.requests, contains('loan fin_ploan_1'));
      expect(find.byKey(const Key('loan-detail-fin_ploan_1')), findsOneWidget);
      expect(find.text('Interest at 5% a year'), findsOneWidget);
      expect(find.byKey(const Key('loan-detail-overdue')), findsOneWidget);
      expect(find.byKey(const Key('loan-installment-1')), findsOneWidget);
      expect(find.textContaining('Part repaid'), findsOneWidget);
      expect(find.byKey(const Key('loan-installment-13')), findsNothing);
      await tapVisible(tester, find.byKey(const Key('loan-schedule-all')));
      expect(find.byKey(const Key('loan-installment-14')), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('a loan without a schedule says when it is repaid',
        (tester) async {
      final c = lending(LendingFake());
      await pumpAt(tester, c);
      await show(tester, c, FinanceSection.debts);
      await tapVisible(tester, find.byKey(const Key('loan-open-fin_ploan_2')));
      expect(find.text('Interest free'), findsOneWidget);
      expect(find.textContaining('Repaid once on'), findsOneWidget);
    });

    testWidgets('a schedule that cannot be read retries; denied says who',
        (tester) async {
      final fake = LendingFake();
      final c = lending(fake);
      await pumpAt(tester, c);
      await show(tester, c, FinanceSection.debts);
      fake.failure = const ApiException(503, 'The database is not reachable.');
      await tapVisible(tester, find.byKey(const Key('loan-open-fin_ploan_1')));
      expect(
          find.text('The entry could not be loaded: The database is not '
              'reachable.'),
          findsOneWidget);
      fake.failure = null;
      await tapVisible(tester, find.text('Retry'));
      expect(find.byKey(const Key('loan-installment-1')), findsOneWidget);
      await tapVisible(tester, find.text('Close'));
      fake.failure = const ApiException(403, 'Forbidden');
      await tapVisible(tester, find.byKey(const Key('loan-open-fin_ploan_1')));
      expect(find.textContaining('Your role cannot see personal money'),
          findsOneWidget);
    });

    testWidgets('edit sets the schedule and rate, checking them first',
        (tester) async {
      final fake = LendingFake();
      final c = lending(fake);
      await pumpAt(tester, c);
      await show(tester, c, FinanceSection.debts);
      await tapVisible(tester, find.byKey(const Key('loan-edit-fin_ploan_2')));
      expect(find.text('Edit the entry with Omar Example'), findsOneWidget);
      Future<void> submit() async {
        await tester.pumpAndSettle();
        await tapVisible(tester, find.byKey(const Key('form-submit')));
      }

      await tester.enterText(
          find.byKey(const Key('field-interest_rate')), '150');
      await submit();
      expect(find.textContaining('at most 100'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('field-interest_rate')), '5');
      await tester.enterText(find.byKey(const Key('field-installments')), '6');
      await submit();
      expect(find.text('Give the first repayment date for monthly repayments.'),
          findsOneWidget);
      await tester.enterText(
          find.byKey(const Key('field-first_due_on')), '2026-10-01');
      await submit();
      expect(fake.requests,
          contains('update fin_ploan_2 6 5 2026-10-01 false false'));
      expect(find.text('Saved Omar Example.'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('a new entry with interest needs a due date or a schedule',
        (tester) async {
      final fake = LendingFake();
      final c = lending(fake);
      await pumpAt(tester, c);
      await show(tester, c, FinanceSection.debts);
      await tapVisible(tester, find.byKey(const Key('loan-add')));
      await tester.enterText(
          find.byKey(const Key('field-person')), 'Lina Example');
      await tester.enterText(find.byKey(const Key('field-amount')), '900');
      await tester.enterText(find.byKey(const Key('field-interest_rate')), '4');
      await tester.pumpAndSettle();
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(
          find.text('Interest needs a due date or monthly repayments to be '
              'worked out.'),
          findsOneWidget);
      await tester.enterText(
          find.byKey(const Key('field-due_on')), '2027-01-31');
      await tester.pumpAndSettle();
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(fake.requests, contains('create lent Lina Example 900 -'));
    });
  });

  group('repayment suggestions', () {
    testWidgets('accept links through the links API; dismiss drops it',
        (tester) async {
      final fake = LendingFake()..proposals = [proposal('1'), proposal('2')];
      final c = lending(fake);
      await pumpAt(tester, c);
      await show(tester, c, FinanceSection.debts);
      expect(find.text('Possible repayments'), findsOneWidget);
      expect(
          find.text('Sara Example repaying you AED 102.50?'), findsNWidgets(2));
      expect(find.text('85% sure'), findsNWidgets(2));
      const first = 'loan_repayment:fin_tx_1:fin_ploan_1';
      await tapVisible(
          tester, find.byKey(const Key('loan-suggestion-accept-$first')));
      expect(fake.requests, contains('accept $first $hash'));
      expect(find.text('Linked as a repayment.'), findsOneWidget);
      expect(fake.requests.where((r) => r == 'loans all 0'), hasLength(2),
          reason: 'the loans reload after a link');
      const second = 'loan_repayment:fin_tx_2:fin_ploan_1';
      await tapVisible(
          tester, find.byKey(const Key('loan-suggestion-dismiss-$second')));
      expect(fake.requests, contains('dismiss $second'));
      expect(find.byKey(const Key('loan-suggestions-empty')), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('long lists page; failures retry; denied says who',
        (tester) async {
      final fake = LendingFake()
        ..proposals = [for (var i = 0; i < 30; i++) proposal('$i')];
      final c = lending(fake);
      await pumpAt(tester, c);
      await show(tester, c, FinanceSection.debts);
      expect(find.byKey(const Key('loan-suggestions-more')), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('loan-suggestions-more')));
      expect(fake.requests, contains('proposals 25'));
      expect(c.personal!.loanSuggestions, hasLength(30));
      expect(find.byKey(const Key('loan-suggestions-more')), findsNothing);

      fake.suggestionsFailure = const ApiException(503, 'Not reachable.');
      await c.personal!.loadLoanSuggestions();
      await tester.pumpAndSettle();
      expect(
          find.text('Possible repayments could not be loaded: Not '
              'reachable.'),
          findsOneWidget);
      fake.suggestionsFailure = null;
      await tapVisible(
          tester,
          find.descendant(
              of: find.byKey(const Key('loan-suggestions')),
              matching: find.text('Retry')));
      expect(find.byKey(const Key('loan-suggestions-more')), findsOneWidget);

      fake.suggestionsFailure = const ApiException(403, 'Forbidden');
      await c.personal!.loadLoanSuggestions();
      await tester.pumpAndSettle();
      expect(
          find.descendant(
              of: find.byKey(const Key('loan-suggestions')),
              matching: find.textContaining('Your role cannot see')),
          findsOneWidget);
    });
  });

  group('expected one-off income', () {
    testWidgets('empty leads to adding; mark received, edit and delete',
        (tester) async {
      final fake = LendingFake();
      final c = lending(fake);
      await pumpAt(tester, c);
      await show(tester, c, FinanceSection.safe);
      expect(find.byKey(const Key('income-empty')), findsOneWidget);
      await tapVisible(
          tester,
          find.descendant(
              of: find.byKey(const Key('income-empty')),
              matching: find.text('Add expected income')));
      await tester.enterText(
          find.byKey(const Key('field-label')), 'Annual bonus');
      await tester.enterText(find.byKey(const Key('field-amount')), '3000');
      await tester.enterText(
          find.byKey(const Key('field-expected_on')), '2026-12-20');
      await tester.pumpAndSettle();
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(fake.requests, contains('income create Annual bonus 3000'));
      expect(find.text('Saved Annual bonus.'), findsOneWidget);
      expect(find.byKey(const Key('income-fin_xinc_1')), findsOneWidget);

      await tapVisible(
          tester, find.byKey(const Key('income-receive-fin_xinc_1')));
      expect(
          fake.requests,
          contains('income update fin_xinc_1 Annual bonus '
              '${isoDay(DateTime.now())}'));
      expect(find.byKey(const Key('income-fin_xinc_1')), findsNothing,
          reason: 'received income is hidden until asked for');
      await tapVisible(tester, find.byKey(const Key('income-show-received')));
      expect(fake.requests, contains('income all 0'));
      expect(find.text('Received'), findsWidgets);

      await tapVisible(tester, find.byKey(const Key('income-edit-fin_xinc_1')));
      await tester.enterText(find.byKey(const Key('field-label')), 'Bonus');
      await tester.pumpAndSettle();
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(fake.requests.last, startsWith('income all'));
      expect(fake.requests,
          contains(startsWith('income update fin_xinc_1 Bonus')));

      await tapVisible(
          tester, find.byKey(const Key('income-delete-fin_xinc_1')));
      expect(find.text('Delete Bonus?'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('income-delete-confirm')));
      expect(fake.requests, contains('income delete fin_xinc_1'));
      expect(find.text('Deleted Bonus.'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('a bad amount is refused; long lists page', (tester) async {
      final fake = LendingFake()
        ..incomeRows = [
          for (var i = 1; i <= 30; i++)
            incomeRow('fin_xinc_$i', 'Refund $i',
                state: i == 1 ? 'late' : 'expected')
        ];
      final c = lending(fake);
      await pumpAt(tester, c);
      await show(tester, c, FinanceSection.safe);
      expect(find.textContaining('Late, not counted'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('income-more')));
      expect(fake.requests, contains('income open 25'));
      expect(c.personal!.income, hasLength(30));
      await tapVisible(tester, find.byKey(const Key('income-add')));
      await tester.enterText(find.byKey(const Key('field-label')), 'Sale');
      await tester.enterText(find.byKey(const Key('field-amount')), '0');
      await tester.pumpAndSettle();
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(find.text('Enter an amount above zero.'), findsOneWidget);
    });

    testWidgets('a failure retries; denied says who can see it',
        (tester) async {
      final fake = LendingFake()
        ..incomeFailure = const ApiException(503, 'Not reachable.');
      final c = lending(fake);
      await pumpAt(tester, c);
      await show(tester, c, FinanceSection.safe);
      expect(find.text('Expected income could not be loaded: Not reachable.'),
          findsOneWidget);
      fake.incomeFailure = null;
      await tapVisible(
          tester,
          find.descendant(
              of: find.byKey(const Key('expected-income')),
              matching: find.text('Retry')));
      expect(find.byKey(const Key('income-empty')), findsOneWidget);
      fake.incomeFailure = const ApiException(403, 'Forbidden');
      await c.personal!.loadIncome();
      await tester.pumpAndSettle();
      expect(
          find.descendant(
              of: find.byKey(const Key('expected-income')),
              matching: find.textContaining('Your role cannot see')),
          findsOneWidget);
    });
  });

  group('layouts', () {
    for (final size in const [Size(390, 844), Size(768, 1024)]) {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        final label =
            '${size.width.toInt()}x${size.height.toInt()} (${locale.languageCode})';
        testWidgets('loans, schedule, proposals and income at $label',
            (tester) async {
          final fake = LendingFake()
            ..proposals = [proposal('1'), proposal('2', direction: 'borrowed')]
            ..incomeRows = [
              incomeRow('fin_xinc_1', 'Annual bonus'),
              incomeRow('fin_xinc_2', 'Refund for a returned laptop',
                  state: 'late'),
            ];
          final c = lending(fake);
          await pumpAt(tester, c, size: size, locale: locale);
          await show(tester, c, FinanceSection.debts);
          expect(tester.takeException(), isNull);
          final rtl = locale.languageCode == 'ar';
          expect(
              Directionality.of(tester.element(find.byType(PeopleLoansView))),
              rtl ? TextDirection.rtl : TextDirection.ltr);
          await tester.ensureVisible(find.byKey(const Key('loan-suggestions')));
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          await tapVisible(
              tester, find.byKey(const Key('loan-open-fin_ploan_1')));
          await tapVisible(tester, find.byKey(const Key('loan-schedule-all')));
          expect(tester.takeException(), isNull);
          await tapVisible(tester, find.byKey(const Key('loan-detail-edit')));
          expect(find.byKey(const Key('loan-form')), findsOneWidget);
          expect(tester.takeException(), isNull);
          Navigator.of(tester.element(find.byKey(const Key('loan-form'))))
              .pop(false);
          await tester.pumpAndSettle();
          Navigator.of(tester
                  .element(find.byKey(const Key('loan-detail-fin_ploan_1'))))
              .pop();
          await tester.pumpAndSettle();
          await show(tester, c, FinanceSection.safe);
          expect(find.byType(ExpectedIncomeView), findsOneWidget);
          await tester
              .ensureVisible(find.byKey(const Key('income-fin_xinc_2')));
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          await tapVisible(tester, find.byKey(const Key('income-add')));
          expect(find.byKey(const Key('income-form')), findsOneWidget);
          expect(tester.takeException(), isNull);
        });
      }
    }
  });
}
