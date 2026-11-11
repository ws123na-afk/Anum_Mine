// The books pages the phone gained for parity with the web
// (docs/business-books.md): the fixed asset register (roll-forward, CSV,
// depreciation settings, disposal, impairments decided by someone else),
// the business's loans (new loan, schedule against actual, repayments and
// voids), a new manual journal entry, and Attach or Dismiss on a receipt
// proposed in the Accountant review. Against the books' fake API with the
// API's JSON shapes, at 320 to 1024 px in English and Arabic. Fixtures are
// plainly fake.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/books/books_controller.dart';
import 'package:anum_mobile/features/books/books_data.dart';
import 'package:anum_mobile/features/books/books_extra_screens.dart';
import 'package:anum_mobile/features/books/books_extra_strings.dart';
import 'package:anum_mobile/features/books/books_more_data.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/fake_export_saver.dart';
import '../records/records_harness.dart';
import 'books_more_test.dart' show MoreApi, openMore, back, base;
import 'books_test.dart' show pumpBooks;

final _dash = RegExp('[–—]');

Map<String, Object?> assetJson(int n,
        {String status = 'active',
        bool detail = false,
        String me = 'user_me'}) =>
    {
      'id': 'basset_$n',
      'name': 'Example truck $n',
      'category': 'vehicles',
      'source_kind': 'bill',
      'source_id': 'bbill_$n',
      'source_live': true,
      'acquired_on': '2026-01-15',
      'currency': 'AED',
      'cost': '120000.00',
      'useful_life_months': 60,
      'residual_value': '0.00',
      'method': 'straight_line',
      'rate_pct': null,
      'depreciation_start': '2026-02-01',
      'status': status,
      'disposed_on': status == 'disposed' ? '2026-09-30' : null,
      'disposal_kind': status == 'disposed' ? 'sale' : null,
      'proceeds': status == 'disposed' ? '90000.00' : '0.00',
      'accumulated_depreciation': '18000.00',
      'impairment': '0.00',
      'net_book_value': '102000.00',
      'as_of': '2026-11-11',
      'gain_or_loss': status == 'disposed' ? '-12000.00' : null,
      'version': 4,
      'pending_impairments': n == 1 ? 2 : 0,
      if (detail) ...{
        'schedule': [
          for (var m = 1; m <= 24; m++)
            {
              'month': '2026-${(m % 12 + 1).toString().padLeft(2, '0')}-01',
              'amount': '2000.00',
              'accumulated': '${m * 2000}.00',
              'state': m < 10 ? 'posted' : 'scheduled',
            }
        ],
        'impairments': [
          {
            'id': 'bimp_1',
            'asset_id': 'basset_$n',
            'entry_date': '2026-10-31',
            'amount': '5000.00',
            'currency': 'AED',
            'reason': 'Example damage',
            'status': 'pending',
            'requested_by': 'user_other',
            'version': 1,
          },
          {
            'id': 'bimp_2',
            'asset_id': 'basset_$n',
            'entry_date': '2026-10-15',
            'amount': '1000.00',
            'currency': 'AED',
            'reason': 'Example scratch',
            'status': 'pending',
            'requested_by': me,
            'version': 1,
          },
        ],
        'can_edit': true,
        'disposal': null,
        'closed_through': null,
        'me': me,
      },
    };

Map<String, Object?> bookLoanJson(int n, {bool detail = false}) => {
      'id': 'bloan_$n',
      'lender': 'Example Bank $n',
      'principal': '60000.00',
      'currency': 'AED',
      'annual_rate': '6',
      'start_date': '2026-01-01',
      'term_months': 36,
      'frequency': 'monthly',
      'method': 'annuity',
      'early_repayment': 'reduce_term',
      'received_adjustment_id': null,
      'status': n == 2 ? 'void' : 'active',
      'version': 1,
      'payment': '1825.00',
      'periods': 36,
      'maturity': '2028-12-01',
      'outstanding': '45000.00',
      'interest_accrued': '120.00',
      'repaid': '15000.00',
      'next_due': '2026-12-01',
      'next_payment': '1825.00',
      'repayments': detail ? 2 : 2,
      'void_reason': null,
      if (detail) ...{
        'schedule': <Object?>[],
        'actual': [
          for (var i = 1; i <= 36; i++)
            {
              'number': i,
              'due': '2026-${(i % 12 + 1).toString().padLeft(2, '0')}-01',
              'payment': '1825.00',
              'interest': '300.00',
              'principal': '1525.00',
              'balance': '0',
              'paid': i < 3 ? '1825.00' : '0',
              'paid_interest': '0',
              'paid_principal': '0',
              'difference': '0',
              'state': i < 3 ? 'paid' : (i == 3 ? 'overdue' : 'upcoming'),
            }
        ],
        'revised': <Object?>[],
        'repayment_rows': [
          {
            'id': 'brep_2',
            'loan_id': 'bloan_$n',
            'paid_on': '2026-03-01',
            'amount': '1825.00',
            'interest': '295.00',
            'principal': '1530.00',
            'currency': 'AED',
            'status': 'active',
          },
          {
            'id': 'brep_1',
            'loan_id': 'bloan_$n',
            'paid_on': '2026-02-01',
            'amount': '1825.00',
            'interest': '300.00',
            'principal': '1525.00',
            'currency': 'AED',
            'status': 'active',
          },
        ],
        'accruals': <Object?>[],
        'can_edit': true,
      },
    };

