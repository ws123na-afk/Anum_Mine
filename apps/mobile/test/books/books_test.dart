// Business books on the phone (docs/business-books.md#mobile) against a
// fake API with the API's JSON shapes (packages/contracts/src/businessBooks.ts):
// setup and permissions, the P&L for a period and basis (read only), the
// bills with open and overdue totals and paging, recording a payment with
// an idempotency key, owner-only voids, and every state.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/books/books_controller.dart';
import 'package:anum_mobile/features/books/books_data.dart';
import 'package:anum_mobile/features/books/books_screen.dart';
import 'package:anum_mobile/features/books/books_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

Map<String, Object?> setupJson(
        {bool configured = true, bool manage = false, bool write = true}) =>
    {
      'settings': configured
          ? {
              'id': 'bset_1',
              'base_currency': 'AED',
              'country': 'AE',
              'fiscal_year_start_month': 1,
              'basis': 'accrual',
              'tax_label': 'VAT',
              'category_sections': <String, Object?>{},
              'rate_max_age_days': 31,
              'version': 2,
              'updated_by': 'user_owner',
              'updated_at': '2026-11-01T08:00:00Z',
              'created_at': '2026-11-01T08:00:00Z',
            }
          : null,
      'permissions': {'read': true, 'write': write, 'manage': manage},
      'counts': {
        'vendors': 2, 'bills': 2, 'cost_centres': 0, 'budgets': 0, //
        'rates': 0, 'adjustments': 0, 'intercompany': 0, 'allocations': 1,
      },
      'default_period':
          configured ? {'from': '2026-01-01', 'to': '2026-12-31'} : null,
      'cost_centres': <Object?>[],
      'allocation_categories': ['fuel'],
      'sections': ['cost_of_sales', 'operating_expense', 'capital'],
      'adjustment_kinds': <Object?>[],
    };

Map<String, Object?> pnlGroup(List<Map<String, Object?>> lines, String total) =>
    {'lines': lines, 'total': total};

Map<String, Object?> reportJson({String basis = 'accrual', bool empty = false}) => {
      'period': {'from': '2026-01-01', 'to': '2026-12-31', 'basis': basis},
      'base_currency': 'AED',
      'pnl': {
        'currency': 'AED',
        'revenue': empty
            ? pnlGroup([], '0.00')
            : pnlGroup([
                {'key': 'service', 'amount': '12000.00'}
              ], '12000.00'),
        'cost_of_sales': pnlGroup([], '0.00'),
        'gross_profit': empty ? '0.00' : '12000.00',
        'gross_margin_pct': empty ? null : '100.0',
        'operating_expenses': empty
            ? pnlGroup([], '0.00')
            : pnlGroup([
                {'key': 'fuel', 'amount': '150.00'}
              ], '150.00'),
        'operating_profit': empty ? '0.00' : '11850.00',
        'other_income': pnlGroup([], '0.00'),
        'finance_costs': pnlGroup([], '0.00'),
        'fx_differences': '0.00',
        'net_profit': empty ? '0.00' : '11850.00',
        'net_margin_pct': empty ? null : '98.8',
        'not_converted': empty
            ? <Object?>[]
            : [
                {
                  'currency': 'SAR',
                  'groups': {'operating_expenses': '300.00'}
                }
              ],
        'complete': empty,
      },
      'tax': {
        'label': 'VAT',
        'country': 'AE',
        'by_currency': <Object?>[],
        'allocations_without_tax_split': 0
      },
      'cash_flow': {'lines': <String, Object?>{}, 'net': <Object?>[], 'paid_by_members': <String, Object?>{}},
      'balances': {
        'as_of': '2026-12-31',
        'aged_on': '2026-11-05',
        'receivables': [
          {'currency': 'AED', 'total': '5000.00', 'current': '5000.00'}
        ],
        'outstanding_invoices': <Object?>[],
        'outstanding_invoices_count': 1,
        'payables': [
          {'currency': 'AED', 'total': '1050.00', 'current': '1050.00'}
        ],
        'unpaid_bills': <Object?>[],
        'unpaid_bills_count': 1,
        'owed_to_members': <Object?>[],
        'debt': <Object?>[],
        'intercompany': <Object?>[],
      },
      'equity': {'contributions': <Object?>[], 'drawings': <Object?>[]},
      'trend': <Object?>[],
      'customers': <Object?>[],
      'projects': <Object?>[],
      'cost_centres': <Object?>[],
      'budgets': <Object?>[],
      'recurring': {'items': <Object?>[], 'per_month': <Object?>[]},
      'allocations_in_review': 0,
      'conversions': <Object?>[],
      'notes': ['Figures in SAR have no rate on or before their date.'],
      'truncated': false,
    };

