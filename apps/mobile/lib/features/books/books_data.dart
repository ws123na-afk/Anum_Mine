import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../records_kit/records_kit.dart';

/// The business books (docs/business-books.md#api), the parts the phone
/// shows: the P&L for a period (read only) and the bills with their
/// payments. Nothing here posts money.

class BooksPermissions {
  const BooksPermissions(
      {required this.read,
      required this.write,
      required this.manage,
      this.account = false});
  factory BooksPermissions.fromJson(JsonReader j) {
    final manage = j.optBool('manage') ?? false;
    return BooksPermissions(
        read: j.optBool('read') ?? false,
        write: j.optBool('write') ?? false,
        manage: manage,
        account: j.optBool('account') ?? manage);
  }
  final bool read, write, manage;

  /// An owner or a named accountant: sees the Accountant review and records
  /// adjustments. An accountant who is not an owner is [account] without
  /// [manage]: their adjustments at or above the approval threshold wait.
  final bool account;
}

class BooksSetup {
  const BooksSetup({
    required this.configured,
    required this.permissions,
    this.baseCurrency,
    this.basis = 'accrual',
    this.version,
    this.periodFrom,
    this.periodTo,
    this.billCount = 0,
    this.userId,
    this.approvalThreshold,
  });
  factory BooksSetup.fromJson(JsonReader j) {
    final settings = j.optObject('settings');
    final period = j.optObject('default_period');
    return BooksSetup(
      configured: settings != null,
      permissions: BooksPermissions.fromJson(j.object('permissions')),
      baseCurrency: settings?.optString('base_currency'),
      basis: settings?.optString('basis') ?? 'accrual',
      version: settings?.optInt('version'),
      periodFrom: period?.optString('from'),
      periodTo: period?.optString('to'),
      billCount: j.optObject('counts')?.optInt('bills') ?? 0,
      userId: j.optString('user_id'),
      approvalThreshold: switch (
          settings?.json['adjustment_approval_threshold']) {
        final String text when text.isNotEmpty => text,
        final num n => '$n',
        _ => null,
      },
    );
  }
  final bool configured;
  final BooksPermissions permissions;
  final String? baseCurrency, periodFrom, periodTo;
  final String basis;
  final int? version;
  final int billCount;

  /// Who is asking: the maker of a pending adjustment never approves it.
  final String? userId;

  /// Adjustments an accountant records at or above this amount in the base
  /// currency wait for a second approval (docs/business-books.md
  /// #adjustment-approval); null means no approval step.
  final String? approvalThreshold;
}

/// The journal adjustment kinds (`BooksAdjustmentKind`).
const adjustmentKinds = [
  'accrual',
  'prepayment',
  'capital_item',
  'loan_received',
  'loan_repayment',
  'loan_interest',
  'loan_interest_accrued',
  'loan_interest_paid',
  'owner_contribution',
  'owner_drawing',
  'owner_reimbursement',
];

/// Kinds spread over months from a first month.
bool adjustmentSpreads(String kind) =>
    kind == 'prepayment' || kind == 'capital_item';

/// A recorded adjustment: `active` (posted), `pending` (waits for another
/// owner or accountant) or `void`.
class BooksAdjustment {
  const BooksAdjustment(
      {required this.id, required this.kind, required this.status});
  factory BooksAdjustment.fromJson(JsonReader j) => BooksAdjustment(
      id: j.string('id'),
      kind: j.optString('kind') ?? '',
      status: j.optString('status') ?? 'active');
  final String id, kind, status;
  bool get pending => status == 'pending';
}

class BooksLine {
  const BooksLine(this.key, this.amount);
  factory BooksLine.fromJson(JsonReader j) =>
      BooksLine(j.string('key'), j.string('amount'));
  final String key, amount;
}

class BooksGroup {
  const BooksGroup(this.lines, this.total);
  factory BooksGroup.fromJson(JsonReader j) =>
      BooksGroup(j.optObjects('lines', BooksLine.fromJson), j.string('total'));
  final List<BooksLine> lines;
  final String total;
}

class BooksAmount {
  const BooksAmount(this.currency, this.amount);
  factory BooksAmount.fromJson(JsonReader j) => BooksAmount(
      j.string('currency'),
      j.optString('amount') ?? j.optString('total') ?? '0');
  final String currency, amount;
}

