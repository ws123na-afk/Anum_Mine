// The rest of the business books on the phone (docs/business-books.md,
// docs/business-tax.md#filing-deadlines): every adjustment with its state,
// filters, paging and Void; supporting documents on adjustments, manual
// journal entries and invoices; the VAT filing settings with the line that
// says which deadline applies; and the dismissed receipts with Restore.
// Against the books' fake API with the API's JSON shapes, at 390x844 and
// 768x1024 in English and Arabic. Fixtures are plainly fake.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/books/books_data.dart';
import 'package:anum_mobile/features/books/books_more_data.dart';
import 'package:anum_mobile/features/books/books_more_strings.dart';
import 'package:anum_mobile/features/books/books_strings.dart';
import 'package:anum_mobile/features/business/business_models.dart';
import 'package:anum_mobile/features/business/invoice_controller.dart';
import 'package:anum_mobile/features/business/invoices.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../business_invoices_test.dart' show FakeInvoices, FakeSaver;
import '../business_test.dart' show app;
import '../records/records_harness.dart';
import 'books_test.dart';

const base = '/api/v1/business/books';

Map<String, Object?> adjustmentJson(int n,
        {String status = 'active',
        String kind = 'accrual',
        String createdBy = 'user_other'}) =>
    {
      'id': 'badj_$n',
      'kind': kind,
      'status': status,
      'entry_date': '2026-10-${(n % 28 + 1).toString().padLeft(2, '0')}',
      'currency': 'AED',
      'amount': '${1000 + n}.00',
      'section': null,
      'category': null,
      'description': 'Office rent example $n',
      'start_month': null,
      'months': null,
      'reverse_on': null,
      'void_reason': status == 'void' ? 'Typed twice' : '',
      'created_by': createdBy,
      'created_at': '2026-10-01T08:00:00Z',
    };

/// The books with adjustments, manual entries, tax settings and dismissed
/// receipts. [account] is an owner or accountant; [owner] an owner.
class MoreApi extends BooksApi {
  MoreApi({this.account = true, this.owner = true}) : super(manage: owner);
  bool account, owner;
  bool many = false, noAdjustments = false;
  bool taxSet = true, canEditTax = true;
  int? adjustmentsFail;
  final voided = <String, String>{};
  final approved = <String>{};
  final attached = <String, List<Map<String, Object?>>>{};
  final restored = <String>{};
  Map<String, Object?>? savedTax;
  bool noDismissed = false, denyDismissed = false, noEntries = false;

  List<Map<String, Object?>> get _adjustments => noAdjustments
      ? []
      : [
          for (var n = 1; n <= (many ? 30 : 3); n++)
            {
              ...adjustmentJson(n,
                  status: n == 2
                      ? 'pending'
                      : n == 3
                          ? 'void'
                          : 'active',
                  kind: n == 1 ? 'accrual' : 'prepayment',
                  createdBy: n == 2 ? 'user_me' : 'user_other'),
              if (voided.containsKey('badj_$n')) ...{
                'status': 'void',
                'void_reason': voided['badj_$n'],
              },
              if (approved.contains('badj_$n')) 'status': 'active',
            }
        ];

  Map<String, Object?> taxSettings() => {
        'id': 'btax_1',
        'jurisdiction': 'AE',
        'registered': true,
        'registration_number': '100000000000003',
        'registration_date': '2025-01-01',
        'filing_frequency': 'quarterly',
        'period_start_month': 1,
        'country': null,
        'filing_due_days': null,
        'zatca_enabled': false,
        'zatca_onboarding': 'not_started',
        'seller_crn': '',
        'seller_street': '',
        'seller_building': '',
        'seller_district': '',
        'seller_city': '',
        'seller_postal_code': '',
        'version': 4,
      };

