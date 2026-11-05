import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../records_kit/records_kit.dart';

/// The business books (docs/business-books.md#api), the parts the phone
/// shows: the P&L for a period (read only) and the bills with their
/// payments. Nothing here posts money.

class BooksPermissions {
  const BooksPermissions(
      {required this.read, required this.write, required this.manage});
  factory BooksPermissions.fromJson(JsonReader j) => BooksPermissions(
      read: j.optBool('read') ?? false,
      write: j.optBool('write') ?? false,
      manage: j.optBool('manage') ?? false);
  final bool read, write, manage;
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
    );
  }
  final bool configured;
  final BooksPermissions permissions;
  final String? baseCurrency, periodFrom, periodTo;
  final String basis;
  final int? version;
  final int billCount;
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
}

class ApiBooksRepository implements BooksRepository {
  const ApiBooksRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/business/books';
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
}

/// The books are not set up yet (no base currency).
bool isNotSetUp(Object error) =>
    error is ApiException && error.code == 'books_not_set_up';
