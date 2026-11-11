// The Finance tools the phone gained in the November 2026 pass
// (docs/finance.md, docs/finance-sms.md, docs/finance-statements.md):
// resolving, completing and reopening a reconciliation (the correcting entry
// with its payload hash), iPhone SMS review and the ledger, statement import
// (CSV and PDF with the preview), owner rules with payload-hash approval,
// Apply and Undo, merchant spellings, subcategories and automation. Against
// the fake Finance API with the API's JSON shapes, in English and Arabic, at
// 320 to 1024 px and 200 percent text. Fixtures are plainly fake.
import 'dart:convert';
import 'dart:typed_data';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/finance/finance_import_screens.dart';
import 'package:anum_mobile/features/finance/finance_models.dart';
import 'package:anum_mobile/features/finance/finance_more_data.dart';
import 'package:anum_mobile/features/finance/finance_recon_actions.dart';
import 'package:anum_mobile/features/finance/finance_recon_screens.dart';
import 'package:anum_mobile/features/finance/finance_rules_screens.dart';
import 'package:anum_mobile/features/finance/finance_sms_screens.dart';
import 'package:anum_mobile/features/finance/finance_tools_data.dart';
import 'package:anum_mobile/features/finance/finance_tools_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';
import 'finance_tools_fake.dart';

final _dash = RegExp('[–—]');

Future<void> tap(WidgetTester tester, Finder finder) =>
    tapVisible(tester, finder);

Future<void> typeInto(WidgetTester tester, Key key, String text) async {
  final finder = find.byKey(key);
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.enterText(
      find.descendant(of: finder, matching: find.byType(EditableText)).first,
      text);
  await tester.pump();
}

Future<void> formField(WidgetTester tester, String name, String text) =>
    typeInto(tester, Key('field-$name'), text);

Future<void> submitForm(WidgetTester tester) =>
    tapVisible(tester, find.byKey(const Key('form-submit')));

Map<String, Object?> lastBody(ToolsApi api, String method, String path) =>
    Map.of(api.sent(method, path).last.body!);

