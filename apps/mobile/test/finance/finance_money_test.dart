// The Money advisor, the Plan, payslips, transfers home, the statement
// keyword rules and the Finance menu that reaches every parity page
// (docs/finance.md#money-advisor, #planning-and-advice, #payslips,
// #sending-money-home, #finance-settings). Against the fake Finance API
// with the API's JSON shapes, in English and Arabic, at 320 to 1024 px and
// 200 percent text. Fixtures are plainly fake.
import 'package:anum_mobile/features/finance/finance_advisor_screens.dart';
import 'package:anum_mobile/features/finance/finance_controller.dart';
import 'package:anum_mobile/features/finance/finance_depth_controller.dart';
import 'package:anum_mobile/features/finance/finance_depth_models.dart';
import 'package:anum_mobile/features/finance/finance_income_screens.dart';
import 'package:anum_mobile/features/finance/finance_keyword_rules.dart';
import 'package:anum_mobile/features/finance/finance_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import '../finance_test.dart' show FakeFinance, app, open;
import '../records/records_harness.dart';
import 'finance_more_fake.dart';

Future<void> confirm(WidgetTester tester) async {
  await tester.tap(find.byKey(const Key('records-confirm')));
  await tester.pumpAndSettle();
}

Future<void> submit(WidgetTester tester) =>
    tapVisible(tester, find.byKey(const Key('form-submit')));

Future<void> enter(WidgetTester tester, String field, String text) async {
  final finder = find.byKey(Key('field-$field'));
  await tester.ensureVisible(finder);
  await tester.enterText(
      find.descendant(of: finder, matching: find.byType(EditableText)).first,
      text);
  await tester.pump();
}

FinanceDepthController depthFor(MoreFinanceApi api) =>
    FinanceDepthController(ApiFinanceDepthRepository(api.client()));

