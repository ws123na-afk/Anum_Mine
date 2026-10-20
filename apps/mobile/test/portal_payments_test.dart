import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/portal/portal_screens.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'portal_test.dart'
    show FakePortal, FakeSaver, invoiceJson, pumpScreen, reveal, sized;

// Reporting a payment, credit notes and the PDF in the customer portal on the
// phone (docs/business-portal.md#reporting-a-payment). A report changes no
// balance: the business confirms it.

Map<String, Object?> reportable({
  bool canReport = true,
  List<Map<String, Object?>> notices = const [],
  List<Map<String, Object?>> credits = const [],
  double credited = 0,
}) =>
    {
      ...invoiceJson(),
      'amount_credited': credited,
      'balance': 950.0 - credited,
      'can_report_payment': canReport,
      'payment_notices': notices,
      'credit_notes': credits,
    };

Map<String, Object?> notice(String id, String status,
        {String reason = '', double amount = 300}) =>
    {
      'id': id,
      'amount': amount,
      'paid_on': '2026-10-11',
      'method': 'bank_transfer',
      'reference': 'TRX-31',
      'status': status,
      'decision_note': reason,
    };

Future<void> openInvoice(WidgetTester tester, FakePortal repo,
    {Locale locale = const Locale('en'),
    double textScale = 1,
    FakeSaver? saver}) async {
  await pumpScreen(
      tester,
      repo,
      (c) => PortalInvoiceScreen(
          controller: c, invoiceId: 'binv_1', saver: saver ?? FakeSaver()),
      locale: locale,
      textScale: textScale);
}

