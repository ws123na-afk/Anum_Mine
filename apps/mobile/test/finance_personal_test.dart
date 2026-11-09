// Personal money on the phone (docs/finance.md#lent-and-borrowed,
// #fixed-commitments, #planning-and-advice): the Money planner, Lent and
// borrowed, Fixed costs and the debt payoff card. Fixtures are plainly fake.
import 'dart:async';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/chat/chat_repository.dart';
import 'package:anum_mobile/features/finance/finance_controller.dart';
import 'package:anum_mobile/features/finance/finance_personal_controller.dart';
import 'package:anum_mobile/features/finance/finance_personal_models.dart';
import 'package:anum_mobile/features/finance/finance_personal_repository.dart';
import 'package:anum_mobile/features/finance/finance_personal_strings.dart';
import 'package:anum_mobile/features/finance/finance_personal_view.dart';
import 'package:anum_mobile/features/finance/finance_screen.dart';
import 'package:anum_mobile/features/finance/finance_strings.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'chat/chat_fakes.dart';
import 'finance_test.dart' show FakeFinance;

Map<String, Object?> loansJson({bool empty = false}) => {
      'items': empty
          ? <Object?>[]
          : [
              {
                'id': 'fin_ploan_1',
                'direction': 'lent',
                'person': 'Sara Example',
                'amount': '1000.00',
                'currency': 'AED',
                'lent_on': '2026-09-01',
                'due_on': '2026-10-01',
                'notes': null,
                'remind': true,
                'remind_days_before': 3,
                'closed_on': null,
                'repaid': '600.00',
                'outstanding': '400.00',
                'handed': '1000.00',
                'status': 'overdue',
                'days_until_due': -39,
                'links': 2,
                'created_at': '2026-09-01T10:00:00Z',
                'updated_at': '2026-10-02T10:00:00Z',
              },
              {
                'id': 'fin_ploan_2',
                'direction': 'borrowed',
                'person': 'Omar Example',
                'amount': 500,
                'currency': 'USD',
                'lent_on': '2026-10-20',
                'due_on': null,
                'notes': 'For the trip',
                'repaid': '0.00',
                'outstanding': '500.00',
                'handed': '0.00',
                'status': 'open',
                'days_until_due': null,
                'links': 0,
              },
            ],
      'total': empty ? 0 : 2,
      'limit': 25,
      'offset': 0,
      'owed': empty
          ? <Object?>[]
          : [
              {'currency': 'AED', 'they_owe_me': '400.00', 'i_owe': '0.00'},
              {'currency': 'USD', 'they_owe_me': '0.00', 'i_owe': '500.00'},
            ],
      'overdue': empty ? 0 : 1,
      'note': 'Money lent or borrowed with a person is not spending.',
    };

Map<String, Object?> commitmentsJson({bool empty = false}) => {
      'month': '2026-11',
      'items': empty
          ? <Object?>[]
          : [
              {
                'kind': 'rent',
                'name': 'Flat rent',
                'currency': 'AED',
                'monthly': '6500.00',
                'source': 'bill',
                'ref_id': 'fin_bill_rent',
                'note': null,
              },
              {
                'kind': 'subscription',
                'name': 'Streaming example',
                'currency': 'AED',
                'monthly': '43.33',
                'source': 'bill',
                'ref_id': 'fin_bill_tv',
                'note': 'yearly',
              },
              {
                'kind': 'remittance',
                'name': 'Family example',
                'currency': 'AED',
                'monthly': '1015.00',
                'source': 'remittance',
                'ref_id': 'fin_remit_1',
                'note': 'with_fee',
              },
            ],
      'not_counted': empty
          ? <Object?>[]
          : [
              {
                'kind': 'loan_minimum',
                'name': 'Car loan',
                'currency': 'AED',
                'monthly': '1200.00',
                'source': 'debt',
                'ref_id': 'fin_debt_car',
                'note': 'in_bill',
              },
            ],
      'totals': empty
          ? <Object?>[]
          : [
              {'currency': 'AED', 'amount': '7558.33'}
            ],
      'income': empty
          ? <Object?>[]
          : [
              {
                'currency': 'AED',
                'monthly': '15000.00',
                'source': 'salary_profile',
                'share_percent': 50
              }
            ],
      'note': 'Monthly figures.',
    };

