import 'dart:convert';
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/business/billing_profile.dart';
import 'package:anum_mobile/features/business/business_controller.dart';
import 'package:anum_mobile/features/business/business_models.dart';
import 'package:anum_mobile/features/business/business_screen.dart';
import 'package:anum_mobile/features/business/invoice_controller.dart';
import 'package:anum_mobile/features/business/invoice_document.dart';
import 'package:anum_mobile/features/business/invoice_form.dart';
import 'package:anum_mobile/features/business/invoice_models.dart';
import 'package:anum_mobile/features/business/invoice_repository.dart';
import 'package:anum_mobile/features/business/invoices.dart';
import 'package:anum_mobile/features/business/portal_access.dart';
import 'package:anum_mobile/features/business/project_money.dart';
import 'package:anum_mobile/src/localization/business_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'business_test.dart' show FakeBusiness, app;

// Business invoices on the phone (docs/business.md#invoices): the per-currency
// summary, the filtered and paged list, a new invoice from milestones, a
// contract share, billable costs, an offer and written lines, the invoice's
// actions driven by the server's `can`, payments, void, the printable copy,
// the billing profile and portal links. Wire shapes as /api/v1/business sends
// them.

const _token =
    'anum_portal.tenant_local.ws_default.bpacc_9.Zk3fQ8m1Vb7nX2cL5hRt9yUpWd4sAe6J';

Map<String, Object?> lineJson(
  String description, {
  String source = 'manual',
  double total = 575,
  String? sourceId,
}) =>
    {
      'description': description,
      'kind': 'service',
      'quantity': 1.0,
      'unit_price': total / 1.15,
      'tax_rate_pct': 15.0,
      'source': source,
      'source_id': sourceId,
      'net': 500.0,
      'tax': 75.0,
      'total': total,
    };

Map<String, Object?> can(String status, {required bool owner, int paid = 0}) =>
    {
      'edit': status == 'draft' || status == 'pending_approval',
      'request_send': status == 'draft',
      'cancel_request': status == 'pending_approval',
      'delete': status == 'draft',
      'void': owner && status == 'sent' && paid == 0,
      'record_payment': status == 'sent',
      'remove_payment': owner && (status == 'sent' || status == 'paid'),
    };

Map<String, Object?> invoiceJson({
  String id = 'binv_1',
  String status = 'draft',
  bool owner = true,
  List<String> issues = const [],
  String decisionNote = '',
  List<Map<String, Object?>> payments = const [],
  double total = 1150,
  String approvalState = 'none',
  bool overdue = false,
}) {
  final paid = payments.fold<double>(0, (s, p) => s + (p['amount']! as double));
  final number = status == 'draft' || status == 'pending_approval'
      ? null
      : 'INV-2026-0001';
  return {
    'id': id,
    'account_id': 'bacc_1',
    'project_id': 'bproj_1',
    'status': status,
    'currency': 'SAR',
    'lines': [
      lineJson('Deposit', source: 'milestone', sourceId: 'bmile_1'),
      lineJson('Training day'),
    ],
    'subtotal': 1000.0,
    'tax': 150.0,
    'total': total,
    'amount_paid': paid,
    'due_in_days': 14,
    'number': number,
    'issue_date': number == null ? null : '2026-10-05',
    'due_date': number == null ? null : '2026-10-19',
    'customer_reference': 'PO-77',
    'notes': 'Thank you.',
    'payment_terms': 'Pay within 14 days.',
    'bill_to_name': 'Gulf Mixers',
    'bill_to_address': 'Riyadh',
    'bill_to_email': 'sara@gulfmixers.example',
    'bill_to_tax_number': '300011112200003',
    'seller': number == null
        ? null
        : {
            'legal_name': 'Shahzeb Digital',
            'address': 'Riyadh, Saudi Arabia',
            'tax_label': 'VAT number',
            'tax_number': '300099998800003',
            'email': 'billing@shahzeb.example',
            'phone': '',
            'bank_details': 'IBAN SA00 0000 0000 0000',
            'footer_note': '',
            'invoice_prefix': 'INV',
            'default_due_days': 14,
            'default_tax_rate_pct': 15.0,
          },
    'void_reason': status == 'void' ? 'Wrong customer' : '',
    'decision_note': decisionNote,
    'account_name': 'Gulf Mixers',
    'project_name': 'Fleet reporting pilot',
    'balance': total - paid,
    'overdue': overdue,
    'days_overdue': overdue ? 14 : 0,
    'payment_state': paid == 0
        ? 'unpaid'
        : paid >= total
            ? 'paid'
            : 'partially_paid',
    'payments': payments,
    'approval_state': approvalState,
    'approval': approvalState == 'pending'
        ? {'id': 'appr_1', 'payload_hash': 'abcdef0123456789abcdef'}
        : null,
    'issues': issues,
    'can': can(status, owner: owner, paid: paid > 0 ? 1 : 0),
  };
}

Map<String, Object?> paymentJson(String id, double amount) => {
      'id': id,
      'amount': amount,
      'paid_on': '2026-10-10',
      'method': 'bank_transfer',
      'reference': 'TRX-1',
      'note': '',
    };

Map<String, Object?> listItem(int i) => {
      'id': 'binv_$i',
      'number': i == 0 ? 'INV-2026-0001' : null,
      'status': i == 0
          ? 'sent'
          : i == 1
              ? 'pending_approval'
              : 'draft',
      'account_name': 'Client $i',
      'project_name': i == 0 ? 'Fleet reporting pilot' : null,
      'currency': 'SAR',
      'total': 1150.0,
      'amount_paid': 0.0,
      'balance': 1150.0,
      'issue_date': i == 0 ? '2026-10-01' : null,
      'due_date': i == 0 ? '2026-10-10' : null,
      'overdue': i == 0,
      'days_overdue': i == 0 ? 9 : 0,
      'payment_state': 'unpaid',
      'customer_reference': '',
      'lines': 2,
    };

Map<String, Object?> summaryJson() => {
      'counts': {'draft': 3, 'sent': 1},
      'currencies': [
        {
          'currency': 'SAR',
          'outstanding': 1150.0,
          'overdue': 1150.0,
          'overdue_count': 1,
          'invoiced_this_month': 2300.0,
          'collected_this_month': 500.0,
          'drafts': 800.0,
        },
        {
          'currency': 'USD',
          'outstanding': 200.0,
          'overdue': 0.0,
          'overdue_count': 0,
          'invoiced_this_month': 0.0,
          'collected_this_month': 0.0,
          'drafts': 0.0,
        },
      ],
      'truncated': false,
      'notes': [
        'Amounts in different currencies are listed apart and never converted.'
      ],
    };