void main() {
  testWidgets(
      'a customer reports a payment and nothing changes until confirmed',
      (tester) async {
    sized(tester, 390, 6000);
    final repo = FakePortal()..invoiceDoc = reportable();
    await openInvoice(tester, repo);
    expect(find.byKey(const Key('portal-report')), findsOneWidget);
    await reveal(tester, find.byKey(const Key('portal-report-open')));
    await tester.tap(find.byKey(const Key('portal-report-open')));
    await tester.pumpAndSettle();

    // The amount starts at the balance; every rule has its own sentence.
    await tester.tap(find.byKey(const Key('portal-report-submit')));
    await tester.pumpAndSettle();
    expect(find.text('Type your full name so the business knows who paid.'),
        findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('portal-report-name')), '  Sara   Khan ');
    await tester.enterText(
        find.byKey(const Key('portal-report-amount')), '9000');
    await tester.enterText(
        find.byKey(const Key('portal-report-date')), '2999-01-01');
    await tester.tap(find.byKey(const Key('portal-report-submit')));
    await tester.pumpAndSettle();
    expect(find.text('The amount is more than the balance of SAR 950.00.'),
        findsOneWidget);
    expect(find.text('Choose the date you paid (not in the future).'),
        findsOneWidget);
    expect(repo.reportBody, isNull);
    expect(repo.reportKeys, isEmpty);

    await tester.enterText(
        find.byKey(const Key('portal-report-amount')), '300');
    await tester.enterText(
        find.byKey(const Key('portal-report-date')), '2026-10-11');
    await tester.enterText(
        find.byKey(const Key('portal-report-reference')), 'TRX-31');
    await tester.tap(find.byKey(const Key('portal-report-submit')));
    await tester.pumpAndSettle();
    expect(repo.reportBody, {
      'payer_name': 'Sara Khan',
      'amount': 300.0,
      'paid_on': '2026-10-11',
      'method': 'bank_transfer',
      'reference': 'TRX-31',
      'note': '',
    });
    expect(
        find.text(
            'Sent. The invoice stays open until the business sees the money and confirms it.'),
        findsOneWidget);
    expect(find.byKey(const Key('portal-notice-bpnote_1')), findsOneWidget);
    expect(find.text('Waiting for the business'), findsOneWidget);
    // The balance on the page is still what it was.
    expect(find.text('SAR 950.00'), findsWidgets);
  });

  testWidgets('a refusal from the server is shown and the form can be reopened',
      (tester) async {
    sized(tester, 390, 6000);
    final repo = FakePortal()
      ..invoiceDoc = reportable()
      ..reportError = const ApiException(
          409, 'You already reported 5 payments that wait for the business.');
    await openInvoice(tester, repo);
    await reveal(tester, find.byKey(const Key('portal-report-open')));
    await tester.tap(find.byKey(const Key('portal-report-open')));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('portal-report-name')), 'Sara Khan');
    await tester.tap(find.byKey(const Key('portal-report-submit')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('portal-invoice-problem')), findsOneWidget);
    // A retry of the same submit sends the same key, so the business sees one report.
    await tester.tap(find.byKey(const Key('portal-report-open')));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('portal-report-name')), 'Sara Khan');
    await tester.tap(find.byKey(const Key('portal-report-submit')));
    await tester.pumpAndSettle();
    expect(repo.reportKeys, hasLength(2));
    expect(repo.reportKeys.first, repo.reportKeys.last);
    expect(repo.reportKeys.first.length, greaterThanOrEqualTo(8));
    expect(
        find.textContaining('You already reported 5 payments'), findsOneWidget);
    expect(find.byKey(const Key('portal-report-open')), findsOneWidget);
  });

  testWidgets('a read-only link says so instead of offering the form',
      (tester) async {
    sized(tester, 390, 6000);
    final repo = FakePortal(canDecide: false)
      ..invoiceDoc = reportable(canReport: false);
    await openInvoice(tester, repo);
    await reveal(tester, find.byKey(const Key('portal-report')));
    expect(find.byKey(const Key('portal-report-open')), findsNothing);
    expect(
        find.text(
            'This link can read but not act. Tell the business you paid directly.'),
        findsOneWidget);
  });

  testWidgets('a rejected report shows what the business wrote',
      (tester) async {
    sized(tester, 390, 6000);
    final repo = FakePortal()
      ..invoiceDoc = reportable(notices: [
        notice('bpnote_1', 'rejected', reason: 'Nothing arrived on that date')
      ]);
    await openInvoice(tester, repo);
    await reveal(tester, find.byKey(const Key('portal-notice-bpnote_1')));
    expect(find.text('Not accepted'), findsOneWidget);
    expect(find.textContaining('The business wrote: Nothing arrived'),
        findsOneWidget);
    expect(find.byKey(const Key('portal-report-open')), findsOneWidget);
  });

  testWidgets(
      'credit notes lower the balance and an invoice settled by credit has no form',
      (tester) async {
    sized(tester, 390, 6000);
    final repo = FakePortal()
      ..invoiceDoc = {
        ...reportable(canReport: false, credited: 100, credits: [
          {
            'number': 'INV-2026-0001-CN1',
            'issue_date': '2026-10-12',
            'amount': 100.0,
            'reason': 'Agreed discount',
            'status': 'issued',
          }
        ]),
        'status': 'paid',
        'payment_state': 'credited',
        'balance': 0.0,
      };
    await openInvoice(tester, repo);
    await reveal(tester, find.byKey(const Key('portal-credit-notes')));
    expect(find.textContaining('INV-2026-0001-CN1'), findsWidgets);
    expect(find.textContaining('Agreed discount'), findsWidgets);
    expect(find.text('Settled by credit'), findsOneWidget);
    expect(find.byKey(const Key('portal-report-open')), findsNothing);
    expect(find.byKey(const Key('portal-how-to-pay')), findsNothing);
  });

  testWidgets('the PDF is fetched through the link and saved with the number',
      (tester) async {
    sized(tester, 390, 6000);
    final saver = FakeSaver();
    final repo = FakePortal()..invoiceDoc = reportable();
    await openInvoice(tester, repo, saver: saver);
    await tester.tap(find.byKey(const Key('portal-invoice-preview')));
    await tester.pumpAndSettle();
    await reveal(tester, find.byKey(const Key('inv-doc-pdf')));
    await tester.tap(find.byKey(const Key('inv-doc-pdf')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('pdf binv_1'));
    expect(saver.saved.single.$1, 'INV-2026-0001.pdf');
    expect(saver.saved.single.$2, startsWith('%PDF'));
  });

  for (final width in [320.0, 768.0]) {
    testWidgets('the report form lays out at $width wide with 200% text',
        (tester) async {
      sized(tester, width, 2600);
      final repo = FakePortal()
        ..invoiceDoc = reportable(notices: [notice('bpnote_1', 'pending')]);
      await openInvoice(tester, repo, textScale: 2);
      await reveal(tester, find.byKey(const Key('portal-report-open')));
      await tester.tap(find.byKey(const Key('portal-report-open')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.tap(find.byKey(const Key('portal-report-submit')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('Arabic renders the report right to left', (tester) async {
    sized(tester, 390, 6000);
    final repo = FakePortal()..invoiceDoc = reportable();
    await openInvoice(tester, repo, locale: const Locale('ar'));
    await reveal(tester, find.byKey(const Key('portal-report-open')));
    expect(find.text('هل دفعت بالفعل؟'), findsOneWidget);
    expect(find.text('لقد دفعت'), findsOneWidget);
    expect(Directionality.of(tester.element(find.text('لقد دفعت'))),
        TextDirection.rtl);
    await tester.tap(find.byKey(const Key('portal-report-open')));
    await tester.pumpAndSettle();
    expect(find.text('اسمك الكامل'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
