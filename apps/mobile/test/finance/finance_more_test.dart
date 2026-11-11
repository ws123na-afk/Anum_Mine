// The Finance pages the phone gained for parity with the web
// (docs/finance.md): reconciliation (exceptions across accounts, sessions,
// Edit figures and Delete), bank loans (schedule, pick, undo, terms), one
// payment split across loans, foreign balances, and trips and projects.
// Against the fake Finance API with the API's JSON shapes, in English and
// Arabic, at 320 to 1024 px and 200 percent text. Fixtures are plainly
// fake.
import 'package:anum_mobile/features/finance/finance_loans_screens.dart';
import 'package:anum_mobile/features/finance/finance_money_strings.dart';
import 'package:anum_mobile/features/finance/finance_more_data.dart';
import 'package:anum_mobile/features/finance/finance_more_strings.dart';
import 'package:anum_mobile/features/finance/finance_personal_models.dart';
import 'package:anum_mobile/features/finance/finance_recon_screens.dart';
import 'package:anum_mobile/features/finance/finance_split_screen.dart';
import 'package:anum_mobile/features/finance/finance_tags_screens.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/fake_export_saver.dart';
import '../records/records_harness.dart';
import 'finance_more_fake.dart';

final _dash = RegExp('[–—]');

Future<void> confirm(WidgetTester tester) async {
  await tester.tap(find.byKey(const Key('records-confirm')));
  await tester.pumpAndSettle();
}

Future<void> submit(WidgetTester tester) async {
  await tapVisible(tester, find.byKey(const Key('form-submit')));
}

Future<void> enter(WidgetTester tester, String field, String text) async {
  final finder = find.byKey(Key('field-$field'));
  await tester.ensureVisible(finder);
  await tester.enterText(
      find.descendant(of: finder, matching: find.byType(EditableText)).first,
      text);
  await tester.pump();
}