void main() {
  test('the catalog matches and uses no long dashes', () {
    expect(FinanceToolsStrings.catalogsMatch, isTrue);
    for (final text in FinanceToolsStrings.allTexts) {
      expect(text.contains(_dash), isFalse, reason: text);
    }
  });

  group('helpers', () {
    test('match candidates have the same amount and the other side', () {
      ReconIssue issue(Map<String, Object?> json) =>
          ReconIssue.fromJson(JsonReader.of(json));
      final row = issue(exception('a', 'not_in_ledger', statementRef: 'r1'));
      final all = [
        row,
        issue(exception('b', 'unmatched_payment', transactionId: 't1')),
        issue(exception('c', 'unmatched_payment',
            transactionId: 't2', amount: '-99.00')),
        issue(exception('d', 'possible_duplicate', transactionId: 't3')),
      ];
      expect(reconMatchCandidates(row, all).map((e) => e.id), ['b']);
      expect(reconMatchCandidates(all[1], all).map((e) => e.id), ['a']);
      expect(sameAmount('-120', '-120.00'), isTrue);
      expect(sameAmount(null, '1'), isFalse);
    });

    test('the last month and moving months', () {
      expect(lastMonth(DateTime(2026, 1, 15)), ('2025-12-01', '2025-12-31'));
      expect(lastMonth(DateTime(2026, 3, 31)), ('2026-02-01', '2026-02-28'));
      expect(shiftMonth('2026-01', -1), '2025-12');
      expect(shiftMonth('2026-12', 1), '2027-01');
    });

    test('the PDF check, roles and import body follow the web', () {
      final view = PdfStatement.fromJson(JsonReader.of(ToolsApi.pdfView()));
      expect(liveCheck(view.rows, '100.00', '50.00').status, 'unknown',
          reason: 'a guessed sign keeps the check unknown');
      view.rows[1].flags.clear();
      expect(liveCheck(view.rows, '100.00', '50.00').ties, isTrue);
      view.rows[1].include = false;
      final off = liveCheck(view.rows, '100.00', '50.00');
      expect(off.differs, isTrue);
      expect(off.difference, '-20.00');
      expect(checkRoles(['date', 'description', 'amount']), isNull);
      expect(checkRoles(['date', 'description', 'skip']), 'pdf.roles.money');
      expect(checkRoles(['description', 'amount']), 'pdf.roles.date');
      final body = view.importBody(
          accountId: 'acc_1',
          opening: '100.00',
          closing: '50.00',
          acceptMismatch: true,
          roles: ['date', 'description', 'amount'],
          remember: true);
      final rows = (body['rows']! as List).cast<Map<String, Object?>>();
      expect(rows, hasLength(2),
          reason: 'an unticked row is sent, not imported');
      expect(rows[1]['include'], isFalse);
      expect(rows[1]['action'], 'skip');
      expect(body['accept_mismatch'], isTrue);
      expect((body['layout']! as Map)['remember'], isTrue);
      expect(body['summary'], hasLength(1));
      expect(body['dropped'], hasLength(1));
      expect(body['period_start'], '2026-10-01');
    });

    test('a row the reader would skip is imported when ticked', () {
      final json = ToolsApi.pdfView();
      final row = Map<String, Object?>.of(
          (json['rows']! as List).first as Map<String, Object?>)
        ..['match'] = {
          'status': 'exact_duplicate',
          'reason': 'Imported before',
          'default_import': false,
        };
      final parsed = PdfRow.fromJson(JsonReader.of(row));
      expect(parsed.include, isFalse);
      parsed.include = true;
      expect(parsed.wire()['action'], 'import');
      expect((RowChoice()..import = false).wire(), {'action': 'skip'});
      final foreign = PdfRow.fromJson(JsonReader.of({
        ...(json['rows']! as List).first as Map<String, Object?>,
        'currency': 'USD',
        'flags': ['foreign_currency'],
      }));
      expect(foreign.include, isFalse);
      expect(foreign.problemIn('AED'), 'problem.foreign');
      expect(foreign.problemIn('USD'), isNull);
      expect((RowChoice()..bill = false).wire(), {'bill': false});
    });
  });

  group('reconciliation', () {
    Future<ToolsApi> openSession(WidgetTester tester,
        {Size size = phone, Locale locale = const Locale('en')}) async {
      final api = ToolsApi();
      await pumpModule(
          tester, ReconSessionScreen(repository: api.repo(), id: 'rec_1'),
          size: size, locale: locale);
      return api;
    }

    testWidgets('attach, reversal, undo: each decision is sent and shown',
        (tester) async {
      final api = await openSession(tester);
      expect(find.text('Complete reconciliation'), findsOneWidget);
      // Not balanced: Complete is off and the reason is said.
      final complete = tester.widget<FilledButton>(find.ancestor(
          of: find.text('Complete reconciliation'),
          matching: find.byType(FilledButton)));
      expect(complete.onPressed, isNull);
      expect(find.textContaining('ANUM never posts a balancing entry'),
          findsOneWidget);
      await tap(tester, find.byKey(const Key('recon-act-attach-pay_1')));
      expect(lastBody(api, 'POST', '/reconciliations/rec_1/resolutions'),
          {'kind': 'attach', 'transaction_id': 'txn_9'});
      expect(find.text('Marked as on this statement.'), findsOneWidget);
      await clearToasts(tester);
      await tap(
          tester, find.byKey(const Key('recon-act-accept_reversal-rev_1')));
      expect(lastBody(api, 'POST', '/reconciliations/rec_1/resolutions'), {
        'kind': 'reversal',
        'transaction_id': 'txn_4',
        'other_transaction_id': 'txn_5'
      });
      await clearToasts(tester);
      expect(find.text('Record confirmed by this statement'), findsOneWidget);
      await tap(tester, find.byKey(const Key('recon-undo-res_1')));
      expect(api.sent('DELETE', '/reconciliations/rec_1/resolutions/res_1'),
          hasLength(1));
      expect(tester.takeException(), isNull);
    });

    testWidgets('match offers only records with the same amount',
        (tester) async {
      final api = await openSession(tester);
      await tap(tester, find.byKey(const Key('recon-act-match-nil_1')));
      expect(find.text('Match to a record'), findsOneWidget);
      // The ledger row of the same amount, not the duplicate.
      expect(find.byKey(const Key('recon-match-pay_1')), findsOneWidget);
      expect(find.byKey(const Key('recon-match-dup_1')), findsNothing);
      await tap(tester, find.byKey(const Key('recon-match-pay_1')));
      expect(lastBody(api, 'POST', '/reconciliations/rec_1/resolutions'), {
        'kind': 'match',
        'statement_ref': 'imp_1#3',
        'transaction_id': 'txn_9'
      });
      expect(find.text('Matched.'), findsOneWidget);
    });

    testWidgets(
        'a correcting entry shows its lines and posts with the payload hash; '
        'then the session completes and reopens with a reason', (tester) async {
      final api = await openSession(tester);
      await tap(tester, find.byKey(const Key('recon-act-correct-nil_1')));
      expect(find.text('Post a correcting entry'), findsOneWidget);
      expect(find.textContaining('expense:shopping'), findsOneWidget);
      expect(find.textContaining('Approval code c0c0c0c0c0c0'), findsOneWidget);
      await tap(tester, find.byKey(const Key('recon-correct-post')));
      expect(lastBody(api, 'POST', '/reconciliations/rec_1/corrections'),
          {'statement_ref': 'imp_1#3', 'payload_hash': correctionHash});
      await clearToasts(tester);
      expect(find.text('Correcting entry posted'), findsOneWidget);
      // It balances now: Complete sends the version it was shown.
      await tap(tester, find.byKey(const Key('recon-complete')));
      expect(lastBody(api, 'POST', '/reconciliations/rec_1/complete'),
          {'version': 4});
      await clearToasts(tester);
      expect(find.byKey(const Key('recon-reopen')), findsOneWidget);
      expect(find.byKey(const Key('recon-edit')), findsNothing);
      await tap(tester, find.byKey(const Key('recon-reopen')));
      await formField(tester, 'reason', 'ab');
      await submitForm(tester);
      expect(find.text('Say why you reopen it (at least 3 characters).'),
          findsOneWidget);
      await formField(tester, 'reason', 'A late refund');
      await submitForm(tester);
      expect(lastBody(api, 'POST', '/reconciliations/rec_1/reopen'),
          {'version': 4, 'reason': 'A late refund'});
      await clearToasts(tester);
      expect(find.textContaining('A late refund'), findsOneWidget);
      expect(find.byKey(const Key('recon-complete')), findsOneWidget);
    });

    testWidgets('a changed correcting entry is refused in the API\'s words',
        (tester) async {
      final api = await openSession(tester);
      api.correctionChanged = true;
      await tap(tester, find.byKey(const Key('recon-act-correct-nil_1')));
      await tap(tester, find.byKey(const Key('recon-correct-post')));
      expect(find.textContaining('changed since you saw it'), findsOneWidget);
      expect(api.resolutions, isEmpty);
    });

    testWidgets('Open transactions closes the session for Finance',
        (tester) async {
      final api = ToolsApi();
      Object? result;
      await pumpModule(
          tester,
          Builder(
              builder: (context) => FilledButton(
                  onPressed: () async {
                    result = await Navigator.of(context).push(
                        MaterialPageRoute<String>(
                            builder: (_) => ReconSessionScreen(
                                repository: api.repo(), id: 'rec_1')));
                  },
                  child: const Text('go'))));
      await tester.tap(find.text('go'));
      await tester.pumpAndSettle();
      await tap(tester, find.byKey(const Key('recon-act-open-dup_1')));
      expect(result, 'transactions');
    });

    testWidgets('start: the account, then the period and the balances',
        (tester) async {
      final api = ToolsApi();
      await pumpModule(tester, ReconciliationScreen(repository: api.repo()));
      await tap(tester, find.byKey(const Key('recon-start')));
      // Archived accounts are not offered.
      expect(find.byKey(const Key('pick-account-acc_3')), findsNothing);
      await tap(tester, find.byKey(const Key('pick-account-acc_2')));
      expect(
          find.textContaining('type what is owed as printed'), findsOneWidget);
      await formField(tester, 'closing', '450.50');
      await formField(tester, 'due', '450.50');
      await submitForm(tester);
      final body = lastBody(api, 'POST', '/reconciliations');
      expect(body['account_id'], 'acc_2');
      expect(body['statement_closing'], '-450.50',
          reason: 'a card owes: kept negative in the ledger');
      expect(body['amount_due'], '450.50');
      expect(body['period_start'], isNotNull);
      // The new session opens.
      expect(find.byKey(const Key('recon-complete')), findsOneWidget);
    });
  });

  group('iPhone SMS', () {
    testWidgets('review: AI help fills the fields, record sends only changes',
        (tester) async {
      final api = ToolsApi();
      await pumpModule(tester, SmsReviewScreen(repository: api.repo()));
      expect(api.sent('GET', '/sms/messages').first.uri.queryParameters,
          containsPair('status', 'review'));
      expect(find.textContaining('Format not confirmed yet'), findsWidgets);
      await tap(tester, find.byKey(const Key('sms-message-sms_1')));
      expect(find.byKey(const Key('sms-body')), findsOneWidget);
      await tap(tester, find.byKey(const Key('sms-suggest')));
      expect(find.text('Example cafe LLC'), findsOneWidget);
      await tap(tester, find.byKey(const Key('sms-confirm-format')));
      await tap(tester, find.byKey(const Key('sms-teach')));
      await tap(tester, find.byKey(const Key('sms-record')));
      final body = lastBody(api, 'POST', '/sms/messages/sms_1/post');
      expect(body['kind'], 'card_purchase');
      expect(body['merchant'], 'Example cafe LLC');
      expect(body['amount'], '25.50');
      expect(body['confirm_format'], isTrue);
      expect(body['teach'], isTrue);
      expect(body.containsKey('account_id'), isFalse,
          reason: 'an unchanged account keeps what was read');
      expect(find.textContaining('A format was proposed'), findsOneWidget);
    });

    testWidgets('all statuses; a recorded message is undone after asking',
        (tester) async {
      final api = ToolsApi();
      await pumpModule(tester, SmsReviewScreen(repository: api.repo()));
      await tap(tester, find.text('All').first);
      await tap(tester, find.byKey(const Key('sms-message-sms_2')));
      expect(find.byKey(const Key('sms-dismiss')), findsNothing);
      await tap(tester, find.byKey(const Key('sms-undo')));
      expect(find.text('Undo this recorded message?'), findsOneWidget);
      await tap(tester, find.byKey(const Key('records-confirm')));
      expect(api.sent('POST', '/sms/messages/sms_2/reverse'), hasLength(1));
    });

    testWidgets('a learned format is switched on and deleted; paste checks',
        (tester) async {
      final api = ToolsApi();
      await pumpModule(tester, SmsReviewScreen(repository: api.repo()));
      await tap(tester, find.byKey(const Key('sms-rule-toggle-smsrule_1')));
      expect(api.sent('POST', '/sms/rules/smsrule_1/activate'), hasLength(1));
      await clearToasts(tester);
      await tap(tester, find.byKey(const Key('sms-rule-delete-smsrule_1')));
      await tap(tester, find.byKey(const Key('records-confirm')));
      expect(api.sent('DELETE', '/sms/rules/smsrule_1'), hasLength(1));
      await clearToasts(tester);
      await tap(tester, find.byKey(const Key('sms-paste')));
      await formField(tester, 'sender', 'EXAMPLEBANK');
      await formField(tester, 'message', 'Purchase of AED 10.00 at Example');
      await submitForm(tester);
      expect(lastBody(api, 'POST', '/sms/paste')['sender'], 'EXAMPLEBANK');
      expect(find.textContaining('Saved for your review in ANUM.'),
          findsOneWidget);
    });

    testWidgets('empty and denied say what to do', (tester) async {
      final api = ToolsApi()..smsEmpty = true;
      await pumpModule(tester, SmsReviewScreen(repository: api.repo()));
      expect(find.text('Nothing here'), findsOneWidget);
      final denied = ToolsApi()..deny = true;
      await pumpModule(tester, SmsReviewScreen(repository: denied.repo()));
      expect(find.textContaining('A viewer of this workspace reads only'),
          findsOneWidget);
    });

    testWidgets('the ledger: figures, warnings, months and the journal',
        (tester) async {
      final api = ToolsApi();
      await pumpModule(tester,
          LedgerScreen(repository: api.repo(), now: DateTime(2026, 11, 5)));
      expect(api.sent('GET', '/ledger/dashboard').last.uri.queryParameters,
          containsPair('month', '2026-11'));
      expect(find.text('AED 9,500.00'), findsOneWidget);
      expect(
          find.textContaining('left out of every figure: 3'), findsOneWidget);
      expect(find.textContaining('Limit used: 10%'), findsOneWidget);
      expect(find.textContaining('Last message said AED 9,990.00'),
          findsOneWidget);
      expect(find.text('Salary from Example Co'), findsOneWidget);
      expect(find.textContaining('Income: salary'), findsOneWidget);
      await tap(tester, find.byKey(const Key('ledger-prev')));
      expect(api.sent('GET', '/ledger/dashboard').last.uri.queryParameters,
          containsPair('month', '2026-10'));
      await tap(tester, find.byKey(const Key('ledger-entry-ent_1')));
      expect(find.text('One transaction and its corrections'), findsOneWidget);
      expect(api.sent('GET', '/ledger/transactions/can_1'), hasLength(1));
    });
  });

  group('statement import', () {
    Uint8List csv() => Uint8List.fromList(
        utf8.encode('Date,Details,Amount\n03/10/2026,Example grocer,-45\n'));

    testWidgets(
        'CSV: the preview, an already imported row ticked in, import and undo',
        (tester) async {
      final api = ToolsApi();
      await pumpModule(
          tester,
          StatementImportScreen(
              repository: api.repo(),
              pickFile: () async => (name: 'statement.csv', bytes: csv())));
      expect(find.text('No statement imported yet'), findsOneWidget);
      await tap(tester, find.byKey(const Key('import-start')));
      await tap(tester, find.byKey(const Key('pick-account-acc_1')));
      final preview = lastBody(api, 'POST', '/imports/preview');
      expect(preview['content'], contains('Example grocer'));
      expect(preview['account_id'], 'acc_1');
      expect(find.byKey(const Key('csv-summary')), findsOneWidget);
      expect(find.textContaining('Line 4: The date is not readable.'),
          findsOneWidget);
      expect(find.textContaining('day first and month first'), findsOneWidget);
      // The duplicate is left out until ticked.
      final dup = tester
          .widget<CheckboxListTile>(find.byKey(const Key('csv-include-3')));
      expect(dup.value, isFalse);
      expect(find.text('Import rows: 1'), findsOneWidget);
      await tap(tester, find.byKey(const Key('csv-include-3')));
      expect(find.text('Import rows: 2'), findsOneWidget);
      await tap(tester, find.byKey(const Key('csv-import')));
      final body = lastBody(api, 'POST', '/imports');
      expect(body['decisions'], [
        {'line': 3, 'action': 'import'}
      ]);
      expect(find.byKey(const Key('import-result')), findsOneWidget);
      await tap(tester, find.text('Close'));
      expect(find.byKey(const Key('import-imp_new')), findsOneWidget);
      await tap(tester, find.byKey(const Key('import-undo-imp_new')));
      await tap(tester, find.byKey(const Key('records-confirm')));
      expect(api.sent('DELETE', '/imports/imp_new'), hasLength(1));
    });

    testWidgets('CSV: columns are chosen and read again', (tester) async {
      final api = ToolsApi();
      await pumpModule(
          tester,
          CsvImportScreen(
              repository: api.repo(),
              account: const FinanceAccountFixture().account,
              filename: 'statement.csv',
              content: 'Date,Details,Amount\n'));
      await tap(tester, find.byKey(const Key('csv-columns')));
      await tap(tester, find.byKey(const Key('csv-map-amount')));
      await tester.tap(find.text('None').last);
      await tester.pumpAndSettle();
      await tap(tester, find.byKey(const Key('csv-map-apply')));
      expect(find.textContaining('an amount column or both debit and credit'),
          findsOneWidget);
      await tap(tester, find.byKey(const Key('csv-map-amount')));
      await tester.tap(find.text('Amount').last);
      await tester.pumpAndSettle();
      await tap(tester, find.byKey(const Key('csv-map-apply')));
      final body = lastBody(api, 'POST', '/imports/preview');
      expect(body['preset'], 'custom');
      expect((body['mapping']! as Map)['amount'], 'Amount');
    });

    testWidgets(
        'PDF: the password is asked, the rows checked, edited and imported',
        (tester) async {
      final api = ToolsApi();
      final transport = FakePdfTransport((query, password) {
        if (password == null) {
          throw const ApiException(422, 'This PDF needs its password.',
              code: 'password_required');
        }
        return ToolsApi.pdfView();
      });
      await pumpModule(
          tester,
          PdfImportScreen(
              repository: api.repo(pdf: transport),
              account: const FinanceAccountFixture().account,
              filename: 'statement.pdf',
              bytes: Uint8List.fromList(const [0x25, 0x50, 0x44, 0x46])));
      expect(
          find.text('This PDF is protected with a password'), findsOneWidget);
      await tap(tester, find.byKey(const Key('pdf-password-open')));
      expect(find.text('Type the password first.'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('pdf-password')), 'example');
      await tap(tester, find.byKey(const Key('pdf-password-open')));
      expect(transport.calls.last.$3, 'example');
      expect(transport.calls.last.$1['account_id'], 'acc_1');
      expect(find.byKey(const Key('pdf-last4-warning')), findsOneWidget);
      expect(find.text('Sign guessed: money in or out?'), findsOneWidget);
      expect(find.textContaining('Rows or lines left out by the reader: 1'),
          findsOneWidget);
      // Edit the second row's amount: the check follows.
      await tap(tester, find.byKey(const Key('pdf-edit-1:2')));
      await typeInto(tester, const Key('pdf-edit-amount'), '-25.00');
      await tap(tester, find.byKey(const Key('pdf-edit-save')));
      expect(find.byKey(const Key('pdf-accept')), findsOneWidget);
      await tap(tester, find.byKey(const Key('pdf-import')));
      expect(find.textContaining('tick that you import them anyway'),
          findsOneWidget);
      await tap(tester, find.byKey(const Key('pdf-accept')));
      await tap(tester, find.byKey(const Key('pdf-import')));
      final body = lastBody(api, 'POST', '/imports/pdf');
      expect(body['file_sha256'], 'd' * 64);
      expect(body['accept_mismatch'], isTrue);
      final rows = (body['rows']! as List).cast<Map<String, Object?>>();
      expect(rows[1]['amount'], '-25.00');
      expect(find.byKey(const Key('import-result')), findsOneWidget);
    });

    testWidgets('PDF: roles are checked before reading again', (tester) async {
      final api = ToolsApi();
      final transport =
          FakePdfTransport((query, _) => ToolsApi.pdfView(roles: null));
      await pumpModule(
          tester,
          PdfImportScreen(
              repository: api.repo(pdf: transport),
              account: const FinanceAccountFixture().account,
              filename: 'statement.pdf',
              bytes: Uint8List.fromList(const [1, 2, 3])));
      await tap(tester, find.byKey(const Key('pdf-role-2')));
      await tester.tap(find.text('Ignore').last);
      await tester.pumpAndSettle();
      await tap(tester, find.byKey(const Key('pdf-reread')));
      expect(find.text('Pick at least one money column.'), findsOneWidget);
      expect(transport.calls, hasLength(1));
      await tap(tester, find.byKey(const Key('pdf-role-2')));
      await tester.tap(find.text('Debit (money out)').last);
      await tester.pumpAndSettle();
      await tap(tester, find.byKey(const Key('pdf-reread')));
      expect(transport.calls.last.$1['roles'], 'date,description,debit');
    });

    testWidgets('a wrong file type is refused before anything is sent',
        (tester) async {
      final api = ToolsApi();
      await pumpModule(
          tester,
          StatementImportScreen(
              repository: api.repo(),
              pickFile: () async =>
                  (name: 'notes.txt', bytes: Uint8List.fromList([65]))));
      await tap(tester, find.byKey(const Key('import-start')));
      await tap(tester, find.byKey(const Key('pick-account-acc_1')));
      expect(find.text('Pick a .csv or .pdf statement from your bank.'),
          findsOneWidget);
      expect(api.sent('POST', '/imports/preview'), isEmpty);
    });
  });

  group('rules', () {
    testWidgets(
        'a new rule: preview, save, approve with the payload hash, apply and undo',
        (tester) async {
      final api = ToolsApi();
      await pumpModule(tester, RulesHubScreen(repository: api.repo()));
      expect(find.text('No rules yet'), findsOneWidget);
      expect(find.textContaining('Older phrase rules still running: 1'),
          findsOneWidget);
      await tap(tester, find.byKey(const Key('rules-new')));
      await tap(tester, find.byKey(const Key('rule-save')));
      expect(
          find.text('Give a merchant, words or a reference.'), findsOneWidget);
      await typeInto(tester, const Key('rule-field-merchant'), 'Example gym');
      await typeInto(tester, const Key('rule-field-min'), '10');
      await tap(tester, find.byKey(const Key('rule-preview-button')));
      expect(find.textContaining('needs its currency'), findsOneWidget);
      await typeInto(tester, const Key('rule-field-currency'), 'aed');
      await tap(tester, find.byKey(const Key('rule-preview-button')));
      expect(find.byKey(const Key('rule-preview')), findsOneWidget);
      expect(find.text('EXAMPLE GYM MONTHLY'), findsOneWidget);
      expect(
          (lastBody(api, 'POST', '/rules/preview')['content']! as Map)['match'],
          containsPair('currency', 'AED'));
      await tap(tester, find.byKey(const Key('rule-save')));
      expect(find.text('Approve version 1?'), findsOneWidget);
      expect(find.textContaining('Approval code a1a1a1a1a1a1'), findsOneWidget);
      await tap(tester, find.byKey(const Key('rule-approve-confirm')));
      expect(lastBody(api, 'POST', '/rules/rule_1/approve'),
          {'version': 1, 'payload_hash': ruleHash});
      await clearToasts(tester);
      await tap(tester, find.byKey(const Key('rule-rule_1')));
      await tap(tester, find.byKey(const Key('rule-apply')));
      await tap(tester, find.byKey(const Key('rule-apply-confirm')));
      expect(lastBody(api, 'POST', '/rules/rule_1/apply'),
          {'preview_hash': previewHash});
      await clearToasts(tester);
      await tester.pageBack();
      await tester.pumpAndSettle();
      await tap(tester, find.byKey(const Key('rule-batch-undo-batch_1')));
      await tap(tester, find.byKey(const Key('records-confirm')));
      expect(api.sent('POST', '/rule-batches/batch_1/undo'), hasLength(1));
      expect(find.textContaining('Put back: 2'), findsOneWidget);
    });

    testWidgets('a waiting change is rejected; an older version restored',
        (tester) async {
      final api = ToolsApi();
      api.ruleVersions
        ..add(api.ruleVersion(1, 'approved'))
        ..add(api.ruleVersion(2, 'approved', merchant: 'Example club'))
        ..add(api.ruleVersion(3, 'proposed', merchant: 'Example spa'));
      await pumpModule(
          tester, RuleDetailScreen(repository: api.repo(), ruleId: 'rule_1'));
      expect(find.text('Version 3 waits for your approval'), findsOneWidget);
      await tap(tester, find.byKey(const Key('rule-reject')));
      expect(lastBody(api, 'POST', '/rules/rule_1/reject'), {'version': 3});
      await clearToasts(tester);
      await tap(tester, find.byKey(const Key('rule-restore-1')));
      expect(lastBody(api, 'POST', '/rules/rule_1/revert'), {'to_version': 1});
      await tap(tester, find.byKey(const Key('rule-approve-confirm')));
      expect(lastBody(api, 'POST', '/rules/rule_1/approve')['version'], 4);
    });

    testWidgets('merchants, subcategories and automation are saved',
        (tester) async {
      final api = ToolsApi();
      await pumpModule(tester, RulesHubScreen(repository: api.repo()));
      await tap(tester, find.byKey(const Key('rules-tab-merchants')));
      expect(find.text('Example mart'), findsOneWidget);
      await tap(tester, find.byKey(const Key('alias-add')));
      await formField(tester, 'alias', 'مثال');
      await formField(tester, 'merchant', 'Example mart');
      await submitForm(tester);
      expect(lastBody(api, 'POST', '/merchants/aliases'),
          {'alias': 'مثال', 'merchant': 'Example mart'});
      await clearToasts(tester);
      await tap(tester, find.byKey(const Key('alias-remove-alias_1')));
      await tap(tester, find.byKey(const Key('records-confirm')));
      expect(api.sent('DELETE', '/merchants/aliases/alias_1'), hasLength(1));
      await clearToasts(tester);

      await tap(tester, find.byKey(const Key('rules-tab-subs')));
      expect(find.text('Bakery'), findsOneWidget);
      await tap(tester, find.byKey(const Key('sub-toggle-sub_1')));
      expect(lastBody(api, 'PATCH', '/subcategories/sub_1'),
          {'parent': 'groceries', 'name': 'Bakery', 'archived': true});
      await clearToasts(tester);
      expect(find.text('Bakery'), findsNothing,
          reason: 'an archived one is hidden unless asked for');

      await tap(tester, find.byKey(const Key('rules-tab-automation')));
      await tap(tester, find.byKey(const Key('auto-classify')));
      await tap(tester, find.byKey(const Key('auto-source-history')));
      await tap(tester, find.byKey(const Key('auto-notice-statement_missing')));
      await tap(tester, find.byKey(const Key('auto-save')));
      final policy = lastBody(api, 'PUT', '/automation');
      expect(policy['auto_classify'], isTrue);
      expect(policy['min_confidence'], '0.90');
      expect(policy['sources'], ['rule', 'history']);
      expect(policy['notices'], ['statement_missing']);
      expect(find.text('Automation saved.'), findsOneWidget);
    });

    testWidgets('a viewer is told whose rules these are', (tester) async {
      final api = ToolsApi()..deny = true;
      await pumpModule(tester, RulesHubScreen(repository: api.repo()));
      expect(find.textContaining('A viewer of this workspace reads only'),
          findsWidgets);
    });
  });

  group('layout', () {
    final screens = <String, Widget Function(FinanceMoreRepository)>{
      'session': (r) => ReconSessionScreen(repository: r, id: 'rec_1'),
      'sms': (r) => SmsReviewScreen(repository: r),
      'sms message': (r) => SmsMessageScreen(
          repository: r,
          message:
              SmsMessage.fromJson(JsonReader.of(ToolsApi().messages.first)),
          accounts: const [],
          aiOn: true),
      'ledger': (r) => LedgerScreen(repository: r, now: DateTime(2026, 11, 5)),
      'import': (r) =>
          StatementImportScreen(repository: r, pickFile: () async => null),
      'csv': (r) => CsvImportScreen(
          repository: r,
          account: const FinanceAccountFixture().account,
          filename: 'statement.csv',
          content: 'x'),
      'pdf': (r) => PdfImportScreen(
          repository: FinanceMoreRepository(r.api,
              pdf: FakePdfTransport((_, __) => ToolsApi.pdfView())),
          account: const FinanceAccountFixture().account,
          filename: 'statement.pdf',
          bytes: Uint8List(4)),
      'rules': (r) => RulesHubScreen(repository: r),
    };
    for (final MapEntry(key: name, value: build) in screens.entries) {
      for (final (size, scale, locale) in [
        (narrow, 2.0, const Locale('en')),
        (narrow, 2.0, const Locale('ar')),
        (phone, 1.0, const Locale('ar')),
        (tablet, 1.3, const Locale('en')),
      ]) {
        testWidgets(
            '$name lays out at ${size.width.toInt()} px, ${scale}x, '
            '${locale.languageCode}', (tester) async {
          final api = ToolsApi();
          await pumpModule(tester, build(api.repo()),
              size: size, textScale: scale, locale: locale);
          expectClean(tester);
          final scrollable = find.byType(Scrollable);
          if (scrollable.evaluate().isNotEmpty) {
            await tester.drag(scrollable.first, const Offset(0, -2000));
            await tester.pumpAndSettle();
            expectClean(tester);
          }
          if (locale.languageCode == 'ar') {
            expect(
                Directionality.of(tester.element(find.byType(Scaffold).last)),
                TextDirection.rtl);
          }
        });
      }
    }
  });
}

/// The account a statement is imported into.
class FinanceAccountFixture {
  const FinanceAccountFixture();
  FinanceAccount get account => const FinanceAccount(
      id: 'acc_1',
      name: 'Example bank',
      kind: 'bank',
      currency: 'AED',
      balance: '1000.00',
      last4: '1234');
}