Map<String, Object?> billableJson({bool milestones = true}) => {
      'account_id': 'bacc_1',
      'project_id': 'bproj_1',
      'currency': 'SAR',
      'tax_rate_pct': 15.0,
      'milestones': milestones
          ? [
              {
                'id': 'bmile_1',
                'position': 1,
                'title': 'Deposit',
                'amount': 1250.0,
                'due_on': '2026-10-30',
                'status': 'accepted',
                'change_order_number': null,
                'billable': true,
                'reason': '',
              },
              {
                'id': 'bmile_2',
                'position': 2,
                'title': 'Final delivery',
                'amount': 1250.0,
                'due_on': null,
                'status': 'planned',
                'change_order_number': null,
                'billable': false,
                'reason': 'Already on invoice INV-2026-0001.',
              },
              {
                'id': 'bmile_3',
                'position': 3,
                'title': 'Change order 1: Extra dashboards',
                'amount': 400.0,
                'due_on': null,
                'status': 'planned',
                'change_order_number': 1,
                'billable': true,
                'reason': '',
              },
            ]
          : <Object?>[],
      'contract': milestones
          ? null
          : {'contract_total': 5000.0, 'invoiced': 1000.0, 'remaining': 4000.0},
      'usage': [
        {
          'id': 'busage_1',
          'description': 'Extra support hours',
          'kind': 'labour',
          'occurred_on': '2026-10-02',
          'quantity': 2.0,
          'unit_cost': 100.0,
          'amount': 200.0,
          'currency': 'SAR',
          'project_id': 'bproj_1',
          'billable': true,
          'reason': '',
        },
        {
          'id': 'busage_2',
          'description': 'Hosting in USD',
          'kind': 'hosting',
          'occurred_on': '2026-10-03',
          'quantity': 1.0,
          'unit_cost': 30.0,
          'amount': 30.0,
          'currency': 'USD',
          'project_id': 'bproj_1',
          'billable': false,
          'reason': 'In USD, not SAR.',
        },
      ],
      'offers': <Object?>[],
    };

Map<String, Object?> accountOnlyBillableJson() => {
      ...billableJson(milestones: false),
      'project_id': null,
      'contract': null,
      'usage': <Object?>[],
      'offers': [
        {
          'id': 'boffer_1',
          'label': 'Monthly support v2',
          'currency': 'SAR',
          'price_type': 'fixed',
          'price': 900.0
        },
        {
          'id': 'boffer_2',
          'label': 'Custom dashboard v1',
          'currency': 'SAR',
          'price_type': 'quote',
          'price': null
        },
      ],
    };

class FakeSaver implements InvoiceDocumentSaver {
  final saved = <(String, String)>[];
  @override
  Future<bool> save(String fileName, Uint8List bytes) async {
    saved.add((fileName, utf8.decode(bytes)));
    return true;
  }
}

class FakeInvoices implements InvoicesRepository {
  FakeInvoices({
    this.total = 45,
    this.deny = false,
    this.owner = true,
    this.noContract = false,
  });
  int total;
  bool deny, owner, noContract;
  bool failList = false;
  final requests = <String>[];
  Map<String, Object?>? createBody, updateBody, paymentBody;
  Map<String, Object?> current = invoiceJson();
  BillingProfile profile = const BillingProfile(
      legalName: 'Shahzeb Digital', invoicePrefix: 'INV', defaultDueDays: 14);
  BillingProfile? savedProfile;
  List<PortalAccess> links = [];

  Invoice get inv => Invoice.fromJson(JsonReader.of(current));

  @override
  Future<InvoiceSummary> summary() async {
    requests.add('summary');
    if (deny) {
      throw const ApiException(
          403, 'Business is for workspace owners and members.');
    }
    return InvoiceSummary.fromJson(JsonReader.of(summaryJson()));
  }

  @override
  Future<BusinessPage<InvoiceListItem>> list(InvoiceFilter filter,
      {required int offset, int limit = 20}) async {
    requests.add('list ${filter.toQuery()} $offset');
    if (deny) {
      throw const ApiException(403,
          'Business is for workspace owners and members: it holds contacts\' personal data.');
    }
    if (failList) throw const ApiException(500, 'The server failed.');
    final count = (total - offset).clamp(0, limit);
    return BusinessPage([
      for (var i = 0; i < count; i++)
        InvoiceListItem.fromJson(JsonReader.of(listItem(offset + i)))
    ], total);
  }

  @override
  Future<Invoice> get(String id) async {
    requests.add('get $id');
    return inv;
  }

  @override
  Future<Billable> billable({String? accountId, String? projectId}) async {
    requests.add('billable $accountId $projectId');
    return Billable.fromJson(JsonReader.of(projectId == null
        ? accountOnlyBillableJson()
        : billableJson(milestones: !noContract)));
  }

  @override
  Future<Invoice> create(Map<String, Object?> body) async {
    requests.add('create');
    createBody = body;
    current = invoiceJson(owner: owner);
    return inv;
  }

  @override
  Future<Invoice> update(String id, Map<String, Object?> body) async {
    requests.add('update $id');
    updateBody = body;
    return inv;
  }

  @override
  Future<void> delete(String id) async => requests.add('delete $id');

  @override
  Future<Invoice> requestApproval(String id) async {
    requests.add('request $id');
    current = invoiceJson(
        status: 'pending_approval', owner: owner, approvalState: 'pending');
    return inv;
  }

  @override
  Future<Invoice> cancelRequest(String id) async {
    requests.add('cancel-request $id');
    current = invoiceJson(owner: owner);
    return inv;
  }

  @override
  Future<Invoice> voidInvoice(String id, String reason) async {
    requests.add('void $id $reason');
    current = invoiceJson(status: 'void', owner: owner);
    return inv;
  }

  @override
  Future<Invoice> recordPayment(String id, Map<String, Object?> body) async {
    requests.add('payment $id');
    paymentBody = body;
    final amount = (body['amount']! as num).toDouble();
    current = invoiceJson(
        status: amount >= 1150 ? 'paid' : 'sent',
        owner: owner,
        payments: [paymentJson('binpay_1', amount)]);
    return inv;
  }

  @override
  Future<Invoice> removePayment(String id, String paymentId) async {
    requests.add('remove-payment $id $paymentId');
    current = invoiceJson(status: 'sent', owner: owner);
    return inv;
  }

  @override
  Future<String> printHtml(String id, String lang) async {
    requests.add('print $id $lang');
    return '<html><body>Invoice $lang</body></html>';
  }

  @override
  Future<BillingProfile> billingProfile() async {
    requests.add('profile');
    return profile;
  }

  @override
  Future<BillingProfile> saveBillingProfile(BillingProfile next) async {
    requests.add('save-profile');
    savedProfile = next;
    profile = next;
    return next;
  }