Map<String, Object?> billJson(String id,
        {String state = 'unpaid',
        String balance = '1050.00',
        bool overdue = false,
        bool canVoid = false,
        List<Map<String, Object?>>? payments}) =>
    {
      'id': id,
      'vendor_id': 'bven_1',
      'vendor_name': 'Gulf Office Supplies',
      'reference': 'INV-2201',
      'description': 'Printer paper and toner',
      'bill_date': '2026-10-20',
      'due_date': '2026-11-19',
      'currency': 'AED',
      'subtotal': '1000.00',
      'tax': '50.00',
      'total': '1050.00',
      'category': 'office',
      'section': 'operating_expense',
      'cost_centre_id': null,
      'project_id': null,
      'recurrence': null,
      'useful_life_months': null,
      'status': 'open',
      'version': 1,
      'created_by': 'user_me',
      'created_at': '2026-10-20T08:00:00Z',
      'updated_at': '2026-10-20T08:00:00Z',
      'voided_by': null,
      'voided_at': null,
      'void_reason': '',
      'amount_paid': state == 'unpaid' ? '0.00' : '1050.00',
      'balance': balance,
      'payment_state': state,
      'overdue': overdue,
      'days_overdue': overdue ? 4 : 0,
      'can': {
        'edit': true,
        'edit_money': state == 'unpaid',
        'pay': state != 'paid',
        'void': canVoid
      },
      if (payments != null) 'payments': payments,
    };

Map<String, Object?> paymentJson(String id) => {
      'id': id,
      'bill_id': 'bbill_1',
      'kind': 'recorded',
      'allocation_id': null,
      'amount': '1050.00',
      'currency': 'AED',
      'paid_on': '2026-11-05',
      'method': 'bank_transfer',
      'reference': 'TRF-1',
      'note': '',
      'recorded_by': 'user_me',
      'voided_by': null,
      'voided_at': null,
      'void_reason': '',
      'created_at': '2026-11-05T08:00:00Z',
      'counts': true,
      'allocation_removed': false,
    };

class BooksApi {
  BooksApi({this.configured = true, this.manage = false, this.emptyBills = false});
  bool configured, manage, emptyBills;
  String billState = 'unpaid';
  final payments = <Map<String, Object?>>[];
  late final api = FakeApi(route);

  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path;
    final q = r.uri.queryParameters;
    const base = '/api/v1/business/books';
    if (r.method == 'GET' && path == '$base/setup') {
      return ok(setupJson(configured: configured, manage: manage));
    }
    if (r.method == 'PUT' && path == '$base/settings') {
      configured = true;
      return ok(setupJson()['settings']! as Map<String, Object?>);
    }
    if (r.method == 'GET' && path == '$base/report') {
      return ok(reportJson(
          basis: q['basis'] ?? 'accrual', empty: q['from'] == '2026-10-01'));
    }
    if (r.method == 'GET' && path == '$base/bills') {
      final offset = int.parse(q['offset'] ?? '0');
      final items = emptyBills
          ? <Map<String, Object?>>[]
          : offset == 0
              ? [
                  billJson('bbill_1', state: billState, canVoid: manage),
                  billJson('bbill_2', overdue: true)
                ]
              : [billJson('bbill_3', state: 'paid', balance: '0.00')];
      return ok({
        'items': items,
        'total': emptyBills ? 0 : 3,
        'limit': 25,
        'offset': offset,
        'summary': {
          'open': [
            {'currency': 'AED', 'amount': '2100.00'}
          ],
          'overdue': [
            {'currency': 'AED', 'amount': '1050.00'}
          ],
        },
      });
    }
    if (r.method == 'GET' && path == '$base/bills/bbill_1') {
      return ok(billJson('bbill_1',
          state: billState,
          balance: billState == 'paid' ? '0.00' : '1050.00',
          canVoid: manage,
          payments: payments));
    }
    if (r.method == 'POST' && path == '$base/bills/bbill_1/payments') {
      payments.add(paymentJson('bpay_1'));
      billState = 'paid';
      return ok(
          billJson('bbill_1',
              state: 'paid', balance: '0.00', canVoid: manage, payments: payments),
          201);
    }
    if (r.method == 'POST' && path == '$base/bills/bbill_1/payments/bpay_1/void') {
      if (!manage) {
        return refuse(
            403,
            'Only workspace owners can do this (settings, exchange rates, adjustments, voiding and intercompany entries).',
            'forbidden');
      }
      payments[0] = {
        ...paymentJson('bpay_1'),
        'voided_at': '2026-11-05T09:00:00Z',
        'void_reason': r.body!['reason'],
        'counts': false
      };
      billState = 'unpaid';
      return ok(billJson('bbill_1', canVoid: true, payments: payments));
    }
    return null;
  }
}