class BooksReport {
  const BooksReport({
    required this.from,
    required this.to,
    required this.basis,
    required this.currency,
    required this.revenue,
    required this.costOfSales,
    required this.grossProfit,
    required this.operatingExpenses,
    required this.operatingProfit,
    required this.otherIncome,
    required this.financeCosts,
    required this.fxDifferences,
    required this.netProfit,
    required this.complete,
    this.grossMarginPct,
    this.netMarginPct,
    this.notConverted = const [],
    this.receivables = const [],
    this.payables = const [],
    this.unpaidBills = 0,
    this.notes = const [],
  });
  factory BooksReport.fromJson(JsonReader j) {
    final period = j.object('period');
    final pnl = j.object('pnl');
    final balances = j.optObject('balances');
    return BooksReport(
      from: period.string('from'),
      to: period.string('to'),
      basis: period.string('basis'),
      currency: pnl.optString('currency') ?? j.string('base_currency'),
      revenue: BooksGroup.fromJson(pnl.object('revenue')),
      costOfSales: BooksGroup.fromJson(pnl.object('cost_of_sales')),
      grossProfit: pnl.string('gross_profit'),
      grossMarginPct: pnl.optString('gross_margin_pct'),
      operatingExpenses: BooksGroup.fromJson(pnl.object('operating_expenses')),
      operatingProfit: pnl.string('operating_profit'),
      otherIncome: BooksGroup.fromJson(pnl.object('other_income')),
      financeCosts: BooksGroup.fromJson(pnl.object('finance_costs')),
      fxDifferences: pnl.optString('fx_differences') ?? '0',
      netProfit: pnl.string('net_profit'),
      netMarginPct: pnl.optString('net_margin_pct'),
      complete: pnl.optBool('complete') ?? true,
      notConverted: [
        for (final item in pnl.optObjects('not_converted', (r) => r))
          for (final entry
              in (item.optMap('groups') ?? const <String, Object?>{}).entries)
            if (entry.value is String)
              (
                currency: item.string('currency'),
                group: entry.key,
                amount: entry.value! as String
              ),
      ],
      receivables:
          balances?.optObjects('receivables', BooksAmount.fromJson) ?? const [],
      payables:
          balances?.optObjects('payables', BooksAmount.fromJson) ?? const [],
      unpaidBills: balances?.optInt('unpaid_bills_count') ?? 0,
      notes: j.optStrings('notes') ?? const [],
    );
  }
  final String from, to, basis, currency;
  final BooksGroup revenue, costOfSales, operatingExpenses, otherIncome;
  final BooksGroup financeCosts;
  final String grossProfit, operatingProfit, fxDifferences, netProfit;
  final String? grossMarginPct, netMarginPct;
  final bool complete;
  final List<({String currency, String group, String amount})> notConverted;
  final List<BooksAmount> receivables, payables;
  final int unpaidBills;
  final List<String> notes;
}

class BooksPayment {
  const BooksPayment({
    required this.id,
    required this.kind,
    required this.amount,
    required this.currency,
    required this.paidOn,
    required this.method,
    required this.counts,
    this.reference = '',
    this.voidedAt,
    this.voidReason = '',
  });
  factory BooksPayment.fromJson(JsonReader j) => BooksPayment(
        id: j.string('id'),
        kind: j.string('kind'),
        amount: j.string('amount'),
        currency: j.string('currency'),
        paidOn: j.string('paid_on'),
        method: j.optString('method') ?? 'other',
        reference: j.optString('reference') ?? '',
        voidedAt: j.optString('voided_at'),
        voidReason: j.optString('void_reason') ?? '',
        counts: j.optBool('counts') ?? true,
      );
  final String id, kind, amount, currency, paidOn, method, reference;
  final String? voidedAt;
  final String voidReason;
  final bool counts;
}