Map<String, Object?> receiptItem() => {
      'key': 'receipt_match:bbill_1:doc_9',
      'producer': 'receipts',
      'kind': 'receipt_match',
      'detected': 'A receipt in the vault matches a bill.',
      'params': {
        'document_id': 'doc_9',
        'document': 'Receipt example',
        'bill_id': 'bbill_1',
        'reference': 'INV-1',
        'vendor': 'Example supplies',
      },
      'severity': 'warning',
      'occurred_on': '2026-11-01',
      'amount': '1050.00',
      'currency': 'AED',
      'ref_id': 'bbill_1',
      'section': 'bills',
      'action': 'link',
      'can_act': true,
      'why_not': null,
    };

class ExtraApi extends MoreApi {
  ExtraApi({super.account, super.owner});
  bool emptyAssets = false, receiptGone = false;
  int? assetsFail;

  @override
  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path;
    final q = r.uri.queryParameters;
    if (r.method == 'GET' && path == '$base/assets') {
      if (assetsFail != null) {
        return refuse(assetsFail!, 'The books are not reachable.');
      }
      final all = emptyAssets
          ? <Map<String, Object?>>[]
          : [
              for (var n = 1; n <= 30; n++)
                assetJson(n, status: n == 3 ? 'disposed' : 'active')
            ];
      final shown = all
          .where((a) => q['status'] == 'all' || a['status'] == q['status'])
          .toList();
      final offset = int.parse(q['offset'] ?? '0');
      return ok({
        'items': shown.skip(offset).take(25).toList(),
        'total': shown.length,
        'limit': 25,
        'offset': offset,
        'period': {'from': '2026-01-01', 'to': '2026-12-31'},
        'as_of': '2026-11-11',
        'roll_forward': emptyAssets
            ? <Object?>[]
            : [
                {
                  'currency': 'AED',
                  'opening_cost': '0.00',
                  'additions': '3600000.00',
                  'disposals_cost': '120000.00',
                  'closing_cost': '3480000.00',
                  'depreciation': '540000.00',
                  'impairment': '0.00',
                  'closing_depreciation': '522000.00',
                  'opening_nbv': '0.00',
                  'closing_nbv': '2958000.00',
                }
              ],
        'base_currency': 'AED',
        'closed_through': null,
        'can_edit': account,
        'pending_impairments': emptyAssets ? 0 : 2,
      });
    }
    if (r.method == 'GET' && path == '$base/assets.csv') {
      return ok({'csv': 'section,name\nregister,Example truck 1\n'});
    }
    final asset =
        RegExp('^$base/assets/(basset_\\d+)(/.*)?\$').firstMatch(path);
    if (asset != null) {
      final n = int.parse(asset.group(1)!.split('_')[1]);
      if (r.method == 'GET') return ok(assetJson(n, detail: true));
      return ok({'ok': true}, r.method == 'POST' ? 201 : 200);
    }
    if (r.method == 'GET' && path == '$base/loans') {
      final status = q['status'] ?? 'active';
      final items = [
        for (var n = 1; n <= 2; n++)
          if (status == 'all' || (status == 'void') == (n == 2)) bookLoanJson(n)
      ];
      return ok({
        'items': items,
        'total': items.length,
        'limit': 25,
        'offset': 0,
        'base_currency': 'AED',
        'can_edit': account,
        'match': <String, Object?>{},
      });
    }
    if (r.method == 'POST' && path == '$base/loans') {
      return ok(bookLoanJson(5), 201);
    }
    final loan = RegExp('^$base/loans/(bloan_\\d+)(/.*)?\$').firstMatch(path);
    if (loan != null) {
      final n = int.parse(loan.group(1)!.split('_')[1]);
      if (r.method == 'GET') return ok(bookLoanJson(n, detail: true));
      return ok({'ok': true}, 201);
    }
    if (r.method == 'GET' && path == '$base/ledger/accounts') {
      return ok({
        'items': [
          {'id': 'bacc_1000', 'code': '1000', 'name': 'Bank'},
          {'id': 'bacc_3000', 'code': '3000', 'name': 'Owner capital'},
          {'id': 'bacc_6000', 'code': '6000', 'name': 'Rent'},
        ],
        'subtypes': <String, Object?>{},
        'can_edit': account,
      });
    }
    if (r.method == 'POST' && path == '$base/ledger/journal') {
      return ok({'id': 'bje_9', 'replayed': false}, 201);
    }
    if (r.method == 'GET' && path == '$base/review') {
      return ok({
        'items': [receiptItem()],
        'total': 1,
        'limit': 25,
        'offset': 0,
        'counts': {'receipt_match': 1},
        'blocking': 0,
        'close_months': <Object?>[],
        'closed_through': null,
        'to_post': 0,
        'producers': ['receipts'],
        'unavailable': <String>[],
      });
    }
    if (r.method == 'POST' &&
        path.startsWith('$base/evidence/receipt-matches/')) {
      if (receiptGone) {
        return refuse(409, 'This receipt is no longer proposed for this bill.',
            'match_gone');
      }
      return ok({'id': 'brd_1'}, 201);
    }
    return super.route(r);
  }
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

