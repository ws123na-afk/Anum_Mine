import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/business/invoice_document.dart';
import 'package:anum_mobile/features/business/invoice_models.dart';
import 'package:anum_mobile/src/localization/business_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'business_invoices_test.dart'
    show
        FakeInvoices,
        FakeSaver,
        controllerFor,
        creditJson,
        invoiceJson,
        listItem,
        noticeJson,
        pumpDetail,
        pumpInvoices,
        reveal,
        sized,
        summaryJson;
import 'business_test.dart' show app;

// Credit notes, customers' payment reports and the invoice PDF on the phone
// (docs/business.md#credit-notes, docs/business-portal.md#reporting-a-payment).

void main() {
  test('the credit note and payment report catalogs match in both languages',
      () {
    final placeholders = RegExp(r'\{(\w+)\}');
    final keys = businessEn.keys.where((k) =>
        k.startsWith('biz.inv.credit') ||
        k.startsWith('biz.inv.notice') ||
        k.startsWith('portal.report') ||
        k == 'biz.inv.doc.pdf' ||
        k == 'biz.inv.noticesWaiting');
    expect(keys.length, greaterThan(40));
    for (final key in keys) {
      expect(businessAr, contains(key), reason: key);
      expect(placeholders.allMatches(businessAr[key]!).map((m) => m[1]).toSet(),
          placeholders.allMatches(businessEn[key]!).map((m) => m[1]).toSet(),
          reason: key);
      for (final text in [businessEn[key]!, businessAr[key]!]) {
        expect(text.contains('—') || text.contains('–'), isFalse, reason: key);
      }
    }
  });

  test('credit notes, reports and the credited amount are read from the wire',
      () {
    final invoice = Invoice.fromJson(JsonReader.of(invoiceJson(
      status: 'sent',
      credits: [
        creditJson('bcrn_1', 115),
        creditJson('bcrn_2', 50, status: 'void')
      ],
      notices: [noticeJson('bpnote_1', 300)],
    )));
    expect(invoice.amountCredited, 115);
    expect(invoice.balance, 1035);
    expect(invoice.creditNotes.map((c) => c.number),
        ['INV-2026-0001-CN1', 'INV-2026-0001-CN2']);
    expect(invoice.creditNotes.last.isVoid, isTrue);
    expect(invoice.paymentNotices.single.pending, isTrue);
    expect(invoice.can.credit, isTrue);
    expect(invoice.can.decideNotice, isTrue);
    // An old response without the new fields still reads.
    final old = Invoice.fromJson(JsonReader.of({
      ...invoiceJson(status: 'sent'),
    }..removeWhere((k, _) => k == 'credit_notes' || k == 'payment_notices')));
    expect(old.creditNotes, isEmpty);
    expect(old.paymentNotices, isEmpty);
    final row = InvoiceListItem.fromJson(JsonReader.of({
      ...listItem(0),
      'amount_credited': 100.0,
      'notices_pending': 2,
    }));
    expect(row.amountCredited, 100);
    expect(row.noticesPending, 2);
    final summary = InvoiceSummary.fromJson(
        JsonReader.of({...summaryJson(), 'notices_pending': 3}));
    expect(summary.noticesPending, 3);
  });

  testWidgets(
      'an owner credits part of an invoice and is stopped at the balance',
      (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices()..current = invoiceJson(status: 'sent');
    await pumpDetail(tester, repo);
    expect(find.byKey(const Key('inv-credits')), findsOneWidget);
    expect(find.textContaining('No credit note'), findsOneWidget);
    await reveal(tester, find.byKey(const Key('inv-credit')));
    await tester.tap(find.byKey(const Key('inv-credit')));
    await tester.pumpAndSettle();

    await tester.enterText(find.byKey(const Key('inv-credit-amount')), '9000');
    await tester.enterText(find.byKey(const Key('inv-credit-reason')), 'ab');
    await tester.tap(find.byKey(const Key('inv-credit-submit')));
    await tester.pumpAndSettle();
    expect(find.text('A credit note is at most the balance of SAR 1,150.00.'),
        findsOneWidget);
    expect(find.text('Say why in at least three characters.'), findsOneWidget);
    expect(repo.requests.any((r) => r.startsWith('credit ')), isFalse);

    await tester.enterText(find.byKey(const Key('inv-credit-amount')), '115');
    await tester.enterText(
        find.byKey(const Key('inv-credit-reason')), '  Agreed   discount ');
    await tester.tap(find.byKey(const Key('inv-credit-submit')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('credit binv_1 115.0 Agreed discount'));
    expect(find.text('Credit note issued.'), findsOneWidget);
    expect(find.textContaining('INV-2026-0001-CN1'), findsWidgets);
    // The invoice cannot be voided while a credit note is in force.
    expect(find.byKey(const Key('inv-void')), findsNothing);

    await reveal(tester, find.byKey(const Key('inv-credit-void-bcrn_1')));
    await tester.tap(find.byKey(const Key('inv-credit-void-bcrn_1')));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('inv-reason')), 'Entered twice');
    await tester.tap(find.byKey(const Key('inv-reason-confirm')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('void-credit binv_1 bcrn_1 Entered twice'));
    expect(find.text('Credit note voided.'), findsOneWidget);
  });

  testWidgets('credit that settles an invoice shows it settled by credit',
      (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices()..current = invoiceJson(status: 'sent');
    await pumpDetail(tester, repo);
    await reveal(tester, find.byKey(const Key('inv-credit')));
    await tester.tap(find.byKey(const Key('inv-credit')));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('inv-credit-reason')), 'Waived');
    await tester.tap(find.byKey(const Key('inv-credit-submit')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('credit binv_1 1150.0 Waived'));
    expect(find.text('Settled by credit'), findsOneWidget);
    expect(find.byKey(const Key('inv-credit')), findsNothing);
    expect(find.text('Nothing is owed, so there is nothing left to credit.'),
        findsOneWidget);
    expect(find.byKey(const Key('inv-pay')), findsNothing);
  });

  testWidgets('a member sees credit notes but cannot issue or void one',
      (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices(owner: false)
      ..current = invoiceJson(
          status: 'sent', owner: false, credits: [creditJson('bcrn_1', 100)]);
    await pumpDetail(tester, repo, manage: false);
    expect(find.byKey(const Key('inv-credit-bcrn_1')), findsOneWidget);
    expect(find.byKey(const Key('inv-credit')), findsNothing);
    expect(find.byKey(const Key('inv-credit-void-bcrn_1')), findsNothing);
    expect(find.text('Only an owner can issue or void a credit note.'),
        findsOneWidget);
  });

  testWidgets('a customer report is confirmed into a payment or rejected',
      (tester) async {
    sized(tester, 390, 6000);
    final repo = FakeInvoices();
    repo.notices
      ..add(noticeJson('bpnote_1', 400))
      ..add(noticeJson('bpnote_2', 100));
    repo.current = invoiceJson(status: 'sent', notices: [...repo.notices]);
    await pumpDetail(tester, repo);
    expect(find.text('Payments the customer reported'), findsOneWidget);
    expect(find.textContaining('reported by Sara Khan'), findsWidgets);

    await reveal(tester, find.byKey(const Key('inv-notice-confirm-bpnote_1')));
    await tester.tap(find.byKey(const Key('inv-notice-confirm-bpnote_1')));
    await tester.pumpAndSettle();
    expect(find.textContaining('This records a payment of SAR 400.00'),
        findsOneWidget);
    await tester.tap(find.byKey(const Key('inv-confirm')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('confirm-notice binv_1 bpnote_1'));
    expect(find.text('Payment recorded from the customer\'s report.'),
        findsOneWidget);
    expect(find.text('Partly paid'), findsOneWidget);

    await reveal(tester, find.byKey(const Key('inv-notice-reject-bpnote_2')));
    await tester.tap(find.byKey(const Key('inv-notice-reject-bpnote_2')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('inv-reason')), 'no');
    await tester.tap(find.byKey(const Key('inv-reason-confirm')));
    await tester.pumpAndSettle();
    expect(
        find.text('Give a reason of at least 3 characters.'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('inv-reason')), 'Nothing arrived');
    await tester.tap(find.byKey(const Key('inv-reason-confirm')));
    await tester.pumpAndSettle();
    expect(repo.requests,
        contains('reject-notice binv_1 bpnote_2 Nothing arrived'));
    expect(find.text('Report rejected.'), findsOneWidget);
    expect(find.byKey(const Key('inv-notice-confirm-bpnote_2')), findsNothing);
  });

  testWidgets('the list and the tiles say when reports are waiting',
      (tester) async {
    sized(tester, 390, 3000);
    final quiet = FakeInvoices();
    await pumpInvoices(tester, quiet);
    expect(find.byKey(const Key('inv-notices-waiting')), findsNothing);
    expect(find.textContaining('Reports: '), findsNothing);

    final busy = FakeInvoices()..noticesPending = 2;
    await pumpInvoices(tester, busy);
    expect(find.byKey(const Key('inv-notices-waiting')), findsOneWidget);
    expect(find.text('Customer payment reports waiting for you: 2.'),
        findsOneWidget);
    expect(find.text('Reports: 2'), findsOneWidget);
  });

  testWidgets('the PDF is fetched from the server and saved with its number',
      (tester) async {
    sized(tester, 390, 5000);
    final saver = FakeSaver();
    final repo = FakeInvoices()..current = invoiceJson(status: 'sent');
    await pumpDetail(tester, repo, saver: saver);
    await tester.tap(find.byKey(const Key('inv-preview')));
    await tester.pumpAndSettle();
    await reveal(tester, find.byKey(const Key('inv-doc-pdf')));
    await tester.tap(find.byKey(const Key('inv-doc-pdf')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('pdf binv_1'));
    expect(saver.saved.single.$1, 'INV-2026-0001.pdf');
    expect(saver.saved.single.$2, startsWith('%PDF'));
    expect(find.text('The PDF was saved.'), findsOneWidget);
  });

  testWidgets('a failed PDF says why and the screen keeps working',
      (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices()..current = invoiceJson(status: 'sent');
    await reset(tester);
    await tester.pumpWidget(app(
        InvoicePreviewScreen(
          document: InvoiceDocument.fromInvoice(repo.inv),
          loadHtml: (_) async => '<html></html>',
          loadPdf: () async => throw StateError('The PDF failed.'),
          saver: FakeSaver(),
        ),
        textScale: 1));
    await tester.pumpAndSettle();
    await reveal(tester, find.byKey(const Key('inv-doc-pdf')));
    await tester.tap(find.byKey(const Key('inv-doc-pdf')));
    await tester.pumpAndSettle();
    expect(find.textContaining('The PDF failed.'), findsOneWidget);
    expect(find.byKey(const Key('inv-doc-pdf')), findsOneWidget);
  });

  testWidgets('the document lists the credit notes and the credited amount',
      (tester) async {
    sized(tester, 390, 6000);
    final repo = FakeInvoices();
    final invoice = Invoice.fromJson(JsonReader.of(
        invoiceJson(status: 'sent', credits: [creditJson('bcrn_1', 115)])));
    await tester.pumpWidget(app(
        InvoicePreviewScreen(
          document: InvoiceDocument.fromInvoice(invoice),
          loadHtml: (_) async => '',
          saver: FakeSaver(),
        ),
        textScale: 1));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('inv-doc-pdf')), findsNothing);
    await reveal(tester, find.text('Credit notes').first);
    expect(find.text('Credit notes'), findsWidgets);
    expect(find.textContaining('INV-2026-0001-CN1'), findsOneWidget);
    expect(find.textContaining('Agreed discount'), findsOneWidget);
    expect(controllerFor(repo).canWrite, isTrue);
  });

  for (final width in [320.0, 768.0]) {
    testWidgets(
        'credit notes and reports lay out at $width wide with 200% text',
        (tester) async {
      sized(tester, width, 2600);
      final repo = FakeInvoices()
        ..current = invoiceJson(
            status: 'sent',
            credits: [creditJson('bcrn_1', 115)],
            notices: [noticeJson('bpnote_1', 400)]);
      await pumpDetail(tester, repo, textScale: 2);
      await tester.scrollUntilVisible(
          find.byKey(const Key('inv-notice-confirm-bpnote_1')), 300,
          scrollable: find.byType(Scrollable).first);
      expect(tester.takeException(), isNull);
      await reveal(tester, find.byKey(const Key('inv-credit')));
      await tester.tap(find.byKey(const Key('inv-credit')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('Arabic renders the credit notes and reports right to left',
      (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices()
      ..current = invoiceJson(
          status: 'sent',
          credits: [creditJson('bcrn_1', 115)],
          notices: [noticeJson('bpnote_1', 400)]);
    await pumpDetail(tester, repo, locale: const Locale('ar'));
    await reveal(tester, find.byKey(const Key('inv-credits')));
    expect(find.text('الإشعارات الدائنة'), findsWidgets);
    expect(find.text('الدفعات التي أبلغ عنها العميل'), findsOneWidget);
    expect(find.text('تأكيد الدفعة'), findsWidgets);
    expect(Directionality.of(tester.element(find.text('تأكيد الدفعة').first)),
        TextDirection.rtl);
    expect(tester.takeException(), isNull);
  });
}

Future<void> reset(WidgetTester tester) =>
    tester.pumpWidget(const SizedBox.shrink());