Map<String, Object?> payoffJson({bool empty = false}) => {
      'currencies': empty
          ? <Object?>[]
          : [
              {
                'currency': 'AED',
                'payment': '2500.00',
                'planned_payment': null,
                'below_minimums': false,
                'debts': [
                  {
                    'id': 'fin_debt_card',
                    'name': 'Credit card',
                    'balance': '12000.00',
                    'apr_percent': '36',
                    'minimum': '600.00',
                    'avalanche_month': 6,
                    'snowball_month': 6,
                  },
                  {
                    'id': 'fin_debt_friend',
                    'name': 'Family loan',
                    'balance': '3000.00',
                    'apr_percent': '0',
                    'minimum': '300.00',
                    'avalanche_month': 9,
                    'snowball_month': 2,
                    'source': 'person_loan',
                  },
                ],
                'avalanche': {
                  'strategy': 'avalanche',
                  'months': 9,
                  'total_interest': '1140.55',
                  'order': ['fin_debt_card', 'fin_debt_friend'],
                },
                'snowball': {
                  'strategy': 'snowball',
                  'months': 9,
                  'total_interest': '1400.10',
                  'order': ['fin_debt_friend', 'fin_debt_card'],
                },
                'cheaper': 'avalanche',
                'interest_saved': '259.55',
                'first_win_months': 2,
                'missing_rates': ['Family loan'],
              },
            ],
      'note': 'Advice only.',
    };

const answerText =
    'Advice only. A car of SAR 60,000.00 can come from your savings: bank and '
    'cash SAR 95,000.00 (your data). Live price: https://cars.example/price '
    'read 2026-11-09. Check it with the seller.';

/// `GET /salary/profile`, or none.
Map<String, Object?> salaryProfileJson({bool none = false}) => {
      'profile': none
          ? null
          : {
              'id': 'fin_salary_1',
              'employer': 'Example Trading LLC',
              'joined_on': '2025-01-01',
              'currency': 'AED',
              'expected_gross': '16000.00',
              'pay_day': 27,
              'account_id': 'fin_acct_1',
              'rules': <Object?>[],
              'created_at': '2026-10-01T10:00:00Z',
              'updated_at': '2026-10-01T10:00:00Z',
              'expected_net': '15000.00',
              'next_pay_on': '2026-11-27',
              'advances': <Object?>[],
            },
    };

/// `GET /salary/months`, newest first: this month not due yet, last month
/// found and waiting for a confirm, the month before missing.
Map<String, Object?> salaryMonthsJson({bool decided = false}) => {
      'currency': 'AED',
      'months': [
        {
          'month': '2026-11',
          'currency': 'AED',
          'pay_on': '2026-11-27',
          'status': 'upcoming',
          'flags': <String>[],
          'expected': {
            'gross': '16000.00',
            'deductions': <Object?>[],
            'advance_repayment': '0.00',
            'net': '15000.00',
            'payslip': false
          },
          'received': '0.00',
          'difference': '-15000.00',
          'found': null,
          'basis': null,
          'rejected': 0,
        },
        {
          'month': '2026-10',
          'currency': 'AED',
          'pay_on': '2026-10-27',
          'status': 'received',
          'flags': <String>[],
          'expected': {
            'gross': '16000.00',
            'deductions': <Object?>[],
            'advance_repayment': '0.00',
            'net': '15000.00',
            'payslip': false
          },
          'received': '15000.00',
          'difference': '0.00',
          'found': {
            'transaction_id': 'fin_txn_salary',
            'posted_on': '2026-10-27',
            'amount': '15000.00',
            'description': 'SALARY EXAMPLE TRADING',
            'account_id': 'fin_acct_1',
            'confidence': 'high',
            'score': 90,
          },
          'basis': decided ? 'confirmed' : 'suggested',
          'rejected': 0,
        },
        {
          'month': '2026-09',
          'currency': 'AED',
          'pay_on': '2026-09-27',
          'status': 'missing',
          'flags': ['missing'],
          'expected': {
            'gross': '16000.00',
            'deductions': <Object?>[],
            'advance_repayment': '0.00',
            'net': '15000.00',
            'payslip': false
          },
          'received': '0.00',
          'difference': '-15000.00',
          'found': null,
          'basis': null,
          'rejected': 0,
        },
      ],
      'total': 3,
      'missing': 1,
      'waiting': decided ? 0 : 1,
      'limit': 3,
      'offset': 0,
    };

class FakePersonal implements FinancePersonalRepository {
  FakePersonal({this.failure, this.empty = false});

  /// No salary profile yet.
  bool noSalary = false;
  bool salaryDecided = false;

  /// Every read fails with this (a 403, an offline 0 or a 503).
  Object? failure;
  bool empty;
  final requests = <String>[];

  void _check() {
    final f = failure;
    if (f != null) throw f;
  }