void main() {
  group('Money advisor', () {
    testWidgets('the note: steps, runway, coming payments and cards',
        (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(tester, AdvisorScreen(repository: api.repo()));
      expect(find.byKey(const Key('adv-only')), findsOneWidget);
      expect(find.text('Pay Example card in full before 20 November.'),
          findsOneWidget);
      expect(find.text('121 days, until 2027-03-01'), findsOneWidget);
      expect(find.text('Nothing goes out by these figures'), findsOneWidget);
      await tester.scrollUntilVisible(find.text('Example rent'), 200);
      expect(find.text('No due day set'), findsOneWidget);
      await tester.scrollUntilVisible(find.textContaining('never clears'), 200);
      expect(find.textContaining('the credit limit'), findsOneWidget);
      expect(find.textContaining('No salary found'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('switches, the weekday, work it out now and a past note',
        (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(tester, AdvisorScreen(repository: api.repo()));
      expect(find.text('Next note on 2026-11-15'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('adv-weekday')));
      await tester.tap(find.text('Friday').last);
      await tester.pumpAndSettle();
      expect(api.fake.sent('PUT', '$fin/advisor/settings').single.body,
          {'enabled': true, 'weekday': 4});
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('adv-enabled')));
      expect(api.fake.sent('PUT', '$fin/advisor/settings').last.body,
          {'enabled': false, 'weekday': 6});
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('adv-generate')));
      expect(api.fake.sent('POST', '$fin/advisor/generate'), hasLength(1));
      expect(find.textContaining('worked out again'), findsOneWidget);
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('adv-past-adv_0')));
      expect(find.text('Week of 2026-10-26'), findsWidgets);
    });

    testWidgets('no note yet offers one; Arabic reads the Arabic step',
        (tester) async {
      final api = MoreFinanceApi()..noteMissing = true;
      await pumpModule(tester, AdvisorScreen(repository: api.repo()));
      expect(find.text('No money note yet'), findsOneWidget);
      expect(find.text('No earlier note yet.'), findsOneWidget);
      api.noteMissing = false;
      await pumpModule(tester, AdvisorScreen(repository: api.repo()),
          locale: const Locale('ar'));
      expect(
          find.text('سدد Example card بالكامل قبل 20 نوفمبر.'), findsOneWidget);
      expect(find.textContaining('121 يومًا'), findsOneWidget);
    });

    testWidgets('denied and failed', (tester) async {
      final api = MoreFinanceApi()..deny = true;
      await pumpModule(tester, AdvisorScreen(repository: api.repo()));
      expect(find.byKey(const Key('records-denied')), findsOneWidget);
      api
        ..deny = false
        ..fail = 500;
      await pumpModule(tester, AdvisorScreen(repository: api.repo()));
      expect(find.byKey(const Key('records-error')), findsOneWidget);
    });
  });

  group('the Plan', () {
    testWidgets('can I afford it, then cash against a loan', (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(tester, PlanScreen(repository: api.repo()));
      await tapVisible(tester, find.byKey(const Key('plan-afford')));
      await enter(tester, 'price', '9000');
      await enter(tester, 'currency', 'aedx');
      await submit(tester);
      expect(find.byKey(const Key('form-error')), findsOneWidget);
      await enter(tester, 'currency', 'aed');
      await enter(tester, 'item', 'Example sofa');
      await submit(tester);
      expect(api.fake.sent('POST', '$fin/planner/afford').single.body, {
        'price': '9000',
        'currency': 'AED',
        'item': 'Example sofa',
        'monthly_cost': null,
      });
      expect(find.textContaining('less than three months'), findsOneWidget);
      expect(find.textContaining('Covers the price'), findsOneWidget);
      expect(find.textContaining('late fees, times: 2'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('plan-compare')));
      await enter(tester, 'price', '80000');
      await submit(tester);
      expect(
          find.text(
              'Enter the loan months or the installment months to compare.'),
          findsOneWidget);
      await enter(tester, 'loan_months', '60');
      await enter(tester, 'loan_rate', '3.5');
      await submit(tester);
      final body = api.fake.sent('POST', '$fin/planner/compare').single.body!;
      expect(body['options'], [
        {'kind': 'cash'},
        {
          'kind': 'bank_loan',
          'months': 60,
          'rate_percent': '3.5',
          'basis': 'reducing',
          'down_payment': '0',
        },
      ]);
      expect(find.textContaining('AED 1,450.00 a month'), findsOneWidget);
      expectClean(tester);
    });
  });

  group('payslips', () {
    testWidgets('add one with its deductions, link the salary, delete',
        (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(tester, PayslipsScreen(repository: api.repo()));
      expect(find.text('No salary credit linked'), findsWidgets);
      await tapVisible(tester, find.byKey(const Key('slip-new')));
      await enter(tester, 'employer', 'Example Co');
      await enter(tester, 'month', '2026-13');
      await enter(tester, 'gross', '12000');
      await enter(tester, 'd_social_insurance', '1080');
      await enter(tester, 'd_loan', '0');
      await submit(tester);
      expect(find.textContaining('YYYY-MM'), findsWidgets);
      await enter(tester, 'month', '2026-10');
      await submit(tester);
      final body = api.fake.sent('POST', '$fin/payslips').single.body!;
      expect(body['deductions'], [
        {'kind': 'social_insurance', 'amount': '1080'}
      ]);
      expect(body['month'], '2026-10');
      // The new payslip opens.
      await tapVisible(tester, find.byKey(const Key('slip-link')));
      expect(
          api.fake
              .sent('GET', '$fin/payment-candidates')
              .single
              .uri
              .queryParameters,
          containsPair('for', 'payslip'));
      await tester.tap(find.byKey(const Key('pay-ctx_payslip_1')));
      await tester.pumpAndSettle();
      expect(api.fake.sent('POST', '$fin/payslips/slip_7/links').single.body,
          {'canonical_id': 'ctx_payslip_1'});
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('record-unlink-link_r1')));
      await confirm(tester);
      expect(api.fake.sent('DELETE', '$fin/payslips/slip_7/links/link_r1'),
          hasLength(1));
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('slip-delete')));
      await confirm(tester);
      expect(api.fake.sent('DELETE', '$fin/payslips/slip_7'), hasLength(1));
      expectClean(tester);
    });

    testWidgets('pages, searches and says when nothing matches',
        (tester) async {
      final api = MoreFinanceApi()..many = true;
      await pumpModule(tester, PayslipsScreen(repository: api.repo()));
      await tapVisible(tester, find.byKey(const Key('records-more')));
      expect(api.fake.sent('GET', '$fin/payslips').last.uri.queryParameters,
          containsPair('offset', '20'));
      api
        ..many = false
        ..empty = true;
      await tester.enterText(find.byType(TextField).first, 'zzz');
      await tester.testTextInput.receiveAction(TextInputAction.search);
      await tester.pumpAndSettle();
      expect(find.text('Nothing matches'), findsOneWidget);
    });
  });

  group('send home', () {
    testWidgets('transfers, providers, a new one, link and edit',
        (tester) async {
      final api = MoreFinanceApi();
      await pumpModule(tester, SendHomeScreen(repository: api.repo()));
      expect(find.byKey(const Key('home-providers')), findsOneWidget);
      expect(find.text('Example exchange, AED>INR'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('home-new')));
      await enter(tester, 'sent_on', '2026-11-01');
      await enter(tester, 'amount', '1000');
      await enter(tester, 'received_amount', '22600');
      await enter(tester, 'received_currency', 'inr');
      await enter(tester, 'recipient', 'Example family');
      await enter(tester, 'provider', 'Example exchange');
      await submit(tester);
      final body = api.fake.sent('POST', '$fin/remittances').single.body!;
      expect(body['received_currency'], 'INR');
      expect(body['fee'], '0');
      expect(body['regular'], false);
      await tapVisible(tester, find.byKey(const Key('home-link')));
      await tester.tap(find.byKey(const Key('pay-ctx_remittance_1')));
      await tester.pumpAndSettle();
      expect(
          api.fake.sent('POST', '$fin/remittances/rem_7/links'), hasLength(1));
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('home-edit')));
      await enter(tester, 'fee', '20');
      await submit(tester);
      expect(api.fake.sent('PATCH', '$fin/remittances/rem_7').single.body,
          containsPair('fee', '20'));
      expectClean(tester);
    });

    testWidgets('empty offers the first transfer', (tester) async {
      final api = MoreFinanceApi()..empty = true;
      await pumpModule(tester, SendHomeScreen(repository: api.repo()));
      expect(find.text('No transfer home yet'), findsOneWidget);
      expect(find.byKey(const Key('home-providers')), findsNothing);
    });
  });

  group('statement keyword rules', () {
    testWidgets('add, refuse a duplicate, edit and delete', (tester) async {
      final api = MoreFinanceApi();
      final depth = depthFor(api);
      await pumpModule(tester, KeywordRulesScreen(controller: depth));
      expect(find.text('Contains "example gym"'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('kw-add')));
      await enter(tester, 'match', 'EXAMPLE GYM');
      await submit(tester);
      expect(
          find.text('A rule with these words already exists.'), findsOneWidget);
      await enter(tester, 'match', 'example landlord');
      await tapVisible(tester, find.byKey(const Key('field-category')));
      await tester.tap(find.text('Cash').last);
      await tester.pumpAndSettle();
      await submit(tester);
      final body = api.fake.sent('PUT', '$fin/settings').single.body!;
      expect(body['base_currency'], 'AED');
      expect(body['timezone'], 'Asia/Dubai');
      expect(body['category_rules'], [
        {'match': 'example gym', 'category': 'health'},
        {'match': 'example landlord', 'category': 'cash'},
      ]);
      expect(find.text('Contains "example landlord"'), findsOneWidget);
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('kw-delete-0')));
      await confirm(tester);
      expect(api.rules, [
        {'match': 'example landlord', 'category': 'cash'}
      ]);
      expectClean(tester);
    });

    testWidgets('none yet offers one; denied explains whose they are',
        (tester) async {
      final api = MoreFinanceApi();
      api.rules.clear();
      await pumpModule(tester, KeywordRulesScreen(controller: depthFor(api)));
      expect(find.text('No keyword rule yet'), findsOneWidget);
      api.deny = true;
      await pumpModule(tester, KeywordRulesScreen(controller: depthFor(api)));
      expect(find.byKey(const Key('records-denied')), findsOneWidget);
    });
  });

  testWidgets('a vehicle cost with no record opens Vehicles on the phone',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final finance = FakeFinance()
      ..suggestionItems.add({
        'key': 'attribution:vehicle_record:fin_txn_9',
        'producer': 'attribution',
        'kind': 'attribution',
        'detected': 'This looks like a fuel or parking cost of Example car.',
        'why': 'Record it on the vehicle to track its cost.',
        'params': {
          'dimension': 'vehicle',
          'vehicle': 'Example car',
          'record': 'none'
        },
        'evidence': <Object?>[],
        'confidence': '0.5',
        'effect': <Object?>[],
        'action': {
          'kind': 'none',
          'transaction_ids': <String>[],
          'category': null,
          'subcategory_id': null,
          'transaction_type': null,
        },
        'href': '#vehicles',
        'occurred_on': '2026-11-01',
        'payload_hash': 'cd12' * 16,
      });
    final c = FinanceController(finance);
    var opened = 0;
    await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.dark(),
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        ...GlobalMaterialLocalizations.delegates
      ],
      home: FinanceScreen(controller: c, openVehicles: (_) => opened++),
    ));
    await tester.pumpAndSettle();
    await c.select(FinanceSection.suggestions);
    await tester.pumpAndSettle();
    final button = find
        .byKey(const Key('sug-vehicles-attribution:vehicle_record:fin_txn_9'));
    await tester.dragUntilVisible(
        button, find.byType(Scrollable).first, const Offset(0, -300));
    await tapVisible(tester, button);
    expect(opened, 1);
    expect(find.text('Record it in Vehicles'), findsOneWidget);
  });

  testWidgets('the Finance menu reaches every parity page', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final api = MoreFinanceApi();
    final c = FinanceController(FakeFinance(),
        depthRepository: ApiFinanceDepthRepository(api.client()),
        more: api.repo());
    await tester.pumpWidget(app(c));
    await open(tester);
    for (final page in const [
      'advisor',
      'plan',
      'recon',
      'loans',
      'payslips',
      'home',
      'tags',
      'fx',
      'keywords'
    ]) {
      await tester.tap(find.byKey(const Key('finance-more')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(Key('finance-more-$page')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.byType(BackButton), findsOneWidget, reason: page);
      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
    }
    // Without the parity routes there is no menu.
    await tester.pumpWidget(KeyedSubtree(
        key: UniqueKey(), child: app(FinanceController(FakeFinance()))));
    await open(tester);
    expect(find.byKey(const Key('finance-more')), findsNothing);
  });

  for (final size in const [
    Size(320, 640),
    Size(390, 844),
    Size(768, 1024),
    Size(1024, 768)
  ]) {
    for (final locale in const [Locale('en'), Locale('ar')]) {
      final name = '${size.width.toInt()} (${locale.languageCode})';
      testWidgets('advisor, plan, payslips, send home and rules at $name',
          (tester) async {
        final scale = size.width == 320 ? 2.0 : 1.0;
        final api = MoreFinanceApi()..many = true;
        final screens = <Widget>[
          AdvisorScreen(repository: api.repo()),
          PlanScreen(repository: api.repo()),
          PayslipsScreen(repository: api.repo()),
          PayslipScreen(repository: api.repo(), id: 'slip_1'),
          SendHomeScreen(repository: api.repo()),
          RemittanceScreen(repository: api.repo(), id: 'rem_1'),
          KeywordRulesScreen(controller: depthFor(api)),
        ];
        for (final screen in screens) {
          await pumpModule(tester, screen,
              size: size, locale: locale, textScale: scale);
          expectClean(tester);
        }
      });
    }
  }
}