  @override
  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path;
    final q = r.uri.queryParameters;
    if (r.method == 'GET' && path == '$base/setup') {
      final json = setupJson(manage: owner);
      json['permissions'] = {
        'read': true,
        'write': true,
        'manage': owner,
        'account': account,
      };
      json['user_id'] = 'user_me';
      return ok(json);
    }
    if (r.method == 'GET' && path == '$base/adjustments') {
      if (adjustmentsFail != null) {
        return refuse(adjustmentsFail!, 'The books are not reachable.');
      }
      final offset = int.parse(q['offset'] ?? '0');
      final rows = _adjustments.where((a) {
        if (q['kind'] != null && a['kind'] != q['kind']) return false;
        if (q['status'] != null) return a['status'] == q['status'];
        if (q['include_void'] == 'true') return true;
        return a['status'] != 'void';
      }).toList();
      return ok({
        'items': rows.skip(offset).take(25).toList(),
        'total': rows.length,
        'limit': 25,
        'offset': offset,
      });
    }
    final voidMatch = RegExp('^$base/adjustments/(badj_\\d+)/(void|approve)\$')
        .firstMatch(path);
    if (r.method == 'POST' && voidMatch != null) {
      final id = voidMatch.group(1)!;
      if (!account) {
        return refuse(403,
            'Only an owner or an accountant of this business can do this.');
      }
      if (voidMatch.group(2) == 'void') {
        voided[id] = r.body!['reason']! as String;
      } else {
        approved.add(id);
      }
      return ok(adjustmentJson(1));
    }
    if (r.method == 'GET' && path == '$base/ledger/journal') {
      expect(q['source_kind'], 'manual');
      return ok({
        'items': noEntries
            ? <Object?>[]
            : [
                {
                  'id': 'bje_1',
                  'entry_date': '2026-01-01',
                  'event_date': '2026-01-01',
                  'source_kind': 'manual',
                  'source_id': null,
                  'description': 'Opening balances example',
                  'status': 'posted',
                  'reverses_id': null,
                  'reversed_by_id': null,
                  'late': false,
                  'created_by': 'user_me',
                  'created_at': '2026-01-01T08:00:00Z',
                  'can_reverse': true,
                  'total': '5000.00',
                  'lines': [
                    {
                      'id': 'bjl_1',
                      'account_code': '1000',
                      'account_name': 'Bank',
                      'debit': '5000.00',
                      'credit': '0.00',
                    },
                    {
                      'id': 'bjl_2',
                      'account_code': '3000',
                      'account_name': 'Owner capital',
                      'debit': '0.00',
                      'credit': '5000.00',
                    },
                  ],
                  'evidence': <Object?>[],
                }
              ],
        'total': noEntries ? 0 : 1,
        'limit': 25,
        'offset': 0,
        'base_currency': 'AED',
        'can_account': account,
      });
    }
    if (r.method == 'GET' && path == '$base/tax') {
      return ok({
        'settings': taxSet ? (savedTax ?? taxSettings()) : null,
        'rates': <Object?>[],
        'can_edit': canEditTax,
        'jurisdictions': ['SA', 'AE', 'OTHER'],
        'codes': <Object?>[],
        'sales_codes': <Object?>[],
        'frequencies': ['monthly', 'quarterly'],
        'periods': <Object?>[],
        'filing_deadline': null,
        'filing_presets': ['AE', 'BH', 'OM', 'SA'],
        'no_vat_countries': ['KW', 'QA'],
        'max_due_days': 120,
        'notice': {'en': 'Not verified.', 'ar': 'غير موثق.'},
      });
    }
    if (r.method == 'PUT' && path == '$base/tax/settings') {
      savedTax = {...taxSettings(), ...r.body!, 'version': 5};
      taxSet = true;
      return ok({'settings': savedTax, 'rates': <Object?>[]});
    }
    if (r.method == 'GET' &&
        path == '$base/evidence/receipt-matches/dismissed') {
      if (denyDismissed) {
        return refuse(403,
            'Only an owner or an accountant of this business can do this.');
      }
      final rows = noDismissed
          ? <Map<String, Object?>>[]
          : [
              {
                'id': 'bdis_1',
                'bill_id': 'bbill_1',
                'document_id': 'doc_1',
                'dismissed_by': 'user_me',
                'dismissed_at': '2026-11-02T08:00:00Z',
                'bill_reference': 'GOS-1',
                'vendor': 'Gulf Office Supplies',
                'bill_date': '2026-10-28',
                'amount': '1050.00',
                'currency': 'AED',
                'document_title': 'Receipt example',
                'bill_found': true,
                'document_found': true,
              },
              {
                'id': 'bdis_2',
                'bill_id': 'bbill_gone',
                'document_id': 'doc_gone',
                'dismissed_by': 'user_me',
                'dismissed_at': '2026-11-01T08:00:00Z',
                'bill_reference': '',
                'vendor': '',
                'bill_date': null,
                'amount': null,
                'currency': null,
                'document_title': '',
                'bill_found': false,
                'document_found': false,
              },
            ].where((row) => !restored.contains(row['id'])).toList();
      return ok(
          {'items': rows, 'total': rows.length, 'limit': 25, 'offset': 0});
    }
    if (r.method == 'DELETE' &&
        path.startsWith('$base/evidence/receipt-matches/dismissed/')) {
      restored.add(path.split('/').last);
      return const ApiResponse(statusCode: 204, body: {});
    }
    if (r.method == 'GET' && path == '$base/evidence') {
      final type = q['source_type']!;
      final ids = q['ids']!.split(',');
      return ok({
        'items': {
          for (final id in ids)
            id: {
              'documents': attached['$type:$id'] ?? <Object?>[],
              'evidence_missing':
                  type == 'adjustment' && (attached['$type:$id'] ?? []).isEmpty,
            }
        },
        'can_attach': account,
      });
    }
    if (r.method == 'GET' && path == '/api/v1/documents') {
      return ok({
        'items': [
          {'id': 'doc_1', 'title': 'Receipt example', 'category': 'receipt'}
        ],
        'next_cursor': null,
      });
    }
    if (r.method == 'POST' && path == '$base/evidence') {
      final key = '${r.body!['source_type']}:${r.body!['source_id']}';
      attached[key] = [
        {
          'link_id': 'vlink_1',
          'document_id': r.body!['document_id'],
          'title': 'Receipt example',
          'category': 'receipt',
          'added_by': 'user_me',
          'added_at': '2026-11-05T08:00:00Z',
        }
      ];
      return ok(attached[key]!.single, 201);
    }
    return super.route(r);
  }
}