  @override
  Future<BusinessPage<ClientAccount>> accounts(
      {String? query, required int offset, int limit = 20}) async {
    requests.add('accounts $query $offset');
    final all = [
      for (var i = 0; i < 3; i++)
        ClientAccount.fromJson(JsonReader.of({
          'id': 'bacc_$i',
          'name': 'Client $i',
          'contacts': 2,
          'open_leads': 1,
          'city': 'Riyadh',
          'country': 'Saudi Arabia',
        }))
    ].where((a) => query == null || query.isEmpty || a.name.contains(query));
    final items = all.toList();
    return BusinessPage(items, items.length);
  }

  @override
  Future<ClientDetail> account(String id) async =>
      ClientDetail.fromJson(JsonReader.of({
        'id': id,
        'name': 'Gulf Mixers',
        'city': 'Riyadh',
        'country': 'Saudi Arabia',
        'do_not_contact': false,
        'contacts': [
          {
            'id': 'bcon_1',
            'name': 'Sara Khan',
            'email': 'sara@gulfmixers.example',
            'role_title': 'Operations',
            'do_not_contact': false
          },
          {
            'id': 'bcon_2',
            'name': 'Omar Ali',
            'email': null,
            'role_title': '',
            'do_not_contact': false
          },
        ],
      }));

  @override
  Future<List<ProjectChoice>> projects() async => [
        ProjectChoice.fromJson(JsonReader.of({
          'id': 'bproj_1',
          'name': 'Fleet reporting pilot',
          'account_id': 'bacc_1',
          'account_name': 'Gulf Mixers',
          'currency': 'SAR',
          'status': 'active',
        }))
      ];

  @override
  Future<List<PortalAccess>> portalAccess(String accountId) async {
    requests.add('access $accountId');
    return links;
  }

  @override
  Future<PortalAccessCreated> createPortalAccess({
    required String accountId,
    required String contactId,
    required int expiresInDays,
    required bool canDecide,
  }) async {
    requests.add('create-access $contactId $expiresInDays $canDecide');
    final json = {
      'id': 'bpacc_9',
      'account_id': accountId,
      'contact_id': contactId,
      'contact_name': 'Omar Ali',
      'contact_email': null,
      'status': 'active',
      'expired': false,
      'can_decide': canDecide,
      'expires_at': '2027-01-17T00:00:00Z',
      'last_used_at': null,
      'created_at': '2026-10-19T00:00:00Z',
      'created_by': 'user_owner',
      'revoked_at': null,
      'token': _token,
      'portal_path': '/portal#access=$_token',
    };
    links = [...links, PortalAccess.fromJson(JsonReader.of(json))];
    return PortalAccessCreated.fromJson(JsonReader.of(json));
  }

  @override
  Future<PortalAccess> revokePortalAccess(String id) async {
    requests.add('revoke $id');
    links = [
      for (final a in links)
        if (a.id != id) a
    ];
    return PortalAccess.fromJson(JsonReader.of({
      'id': id,
      'contact_id': 'bcon_1',
      'contact_name': 'Sara Khan',
      'status': 'revoked',
      'expired': false,
      'can_decide': true,
      'expires_at': '2027-01-17T00:00:00Z',
    }));
  }
}

PortalAccess accessFor(String id, String contactId,
        {bool canDecide = true, String? lastUsed}) =>
    PortalAccess.fromJson(JsonReader.of({
      'id': id,
      'contact_id': contactId,
      'contact_name': 'Sara Khan',
      'contact_email': 'sara@gulfmixers.example',
      'status': 'active',
      'expired': false,
      'can_decide': canDecide,
      'expires_at': '2027-01-17T00:00:00Z',
      'last_used_at': lastUsed,
    }));

/// Scrolls [finder] into view and lets the frame land before anything taps it.
Future<void> reveal(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
}

/// Drops the current screen so the next one starts from its own initState.
Future<void> reset(WidgetTester tester) =>
    tester.pumpWidget(const SizedBox.shrink());

void sized(WidgetTester tester, double width, [double height = 3000]) {
  tester.view.physicalSize = Size(width, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

InvoicesController controllerFor(FakeInvoices repo,
    {bool write = true, bool manage = true}) {
  final c = InvoicesController(repo)
    ..permissions =
        BusinessPermissions(write: write, manage: manage, decide: manage);
  return c;
}

Future<void> pumpInvoices(WidgetTester tester, FakeInvoices repo,
    {Locale locale = const Locale('en'),
    double textScale = 1,
    VoidCallback? onOpenApprovals}) async {
  final invoices = controllerFor(repo);
  await reset(tester);
  await tester.pumpWidget(app(
      BusinessScreen(
          controller: BusinessController(FakeBusiness(), invoices: invoices),
          onOpenApprovals: onOpenApprovals),
      locale: locale,
      textScale: textScale));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const Key('biz-tab-invoices')));
  await tester.pumpAndSettle();
}

Future<InvoicesController> pumpDetail(WidgetTester tester, FakeInvoices repo,
    {bool write = true,
    bool manage = true,
    double textScale = 1,
    Locale locale = const Locale('en'),
    VoidCallback? onOpenApprovals,
    InvoiceDocumentSaver? saver}) async {
  final c = controllerFor(repo, write: write, manage: manage);
  await reset(tester);
  await tester.pumpWidget(app(
      InvoiceDetailScreen(
          controller: c,
          invoiceId: 'binv_1',
          onOpenApprovals: onOpenApprovals,
          saver: saver ?? FakeSaver()),
      textScale: textScale,
      locale: locale));
  await tester.pumpAndSettle();
  return c;
}