class BooksBill {
  const BooksBill({
    required this.id,
    required this.vendorName,
    required this.reference,
    required this.description,
    required this.billDate,
    required this.dueDate,
    required this.currency,
    required this.subtotal,
    required this.tax,
    required this.total,
    required this.category,
    required this.section,
    required this.status,
    required this.amountPaid,
    required this.balance,
    required this.paymentState,
    required this.overdue,
    required this.daysOverdue,
    required this.canPay,
    required this.canVoid,
    required this.version,
    this.recurrence,
    this.voidReason = '',
    this.payments,
  });
  factory BooksBill.fromJson(JsonReader j) {
    final can = j.optObject('can');
    return BooksBill(
      id: j.string('id'),
      vendorName: j.optString('vendor_name') ?? '',
      reference: j.optString('reference') ?? '',
      description: j.optString('description') ?? '',
      billDate: j.string('bill_date'),
      dueDate: j.optString('due_date') ?? j.string('bill_date'),
      currency: j.string('currency'),
      subtotal: j.string('subtotal'),
      tax: j.optString('tax') ?? '0',
      total: j.string('total'),
      category: j.optString('category') ?? '',
      section: j.optString('section') ?? '',
      recurrence: j.optString('recurrence'),
      status: j.string('status'),
      voidReason: j.optString('void_reason') ?? '',
      amountPaid: j.optString('amount_paid') ?? '0',
      balance: j.string('balance'),
      paymentState: j.string('payment_state'),
      overdue: j.optBool('overdue') ?? false,
      daysOverdue: j.optInt('days_overdue') ?? 0,
      canPay: can?.optBool('pay') ?? false,
      canVoid: can?.optBool('void') ?? false,
      version: j.integer('version'),
      payments: j['payments'] == null
          ? null
          : j.optObjects('payments', BooksPayment.fromJson),
    );
  }
  final String id, vendorName, reference, description, billDate, dueDate;
  final String currency, subtotal, tax, total, category, section, status;
  final String amountPaid, balance, paymentState, voidReason;
  final String? recurrence;
  final bool overdue, canPay, canVoid;
  final int daysOverdue, version;
  final List<BooksPayment>? payments;
}

class BooksBillsChunk extends RecordsChunk<BooksBill> {
  BooksBillsChunk(RecordsChunk<BooksBill> page, this.open, this.overdue)
      : super(page.items, next: page.next, total: page.total);
  final List<BooksAmount> open, overdue;
}

/// One item of the Accountant review (docs/business-books.md#accountant-review).
class ReviewItem {
  const ReviewItem({
    required this.key,
    required this.kind,
    required this.detected,
    required this.section,
    required this.action,
    required this.canAct,
    required this.blocking,
    this.params = const {},
    this.occurredOn,
    this.amount,
    this.currency,
    this.refId,
    this.whyNot,
  });
  factory ReviewItem.fromJson(JsonReader j) => ReviewItem(
        key: j.string('key'),
        kind: j.string('kind'),
        detected: j.optString('detected') ?? '',
        params: {
          for (final e in (j.optMap('params') ?? const {}).entries)
            if (e.value != null) e.key: '${e.value}'
        },
        blocking: j.optString('severity') == 'blocking',
        occurredOn: j.optString('occurred_on'),
        amount: switch (j['amount']) {
          final String a => a,
          final num n => '$n',
          _ => null,
        },
        currency: j.optString('currency'),
        refId: j.optString('ref_id'),
        section: j.optString('section') ?? '',
        action: j.optString('action') ?? 'open',
        canAct: j.optBool('can_act') ?? true,
        whyNot: j.optString('why_not'),
      );
  final String key, kind, detected, section, action;
  final Map<String, String> params;
  final bool blocking, canAct;
  final String? occurredOn, amount, currency, refId, whyNot;

  /// The draft bill's version, sent with Approve.
  int? get version => int.tryParse(params['version'] ?? '');
}

/// One month of the close checklist.
class CloseMonth {
  const CloseMonth({
    required this.month,
    required this.status,
    required this.issues,
    required this.drafts,
    required this.allocations,
    required this.ready,
  });
  factory CloseMonth.fromJson(JsonReader j) => CloseMonth(
        month: j.string('month'),
        status: j.optString('status') ?? 'open',
        issues: j.optInt('issues') ?? 0,
        drafts: j.optInt('drafts') ?? 0,
        allocations: j.optInt('allocations_in_review') ?? 0,
        ready: j.optBool('ready') ?? false,
      );
  final String month, status;
  final int issues, drafts, allocations;
  final bool ready;
}

class ReviewChunk extends RecordsChunk<ReviewItem> {
  ReviewChunk(
    RecordsChunk<ReviewItem> page, {
    this.counts = const {},
    this.blocking = 0,
    this.months = const [],
    this.toPost = 0,
    this.unavailable = const [],
  }) : super(page.items, next: page.next, total: page.total);
  factory ReviewChunk.fromJson(JsonReader j) => ReviewChunk(
        RecordsChunk.offsetPage(j, ReviewItem.fromJson),
        counts: {
          for (final e in (j.optMap('counts') ?? const {}).entries)
            if (e.value is num) e.key: (e.value! as num).toInt()
        },
        blocking: j.optInt('blocking') ?? 0,
        months: j.optObjects('close_months', CloseMonth.fromJson),
        toPost: j.optInt('to_post') ?? 0,
        unavailable: j.optStrings('unavailable') ?? const [],
      );
  final Map<String, int> counts;
  final int blocking, toPost;
  final List<CloseMonth> months;
  final List<String> unavailable;
}