void main() {
  test('the catalogs match and use no long dashes', () {
    expect(FinanceMoreStrings.catalogsMatch, isTrue);
    expect(FinanceMoneyStrings.catalogsMatch, isTrue);
    for (final text in [
      ...FinanceMoreStrings.allTexts,
      ...FinanceMoneyStrings.allTexts
    ]) {
      expect(text.contains(_dash), isFalse, reason: text);
    }
  });

  test('card and loan figures are typed owed and sent negative', () {
    expect(ledgerFigure('card', '1200.00'), '-1200.00');
    expect(ledgerFigure('card', '-50'), '-50');
    expect(ledgerFigure('bank', '1200.00'), '1200.00');
    expect(typedFigure('loan', '-1200.00'), '1200.00');
    expect(typedFigure('card', '25.00'), '-25.00');
    expect(typedFigure('bank', null), '');
  });

  test('split amounts are compared in cents and due now is chosen', () {
    expect(cents('12.5'), 1250);
    expect(cents('0.05'), 5);
    expect(cents('1.234'), isNull);
    expect(centsText(1050), '10.50');
    final loan = PersonLoan.fromJson(
        JsonReader.of(personLoan('fin_ploan_1', '1000.00')));
    expect(dueNow(loan), '100.00');
  });

  group('reconciliation', () {
    testWidgets('lists what needs you, sorted by the server, and exports it',
        (tester) async {
      final api = MoreFinanceApi()..many = true;
      final saver = FakeExportSaver();
      await pumpModule(
          tester, ReconciliationScreen(repository: api.repo(), saver: saver));
      expect(find.text('Statement row not in your books'), findsWidgets);
      expect(find.textContaining('No statement for 2026-08'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('recon-sort')));
      await tester.tap(find.text('Amount').last);
      await tester.pumpAndSettle();
      expect(
          api.fake
              .sent('GET', '$fin/reconciliations/exceptions')
              .last
              .uri
              .queryParameters['sort'],
          'amount');
      await tapVisible(tester, find.byKey(const Key('recon-sort-direction')));
      final last =
          api.fake.sent('GET', '$fin/reconciliations/exceptions').last.uri;
      expect(last.queryParameters['desc'], 'true');
      await tapVisible(tester, find.byKey(const Key('records-more')).first);
      expect(
          api.fake
              .sent('GET', '$fin/reconciliations/exceptions')
              .last
              .uri
              .queryParameters,
          containsPair('offset', '20'));
      await tapVisible(tester, find.byKey(const Key('recon-export')));
      expect(
          api.fake
              .sent('GET', '$fin/reconciliations/exceptions/export')
              .single
              .uri
              .queryParameters,
          {'sort': 'amount', 'desc': 'true'});
      expect(saver.saved['anum-reconciliation-exceptions.csv'],
          startsWith('account,kind'));
      expectClean(tester);
    });

    testWidgets('sessions filter by state and say each one\'s status',
        (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(tester, ReconciliationScreen(repository: api.repo()));
      expect(find.byKey(const Key('recon-session-rec_1')), findsOneWidget);
      expect(find.textContaining('Changed since completed'), findsOneWidget);
      await tapVisible(tester, find.text('Completed').first);
      expect(
          api.fake.sent('GET', '$fin/reconciliations').last.uri.queryParameters,
          containsPair('state', 'completed'));
    });

    testWidgets('an open card session edits its figures as owed',
        (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(
          tester, ReconSessionScreen(repository: api.repo(), id: 'rec_1'));
      expect(find.text('Card statement check'), findsOneWidget);
      expect(find.textContaining('do not cover the minimum'), findsOneWidget);
      expect(find.text('An unexplained difference of AED -50.00.'),
          findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('recon-edit')));
      // The stored -1200.00 is shown as the 1200.00 owed on the statement.
      expect(find.text('1200.00'), findsWidgets);
      await enter(tester, 'closing', '1250.50');
      await enter(tester, 'minimum', 'x');
      await submit(tester);
      expect(find.byKey(const Key('form-error')), findsOneWidget);
      await enter(tester, 'minimum', '62.50');
      await submit(tester);
      final body =
          api.fake.sent('PATCH', '$fin/reconciliations/rec_1').single.body!;
      expect(body['version'], 3);
      expect(body['statement_closing'], '-1250.50');
      expect(body['statement_opening'], '-900.00');
      expect(body['minimum_due'], '62.50');
      expect(find.text('Figures saved. The equation was worked out again.'),
          findsOneWidget);
      expectClean(tester);
    });

    testWidgets('delete asks first, and a refusal is shown in its words',
        (tester) async {
      final api = MoreFinanceApi()..deleteRefused = true;
      await pumpModule(
          tester, ReconSessionScreen(repository: api.repo(), id: 'rec_1'));
      await tapVisible(tester, find.byKey(const Key('recon-delete')));
      expect(
          find.textContaining('Example card for 2026-09-01'), findsOneWidget);
      await confirm(tester);
      expect(find.textContaining('keeps its history'), findsOneWidget);
      api.deleteRefused = false;
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('recon-delete')));
      await confirm(tester);
      expect(api.fake.sent('DELETE', '$fin/reconciliations/rec_1').length, 2);
    });

    testWidgets('a completed session has no Edit or Delete; issues page',
        (tester) async {
      final api = MoreFinanceApi()..many = true;
      await pumpModule(
          tester, ReconSessionScreen(repository: api.repo(), id: 'rec_2'));
      expect(find.byKey(const Key('recon-edit')), findsNothing);
      expect(find.byKey(const Key('recon-delete')), findsNothing);
      expect(find.textContaining('keeps its history'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('recon-issues-more')));
      expect(
          api.fake
              .sent('GET', '$fin/reconciliations/rec_2')
              .last
              .uri
              .queryParameters['offset'],
          '50');
      expect(
          find.byKey(const Key('recon-detail-issue-exc_60')), findsOneWidget);
    });

    testWidgets('empty, denied and failed say what to do', (tester) async {
      final api = MoreFinanceApi()..empty = true;
      await pumpModule(tester, ReconciliationScreen(repository: api.repo()));
      expect(find.text('Nothing needs you: every open session agrees.'),
          findsOneWidget);
      expect(find.text('No reconciliation yet'), findsOneWidget);
      api.deny = true;
      await pumpModule(tester, ReconciliationScreen(repository: api.repo()));
      expect(find.byKey(const Key('records-denied')), findsNWidgets(2));
      api
        ..deny = false
        ..fail = 503;
      await pumpModule(
          tester, ReconSessionScreen(repository: api.repo(), id: 'rec_1'));
      expect(find.byKey(const Key('records-error')), findsOneWidget);
      api.fail = null;
      await tapVisible(tester, find.text('Try again'));
      expect(find.text('Statement against your books'), findsOneWidget);
    });
  });

  group('bank loans', () {
    testWidgets('the list, the schedule, a pick and an undo', (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(tester, BankLoansScreen(repository: api.repo()));
      expect(find.textContaining('Left AED 27,100.00'), findsOneWidget);
      expect(find.textContaining('Behind schedule, months: 1'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('loan-debt_1')));
      expect(find.byKey(const Key('loan-row-12')), findsOneWidget);
      expect(find.byKey(const Key('loan-row-13')), findsNothing);
      await tapVisible(tester, find.byKey(const Key('loan-all')));
      expect(find.byKey(const Key('loan-row-24')), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('loan-pick-2')));
      await tester.tap(find.byKey(const Key('loan-fit-ctx_b')));
      await tester.pumpAndSettle();
      final pick = api.fake.sent('POST', '$fin/debts/debt_1/payments').single;
      expect(pick.body, {'canonical_id': 'ctx_b'});
      expect(pick.headers['Idempotency-Key'], isNotEmpty);
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('loan-undo-lbatch_1')));
      await confirm(tester);
      expect(api.fake.sent('POST', '$fin/loan-batches/lbatch_1/undo'),
          hasLength(1));
      expect(find.text('Undone'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('editing the terms sends them all; remove asks first',
        (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(
          tester, BankLoanScreen(repository: api.repo(), debtId: 'debt_1'));
      await tapVisible(tester, find.byKey(const Key('loan-edit')));
      await enter(tester, 'term_months', '700');
      await submit(tester);
      expect(find.text('Enter the term as whole months from 1 to 600.'),
          findsOneWidget);
      await enter(tester, 'term_months', '36');
      await submit(tester);
      final body = api.fake.sent('PUT', '$fin/debts/debt_1/loan').single.body!;
      expect(body['term_months'], 36);
      expect(body['repayment'], 'bank_debit');
      expect(body['account_id'], 'acc_1');
      expect(body['rate'], '5.0000');
      expect(body['closed_on'], isNull);
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('loan-remove')));
      await confirm(tester);
      expect(api.fake.sent('DELETE', '$fin/debts/debt_1/loan'), hasLength(1));
    });

    testWidgets('terms are added to a debt that has none', (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(tester, BankLoansScreen(repository: api.repo()));
      await tapVisible(tester, find.byKey(const Key('loan-add')));
      expect(find.byKey(const Key('loan-debt-debt_1')), findsNothing);
      await tester.tap(find.byKey(const Key('loan-debt-debt_2')));
      await tester.pumpAndSettle();
      await enter(tester, 'start_on', '2026-02-01');
      await enter(tester, 'amount', '20000');
      await submit(tester);
      expect(find.text('Enter the term in months or the instalment.'),
          findsOneWidget);
      await enter(tester, 'installment', '950');
      await submit(tester);
      final body = api.fake.sent('PUT', '$fin/debts/debt_2/loan').single.body!;
      expect(body['amount'], '20000');
      expect(body['installment'], '950');
      expect(body['term_months'], isNull);
      expect(body['repayment'], 'manual');
    });

    testWidgets('no loan yet offers to add one; denied says whose it is',
        (tester) async {
      final api = MoreFinanceApi()..empty = true;
      await pumpModule(tester, BankLoansScreen(repository: api.repo()));
      expect(find.text('No loan terms yet'), findsOneWidget);
      api.deny = true;
      await pumpModule(tester, BankLoansScreen(repository: api.repo()));
      expect(find.byKey(const Key('records-denied')), findsOneWidget);
    });
  });

  group('one payment split across loans', () {
    PersonLoan from() => PersonLoan.fromJson(
        JsonReader.of(personLoan('fin_ploan_1', '1000.00')));

    testWidgets('offers the person\'s open loans in its currency and splits',
        (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(
          tester, SplitPaymentScreen(repository: api.repo(), loan: from()));
      expect(
          api.fake.sent('GET', '$fin/people-loans').single.uri.queryParameters,
          containsPair('q', 'Sara Example'));
      // The USD loan and the repaid one are not offered.
      expect(find.byKey(const Key('split-part-fin_ploan_3')), findsNothing);
      expect(find.byKey(const Key('split-part-fin_ploan_4')), findsNothing);
      await tapVisible(tester, find.byKey(const Key('split-pick')));
      expect(
          api.fake
              .sent('GET', '$fin/payment-candidates')
              .single
              .uri
              .queryParameters,
          allOf(
              containsPair('for', 'person_loan'),
              containsPair('direction', 'in'),
              containsPair('currency', 'AED')));
      await tester.tap(find.byKey(const Key('pay-ctx_split')));
      await tester.pumpAndSettle();
      await tester.enterText(
          find.byKey(const Key('split-part-fin_ploan_1')), '250');
      await tester.enterText(
          find.byKey(const Key('split-part-fin_ploan_2')), '100');
      await tapVisible(tester, find.byKey(const Key('split-save')));
      expect(find.text('The parts add up to more than is left of the payment.'),
          findsOneWidget);
      await tester.enterText(
          find.byKey(const Key('split-part-fin_ploan_1')), '200');
      await tapVisible(tester, find.byKey(const Key('split-save')));
      final sent = api.fake.sent('POST', '$fin/people-loans/split').single;
      expect(sent.body, {
        'canonical_id': 'ctx_split',
        'kind': 'repayment',
        'parts': [
          {'loan_id': 'fin_ploan_1', 'amount': '200.00'},
          {'loan_id': 'fin_ploan_2', 'amount': '100.00'},
        ],
      });
      expect(sent.headers['Idempotency-Key'], startsWith('split'));
    });

    testWidgets('a single part, or a part over a loan, is refused first',
        (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(
          tester, SplitPaymentScreen(repository: api.repo(), loan: from()));
      await tapVisible(tester, find.byKey(const Key('split-pick')));
      await tester.tap(find.byKey(const Key('pay-ctx_split')));
      await tester.pumpAndSettle();
      await tester.enterText(
          find.byKey(const Key('split-part-fin_ploan_2')), '');
      await tapVisible(tester, find.byKey(const Key('split-save')));
      expect(
          find.text('Enter an amount for at least two loans.'), findsOneWidget);
      await tester.enterText(
          find.byKey(const Key('split-part-fin_ploan_2')), '450');
      await tapVisible(tester, find.byKey(const Key('split-save')));
      expect(find.textContaining('still expects (AED 400.00)'), findsOneWidget);
      expect(api.fake.sent('POST', '$fin/people-loans/split'), isEmpty);
    });
  });

  group('foreign balances, trips and projects', () {
    testWidgets('foreign balances show worth now, then and what is missing',
        (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(tester, FxBalancesScreen(repository: api.repo()));
      expect(find.text('AED 3,672.50'), findsOneWidget);
      expect(find.textContaining('Rate not available: add a rate for GBP'),
          findsOneWidget);
      expect(find.text('Accounts left out of the total: 1'), findsOneWidget);
      api.empty = true;
      await pumpModule(tester, FxBalancesScreen(repository: api.repo()));
      expect(find.text('No account in another currency'), findsOneWidget);
    });

    testWidgets('a trip is created, tagged, untagged and deleted',
        (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(tester, TagsScreen(repository: api.repo()));
      expect(find.text('Example trip 1'), findsOneWidget);
      await tapVisible(tester, find.text('Trips').first);
      expect(api.fake.sent('GET', '$fin/tags').last.uri.queryParameters,
          containsPair('kind', 'trip'));
      await tapVisible(tester, find.byKey(const Key('tag-new')));
      await enter(tester, 'name', 'Example holiday');
      await enter(tester, 'starts_on', '2026-12-10');
      await enter(tester, 'ends_on', '2026-12-01');
      await submit(tester);
      expect(find.text('The end date cannot be before the start.'),
          findsOneWidget);
      await enter(tester, 'ends_on', '2026-12-20');
      await submit(tester);
      final made = api.fake.sent('POST', '$fin/tags').single.body!;
      expect(made, containsPair('name', 'Example holiday'));
      expect(made, containsPair('kind', 'trip'));
      // The new trip opens.
      expect(find.byKey(const Key('tag-add')), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('tag-add')));
      await tester.tap(find.byKey(const Key('pay-ctx_trip_1')));
      await tester.pumpAndSettle();
      expect(api.fake.sent('POST', '$fin/tags/tag_9/links').single.body,
          {'canonical_id': 'ctx_trip_1'});
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('tag-untag-link_t1')));
      await confirm(tester);
      expect(api.fake.sent('DELETE', '$fin/tags/tag_9/links/link_t1'),
          hasLength(1));
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('tag-delete')));
      await confirm(tester);
      expect(api.fake.sent('DELETE', '$fin/tags/tag_9'), hasLength(1));
      expectClean(tester);
    });

    testWidgets('no trip yet offers one', (tester) async {
      final api = MoreFinanceApi()..empty = true;
      await pumpModule(tester, TagsScreen(repository: api.repo()));
      expect(find.text('No trip or project yet'), findsOneWidget);
    });
  });

  for (final size in const [
    Size(320, 640),
    Size(390, 844),
    Size(768, 1024),
    Size(1024, 768)
  ]) {
    for (final locale in const [Locale('en'), Locale('ar')]) {
      final name = '${size.width.toInt()} (${locale.languageCode})';
      testWidgets('reconciliation, loans and tags lay out at $name',
          (tester) async {
        final scale = size.width == 320 ? 2.0 : 1.0;
        final api = MoreFinanceApi()..many = true;
        final rtl = locale.languageCode == 'ar';
        final screens = <Widget>[
          ReconciliationScreen(repository: api.repo()),
          ReconSessionScreen(repository: api.repo(), id: 'rec_1'),
          BankLoansScreen(repository: api.repo()),
          BankLoanScreen(repository: api.repo(), debtId: 'debt_1'),
          SplitPaymentScreen(
              repository: api.repo(),
              loan: PersonLoan.fromJson(
                  JsonReader.of(personLoan('fin_ploan_1', '1000.00')))),
          FxBalancesScreen(repository: api.repo()),
          TagsScreen(repository: api.repo()),
          TagScreen(repository: api.repo(), id: 'tag_1'),
        ];
        for (final screen in screens) {
          await pumpModule(tester, screen,
              size: size, locale: locale, textScale: scale);
          expectClean(tester);
          expect(Directionality.of(tester.element(find.byType(Scaffold).first)),
              rtl ? TextDirection.rtl : TextDirection.ltr);
        }
      });
    }
  }
}