void main() {
  test('the invoice and portal catalogs match and use no long dashes', () {
    expect(businessAr.keys.toSet(), businessEn.keys.toSet());
    final placeholders = RegExp(r'\{(\w+)\}');
    for (final key in businessEn.keys.where((k) =>
        k.startsWith('biz.inv') ||
        k.startsWith('biz.portal') ||
        k.startsWith('portal.') ||
        k.startsWith('biz.clients'))) {
      expect(placeholders.allMatches(businessAr[key]!).map((m) => m[1]).toSet(),
          placeholders.allMatches(businessEn[key]!).map((m) => m[1]).toSet(),
          reason: key);
      expect(
          businessEn[key]!.contains('\u2014') ||
              businessEn[key]!.contains('\u2013'),
          isFalse,
          reason: key);
      expect(
          businessAr[key]!.contains('\u2014') ||
              businessAr[key]!.contains('\u2013'),
          isFalse,
          reason: key);
    }
    // Every status, method, kind and source a screen builds a key from.
    for (final status in invoiceStatuses) {
      expect(businessEn, contains('biz.inv.status.$status'));
    }
    for (final method in paymentMethods) {
      expect(businessEn, contains('biz.inv.method.$method'));
    }
    for (final kind in lineKinds) {
      expect(businessEn, contains('biz.inv.kind.$kind'));
    }
    for (final source in [
      'milestone',
      'contract',
      'usage',
      'offer',
      'manual'
    ]) {
      expect(businessEn, contains('biz.inv.source.$source'));
    }
  });

  test('the controller loads the tiles, pages the list and applies filters',
      () async {
    final repo = FakeInvoices();
    final c = controllerFor(repo);
    await c.load();
    expect(c.state, BusinessLoad.ready);
    expect(c.summary!.currencies.map((f) => f.currency), ['SAR', 'USD']);
    expect(c.invoices.items, hasLength(20));
    expect(c.invoices.hasMore, isTrue);
    await c.more();
    expect(c.invoices.items, hasLength(40));
    await c.more();
    expect(c.invoices.items, hasLength(45));
    expect(c.invoices.hasMore, isFalse);

    await c.applyFilter(const InvoiceFilter(status: 'sent'));
    expect(repo.requests.last, 'list {status: sent} 0');
    await c.search('Gulf');
    expect(repo.requests.last, 'list {status: sent, q: Gulf} 0');
    await c
        .applyFilter(c.filter.copyWith(overdueOnly: true, status: () => null));
    // Overdue ignores the status and the search on the server, so neither is sent.
    expect(repo.requests.last, 'list {overdue: true} 0');
    expect(c.filter.active, isTrue);
  });

  test('a refused caller keeps the API sentence and a failed list retries',
      () async {
    final denied = controllerFor(FakeInvoices(deny: true));
    await denied.load();
    expect(denied.state, BusinessLoad.denied);
    expect(denied.error, contains('workspace owners and members'));

    final repo = FakeInvoices()..failList = true;
    final c = controllerFor(repo);
    await c.load();
    expect(c.state, BusinessLoad.error);
    expect(c.error, 'The server failed.');
    repo.failList = false;
    await c.load();
    expect(c.state, BusinessLoad.ready);

    final empty = controllerFor(FakeInvoices(total: 0));
    await empty.load();
    expect(empty.state, BusinessLoad.ready);
    expect(empty.invoices.items, isEmpty);
  });

  testWidgets('summary tiles are per currency and the list pages and filters',
      (tester) async {
    sized(tester, 390, 4000);
    final repo = FakeInvoices();
    await pumpInvoices(tester, repo);
    // Two currencies are two groups of tiles, never added together.
    expect(find.text('SAR'), findsOneWidget);
    expect(find.text('USD'), findsOneWidget);
    expect(find.text('SAR 2,300.00'), findsOneWidget);
    expect(find.text('USD 200.00'), findsOneWidget);
    expect(find.textContaining('never converted'), findsOneWidget);
    expect(find.byKey(const Key('inv-row-binv_0')), findsOneWidget);
    expect(find.textContaining('9 days overdue'), findsWidgets);
    await reveal(tester, find.byKey(const Key('inv-more')));
    await tester.tap(find.byKey(const Key('inv-more')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('list {} 20'));

    await reveal(tester, find.byKey(const Key('inv-filter-sent')));
    await tester.tap(find.byKey(const Key('inv-filter-sent')));
    await tester.pumpAndSettle();
    expect(repo.requests.last, 'list {status: sent} 0');
    await reveal(tester, find.byKey(const Key('inv-filter-overdue')));
    await tester.tap(find.byKey(const Key('inv-filter-overdue')));
    await tester.pumpAndSettle();
    expect(repo.requests.last, 'list {overdue: true} 0');

    await tester.tap(find.byKey(const Key('inv-filter-overdue')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('inv-search')), 'Client 3');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(repo.requests.last, 'list {q: Client 3} 0');
  });

  testWidgets('the list has an empty state with a next action and a 403 state',
      (tester) async {
    sized(tester, 390);
    await pumpInvoices(tester, FakeInvoices(total: 0));
    expect(find.text('No invoices yet'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'New invoice'), findsWidgets);

    await pumpInvoices(tester, FakeInvoices(deny: true));
    expect(find.textContaining('workspace owners and members'), findsWidgets);
  });

  testWidgets('an error offers a retry', (tester) async {
    sized(tester, 390);
    final repo = FakeInvoices()..failList = true;
    await pumpInvoices(tester, repo);
    expect(find.textContaining('Could not load: The server failed.'),
        findsOneWidget);
    repo.failList = false;
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('inv-row-binv_0')), findsOneWidget);
  });

  testWidgets('a new invoice from milestones, costs and a written line',
      (tester) async {
    sized(tester, 390, 6000);
    final repo = FakeInvoices();
    final c = controllerFor(repo);
    await tester.pumpWidget(app(NewInvoiceScreen(controller: c)));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('inv-new-project')));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('Fleet reporting pilot').last);
    await tester.pumpAndSettle();
    expect(repo.requests, contains('billable bacc_1 bproj_1'));
    // A milestone already invoiced is off, with the reason; a change order's
    // milestone shows its number.
    expect(find.textContaining('Already on invoice INV-2026-0001.'),
        findsOneWidget);
    expect(find.text('Change order 1: Extra dashboards (change order 1)'),
        findsOneWidget);
    final disabled = tester.widget<CheckboxListTile>(
        find.byKey(const Key('inv-milestone-bmile_2')));
    expect(disabled.onChanged, isNull);
    final usageOff = tester
        .widget<CheckboxListTile>(find.byKey(const Key('inv-usage-busage_2')));
    expect(usageOff.onChanged, isNull);

    await tester.tap(find.byKey(const Key('inv-milestone-bmile_1')));
    await tester.tap(find.byKey(const Key('inv-milestone-bmile_3')));
    await tester.tap(find.byKey(const Key('inv-usage-busage_1')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('inv-markup')), '10');
    await reveal(tester, find.byKey(const Key('inv-add-line')));
    await tester.tap(find.byKey(const Key('inv-add-line')));
    await tester.pumpAndSettle();

    // A written line without a price is refused before anything is sent.
    await tester.enterText(
        find.byKey(const Key('inv-line-desc-0')), 'Training day');
    await reveal(tester, find.byKey(const Key('inv-create')));
    await tester.tap(find.byKey(const Key('inv-create')));
    await tester.pumpAndSettle();
    expect(find.text('Enter the unit price (zero or more).'), findsOneWidget);
    expect(repo.createBody, isNull);

    await tester.enterText(find.byKey(const Key('inv-line-price-0')), '500');
    await tester.enterText(find.byKey(const Key('inv-due-days')), '30');
    await tester.enterText(find.byKey(const Key('inv-reference')), 'PO-77');
    await reveal(tester, find.byKey(const Key('inv-create')));
    await tester.tap(find.byKey(const Key('inv-create')));
    await tester.pumpAndSettle();
    expect(repo.createBody, {
      'project_id': 'bproj_1',
      'milestone_ids': unorderedEquals(['bmile_1', 'bmile_3']),
      'usage_ids': ['busage_1'],
      'usage_markup_pct': 10.0,
      'lines': [
        {
          'description': 'Training day',
          'kind': 'service',
          'quantity': 1.0,
          'unit_price': 500.0,
        }
      ],
      'due_in_days': 30,
      'customer_reference': 'PO-77',
    });
  });

  testWidgets('a project without milestones bills a share of the contract',
      (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices(noContract: true);
    await tester
        .pumpWidget(app(NewInvoiceScreen(controller: controllerFor(repo))));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('inv-new-project')));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('Fleet reporting pilot').last);
    await tester.pumpAndSettle();
    expect(find.textContaining('left SAR 4,000.00'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('inv-contract-amount')), '6000');
    await reveal(tester, find.byKey(const Key('inv-create')));
    await tester.tap(find.byKey(const Key('inv-create')));
    await tester.pumpAndSettle();
    expect(find.text('Bill between 0.01 and SAR 4,000.00.'), findsOneWidget);
    expect(repo.createBody, isNull);
    await tester.enterText(
        find.byKey(const Key('inv-contract-amount')), '2000');
    await tester.tap(find.byKey(const Key('inv-create')));
    await tester.pumpAndSettle();
    expect(repo.createBody!['contract_amount'], 2000.0);
    expect(repo.createBody!['project_id'], 'bproj_1');
  });

  testWidgets('a client account and an offer, with the price it needs',
      (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices();
    await tester
        .pumpWidget(app(NewInvoiceScreen(controller: controllerFor(repo))));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('inv-new-account')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('inv-account-bacc_1')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('billable bacc_1 null'));
    await reveal(tester, find.byKey(const Key('inv-offer')));
    await tester.tap(find.byKey(const Key('inv-offer')));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('Custom dashboard v1').last);
    await tester.pumpAndSettle();
    await reveal(tester, find.byKey(const Key('inv-create')));
    await tester.tap(find.byKey(const Key('inv-create')));
    await tester.pumpAndSettle();
    expect(find.text('This offer has no fixed price: enter the price.'),
        findsWidgets);
    expect(repo.createBody, isNull);
    await tester.enterText(find.byKey(const Key('inv-offer-price')), '1500');
    await tester.tap(find.byKey(const Key('inv-create')));
    await tester.pumpAndSettle();
    expect(repo.createBody!['account_id'], 'bacc_1');
    expect(repo.createBody!['currency'], 'SAR');
    expect(repo.createBody!['offer_id'], 'boffer_2');
    expect(repo.createBody!['offer_unit_price'], 1500.0);
  });

  testWidgets('creating with nothing chosen says what to choose',
      (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices();
    await tester
        .pumpWidget(app(NewInvoiceScreen(controller: controllerFor(repo))));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('inv-new-project')));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('Fleet reporting pilot').last);
    await tester.pumpAndSettle();
    await reveal(tester, find.byKey(const Key('inv-create')));
    await tester.tap(find.byKey(const Key('inv-create')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Choose what to invoice'), findsOneWidget);
    expect(repo.createBody, isNull);
  });

  testWidgets('a draft: the owner edits, asks for approval and deletes',
      (tester) async {
    sized(tester, 390, 3000);
    final repo = FakeInvoices();
    await pumpDetail(tester, repo);
    expect(find.text('Draft'), findsWidgets);
    expect(find.byKey(const Key('inv-edit')), findsOneWidget);
    expect(find.byKey(const Key('inv-request')), findsOneWidget);
    expect(find.byKey(const Key('inv-delete')), findsOneWidget);
    expect(find.byKey(const Key('inv-void')), findsNothing);
    expect(find.byKey(const Key('inv-pay')), findsNothing);
    // The server's figures are shown, not recomputed.
    expect(find.text('SAR 1,150.00'), findsWidgets);
    expect(
        find.textContaining('Net SAR 500.00, tax SAR 75.00, total SAR 575.00'),
        findsNWidgets(2));

    await tester.tap(find.byKey(const Key('inv-request')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('request binv_1'));
    expect(find.byKey(const Key('inv-approval')), findsOneWidget);
    expect(find.text('Approval check code abcdef012345'), findsOneWidget);
    expect(find.byKey(const Key('inv-cancel-request')), findsOneWidget);
    expect(find.byKey(const Key('inv-request')), findsNothing);
    await tester.tap(find.byKey(const Key('inv-cancel-request')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('cancel-request binv_1'));

    await tester.tap(find.byKey(const Key('inv-delete')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('inv-confirm')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('delete binv_1'));
  });

  testWidgets('a draft with issues cannot be sent and a rejection is shown',
      (tester) async {
    sized(tester, 390, 3000);
    final repo = FakeInvoices()
      ..current = invoiceJson(
          issues: ['Add your legal name in the billing profile.'],
          decisionNote: 'Rejected by owner: wrong amount');
    await pumpDetail(tester, repo);
    expect(find.byKey(const Key('inv-issues')), findsOneWidget);
    expect(find.textContaining('Add your legal name'), findsOneWidget);
    expect(find.byKey(const Key('inv-decision-note')), findsOneWidget);
    expect(find.text('Rejected by owner: wrong amount'), findsOneWidget);
    final request =
        tester.widget<FilledButton>(find.byKey(const Key('inv-request')));
    expect(request.onPressed, isNull);
  });

  testWidgets('waiting for approval links to Approvals', (tester) async {
    sized(tester, 390, 3000);
    var opened = false;
    final repo = FakeInvoices()
      ..current =
          invoiceJson(status: 'pending_approval', approvalState: 'pending');
    await pumpDetail(tester, repo, onOpenApprovals: () => opened = true);
    expect(find.text('Waiting for an owner'), findsOneWidget);
    expect(find.textContaining('must approve sending this invoice'),
        findsOneWidget);
    await tester.tap(find.byKey(const Key('inv-open-approvals')));
    expect(opened, isTrue);
  });

  testWidgets('a sent invoice: payment, remove it and void are owner-aware',
      (tester) async {
    sized(tester, 390, 3600);
    final repo = FakeInvoices()
      ..current = invoiceJson(status: 'sent', overdue: true);
    await pumpDetail(tester, repo);
    expect(find.text('INV-2026-0001'), findsWidgets);
    expect(find.text('14 days overdue'), findsOneWidget);
    expect(find.byKey(const Key('inv-edit')), findsNothing);
    expect(find.byKey(const Key('inv-void')), findsOneWidget);

    await tester.tap(find.byKey(const Key('inv-pay')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('inv-pay-amount')), '9000');
    await tester.tap(find.byKey(const Key('inv-pay-submit')));
    await tester.pumpAndSettle();
    expect(find.text('The payment is more than the balance of SAR 1,150.00.'),
        findsOneWidget);
    await tester.enterText(find.byKey(const Key('inv-pay-amount')), '500');
    await tester.enterText(find.byKey(const Key('inv-pay-date')), 'soon');
    await tester.tap(find.byKey(const Key('inv-pay-submit')));
    await tester.pumpAndSettle();
    expect(find.text('Use the date format YYYY-MM-DD.'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('inv-pay-date')), '2026-10-10');
    await tester.enterText(find.byKey(const Key('inv-pay-reference')), 'TRX-1');
    await tester.tap(find.byKey(const Key('inv-pay-submit')));
    await tester.pumpAndSettle();
    expect(repo.paymentBody, {
      'amount': 500.0,
      'paid_on': '2026-10-10',
      'method': 'bank_transfer',
      'reference': 'TRX-1',
      'note': '',
    });
    expect(find.text('Payment recorded.'), findsOneWidget);
    expect(find.text('Partly paid'), findsOneWidget);
    // With a payment on it, the invoice cannot be voided until it is removed.
    expect(find.byKey(const Key('inv-void')), findsNothing);
    expect(
        find.byKey(const Key('inv-payment-remove-binpay_1')), findsOneWidget);

    await tester.tap(find.byKey(const Key('inv-payment-remove-binpay_1')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('inv-confirm')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('remove-payment binv_1 binpay_1'));

    await tester.tap(find.byKey(const Key('inv-void')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('inv-reason')), 'ab');
    await tester.tap(find.byKey(const Key('inv-reason-confirm')));
    await tester.pumpAndSettle();
    expect(
        find.text('Give a reason of at least 3 characters.'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('inv-reason')), 'Wrong customer');
    await tester.tap(find.byKey(const Key('inv-reason-confirm')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('void binv_1 Wrong customer'));
    expect(find.text('The invoice is void.'), findsOneWidget);
    expect(find.byKey(const Key('inv-pay')), findsNothing);
  });

  testWidgets('a member cannot void or remove a payment', (tester) async {
    sized(tester, 390, 3600);
    final repo = FakeInvoices(owner: false)
      ..current = invoiceJson(
          status: 'sent',
          owner: false,
          payments: [paymentJson('binpay_1', 200)]);
    await pumpDetail(tester, repo, manage: false);
    expect(find.byKey(const Key('inv-pay')), findsOneWidget);
    expect(find.byKey(const Key('inv-void')), findsNothing);
    expect(find.byKey(const Key('inv-payment-binpay_1')), findsOneWidget);
    expect(find.byKey(const Key('inv-payment-remove-binpay_1')), findsNothing);
  });

  testWidgets('editing a draft drops a sourced line and keeps written ones',
      (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices();
    await pumpDetail(tester, repo);
    await tester.tap(find.byKey(const Key('inv-edit')));
    await tester.pumpAndSettle();
    // The milestone line (position 0) can be taken off; the written one is a form.
    await tester.tap(find.byKey(const Key('inv-keep-0')));
    await tester.enterText(find.byKey(const Key('inv-line-price-0')), '600');
    await tester.enterText(find.byKey(const Key('inv-notes')), '');
    await reveal(tester, find.byKey(const Key('inv-edit-save')));
    await tester.tap(find.byKey(const Key('inv-edit-save')));
    await tester.pumpAndSettle();
    final body = repo.updateBody!;
    expect(body['remove_lines'], [0]);
    expect((body['lines']! as List).single, containsPair('unit_price', 600.0));
    expect(body['notes'], '');
    expect(body['bill_to_name'], 'Gulf Mixers');
    expect(find.text('Saved.'), findsOneWidget);
  });

  testWidgets('editing refuses an invoice with no line or no customer name',
      (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices();
    await pumpDetail(tester, repo);
    await tester.tap(find.byKey(const Key('inv-edit')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('inv-keep-0')));
    await tester.tap(find.byKey(const Key('inv-line-remove-0')));
    await tester.pumpAndSettle();
    await reveal(tester, find.byKey(const Key('inv-edit-save')));
    await tester.tap(find.byKey(const Key('inv-edit-save')));
    await tester.pumpAndSettle();
    expect(find.text('An invoice needs at least one line.'), findsOneWidget);
    expect(repo.updateBody, isNull);
    await tester.tap(find.byKey(const Key('inv-keep-0')));
    await tester.enterText(find.byKey(const Key('inv-bill-name')), '');
    await reveal(tester, find.byKey(const Key('inv-edit-save')));
    await tester.tap(find.byKey(const Key('inv-edit-save')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('inv-name-problem')), findsOneWidget);
    expect(repo.updateBody, isNull);
  });

  testWidgets('a pending invoice warns that editing cancels the request',
      (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices()
      ..current =
          invoiceJson(status: 'pending_approval', approvalState: 'pending');
    await pumpDetail(tester, repo);
    await tester.tap(find.byKey(const Key('inv-edit')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('inv-edit-lapses')), findsOneWidget);
  });

  testWidgets('the printable copy: preview, copy as text, save the page',
      (tester) async {
    sized(tester, 390, 5000);
    final saver = FakeSaver();
    final repo = FakeInvoices()..current = invoiceJson(status: 'sent');
    String? copied;
    tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, (call) async {
      if (call.method == 'Clipboard.setData') {
        copied = (call.arguments as Map)['text'] as String;
      }
      return null;
    });
    addTearDown(() => tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null));
    await pumpDetail(tester, repo, saver: saver);
    await tester.tap(find.byKey(const Key('inv-preview')));
    await tester.pumpAndSettle();
    // Seller, buyer, lines, totals and the bank details, natively.
    expect(find.text('Shahzeb Digital'), findsOneWidget);
    expect(find.text('Gulf Mixers'), findsOneWidget);
    expect(find.text('Deposit'), findsOneWidget);
    expect(find.text('IBAN SA00 0000 0000 0000'), findsOneWidget);
    expect(find.textContaining('Save as PDF'), findsOneWidget);

    await tester.tap(find.byKey(const Key('inv-doc-copy')));
    await tester.pumpAndSettle();
    expect(copied, contains('Invoice INV-2026-0001'));
    expect(copied, contains('From: Shahzeb Digital'));
    expect(copied, contains('Total: SAR 1,150.00'));
    expect(copied, isNot(contains('\u2014')));

    await tester.tap(find.byKey(const Key('inv-doc-save')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('print binv_1 en'));
    expect(saver.saved.single.$1, 'INV-2026-0001.html');
    expect(saver.saved.single.$2, contains('Invoice en'));
  });

  testWidgets('a draft preview says it is not a valid invoice', (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices();
    await pumpDetail(tester, repo);
    await tester.tap(find.byKey(const Key('inv-preview')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Not sent'), findsWidgets);
    expect(find.textContaining('billing profile is not filled in'),
        findsOneWidget);
  });

  testWidgets('the billing profile: an owner edits, a member only reads',
      (tester) async {
    sized(tester, 390, 5000);
    final repo = FakeInvoices();
    final owner = controllerFor(repo);
    await tester.pumpWidget(app(BillingProfileScreen(controller: owner)));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('profile-save')), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('profile-prefix')), 'bad prefix!');
    await reveal(tester, find.byKey(const Key('profile-save')));
    await tester.tap(find.byKey(const Key('profile-save')));
    await tester.pumpAndSettle();
    expect(
        find.text('Use 1 to 8 letters or digits, no spaces.'), findsOneWidget);
    expect(repo.savedProfile, isNull);
    await tester.enterText(find.byKey(const Key('profile-prefix')), 'SHD');
    await tester.enterText(
        find.byKey(const Key('profile-taxNumber')), '3000999');
    await tester.tap(find.byKey(const Key('profile-save')));
    await tester.pumpAndSettle();
    expect(repo.savedProfile!.invoicePrefix, 'SHD');
    expect(repo.savedProfile!.taxNumber, '3000999');
    expect(find.text('Billing profile saved.'), findsOneWidget);

    final member = controllerFor(FakeInvoices(), manage: false);
    await reset(tester);
    await tester.pumpWidget(app(BillingProfileScreen(controller: member)));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('profile-save')), findsNothing);
    expect(find.textContaining('Only an owner changes'), findsOneWidget);
    expect(
        tester
            .widget<TextField>(find.byKey(const Key('profile-name')))
            .readOnly,
        isTrue);
  });

  testWidgets('portal links: an owner creates one and sees it once',
      (tester) async {
    sized(tester, 390, 4000);
    final repo = FakeInvoices()..links = [accessFor('bpacc_1', 'bcon_1')];
    String? copied;
    tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, (call) async {
      if (call.method == 'Clipboard.setData') {
        copied = (call.arguments as Map)['text'] as String;
      }
      return null;
    });
    addTearDown(() => tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null));
    await tester.pumpWidget(app(ClientScreen(
        controller: controllerFor(repo),
        accountId: 'bacc_1',
        webAppUrl: 'https://app.example.test')));
    await tester.pumpAndSettle();
    expect(find.text('Sara Khan'), findsWidgets);
    expect(find.byKey(const Key('portal-access-bpacc_1')), findsOneWidget);
    expect(find.text('Link works'), findsOneWidget);
    expect(find.textContaining('Last used: never'), findsOneWidget);

    await tester.tap(find.byKey(const Key('portal-create-bcon_2')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('portal-days')), '0');
    await tester.tap(find.byKey(const Key('portal-create-confirm')));
    await tester.pumpAndSettle();
    expect(find.text('Use a whole number from 1 to 365.'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('portal-days')), '30');
    await tester.tap(find.byKey(const Key('portal-decide')));
    await tester.tap(find.byKey(const Key('portal-create-confirm')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('create-access bcon_2 30 false'));
    // The link is shown with a credential warning and a copy button.
    expect(find.byKey(const Key('portal-link-warning')), findsOneWidget);
    expect(find.textContaining('credential'), findsOneWidget);
    expect(find.byKey(const Key('portal-link')), findsOneWidget);
    expect(
        find.textContaining(
            'https://app.example.test/portal#access=anum_portal.'),
        findsOneWidget);
    await tester.tap(find.byKey(const Key('portal-copy')));
    await tester.pumpAndSettle();
    expect(copied, 'https://app.example.test/portal#access=$_token');
    await tester.tap(find.byKey(const Key('portal-link-done')));
    await tester.pumpAndSettle();
    // Gone for good: nothing on the screen carries the token afterwards.
    expect(find.textContaining(_token), findsNothing);
    expect(find.textContaining('anum_portal.'), findsNothing);
  });

  testWidgets('portal links: revoke asks first; a member cannot create',
      (tester) async {
    sized(tester, 390, 4000);
    final repo = FakeInvoices()..links = [accessFor('bpacc_1', 'bcon_1')];
    await tester.pumpWidget(app(
        ClientScreen(controller: controllerFor(repo), accountId: 'bacc_1')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('portal-revoke-bpacc_1')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('portal-revoke-confirm')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('revoke bpacc_1'));
    expect(find.text('The link is turned off.'), findsOneWidget);

    final member = FakeInvoices()..links = [accessFor('bpacc_1', 'bcon_1')];
    await reset(tester);
    await tester.pumpWidget(app(ClientScreen(
        controller: controllerFor(member, manage: false),
        accountId: 'bacc_1')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('portal-create-bcon_1')), findsNothing);
    expect(find.byKey(const Key('portal-revoke-bpacc_1')), findsNothing);
    expect(
        find.text('Only an owner creates or turns off a link.'), findsWidgets);
    // Members still see who has access.
    expect(find.byKey(const Key('portal-access-bpacc_1')), findsOneWidget);
  });

  testWidgets('clients are searchable and open an account', (tester) async {
    sized(tester, 390, 3000);
    final repo = FakeInvoices();
    final invoices = controllerFor(repo);
    await tester.pumpWidget(app(BusinessScreen(
        controller: BusinessController(FakeBusiness(), invoices: invoices))));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('biz-tab-clients')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('client-bacc_0')), findsOneWidget);
    await tester.enterText(find.byKey(const Key('clients-search')), 'Client 2');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(repo.requests, contains('accounts Client 2 0'));
    expect(find.byKey(const Key('client-bacc_0')), findsNothing);
    await tester.tap(find.byKey(const Key('client-bacc_2')));
    await tester.pumpAndSettle();
    expect(find.text('Sara Khan'), findsWidgets);
  });

  test('a customer decision reads on proposals and change orders', () {
    final decision = {
      'decision': 'approved',
      'signer_name': 'Sara Khan',
      'note': 'Go ahead.',
      'decided_at': '2026-10-18T09:00:00Z',
      'version': 2,
      'content_hash': 'x' * 64,
      'current': true,
    };
    final proposal = BusinessProposal.fromJson(JsonReader.of({
      'id': 'bprop_1',
      'title': 'Fleet reporting pilot',
      'status': 'pending_approval',
      'account_name': 'Company 0',
      'current_version': 2,
      'current': {'currency': 'SAR', 'total': 2500.0, 'issues': <String>[]},
      'approval_state': 'pending',
      'decision_note': '',
      'portal_decision': decision,
    }));
    expect(proposal.portalDecision!.approved, isTrue);
    expect(proposal.portalDecision!.current, isTrue);
    expect(proposal.portalDecision!.signerName, 'Sara Khan');
    final none = BusinessProposal.fromJson(JsonReader.of({
      'id': 'bprop_2',
      'title': 'Other',
      'status': 'draft',
      'account_name': 'Company 0',
      'current_version': 1,
      'current': {'currency': 'SAR', 'total': 1.0, 'issues': <String>[]},
      'approval_state': 'none',
      'decision_note': '',
      'portal_decision': null,
    }));
    expect(none.portalDecision, isNull);
    final order = ChangeOrder.fromJson(JsonReader.of({
      'id': 'bchg_1',
      'number': 1,
      'title': 'Extra dashboards',
      'status': 'pending_approval',
      'currency': 'SAR',
      'timeline_days': 3,
      'issues': <String>[],
      'stale': false,
      'portal_decision': {
        ...decision,
        'decision': 'declined',
        'current': false
      },
    }));
    expect(order.portalDecision!.approved, isFalse);
  });

  testWidgets('the decision line says who, when, whether it is current',
      (tester) async {
    sized(tester, 320);
    final d = PortalDecisionSummary(
        decision: 'declined',
        signerName: 'Sara Khan',
        note: 'Too expensive.',
        decidedAt: DateTime.utc(2026, 10, 18, 9),
        current: false);
    await tester.pumpWidget(
        app(Scaffold(body: PortalDecisionLine(decision: d)), textScale: 2));
    expect(tester.takeException(), isNull);
    expect(find.text('Declined by Sara Khan in the portal on 2026-10-18'),
        findsOneWidget);
    expect(find.textContaining('earlier version'), findsOneWidget);
    expect(find.text('Their note: Too expensive.'), findsOneWidget);
    expect(find.textContaining('owner still confirms in Approvals'),
        findsOneWidget);
  });

  testWidgets('a proposal card shows the customer\'s decision', (tester) async {
    sized(tester, 390, 3000);
    final business = _WithDecision();
    await tester.pumpWidget(
        app(BusinessScreen(controller: BusinessController(business))));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('biz-tab-proposals')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('portal-decision')), findsOneWidget);
    expect(find.text('Approved by Sara Khan in the portal on 2026-10-18'),
        findsOneWidget);
  });

  for (final width in [320.0, 768.0, 1024.0]) {
    testWidgets('invoices lay out at $width wide with 200% text',
        (tester) async {
      sized(tester, width, 2400);
      final repo = FakeInvoices()
        ..current = invoiceJson(
            status: 'sent',
            overdue: true,
            payments: [paymentJson('binpay_1', 200)]);
      await pumpInvoices(tester, repo, textScale: 2);
      expect(tester.takeException(), isNull);
      expect(find.byKey(const Key('inv-row-binv_0')), findsOneWidget);

      final c = controllerFor(repo);
      await tester.pumpWidget(app(
          InvoiceDetailScreen(
              controller: c, invoiceId: 'binv_1', saver: FakeSaver()),
          textScale: 2));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.scrollUntilVisible(
          find.byKey(const Key('inv-payment-binpay_1')), 300,
          scrollable: find.byType(Scrollable).first);
      expect(tester.takeException(), isNull);

      await tester.pumpWidget(
          app(NewInvoiceScreen(controller: controllerFor(repo)), textScale: 2));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('inv-new-project')));
      await tester.pumpAndSettle();
      await tester.tap(find.textContaining('Fleet reporting pilot').last);
      await tester.pumpAndSettle();
      await reveal(tester, find.byKey(const Key('inv-add-line')));
      await tester.tap(find.byKey(const Key('inv-add-line')));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.byKey(const Key('inv-create')), 400,
          scrollable: find.byType(Scrollable).first);
      expect(tester.takeException(), isNull);

      await tester.pumpWidget(app(
          InvoicePreviewScreen(
              document: InvoiceDocument.fromInvoice(repo.inv),
              loadHtml: (_) async => '<html></html>',
              saver: FakeSaver()),
          textScale: 2));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);

      await tester.pumpWidget(app(
          ClientScreen(
              controller:
                  controllerFor(repo..links = [accessFor('bpacc_1', 'bcon_1')]),
              accountId: 'bacc_1'),
          textScale: 2));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);

      await tester.pumpWidget(app(
          BillingProfileScreen(controller: controllerFor(repo)),
          textScale: 2));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('Arabic renders the invoices right to left', (tester) async {
    sized(tester, 390, 3000);
    final repo = FakeInvoices();
    await pumpInvoices(tester, repo, locale: const Locale('ar'));
    expect(tester.takeException(), isNull);
    expect(find.text('فاتورة جديدة'), findsOneWidget);
    expect(find.text('الفواتير'), findsWidgets);
    expect(find.text('المستحق'), findsWidgets);
    expect(Directionality.of(tester.element(find.text('فاتورة جديدة'))),
        TextDirection.rtl);

    repo.current = invoiceJson(status: 'sent');
    await pumpDetail(tester, repo, locale: const Locale('ar'));
    expect(find.text('تسجيل دفعة'), findsOneWidget);
    expect(find.text('إلغاء الفاتورة'), findsOneWidget);
    expect(find.text('طباعة أو PDF'), findsOneWidget);
    await tester.tap(find.byKey(const Key('inv-pay')));
    await tester.pumpAndSettle();
    expect(find.text('تاريخ الاستلام'), findsOneWidget);
  });
}

/// A business fake whose proposal carries a customer decision.
class _WithDecision extends FakeBusiness {
  @override
  Future<BusinessPage<BusinessProposal>> proposals(
          {required int offset, int limit = 20}) async =>
      BusinessPage([
        BusinessProposal.fromJson(JsonReader.of({
          'id': 'bprop_1',
          'title': 'Fleet reporting pilot',
          'status': 'pending_approval',
          'account_name': 'Company 0',
          'current_version': 2,
          'current': {'currency': 'SAR', 'total': 2500.0, 'issues': <String>[]},
          'approval_state': 'pending',
          'decision_note': '',
          'portal_decision': {
            'decision': 'approved',
            'signer_name': 'Sara Khan',
            'note': '',
            'decided_at': '2026-10-18T09:00:00Z',
            'version': 2,
            'content_hash': 'y' * 64,
            'current': true,
          },
        }))
      ], 1);
}