/// A vault document attached to a record as evidence.
class EvidenceDoc {
  const EvidenceDoc(
      {required this.linkId,
      required this.documentId,
      required this.title,
      this.category = '',
      this.addedAt});
  factory EvidenceDoc.fromJson(JsonReader j) => EvidenceDoc(
        linkId: j.string('link_id'),
        documentId: j.string('document_id'),
        title: j.optString('title') ?? '',
        category: j.optString('category') ?? '',
        addedAt: j.optString('added_at'),
      );
  final String linkId, documentId, title, category;
  final String? addedAt;
}

/// What is attached to each record, whether it needs a document (at or
/// above the evidence threshold with nothing attached), and whether the
/// caller may attach one.
class EvidencePage {
  const EvidencePage({this.items = const {}, this.canAttach = false});
  factory EvidencePage.fromJson(JsonReader j) {
    final items = j.optObject('items');
    return EvidencePage(
      items: {
        if (items != null)
          for (final id in items.json.keys)
            id: (
              documents: items
                  .object(id)
                  .optObjects('documents', EvidenceDoc.fromJson),
              missing: items.object(id).optBool('evidence_missing') ?? false,
            ),
      },
      canAttach: j.optBool('can_attach') ?? false,
    );
  }
  final Map<String, ({List<EvidenceDoc> documents, bool missing})> items;
  final bool canAttach;
}

/// A workspace document of the vault that may be attached.
class AttachableDoc {
  const AttachableDoc(
      {required this.id, required this.title, required this.category});
  factory AttachableDoc.fromJson(JsonReader j) => AttachableDoc(
      id: j.string('id'),
      title: j.optString('title') ?? '',
      category: j.optString('category') ?? '');
  final String id, title, category;
}

abstract interface class BooksRepository {
  Future<BooksSetup> setup();
  Future<void> saveSettings(Map<String, Object?> body);
  Future<BooksReport> report({String? from, String? to, String? basis});
  Future<BooksBillsChunk> bills({String query, String status, String? token});
  Future<BooksBill> bill(String id);
  Future<BooksBill> pay(String billId, Map<String, Object?> body,
      {required String idempotencyKey});
  Future<BooksBill> voidPayment(String billId, String paymentId, String reason);
  Future<BooksBill> voidBill(String billId, String reason);

  /// The Accountant review (owners and accountants), one page.
  Future<ReviewChunk> review({String kind, String? token});

  /// Approves a draft bill (never one's own: the API refuses it).
  Future<void> approveBill(String billId, int version);

  /// Records a journal adjustment (owners and accountants). One
  /// [idempotencyKey] per form, so a retry records it once.
  Future<BooksAdjustment> createAdjustment(Map<String, Object?> body,
      {required String idempotencyKey});

  /// Approves a pending adjustment (never one's own: the API refuses it).
  Future<void> approveAdjustment(String adjustmentId);

  /// Rejects a pending adjustment (or voids an active one) with a reason.
  Future<void> voidAdjustment(String adjustmentId, String reason);

  /// The documents attached to up to 100 records of one type.
  Future<EvidencePage> evidence(String sourceType, List<String> ids);
  Future<void> attach(String sourceType, String sourceId, String documentId);
  Future<void> detach(String sourceType, String sourceId, String linkId);

  /// Workspace documents of this business's vault, newest first.
  Future<RecordsChunk<AttachableDoc>> documents({String query, String? token});
}

class ApiBooksRepository implements BooksRepository {
  const ApiBooksRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/business/books';
  static const _documents = '/api/v1/documents';
  static const pageSize = 25;
  String _id(String id) => Uri.encodeComponent(id);

  @override
  Future<BooksSetup> setup() async =>
      BooksSetup.fromJson(JsonReader(await api.request('GET', '$_base/setup')));

  @override
  Future<void> saveSettings(Map<String, Object?> body) =>
      api.request('PUT', '$_base/settings', body: body);