  @override
  Future<PersonLoanPage> loans(
      {LoanDirection? direction, required int offset, int limit = 25}) async {
    requests.add('loans ${direction?.name ?? 'all'} $offset');
    _check();
    final page =
        PersonLoanPage.fromJson(JsonReader.of(loansJson(empty: empty)));
    if (direction == null) return page;
    return PersonLoanPage(
        items: page.items.where((l) => l.direction == direction).toList(),
        total: page.items.where((l) => l.direction == direction).length,
        owed: page.owed,
        overdue: page.overdue);
  }

  @override
  Future<PersonLoan> createLoan(PersonLoanInput input) async {
    final body = input.toJson();
    requests.add('create ${body['direction']} ${body['person']} '
        '${body['amount']} ${body['currency'] ?? '-'}');
    empty = false;
    return PersonLoan.fromJson(
        JsonReader.of((loansJson()['items']! as List<Object?>).first));
  }

  @override
  Future<List<LoanCandidate>> repaymentCandidates(PersonLoan loan,
      {String query = ''}) async {
    requests.add('candidates ${loan.id}');
    return [
      LoanCandidate.fromJson(JsonReader.of({
        'canonical_id': 'fin_tx_9',
        'posted_on': '2026-11-02',
        'merchant': 'TRANSFER FROM SARA EXAMPLE',
        'amount': '400.00',
        'currency': 'AED',
        'remaining': '400.00',
      })),
    ];
  }

  @override
  Future<void> linkRepayment(String loanId,
      {required String canonicalId, required String idempotencyKey}) async {
    requests.add('link $loanId $canonicalId');
  }

  @override
  Future<FixedCommitments> fixedCommitments() async {
    requests.add('fixed');
    _check();
    return FixedCommitments.fromJson(
        JsonReader.of(commitmentsJson(empty: empty)));
  }

  @override
  Future<PayoffPage> payoff() async {
    requests.add('payoff');
    _check();
    return PayoffPage.fromJson(JsonReader.of(payoffJson(empty: empty)));
  }

  @override
  Future<SalaryView> salary({int months = 3}) async {
    requests.add('salary');
    _check();
    final found =
        JsonReader.of(salaryProfileJson(none: noSalary)).optObject('profile');
    if (found == null) return const SalaryView();
    return SalaryView(
        profile: SalaryProfile.fromJson(found),
        months: SalaryMonths.fromJson(
            JsonReader.of(salaryMonthsJson(decided: salaryDecided))));
  }

  @override
  Future<void> decideSalary(String month,
      {required String transactionId, required bool confirm}) async {
    requests
        .add('salary $month $transactionId ${confirm ? 'confirm' : 'reject'}');
    salaryDecided = true;
  }
}

class FakePlanner implements MoneyPlanner {
  FakePlanner({this.failure, this.hold = false});
  Object? failure;
  bool hold;
  final questions = <String>[];
  Completer<PlannerAnswer>? _pending;

  @override
  Future<PlannerAnswer> ask(String question) async {
    questions.add(question);
    if (hold) {
      _pending = Completer<PlannerAnswer>();
      return _pending!.future;
    }
    final f = failure;
    if (f != null) throw f;
    return const PlannerAnswer(text: answerText, conversationId: 'conv_1');
  }

  @override
  void stop() {
    _pending?.completeError(const PlannerFailure('Stopped.', code: 'stopped'));
  }
}

/// Answers the personal money routes like the API does.
class PersonalTransport implements ApiTransport {
  final requests = <ApiRequest>[];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    final body = switch (path) {
      '/api/v1/finance/people-loans' when request.method == 'GET' =>
        loansJson(),
      '/api/v1/finance/people-loans' =>
        (loansJson()['items']! as List<Object?>).first! as Map<String, Object?>,
      '/api/v1/finance/payment-candidates' => {'items': <Object?>[]},
      '/api/v1/finance/fixed-commitments' => commitmentsJson(),
      '/api/v1/finance/planner/payoff' => payoffJson(),
      '/api/v1/finance/salary/profile' => salaryProfileJson(),
      '/api/v1/finance/salary/months' => salaryMonthsJson(),
      _ => {
          'link': {'id': 'link_1'},
          'replayed': false
        },
    };
    return ApiResponse(statusCode: 200, body: body);
  }
}

Widget app(FinanceController controller,
        {Locale locale = const Locale('en'),
        double textScale = 1,
        String webAppUrl = '',
        MoneyLinkOpener? openLink}) =>
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
          openLink: openLink ?? (_) async => true),
    );

FinanceController finance({FakePersonal? personal, MoneyPlanner? planner}) =>
    FinanceController(FakeFinance(),
        personalRepository: personal ?? FakePersonal(),
        planner: planner ?? FakePlanner());

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