/// Leaves the current page with the app bar's back button (in any
/// language).
Future<void> back(WidgetTester tester) async {
  await tester.tap(find.byType(BackButton).last);
  await tester.pumpAndSettle();
}

Future<void> openMore(WidgetTester tester, MoreApi fake, String page,
    {Size size = phone, Locale locale = const Locale('en')}) async {
  await pumpBooks(tester, fake, size: size, locale: locale);
  await tester.tap(find.byKey(const Key('books-more')));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(Key('books-more-$page')));
  await tester.pumpAndSettle();
}

void main() {
  test('the catalogs match', () {
    expect(BooksMoreStrings.catalogsMatch, isTrue);
    expect(BooksStrings.catalogsMatch, isTrue);
  });

  group('filing deadline', () {
    const ae = TaxFilingForm(
        jurisdiction: 'AE', frequency: 'quarterly', cycleMonth: 1);
    test('the owner due day wins, then a preset, no VAT, none', () {
      expect(ae.source(), DeadlineSource.preset);
      expect(ae.presetRule(), 'deadline.days28');
      expect(ae.copyWith(dueDays: '20').source(), DeadlineSource.owner);
      final other = ae.copyWith(jurisdiction: 'OTHER');
      expect(other.copyWith(country: 'bh').source(), DeadlineSource.preset);
      expect(other.copyWith(country: 'BH').presetRule(),
          'deadline.endOfNextMonth');
      expect(other.copyWith(country: 'OM').presetRule(), 'deadline.days30');
      expect(other.copyWith(country: 'KW').source(), DeadlineSource.noVat);
      expect(other.copyWith(country: 'EG').source(), DeadlineSource.none);
    });
    test('the form checks what the API checks', () {
      final other = ae.copyWith(jurisdiction: 'OTHER');
      expect(other.copyWith(country: 'B1').problem(), 'problem.country');
      expect(other.copyWith(country: 'SA').problem(),
          'problem.countryJurisdiction');
      expect(ae.copyWith(dueDays: '0').problem(), 'problem.dueDays');
      expect(ae.copyWith(dueDays: '121').problem(), 'problem.dueDays');
      expect(ae.copyWith(dueDays: '28').problem(), isNull);
    });
    test('the body keeps the registration and ZATCA fields as saved', () {
      final overview = TaxOverview.fromJson(JsonReader({
        'settings': MoreApi().taxSettings(),
        'can_edit': true,
      }));
      final body = overview.body(overview
          .form()
          .copyWith(jurisdiction: 'OTHER', country: 'bh', dueDays: '30'));
      expect(body['registration_number'], '100000000000003');
      expect(body['registered'], isTrue);
      expect(body['country'], 'BH');
      expect(body['filing_due_days'], 30);
      expect(body['zatca_enabled'], isFalse);
      expect(body['version'], 4);
      expect(overview.body(overview.form())['country'], isNull);
    });
  });

  group('repository', () {
    test('adjustment filters map to the API query', () async {
      final fake = MoreApi();
      final repo = ApiBooksMoreRepository(fake.api.client());
      await repo.adjustments(status: 'pending', kind: 'accrual');
      expect(fake.api.requests.last.uri.queryParameters,
          containsPair('status', 'pending'));
      expect(fake.api.requests.last.uri.queryParameters['kind'], 'accrual');
      final all = await repo.adjustments(status: 'with_void');
      expect(
          fake.api.requests.last.uri.queryParameters['include_void'], 'true');
      expect(all.items.map((a) => a.status), ['active', 'pending', 'void']);
      final live = await repo.adjustments();
      expect(fake.api.requests.last.uri.queryParameters.keys,
          isNot(contains('status')));
      expect(live.items, hasLength(2));
      await repo.restoreReceipt('bdis_1');
      expect(fake.api.requests.last.method, 'DELETE');
      expect(fake.api.requests.last.uri.path,
          '$base/evidence/receipt-matches/dismissed/bdis_1');
    });
  });

  group('adjustments', () {
    testWidgets('states, filters and paging', (tester) async {
      final fake = MoreApi()..many = true;
      await openMore(tester, fake, 'adjustments');
      expect(find.byKey(const Key('adjustment-badj_1')), findsOneWidget);
      expect(find.textContaining('Waits for approval'), findsWidgets);
      await tapVisible(tester, find.byKey(const Key('records-more')));
      expect(find.byKey(const Key('adjustment-badj_30')), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('filter-pending')));
      expect(fake.api.requests.last.uri.queryParameters['status'], 'pending');
      expect(find.byKey(const Key('adjustment-badj_2')), findsOneWidget);
      expect(find.byKey(const Key('adjustment-badj_1')), findsNothing);
      await tapVisible(tester, find.byKey(const Key('filter-with_void')));
      expect(
          fake.api.requests.last.uri.queryParameters['include_void'], 'true');
      expect(find.textContaining('Voided'), findsWidgets);
      await tapVisible(tester, find.byKey(const Key('adjustments-kind')));
      await tester.tap(find.text('Accrued cost').last);
      await tester.pumpAndSettle();
      expect(fake.api.requests.last.uri.queryParameters['kind'], 'accrual');
      expect(find.byKey(const Key('adjustment-badj_1')), findsOneWidget);
      expect(find.byKey(const Key('adjustment-badj_2')), findsNothing);
      expectClean(tester);
    });

    testWidgets('an accountant voids an active adjustment with a reason',
        (tester) async {
      final fake = MoreApi(owner: false);
      await openMore(tester, fake, 'adjustments');
      await tapVisible(tester, find.byKey(const Key('adjustment-badj_1')));
      expect(find.text('Active'), findsWidgets);
      await tapVisible(tester, find.byKey(const Key('adjustment-void')));
      expect(find.text('Void this adjustment'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('field-reason')), 'no');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(
          find.text('Give a reason of at least 3 characters.'), findsOneWidget);
      expect(fake.api.sent('POST', '$base/adjustments/badj_1/void'), isEmpty);
      await tester.enterText(
          find.byKey(const Key('field-reason')), 'Duplicate of a bill');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(fake.api.sent('POST', '$base/adjustments/badj_1/void').single.body,
          {'reason': 'Duplicate of a bill'});
      expect(find.text('Adjustment voided.'), findsOneWidget);
      expect(find.byKey(const Key('adjustment-void-reason')), findsOneWidget);
      expect(find.byKey(const Key('adjustment-void')), findsNothing);
      await back(tester);
      await tester.pumpAndSettle();
      expect(find.textContaining('Voided'), findsWidgets);
      expectClean(tester);
    });

    testWidgets('its maker cannot approve a pending one; another can',
        (tester) async {
      final fake = MoreApi();
      await openMore(tester, fake, 'adjustments');
      await tapVisible(tester, find.byKey(const Key('adjustment-badj_2')));
      expect(find.byKey(const Key('adjustment-own')), findsOneWidget);
      expect(find.byKey(const Key('adjustment-approve')), findsNothing);
      expect(find.text('Reject'), findsOneWidget);
    });

    testWidgets('a member sees who acts and no Void; no dismissed receipts',
        (tester) async {
      final fake = MoreApi(account: false, owner: false);
      await pumpBooks(tester, fake);
      await tester.tap(find.byKey(const Key('books-more')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('books-more-dismissed')), findsNothing);
      await tester.tap(find.byKey(const Key('books-more-adjustments')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('adjustments-new')), findsNothing);
      await tapVisible(tester, find.byKey(const Key('adjustment-badj_1')));
      expect(
          find.byKey(const Key('adjustment-accountants-only')), findsOneWidget);
      expect(find.byKey(const Key('adjustment-void')), findsNothing);
      expect(find.byKey(const Key('evidence-attach')), findsNothing);
    });

    testWidgets('an adjustment takes a supporting document from the vault',
        (tester) async {
      final fake = MoreApi();
      await openMore(tester, fake, 'adjustments');
      await tapVisible(tester, find.byKey(const Key('adjustment-badj_1')));
      expect(find.byKey(const Key('bill-evidence-missing')), findsOneWidget);
      expect(find.textContaining('This adjustment is at or above'),
          findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('evidence-attach')));
      await tapVisible(tester, find.byKey(const Key('pick-doc_1')));
      expect(fake.api.sent('POST', '$base/evidence').single.body, {
        'source_type': 'adjustment',
        'source_id': 'badj_1',
        'document_id': 'doc_1',
      });
      expect(find.byKey(const Key('evidence-vlink_1')), findsOneWidget);
      expect(find.byKey(const Key('bill-evidence-missing')), findsNothing);
      expectClean(tester);
    });

    testWidgets('empty offers a new adjustment; no match clears filters',
        (tester) async {
      final fake = MoreApi()..noAdjustments = true;
      await openMore(tester, fake, 'adjustments');
      expect(find.byKey(const Key('adjustments-empty')), findsOneWidget);
      expect(find.widgetWithText(FilledButton, 'New adjustment'), findsWidgets);
      await tapVisible(tester, find.byKey(const Key('filter-pending')));
      expect(find.byKey(const Key('adjustments-no-match')), findsOneWidget);
      await tapVisible(tester, find.text('Clear filters'));
      expect(find.byKey(const Key('adjustments-empty')), findsOneWidget);
    });

    testWidgets('denied, offline and failed loads say what to do',
        (tester) async {
      final fake = MoreApi()..adjustmentsFail = 403;
      await openMore(tester, fake, 'adjustments');
      expect(find.byKey(const Key('records-denied')), findsOneWidget);
      expect(find.textContaining('Ask a workspace owner'), findsOneWidget);
      fake.adjustmentsFail = 503;
      await tester.drag(find.byType(Scrollable).first, const Offset(0, 400));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('records-error')), findsOneWidget);
      fake.adjustmentsFail = 0;
      await tapVisible(tester, find.text('Try again'));
      expect(find.text('No connection'), findsOneWidget);
      fake.adjustmentsFail = null;
      await tapVisible(tester, find.text('Try again'));
      expect(find.byKey(const Key('adjustment-badj_1')), findsOneWidget);
    });
  });

  group('manual journal entries', () {
    testWidgets('an entry shows its lines and takes a document',
        (tester) async {
      final fake = MoreApi();
      await openMore(tester, fake, 'journal');
      expect(find.text('Opening balances example'), findsOneWidget);
      expect(find.text('AED 5,000.00'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('journal-bje_1')));
      expect(find.text('1000 Bank'), findsOneWidget);
      expect(find.text('3000 Owner capital'), findsOneWidget);
      expect(find.text('Credit'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('evidence-attach')));
      await tapVisible(tester, find.byKey(const Key('pick-doc_1')));
      expect(fake.api.sent('POST', '$base/evidence').single.body,
          containsPair('source_type', 'journal'));
      expect(find.byKey(const Key('evidence-vlink_1')), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('no entries says where they are recorded', (tester) async {
      final fake = MoreApi()..noEntries = true;
      await openMore(tester, fake, 'journal');
      expect(find.byKey(const Key('journal-empty')), findsOneWidget);
      expect(
          find.textContaining('on the web in Books, Journal'), findsOneWidget);
    });
  });

  group('tax settings', () {
    testWidgets('the deadline line follows the form; save sends it all',
        (tester) async {
      final fake = MoreApi();
      await openMore(tester, fake, 'tax');
      String line() =>
          tester.widget<Text>(find.byKey(const Key('tax-deadline-line'))).data!;
      expect(line(), contains('28 days after the period ends'));
      await tapVisible(tester, find.byKey(const Key('tax-jurisdiction')));
      await tester.tap(find.text('Other country').last);
      await tester.pumpAndSettle();
      expect(line(), contains('ANUM knows no deadline'));
      await tester.enterText(find.byKey(const Key('tax-country')), 'BH');
      await tester.pumpAndSettle();
      expect(line(), contains('the last day of the month after the period'));
      await tester.enterText(find.byKey(const Key('tax-country')), 'KW');
      await tester.pumpAndSettle();
      expect(line(), contains('This country has no VAT'));
      await tester.enterText(find.byKey(const Key('tax-due')), '200');
      await tester.pumpAndSettle();
      expect(line(), contains('200 days'));
      await tapVisible(tester, find.byKey(const Key('tax-save')));
      expect(find.text('The due day is a whole number of days from 1 to 120.'),
          findsOneWidget);
      expect(fake.api.sent('PUT', '$base/tax/settings'), isEmpty);
      await tester.enterText(find.byKey(const Key('tax-due')), '1');
      await tester.pumpAndSettle();
      expect(line(), contains('due 1 day after'));
      await tapVisible(tester, find.byKey(const Key('tax-frequency')));
      await tester.tap(find.text('Monthly').last);
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('tax-cycle')), findsNothing);
      await tapVisible(tester, find.byKey(const Key('tax-save')));
      final body = fake.api.sent('PUT', '$base/tax/settings').single.body!;
      expect(body['jurisdiction'], 'OTHER');
      expect(body['country'], 'KW');
      expect(body['filing_due_days'], 1);
      expect(body['filing_frequency'], 'monthly');
      expect(body['registration_number'], '100000000000003');
      expect(body['version'], 4);
      expect(find.text('Tax settings saved.'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('a stale save shows the API sentence', (tester) async {
      final fake = MoreApi();
      await openMore(tester, fake, 'tax');
      fake.api.failWith = 409;
      fake.api.failMessage =
          'Someone changed this since you opened it. Reload and try again.';
      await tapVisible(tester, find.byKey(const Key('tax-save')));
      expect(find.byKey(const Key('tax-error')), findsOneWidget);
      expect(find.textContaining('Someone changed this'), findsOneWidget);
    });

    testWidgets('a member reads it; nothing set tells them who sets it',
        (tester) async {
      final fake = MoreApi(account: false, owner: false)..canEditTax = false;
      await openMore(tester, fake, 'tax');
      expect(find.byKey(const Key('tax-read-only')), findsOneWidget);
      expect(find.byKey(const Key('tax-save')), findsNothing);
      expect(find.byKey(const Key('tax-deadline-line')), findsOneWidget);
      fake.taxSet = false;
      await tester.drag(find.byType(Scrollable).first, const Offset(0, 400));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('tax-not-set')), findsOneWidget);
    });

    testWidgets('an owner sets tax up from the books country', (tester) async {
      final fake = MoreApi()..taxSet = false;
      await openMore(tester, fake, 'tax');
      expect(find.byKey(const Key('tax-not-set-text')), findsOneWidget);
      // The books are in the UAE (setup country AE).
      expect(find.text('United Arab Emirates'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('tax-save')));
      final body = fake.api.sent('PUT', '$base/tax/settings').single.body!;
      expect(body['jurisdiction'], 'AE');
      expect(body['version'], isNull);
      expect(body['registered'], isFalse);
    });
  });

  group('dismissed receipts', () {
    testWidgets('lists them, says what is gone, restores one', (tester) async {
      final fake = MoreApi();
      await openMore(tester, fake, 'dismissed');
      expect(
          find.text('Receipt example for the bill GOS-1 from Gulf Office '
              'Supplies'),
          findsOneWidget);
      expect(
          find.text(
              'A receipt no longer in the vault, a bill that was removed'),
          findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('restore-bdis_1')));
      expect(
          fake.api.sent(
              'DELETE', '$base/evidence/receipt-matches/dismissed/bdis_1'),
          hasLength(1));
      expect(find.byKey(const Key('dismissed-bdis_1')), findsNothing);
      expect(
          find.text('Restored. The receipt can be proposed for this bill '
              'again.'),
          findsOneWidget);
      expectClean(tester);
    });

    testWidgets('empty and denied', (tester) async {
      final fake = MoreApi()..noDismissed = true;
      await openMore(tester, fake, 'dismissed');
      expect(find.byKey(const Key('dismissed-empty')), findsOneWidget);
      fake.denyDismissed = true;
      await tapVisible(tester, find.text('Refresh'));
      expect(find.byKey(const Key('records-denied')), findsOneWidget);
    });
  });

  testWidgets('an invoice shows its supporting documents', (tester) async {
    final books = MoreApi();
    final c = InvoicesController(FakeInvoices(),
        books: ApiBooksRepository(books.api.client()))
      ..permissions =
          const BusinessPermissions(write: true, manage: true, decide: true);
    tester.view.physicalSize = phone;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(app(InvoiceDetailScreen(
        controller: c, invoiceId: 'binv_1', saver: FakeSaver())));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
        find.byKey(const Key('evidence-attach')), 300,
        scrollable: find.byType(Scrollable).first);
    await tapVisible(tester, find.byKey(const Key('evidence-attach')));
    await tapVisible(tester, find.byKey(const Key('pick-doc_1')));
    expect(books.api.sent('POST', '$base/evidence').single.body,
        containsPair('source_type', 'invoice'));
    expect(find.byKey(const Key('evidence-vlink_1')), findsOneWidget);
    expectClean(tester);
  });

  for (final size in const [phone, Size(768, 1024)]) {
    for (final locale in const [Locale('en'), Locale('ar')]) {
      final name = '${size.width.toInt()}x${size.height.toInt()} '
          '(${locale.languageCode})';
      testWidgets('every new books screen lays out at $name', (tester) async {
        final rtl = locale.languageCode == 'ar';
        final fake = MoreApi()..many = true;
        await openMore(tester, fake, 'adjustments', size: size, locale: locale);
        expectClean(tester);
        expect(
            Directionality.of(
                tester.element(find.byKey(const Key('adjustment-badj_1')))),
            rtl ? TextDirection.rtl : TextDirection.ltr);
        await tapVisible(tester, find.byKey(const Key('adjustment-badj_1')));
        await tester.ensureVisible(find.byKey(const Key('evidence-attach')));
        await tester.pumpAndSettle();
        expectClean(tester);
        await back(tester);
        await tester.pumpAndSettle();
        await back(tester);
        await tester.pumpAndSettle();
        for (final page in ['journal', 'tax', 'dismissed']) {
          await tester.tap(find.byKey(const Key('books-more')));
          await tester.pumpAndSettle();
          await tester.tap(find.byKey(Key('books-more-$page')));
          await tester.pumpAndSettle();
          expectClean(tester);
          if (page == 'tax') {
            await tapVisible(tester, find.byKey(const Key('tax-jurisdiction')));
            await tester
                .tap(find.text(rtl ? 'دولة أخرى' : 'Other country').last);
            await tester.pumpAndSettle();
            await tester.ensureVisible(find.byKey(const Key('tax-save')));
            await tester.pumpAndSettle();
            expectClean(tester);
          }
          if (page == 'journal') {
            await tapVisible(tester, find.byKey(const Key('journal-bje_1')));
            expectClean(tester);
            await back(tester);
            await tester.pumpAndSettle();
          }
          await back(tester);
          await tester.pumpAndSettle();
        }
      });
    }
  }
}