Future<BooksController> pumpBooks(WidgetTester tester, BooksApi fake,
    {Size size = phone, double textScale = 1, Locale locale = const Locale('en')}) async {
  final controller = BooksController(ApiBooksRepository(fake.api.client()),
      today: () => DateTime(2026, 11, 5));
  addTearDown(controller.dispose);
  await pumpModule(tester, BooksScreen(controller: controller),
      size: size, textScale: textScale, locale: locale);
  return controller;
}

void main() {
  test('catalogs match in English and Arabic', () {
    expect(BooksStrings.catalogsMatch, isTrue);
  });

  group('repository', () {
    test('reads setup, report and a bill page with totals', () async {
      final fake = BooksApi();
      final repo = ApiBooksRepository(fake.api.client());
      final setup = await repo.setup();
      expect(setup.configured, isTrue);
      expect(setup.permissions.manage, isFalse);
      expect(setup.periodFrom, '2026-01-01');
      final report = await repo.report(from: '2026-01-01', to: '2026-12-31', basis: 'cash');
      expect(report.basis, 'cash');
      expect(report.netProfit, '11850.00');
      expect(report.notConverted.single.currency, 'SAR');
      expect(report.receivables.single.amount, '5000.00');
      final page = await repo.bills(status: 'overdue');
      expect(page.items, hasLength(2));
      expect(page.open.single.amount, '2100.00');
      expect(page.next, '2');
      expect(fake.api.requests.last.uri.queryParameters['status'], 'overdue');
    });

    test('a payment carries its idempotency key', () async {
      final fake = BooksApi();
      final repo = ApiBooksRepository(fake.api.client());
      final bill = await repo.pay('bbill_1',
          {'kind': 'recorded', 'amount': '1050.00', 'paid_on': '2026-11-05'},
          idempotencyKey: 'key_example');
      expect(bill.paymentState, 'paid');
      expect(bill.payments!.single.method, 'bank_transfer');
      expect(fake.api.requests.last.headers['Idempotency-Key'], 'key_example');
    });

    test('a member voiding a payment gets the owner-only refusal', () async {
      final fake = BooksApi();
      final repo = ApiBooksRepository(fake.api.client());
      await expectLater(repo.voidPayment('bbill_1', 'bpay_1', 'Typed twice'),
          throwsA(isA<ApiException>()
              .having((e) => e.isPermissionDenied, 'denied', isTrue)
              .having((e) => e.message, 'message', contains('workspace owners'))));
    });
  });

  group('screen', () {
    testWidgets('P&L for the financial year, then cash basis and last month',
        (tester) async {
      final fake = BooksApi();
      await pumpBooks(tester, fake);
      expect(find.byKey(const Key('books-net-profit')), findsOneWidget);
      expect(find.text('AED 11,850.00'), findsWidgets);
      expect(find.byKey(const Key('books-not-converted')), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('basis-cash')));
      expect(fake.api.requests.last.uri.queryParameters['basis'], 'cash');
      await tapVisible(tester, find.byKey(const Key('period-last')));
      expect(fake.api.requests.last.uri.queryParameters,
          containsPair('from', '2026-10-01'));
      expect(find.text('Nothing in this period yet'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('bills with totals and paging; a member records a payment',
        (tester) async {
      final fake = BooksApi();
      await pumpBooks(tester, fake);
      await tester.tap(find.text('Bills'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('bills-totals')), findsOneWidget);
      expect(find.textContaining('Open: AED 2,100.00'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('records-more')));
      expect(find.byKey(const Key('bill-bbill_3')), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('bill-bbill_1')));
      // Owner-only voids are explained, not offered, to a member.
      expect(find.byKey(const Key('bill-void')), findsNothing);
      expect(find.textContaining('Only workspace owners can void'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('bill-pay')));
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      final sent = fake.api
          .sent('POST', '/api/v1/business/books/bills/bbill_1/payments')
          .single;
      expect(sent.body!['amount'], '1050.00');
      expect(sent.body!['kind'], 'recorded');
      expect(sent.headers['Idempotency-Key'], startsWith('bpay'));
      expect(find.byKey(const Key('payment-bpay_1')), findsOneWidget);
      expect(find.text('Paid'), findsWidgets);
      expect(find.byKey(const Key('void-payment-bpay_1')), findsNothing);
      expectClean(tester);
    });

    testWidgets('an owner voids a payment with a reason', (tester) async {
      final fake = BooksApi(manage: true)..payments.add(paymentJson('bpay_1'));
      fake.billState = 'paid';
      await pumpBooks(tester, fake);
      await tester.tap(find.text('Bills'));
      await tester.pumpAndSettle();
      await tapVisible(tester, find.byKey(const Key('bill-bbill_1')));
      await tapVisible(tester, find.byKey(const Key('void-payment-bpay_1')));
      await tester.enterText(find.byKey(const Key('field-reason')), 'Typed twice');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(find.textContaining('Voided: Typed twice'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('books not set up: an owner sets them up', (tester) async {
      final fake = BooksApi(configured: false, manage: true);
      await pumpBooks(tester, fake);
      expect(find.byKey(const Key('books-not-set-up')), findsOneWidget);
      await tester.tap(find.text('Set up the books'));
      await tester.pumpAndSettle();
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      final body =
          fake.api.sent('PUT', '/api/v1/business/books/settings').single.body!;
      expect(body['base_currency'], 'AED');
      expect(body['version'], isNull);
      expect(find.byKey(const Key('books-net-profit')), findsOneWidget);
    });

    testWidgets('books not set up: a member is told to ask an owner',
        (tester) async {
      await pumpBooks(tester, BooksApi(configured: false));
      expect(find.textContaining('A workspace owner sets the base currency'),
          findsOneWidget);
      expect(find.text('Set up the books'), findsNothing);
    });

    testWidgets('no bills yet', (tester) async {
      await pumpBooks(tester, BooksApi(emptyBills: true));
      await tester.tap(find.text('Bills'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('bills-empty')), findsOneWidget);
    });

    testWidgets('a failed load says what failed', (tester) async {
      final fake = BooksApi();
      fake.api.failWith = 500;
      fake.api.failMessage = 'The books could not be read.';
      await pumpBooks(tester, fake);
      expect(find.byKey(const Key('records-error')), findsOneWidget);
      expect(find.textContaining('The books could not be read.'), findsOneWidget);
    });

    testWidgets('a viewer is told who can open the books', (tester) async {
      final fake = BooksApi();
      fake.api.failWith = 403;
      fake.api.failMessage =
          'The business books are for workspace owners and members. Ask a workspace owner to make you a member.';
      await pumpBooks(tester, fake);
      expect(find.byKey(const Key('records-denied')), findsOneWidget);
    });

    testWidgets('Arabic, 320 px, 200 percent text, and a tablet',
        (tester) async {
      await pumpBooks(tester, BooksApi(),
          size: narrow, textScale: 2, locale: const Locale('ar'));
      expect(find.text('دفاتر الأعمال'), findsWidgets);
      expectClean(tester);
      await tester.tap(find.text('فواتير الموردين'));
      await tester.pumpAndSettle();
      expectClean(tester);
      await pumpBooks(tester, BooksApi(), size: tablet);
      expectClean(tester);
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
    });
  });
}