void main() {
  group('models', () {
    test('the catalogs match in English and Arabic', () {
      expect(FinancePersonalStrings.catalogsMatch, isTrue);
      expect(FinanceStrings.catalogsMatch, isTrue);
    });

    test('loans read with totals per direction and currency', () {
      final page = PersonLoanPage.fromJson(JsonReader.of(loansJson()));
      expect(page.total, 2);
      expect(page.overdue, 1);
      final lent = page.items.first;
      expect(lent.direction, LoanDirection.lent);
      expect(lent.status, LoanStatus.overdue);
      expect(lent.outstanding, '400.00');
      expect(lent.dueOn, '2026-10-01');
      final borrowed = page.items.last;
      expect(borrowed.direction, LoanDirection.borrowed);
      expect(borrowed.amount, '500', reason: 'a number is kept as text');
      expect(borrowed.dueOn, isNull);
      expect(borrowed.settled, isFalse);
      expect(page.owed.map((o) => (o.currency, o.theyOweMe, o.iOwe)), [
        ('AED', '400.00', '0.00'),
        ('USD', '0.00', '500.00'),
      ]);
    });

    test('a new loan sends only what was given', () {
      expect(
          const PersonLoanInput(
                  direction: LoanDirection.borrowed,
                  person: 'Omar Example',
                  amount: '500',
                  lentOn: '2026-11-01',
                  currency: 'usd',
                  dueOn: '')
              .toJson(),
          {
            'direction': 'borrowed',
            'person': 'Omar Example',
            'amount': '500',
            'currency': 'USD',
            'lent_on': '2026-11-01',
          });
    });

    test('fixed costs and the payoff comparison read', () {
      final fixed = FixedCommitments.fromJson(JsonReader.of(commitmentsJson()));
      expect(fixed.items, hasLength(3));
      expect(fixed.incomeFor('AED')!.percent, 50);
      expect(fixed.incomeFor('USD'), isNull);
      expect(fixed.notCounted.single.note, 'in_bill');
      expect(fixed.totals.single.amount, '7558.33');
      final payoff = PayoffPage.fromJson(JsonReader.of(payoffJson()));
      final aed = payoff.currencies.single;
      expect(aed.cheaper, 'avalanche');
      expect(aed.names(aed.snowball), ['Family loan', 'Credit card']);
      expect(aed.avalanche.months, 9);
      expect(aed.missingRates, ['Family loan']);
      expect(aed.fromPeople, ['Family loan']);
      expect(
          FixedCommitments.fromJson(JsonReader.of(commitmentsJson(empty: true)))
              .isEmpty,
          isTrue);
    });

    test('planner sources are the https addresses, once each', () {
      expect(
          plannerSources('See https://a.example/x. Also http://b.example and '
              'https://a.example/x, then https://c.example/rate?y=1).'),
          [
            Uri.parse('https://a.example/x'),
            Uri.parse('https://c.example/rate?y=1'),
          ]);
      expect(plannerSources('From your own records only.'), isEmpty);
    });
  });

  group('repository and planner', () {
    test('the routes and bodies match the API', () async {
      final transport = PersonalTransport();
      final api = AnumApiClient(
          baseUri: Uri.parse('https://anum.test'),
          transport: transport,
          sessions: await signedInSessions());
      final repo = ApiFinancePersonalRepository(api);
      final page = await repo.loans(direction: LoanDirection.lent, offset: 25);
      expect(transport.requests.last.uri.path, '/api/v1/finance/people-loans');
      expect(transport.requests.last.uri.queryParameters,
          {'direction': 'lent', 'offset': '25', 'limit': '25'});
      await repo.createLoan(const PersonLoanInput(
          direction: LoanDirection.lent,
          person: 'Sara Example',
          amount: '1000',
          lentOn: '2026-09-01'));
      expect(transport.requests.last.method, 'POST');
      expect(transport.requests.last.body, {
        'direction': 'lent',
        'person': 'Sara Example',
        'amount': '1000',
        'lent_on': '2026-09-01',
      });
      await repo.repaymentCandidates(page.items.first, query: 'sara');
      expect(transport.requests.last.uri.queryParameters, {
        'for': 'person_loan',
        'direction': 'in',
        'currency': 'AED',
        'amount': '400.00',
        'q': 'sara',
      });
      await repo.linkRepayment('fin_ploan_1',
          canonicalId: 'fin_tx_9', idempotencyKey: 'pay-example');
      expect(transport.requests.last.uri.path,
          '/api/v1/finance/people-loans/fin_ploan_1/links');
      expect(transport.requests.last.body,
          {'canonical_id': 'fin_tx_9', 'kind': 'repayment'});
      expect(transport.requests.last.headers['Idempotency-Key'], 'pay-example');
      expect((await repo.fixedCommitments()).items, hasLength(3));
      expect((await repo.payoff()).currencies.single.currency, 'AED');
      expect(
          transport.requests.last.uri.path, '/api/v1/finance/planner/payoff');
      final salary = await repo.salary();
      expect(salary.profile!.employer, 'Example Trading LLC');
      expect(salary.current!.status, 'upcoming');
      expect(salary.waiting.single.month, '2026-10');
      expect(transport.requests.last.uri.path, '/api/v1/finance/salary/months');
      expect(transport.requests.last.uri.queryParameters, {'limit': '3'});
      await repo.decideSalary('2026-10',
          transactionId: 'fin_txn_salary', confirm: false);
      expect(transport.requests.last.method, 'POST');
      expect(transport.requests.last.uri.path,
          '/api/v1/finance/salary/months/2026-10/decision');
      expect(transport.requests.last.body,
          {'transaction_id': 'fin_txn_salary', 'decision': 'reject'});
    });

    test('the planner asks the Assistant and reads the streamed answer',
        () async {
      final conversations = FakeConversationsApi();
      final events = ScriptedEvents();
      final api = AnumApiClient(
          baseUri: Uri.parse('https://anum.test'),
          transport: conversations,
          sessions: await signedInSessions());
      final planner = ChatMoneyPlanner(ChatRepository(api, events));
      final answer = planner.ask('Can I afford a car of 60,000 SAR?');
      await pumpEventQueue();
      expect(events.calls.single.$1, '/api/v1/conversations/conv_new_1/ask');
      expect(events.calls.single.$2,
          {'content': 'Can I afford a car of 60,000 SAR?'});
      events.emit('delta', {'text': 'Advice only. '});
      events.emit('delta', {'text': 'Yes, from savings.'});
      events.emit('done', {
        'assistant_turn': turnJson('turn_2', 'assistant',
            'Advice only. Yes, from savings. https://cars.example/p'),
      });
      await events.end();
      final result = await answer;
      expect(result.text,
          'Advice only. Yes, from savings. https://cars.example/p');
      expect(result.conversationId, 'conv_new_1');
      expect(result.sources, [Uri.parse('https://cars.example/p')]);

      final failing = planner.ask('Which debt first?');
      await pumpEventQueue();
      events.emit('error', {
        'code': 'model_budget_exceeded',
        'message': 'The monthly model budget is used up.',
      });
      await events.end();
      await expectLater(
          failing,
          throwsA(isA<PlannerFailure>()
              .having((f) => f.code, 'code', 'model_budget_exceeded')));

      final stopped = planner.ask('Which loan is better?');
      await pumpEventQueue();
      planner.stop();
      await expectLater(
          stopped,
          throwsA(
              isA<PlannerFailure>().having((f) => f.code, 'code', 'stopped')));
      // The two answered streams ended; the third was stopped.
      expect(events.cancels, 3);
    });
  });

  group('controller', () {
    test('a 403 is denied and no connection is offline', () async {
      final denied = FinancePersonalController(
          FakePersonal(failure: const ApiException(403, 'Forbidden')));
      await denied.loadLoans();
      expect(denied.loansState, MoneyLoad.denied);
      final offline = FinancePersonalController(FakePersonal(
          failure: const ApiException(0, 'The server could not be reached.')));
      await offline.loadCommitments();
      expect(offline.commitmentsState, MoneyLoad.offline);
      final failing = FinancePersonalController(
          FakePersonal(failure: const ApiException(503, 'Database down')));
      await failing.loadPayoff();
      expect(failing.payoffState, MoneyLoad.error);
      expect(failing.payoffError, 'Database down');
    });

    test(
        'the planner keeps the question and its answer; an empty one is not sent',
        () async {
      final planner = FakePlanner();
      final c = FinancePersonalController(FakePersonal(), planner: planner);
      await c.ask('   ');
      expect(planner.questions, isEmpty);
      await c.ask(' Can I afford it? ');
      expect(c.question, 'Can I afford it?');
      expect(c.plannerState, MoneyLoad.ready);
      expect(c.answer!.sources, [Uri.parse('https://cars.example/price')]);
      c.reset();
      expect(c.answer, isNull);
      expect(c.plannerState, MoneyLoad.idle);
    });
  });

  group('screens', () {
    testWidgets('the planner answers an example with its source and advice',
        (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final opened = <Uri>[];
      final planner = FakePlanner();
      final c = finance(planner: planner);
      await tester.pumpWidget(app(c, openLink: (uri) async {
        opened.add(uri);
        return true;
      }));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.planner);
      expect(find.text('Ask your first question'), findsOneWidget);
      expect(
          find.text('Advice only: ANUM never pays, buys or transfers money '
              'for you.'),
          findsOneWidget);
      await tapVisible(tester,
          find.widgetWithText(ActionChip, 'Can I afford a car of 60,000 SAR?'));
      expect(planner.questions, ['Can I afford a car of 60,000 SAR?']);
      expect(find.byKey(const Key('planner-answer')), findsOneWidget);
      expect(find.text('Advice only'), findsOneWidget);
      expect(find.text('Live sources'), findsOneWidget);
      await tapVisible(tester, find.text('cars.example'));
      expect(opened, [Uri.parse('https://cars.example/price')]);
      expect(tester.takeException(), isNull);
    });

    testWidgets('a typed question is sent; a failure says why and retries',
        (tester) async {
      final planner = FakePlanner(
          failure: const PlannerFailure('The model did not answer.'));
      final c = finance(planner: planner);
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.planner);
      await tester.enterText(
          find.byKey(const Key('planner-question')), 'Which debt first?');
      await tapVisible(tester, find.byKey(const Key('planner-send')));
      expect(find.textContaining('The planner did not answer'), findsOneWidget);
      planner.failure = null;
      await tapVisible(tester, find.text('Retry'));
      expect(planner.questions, ['Which debt first?', 'Which debt first?']);
      expect(find.byKey(const Key('planner-answer')), findsOneWidget);
    });

    testWidgets('the planner shows working, stops, and says who may ask',
        (tester) async {
      final planner = FakePlanner(hold: true);
      final c = finance(planner: planner);
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.planner);
      await tester.enterText(
          find.byKey(const Key('planner-question')), 'Can I afford it?');
      await tester.ensureVisible(find.byKey(const Key('planner-send')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('planner-send')));
      await tester.pump();
      expect(find.text('Working it out from your records'), findsOneWidget);
      await tester.ensureVisible(find.text('Stop'));
      await tester.pump();
      await tester.tap(find.text('Stop'));
      await tester.pumpAndSettle();
      expect(find.text('Ask your first question'), findsOneWidget);

      planner
        ..hold = false
        ..failure = const ApiException(403, 'Forbidden');
      await tapVisible(tester, find.byKey(const Key('planner-send')));
      expect(find.textContaining('Your role cannot use the assistant'),
          findsOneWidget);
      planner.failure = const ApiException(0, 'No connection');
      await tapVisible(tester, find.byKey(const Key('planner-send')));
      expect(find.textContaining('You are offline'), findsOneWidget);
    });

    testWidgets('lent and borrowed: totals, overdue, add and a repayment',
        (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final repo = FakePersonal();
      final c = finance(personal: repo);
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.debts);
      expect(find.text('What I owe and what I am owed'), findsOneWidget);
      expect(find.text('Overdue: 1'), findsOneWidget);
      expect(find.text('AED 400.00'), findsOneWidget);
      expect(find.text('USD 500.00'), findsOneWidget);
      expect(find.text('Sara Example'), findsOneWidget);
      expect(find.text('Left AED 400.00 of AED 1,000.00'), findsOneWidget);
      expect(find.text('Overdue'), findsOneWidget);

      await tapVisible(tester, find.widgetWithText(ChoiceChip, 'I owe'));
      expect(repo.requests, contains('loans borrowed 0'));
      expect(find.text('Sara Example'), findsNothing);
      expect(find.text('Omar Example'), findsOneWidget);
      await tapVisible(tester, find.widgetWithText(ChoiceChip, 'All'));

      await tapVisible(tester, find.byKey(const Key('loan-add')));
      await tester.enterText(
          find.byKey(const Key('field-person')), 'Lina Example');
      await tester.enterText(find.byKey(const Key('field-amount')), '0');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(find.text('Enter an amount above zero.'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('field-amount')), '250');
      await tester.enterText(find.byKey(const Key('field-currency')), 'aed');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(repo.requests, contains('create lent Lina Example 250 AED'));
      expect(find.text('Saved.'), findsOneWidget);

      await tapVisible(tester, find.byKey(const Key('loan-repay-fin_ploan_1')));
      expect(find.text('Repayment with Sara Example'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('candidate-fin_tx_9')));
      expect(repo.requests, contains('link fin_ploan_1 fin_tx_9'));
      expect(find.text('Repayment recorded.'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('lent and borrowed: empty leads to adding, errors retry',
        (tester) async {
      final repo = FakePersonal(empty: true);
      final c = finance(personal: repo);
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.debts);
      expect(find.text('Nothing lent or borrowed yet'), findsOneWidget);
      expect(find.text('Add an entry'), findsWidgets);

      repo.failure = const ApiException(503, 'The database is not reachable.');
      await c.personal!.loadLoans();
      await tester.pumpAndSettle();
      expect(
          find.text('Lent and borrowed could not be loaded: The database is '
              'not reachable.'),
          findsOneWidget);
      repo.failure = null;
      await tapVisible(tester, find.text('Retry'));
      expect(find.text('Nothing lent or borrowed yet'), findsOneWidget);

      repo.failure = const ApiException(403, 'Forbidden');
      await c.personal!.loadLoans();
      await tester.pumpAndSettle();
      expect(find.textContaining('Your role cannot see personal money'),
          findsOneWidget);
    });

    testWidgets(
        'fixed costs: totals per currency, the list and what is left out',
        (tester) async {
      final c = finance();
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.bills);
      expect(find.text('Fixed monthly costs'), findsOneWidget);
      expect(find.text('Total in AED'), findsOneWidget);
      expect(find.text('AED 7,558.33'), findsOneWidget);
      expect(find.text('Flat rent'), findsOneWidget);
      expect(find.text('Subscription · yearly'), findsOneWidget);
      expect(find.text('Transfer home · fee included'), findsOneWidget);
      expect(find.text('Left out'), findsOneWidget);
      expect(find.text('Loan minimum · counted as the bill'), findsOneWidget);
    });

    testWidgets('fixed costs: empty sits under the bills and their Add bill',
        (tester) async {
      final c = finance(personal: FakePersonal(empty: true));
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.bills);
      expect(find.text('No fixed monthly costs yet'), findsOneWidget);
      expect(find.textContaining('with Add bill above'), findsOneWidget);
      expect(find.text('Rent'), findsWidgets);
      expect(find.widgetWithText(FilledButton, 'Add bill'), findsOneWidget);
    });

    testWidgets('fixed costs show their share of income on Bills',
        (tester) async {
      final c = finance();
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.bills);
      expect(find.text('Rent'), findsWidgets);
      expect(
          find.text('Fixed costs take 50% of your income '
              '(AED 15,000.00 a month).'),
          findsOneWidget);
      expect(find.text('Income from your salary profile.'), findsOneWidget);
    });

    testWidgets('debts list people below the payoff, with the borrowed ones',
        (tester) async {
      final c = finance();
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.debts);
      expect(find.text('People'), findsOneWidget);
      expect(find.text('Includes money borrowed from: Family loan'),
          findsOneWidget);
      expect(find.text('Sara Example'), findsOneWidget);
      final payoff = tester.getTopLeft(find.text('Which debt to pay first'));
      final people = tester.getTopLeft(find.text('People'));
      expect(payoff.dy, lessThan(people.dy));
    });

    testWidgets('salary: expected against received, missing, and a confirm',
        (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final repo = FakePersonal();
      final c = finance(personal: repo);
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.safe);
      expect(find.text('Example Trading LLC · paid on day 27'), findsOneWidget);
      expect(find.text('Month 2026-11'), findsOneWidget);
      expect(find.text('Not due yet'), findsOneWidget);
      expect(find.text('Months with no salary found: 1'), findsOneWidget);
      expect(find.text('Is this your salary for 2026-10?'), findsOneWidget);
      expect(find.text('SALARY EXAMPLE TRADING'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('salary-confirm-2026-10')));
      expect(repo.requests, contains('salary 2026-10 fin_txn_salary confirm'));
      expect(find.text('Confirmed as your salary.'), findsOneWidget);
      expect(find.text('Is this your salary for 2026-10?'), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('salary: no profile says where to add it; errors retry',
        (tester) async {
      final repo = FakePersonal()..noSalary = true;
      final opened = <Uri>[];
      final c = finance(personal: repo);
      await tester.pumpWidget(
          app(c, webAppUrl: 'https://app.example/', openLink: (uri) async {
        opened.add(uri);
        return true;
      }));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.safe);
      expect(find.text('No salary profile yet'), findsOneWidget);
      await tapVisible(
          tester,
          find.descendant(
              of: find.byType(SalaryCard), matching: find.byType(TextButton)));
      expect(opened.first.fragment, 'finance-advisor-salary');

      repo.failure = const ApiException(503, 'The database is not reachable.');
      await c.personal!.loadSalary();
      await tester.pumpAndSettle();
      expect(
          find.text('The salary check could not be loaded: The database is '
              'not reachable.'),
          findsOneWidget);
      repo.failure = const ApiException(403, 'Forbidden');
      await c.personal!.loadSalary();
      await tester.pumpAndSettle();
      expect(find.textContaining('Your role cannot see personal money'),
          findsOneWidget);
    });

    testWidgets('debts show avalanche against snowball above the list',
        (tester) async {
      final c = finance();
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.debts);
      expect(find.text('Which debt to pay first'), findsOneWidget);
      expect(find.text('Paying AED 2,500.00 a month'), findsOneWidget);
      expect(find.text('Avalanche'), findsOneWidget);
      expect(find.text('Snowball'), findsOneWidget);
      expect(find.text('9 months'), findsNWidgets(2));
      expect(find.text('AED 1,140.55'), findsOneWidget);
      expect(find.text('Order: Family loan, Credit card'), findsOneWidget);
      expect(
          find.text('Avalanche saves AED 259.55 in interest.'), findsOneWidget);
      expect(find.textContaining('No rate entered for: Family loan'),
          findsOneWidget);
    });

    testWidgets('debts with nothing to compare say where to add them',
        (tester) async {
      final c = finance(personal: FakePersonal(empty: true));
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.debts);
      expect(find.textContaining('No debts to compare'), findsOneWidget);
    });

    testWidgets(
        'Payslips and Send home link to the web when the address is known',
        (tester) async {
      final opened = <Uri>[];
      final c = finance();
      await tester.pumpWidget(
          app(c, webAppUrl: 'https://app.example/', openLink: (uri) async {
        opened.add(uri);
        return true;
      }));
      await tester.pumpAndSettle();
      await tapVisible(
          tester,
          find.text('Payslips, the salary profile and Send home are on the '
              'web: open'));
      expect(
          opened, [Uri.parse('https://app.example/#finance-advisor-salary')]);
      expect(financeWebLink('', 'finance-payslips'), isNull);
      expect(financeWebLink('not a url', 'finance-payslips'), isNull);
    });

    testWidgets('without personal money the sections are not offered',
        (tester) async {
      final c = FinanceController(FakeFinance());
      await tester.pumpWidget(app(c));
      await tester.pumpAndSettle();
      expect(find.widgetWithText(ChoiceChip, 'Money planner'), findsNothing);
      expect(
          find.text('Payslips, the salary profile and Send home are on '
              'the web.'),
          findsNothing);
    });

    testWidgets('Arabic reads right to left in the planner and the loans',
        (tester) async {
      final c = finance();
      await tester.pumpWidget(app(c, locale: const Locale('ar')));
      await tester.pumpAndSettle();
      await show(tester, c, FinanceSection.planner);
      expect(Directionality.of(tester.element(find.byType(MoneyPlannerView))),
          TextDirection.rtl);
      expect(find.text('مخطط المال'), findsWidgets);
      await tapVisible(
          tester, find.widgetWithText(ActionChip, 'أي دين أسدد أولا؟'));
      expect(find.text('نصيحة فقط'), findsOneWidget);
      await show(tester, c, FinanceSection.debts);
      expect(Directionality.of(tester.element(find.byType(PeopleLoansView))),
          TextDirection.rtl);
      expect(find.text('ما عليّ وما لي'), findsOneWidget);
      expect(find.text('متأخر: 1'), findsOneWidget);
      await show(tester, c, FinanceSection.bills);
      expect(find.text('التكاليف الشهرية الثابتة'), findsOneWidget);
      await show(tester, c, FinanceSection.debts);
      expect(find.text('أي دين أسدد أولًا'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    for (final (width, height) in [
      (360.0, 640.0),
      (390.0, 844.0),
      (768.0, 1024.0)
    ]) {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        testWidgets(
            'personal money lays out at ${width.toInt()}x${height.toInt()} '
            'with 200% text (${locale.languageCode})', (tester) async {
          tester.view.physicalSize = Size(width, height);
          tester.view.devicePixelRatio = 1;
          addTearDown(tester.view.reset);
          final c = finance();
          await tester.pumpWidget(app(c, locale: locale, textScale: 2));
          await tester.pumpAndSettle();
          for (final section in const [
            FinanceSection.planner,
            FinanceSection.safe,
            FinanceSection.bills,
            FinanceSection.debts,
          ]) {
            await show(tester, c, section);
            expect(tester.takeException(), isNull, reason: section.name);
          }
          await show(tester, c, FinanceSection.planner);
          await tapVisible(tester, find.byType(ActionChip).first);
          expect(tester.takeException(), isNull);
          expect(find.byKey(const Key('planner-answer')), findsOneWidget);
        });
      }
    }
  });
}