void main() {
  test('the catalog matches and uses no long dashes', () {
    expect(BooksExtraStrings.catalogsMatch, isTrue);
    for (final text in BooksExtraStrings.allTexts) {
      expect(text.contains(_dash), isFalse, reason: text);
    }
  });

  group('fixed assets', () {
    testWidgets('the register pages, filters, exports and rolls forward',
        (tester) async {
      final fake = ExtraApi();
      await openMore(tester, fake, 'assets');
      expect(find.byKey(const Key('asset-basset_1')), findsOneWidget);
      expect(find.textContaining('Impairments waiting for approval: 2'),
          findsOneWidget);
      await tester.scrollUntilVisible(
          find.byKey(const Key('assets-roll-AED')), 400,
          scrollable: find
              .descendant(
                  of: find.byType(FixedAssetsScreen),
                  matching: find.byType(Scrollable))
              .first);
      expect(find.text('AED 2,958,000.00'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('records-more')).last);
      expect(fake.api.sent('GET', '$base/assets').last.uri.queryParameters,
          containsPair('offset', '25'));
      await tapVisible(tester, find.text('Disposed of').first);
      expect(fake.api.sent('GET', '$base/assets').last.uri.queryParameters,
          containsPair('status', 'disposed'));
      expect(find.byKey(const Key('asset-basset_3')), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('exports the register through the save dialog', (tester) async {
      final fake = ExtraApi();
      final saver = FakeExportSaver();
      final client = fake.api.client();
      await pumpModule(
          tester,
          FixedAssetsScreen(
              controller: BooksController(ApiBooksRepository(client)),
              more: ApiBooksMoreRepository(client),
              saver: saver));
      await tapVisible(tester, find.byKey(const Key('assets-export')));
      expect(fake.api.sent('GET', '$base/assets.csv'), hasLength(1));
      expect(saver.saved['fixed-assets.csv'], startsWith('section,name'));
      expect(find.text('Saved fixed-assets.csv.'), findsOneWidget);
    });

    testWidgets('an asset: settings, disposal and impairments', (tester) async {
      final fake = ExtraApi();
      await openMore(tester, fake, 'assets');
      await tapVisible(tester, find.byKey(const Key('asset-basset_1')));
      expect(find.byKey(const Key('asset-all')), findsOneWidget);
      // Someone else's request: Approve; the person's own: who decides.
      expect(
          find.byKey(const Key('impairment-approve-bimp_1')), findsOneWidget);
      expect(find.byKey(const Key('impairment-approve-bimp_2')), findsNothing);
      expect(find.textContaining('Another owner or accountant approves it'),
          findsWidgets);
      await tapVisible(
          tester, find.byKey(const Key('impairment-approve-bimp_1')));
      expect(
          fake.api
              .sent('POST', '$base/assets/basset_1/impairments/bimp_1/approve')
              .single
              .body,
          {'version': 1});
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('asset-edit')));
      await enter(tester, 'life', '700');
      await submit(tester);
      expect(find.text('The useful life is whole months from 1 to 600.'),
          findsOneWidget);
      await enter(tester, 'life', '48');
      await submit(tester);
      final edit = fake.api.sent('PATCH', '$base/assets/basset_1').single.body!;
      expect(edit['useful_life_months'], 48);
      expect(edit['version'], 4);
      expect(edit['rate_pct'], isNull);
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('asset-dispose')));
      await submit(tester);
      expect(
          find.textContaining('what the asset was sold for'), findsOneWidget);
      await enter(tester, 'proceeds', '95000');
      await submit(tester);
      expect(fake.api.sent('POST', '$base/assets/basset_1/dispose').single.body,
          containsPair('proceeds', '95000'));
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('asset-impair')));
      await enter(tester, 'amount', '2000');
      await enter(tester, 'reason', 'bad');
      await submit(tester);
      expect(
          find.text('Give a reason of 5 characters or more.'), findsOneWidget);
      await enter(tester, 'reason', 'Example flood damage');
      await submit(tester);
      expect(
          fake.api
              .sent('POST', '$base/assets/basset_1/impairments')
              .single
              .body,
          containsPair('amount', '2000'));
      expectClean(tester);
    });

    testWidgets('a member reads; empty and failed say what to do',
        (tester) async {
      final fake = ExtraApi(account: false, owner: false);
      await openMore(tester, fake, 'assets');
      expect(find.textContaining('Only an owner or an accountant'),
          findsOneWidget);
      fake.emptyAssets = true;
      await openMore(tester, fake, 'assets');
      expect(find.text('No fixed assets yet'), findsOneWidget);
      fake.assetsFail = 503;
      await openMore(tester, fake, 'assets');
      expect(find.byKey(const Key('records-error')), findsOneWidget);
    });
  });

  group('loans', () {
    testWidgets('a new loan, a repayment and voiding the latest',
        (tester) async {
      final fake = ExtraApi();
      await openMore(tester, fake, 'loans');
      expect(find.byKey(const Key('bloan-bloan_1')), findsOneWidget);
      expect(find.byKey(const Key('bloan-bloan_2')), findsNothing);
      await tapVisible(tester, find.byKey(const Key('bloans-new')));
      await enter(tester, 'lender', 'Example Bank');
      await enter(tester, 'principal', '50000');
      await enter(tester, 'rate', '5');
      await enter(tester, 'term', '0');
      await submit(tester);
      expect(
          find.text('The term is whole months from 1 to 600.'), findsOneWidget);
      await enter(tester, 'term', '24');
      await submit(tester);
      final made = fake.api.sent('POST', '$base/loans').single;
      expect(made.body, containsPair('term_months', 24));
      expect(made.body, containsPair('currency', 'AED'));
      expect(made.headers['Idempotency-Key'], startsWith('bloan'));
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('bloan-bloan_1')));
      expect(find.byKey(const Key('bloan-row-12')), findsOneWidget);
      expect(find.byKey(const Key('bloan-row-13')), findsNothing);
      expect(find.byKey(const Key('brepay-void-brep_2')), findsOneWidget);
      expect(find.byKey(const Key('brepay-void-brep_1')), findsNothing);
      expect(find.byKey(const Key('bloan-void')), findsNothing);
      await tapVisible(tester, find.byKey(const Key('bloan-record')));
      await submit(tester);
      final paid =
          fake.api.sent('POST', '$base/loans/bloan_1/repayments').single;
      expect(paid.body, containsPair('amount', '1825.00'));
      expect(paid.headers['Idempotency-Key'], startsWith('brepay'));
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('brepay-void-brep_2')));
      await enter(tester, 'reason', 'Example typed twice');
      await submit(tester);
      expect(
          fake.api
              .sent('POST', '$base/loans/bloan_1/repayments/brep_2/void')
              .single
              .body,
          {'reason': 'Example typed twice'});
      expectClean(tester);
    });

    testWidgets('a member reads loans and is told who records them',
        (tester) async {
      final fake = ExtraApi(account: false, owner: false);
      await openMore(tester, fake, 'loans');
      expect(find.byKey(const Key('bloans-new')), findsNothing);
      expect(find.textContaining('Only an owner or an accountant'),
          findsOneWidget);
      await tapVisible(tester, find.text('Voided loans'));
      expect(find.byKey(const Key('bloan-bloan_2')), findsOneWidget);
    });
  });

  group('a new manual journal entry', () {
    testWidgets('lines must balance, then it posts', (tester) async {
      final fake = ExtraApi();
      await openMore(tester, fake, 'journal');
      await tapVisible(tester, find.byKey(const Key('journal-new')));
      await tester.enterText(
          find.byKey(const Key('je-description')), 'Example opening balances');
      for (final (i, account) in ['bacc_1000', 'bacc_3000'].indexed) {
        await tapVisible(tester, find.byKey(Key('je-account-$i')));
        await tester.tap(find
            .text(account == 'bacc_1000' ? '1000 Bank' : '3000 Owner capital')
            .last);
        await tester.pumpAndSettle();
      }
      await tester.enterText(find.byKey(const Key('je-debit-0')), '5000');
      await tester.enterText(find.byKey(const Key('je-credit-1')), '4000');
      await tapVisible(tester, find.byKey(const Key('je-save')));
      expect(find.textContaining('differ by AED 1,000.00'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('je-credit-1')), '5000');
      await tester.enterText(find.byKey(const Key('je-debit-1')), '1');
      await tapVisible(tester, find.byKey(const Key('je-save')));
      expect(find.textContaining('Line 2:'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('je-debit-1')), '');
      await tapVisible(tester, find.byKey(const Key('je-add-line')));
      expect(find.byKey(const Key('je-line-2')), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('je-remove-2')));
      await tapVisible(tester, find.byKey(const Key('je-save')));
      final sent = fake.api.sent('POST', '$base/ledger/journal').single;
      expect(sent.body!['description'], 'Example opening balances');
      expect(sent.body!['lines'], [
        {'account_id': 'bacc_1000', 'debit': '5000.00', 'credit': '0.00'},
        {'account_id': 'bacc_3000', 'debit': '0.00', 'credit': '5000.00'},
      ]);
      expect(sent.headers['Idempotency-Key'], startsWith('manual'));
      expectClean(tester);
    });

    testWidgets('a member cannot record one', (tester) async {
      await openMore(tester, ExtraApi(account: false, owner: false), 'journal');
      expect(find.byKey(const Key('journal-new')), findsNothing);
    });
  });

  group('receipts proposed in the review', () {
    testWidgets('attach, and dismiss after a confirm', (tester) async {
      final fake = ExtraApi();
      await pumpBooks(tester, fake);
      await tapVisible(tester, find.byKey(const Key('books-tab-review')));
      const key = 'receipt_match:bbill_1:doc_9';
      await tapVisible(tester, find.byKey(const Key('review-attach-$key')));
      expect(
          fake.api
              .sent('POST', '$base/evidence/receipt-matches/accept')
              .single
              .body,
          {'bill_id': 'bbill_1', 'document_id': 'doc_9'});
      expect(find.text('Receipt attached to the bill.'), findsOneWidget);
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('review-dismiss-$key')));
      await tester.tap(find.byKey(const Key('records-confirm')));
      await tester.pumpAndSettle();
      expect(
          fake.api
              .sent('POST', '$base/evidence/receipt-matches/dismiss')
              .single
              .body,
          {'bill_id': 'bbill_1', 'document_id': 'doc_9'});
      await clearToasts(tester);
      fake.receiptGone = true;
      await tapVisible(tester, find.byKey(const Key('review-attach-$key')));
      expect(find.text('This receipt is no longer proposed for this bill.'),
          findsOneWidget);
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
      testWidgets('assets, loans and a new entry lay out at $name',
          (tester) async {
        final fake = ExtraApi();
        for (final page in const ['assets', 'loans']) {
          await openMore(tester, fake, page, size: size, locale: locale);
          expectClean(tester);
          await tapVisible(
              tester,
              find.byKey(
                  Key(page == 'assets' ? 'asset-basset_1' : 'bloan-bloan_1')));
          expectClean(tester);
          await back(tester);
        }
        await openMore(tester, fake, 'journal', size: size, locale: locale);
        await tapVisible(tester, find.byKey(const Key('journal-new')));
        expectClean(tester);
      });
    }
  }
}