  @override
  Future<BooksReport> report({String? from, String? to, String? basis}) async =>
      BooksReport.fromJson(JsonReader(await api.request(
          'GET',
          '$_base/report${recordsQuery({
                'from': from,
                'to': to,
                'basis': basis
              })}')));

  @override
  Future<BooksBillsChunk> bills(
      {String query = '', String status = '', String? token}) async {
    final body = JsonReader(await api.request(
        'GET',
        '$_base/bills${recordsQuery({
              'q': query,
              'status': status,
              'offset': token ?? 0,
              'limit': pageSize,
            })}'));
    final summary = body.optObject('summary');
    return BooksBillsChunk(
      RecordsChunk.offsetPage(body, BooksBill.fromJson),
      summary?.optObjects('open', BooksAmount.fromJson) ?? const [],
      summary?.optObjects('overdue', BooksAmount.fromJson) ?? const [],
    );
  }

  @override
  Future<BooksBill> bill(String id) async => BooksBill.fromJson(
      JsonReader(await api.request('GET', '$_base/bills/${_id(id)}')));

  @override
  Future<BooksBill> pay(String billId, Map<String, Object?> body,
          {required String idempotencyKey}) async =>
      BooksBill.fromJson(JsonReader(await api.request(
          'POST', '$_base/bills/${_id(billId)}/payments',
          body: body, headers: {'Idempotency-Key': idempotencyKey})));

  @override
  Future<BooksBill> voidPayment(
          String billId, String paymentId, String reason) async =>
      BooksBill.fromJson(JsonReader(await api.request(
          'POST', '$_base/bills/${_id(billId)}/payments/${_id(paymentId)}/void',
          body: {'reason': reason})));

  @override
  Future<BooksBill> voidBill(String billId, String reason) async =>
      BooksBill.fromJson(JsonReader(await api.request(
          'POST', '$_base/bills/${_id(billId)}/void',
          body: {'reason': reason})));

  @override
  Future<ReviewChunk> review({String kind = '', String? token}) async =>
      ReviewChunk.fromJson(JsonReader(await api.request(
          'GET',
          '$_base/review${recordsQuery({
                'kind': kind,
                'offset': token ?? 0,
                'limit': pageSize,
              })}')));

  @override
  Future<void> approveBill(String billId, int version) =>
      api.request('POST', '$_base/bills/${_id(billId)}/approve',
          body: {'version': version});

  @override
  Future<BooksAdjustment> createAdjustment(Map<String, Object?> body,
          {required String idempotencyKey}) async =>
      BooksAdjustment.fromJson(JsonReader(await api.request(
          'POST', '$_base/adjustments',
          body: body, headers: {'Idempotency-Key': idempotencyKey})));

  @override
  Future<void> approveAdjustment(String adjustmentId) =>
      api.request('POST', '$_base/adjustments/${_id(adjustmentId)}/approve');

  @override
  Future<void> voidAdjustment(String adjustmentId, String reason) =>
      api.request('POST', '$_base/adjustments/${_id(adjustmentId)}/void',
          body: {'reason': reason});

  @override
  Future<EvidencePage> evidence(String sourceType, List<String> ids) async =>
      EvidencePage.fromJson(JsonReader(await api.request(
          'GET',
          '$_base/evidence${recordsQuery({
                'source_type': sourceType,
                'ids': ids.join(','),
              })}')));

  @override
  Future<void> attach(String sourceType, String sourceId, String documentId) =>
      api.request('POST', '$_base/evidence', body: {
        'source_type': sourceType,
        'source_id': sourceId,
        'document_id': documentId,
      });

  @override
  Future<void> detach(String sourceType, String sourceId, String linkId) =>
      api.request('DELETE',
          '$_base/evidence/${_id(sourceType)}/${_id(sourceId)}/${_id(linkId)}');

  @override
  Future<RecordsChunk<AttachableDoc>> documents(
      {String query = '', String? token}) async {
    final page = JsonReader(await api.request(
        'GET',
        '$_documents${recordsQuery({
              'access_class': 'workspace',
              'status': 'active',
              'sort': 'updated',
              'q': query,
              'cursor': token,
              'limit': 20,
            })}'));
    return RecordsChunk(page.objects('items', AttachableDoc.fromJson),
        next: page.optString('next_cursor'));
  }
}

/// The books are not set up yet (no base currency).
bool isNotSetUp(Object error) =>
    error is ApiException && error.code == 'books_not_set_up';
