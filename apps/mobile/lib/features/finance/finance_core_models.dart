import '../../data/json_read.dart';
import 'finance_models.dart';

/// The Personal Finance dashboard contract (docs/finance.md#dashboard) and the
/// transaction register and its detail (docs/finance.md#transaction-detail),
/// as the phone reads them. Every figure is the server's: the phone parses and
/// shows it, never adds or converts. The web reads the same answer the same
/// way (`apps/web/src/lib/financeDashboard.ts`); both are tested against one
/// shared fixture (`packages/contracts/fixtures/finance-summary.json`).

/// Money that moved this month but is neither income nor spend, by why.
class ExcludedFlow {
  const ExcludedFlow(
      this.kind, this.currency, this.moneyIn, this.moneyOut, this.count);
  final String kind, currency, moneyIn, moneyOut;
  final int count;
  factory ExcludedFlow.fromJson(JsonReader r) => ExcludedFlow(
      r.string('kind'),
      r.string('currency'),
      r.string('money_in'),
      r.string('money_out'),
      r.optInt('count') ?? 0);
  static const order = [
    'own_transfer',
    'card_payment',
    'loan_principal',
    'lending',
    'other_transfer'
  ];
}

/// One currency's cash against card credit.
class PositionPart {
  const PositionPart(
      {required this.currency,
      required this.cash,
      required this.investments,
      required this.cardOwed,
      required this.cardInCredit,
      required this.loansOwed,
      this.cardLimit,
      this.cardAvailable});
  final String currency, cash, investments, cardOwed, cardInCredit, loansOwed;
  final String? cardLimit, cardAvailable;
  factory PositionPart.fromJson(JsonReader r) => PositionPart(
        currency: r.string('currency'),
        cash: r.string('cash'),
        investments: r.optString('investments') ?? '0',
        cardOwed: r.string('card_owed'),
        cardInCredit: r.optString('card_in_credit') ?? '0',
        loansOwed: r.optString('loans_owed') ?? '0',
        cardLimit: r.optString('card_limit'),
        cardAvailable: r.optString('card_available'),
      );
}

/// A bill due date or an installment due in 30 days with no matched payment.
class UpcomingPayment {
  const UpcomingPayment(
      {required this.kind,
      required this.id,
      required this.name,
      required this.amount,
      required this.currency,
      required this.dueOn,
      required this.daysUntil,
      this.provider,
      this.overdue = false,
      this.status = 'pending'});
  final String kind, id, name, amount, currency, dueOn, status;
  final String? provider;
  final int daysUntil;
  final bool overdue;
  factory UpcomingPayment.fromJson(JsonReader r) => UpcomingPayment(
        kind: r.string('kind'),
        id: r.string('id'),
        name: r.string('name'),
        provider: r.optString('provider'),
        amount: r.string('amount'),
        currency: r.string('currency'),
        dueOn: r.string('due_on'),
        daysUntil: r.optInt('days_until') ?? 0,
        overdue: r.optBool('overdue') ?? false,
        status: r.optString('status') ?? 'pending',
      );
}

/// The month's money in by kind (salary, interest, other income, cashback,
/// refunds without a purchase).
class IncomeLine {
  const IncomeLine(this.kind, this.currency, this.amount, this.count);
  final String kind, currency, amount;
  final int count;
  factory IncomeLine.fromJson(JsonReader r) => IncomeLine(r.string('kind'),
      r.string('currency'), r.string('amount'), r.optInt('count') ?? 0);
}

/// The shown month in the salary check (the same as `GET /salary/months`).
class SalaryStatus {
  const SalaryStatus(
      {required this.month,
      required this.currency,
      required this.payOn,
      required this.expectedNet,
      required this.received,
      required this.difference,
      required this.status,
      this.basis,
      this.transactionId,
      this.waiting = 0,
      this.missing = 0});
  final String month, currency, payOn, expectedNet, received, difference;
  final String status;
  final String? basis, transactionId;
  final int waiting, missing;
  factory SalaryStatus.fromJson(JsonReader r) => SalaryStatus(
        month: r.string('month'),
        currency: r.string('currency'),
        payOn: r.string('pay_on'),
        expectedNet: r.string('expected_net'),
        received: r.string('received'),
        difference: r.string('difference'),
        status: r.string('status'),
        basis: r.optString('basis'),
        transactionId: r.optString('transaction_id'),
        waiting: r.optInt('waiting') ?? 0,
        missing: r.optInt('missing') ?? 0,
      );
}

/// What waits for the person.
class ReviewCounts {
  const ReviewCounts(
      {this.uncategorized = 0,
      this.smsReview = 0,
      this.linksNeedReview = 0,
      this.installmentsReview = 0,
      this.total = 0});
  final int uncategorized,
      smsReview,
      linksNeedReview,
      installmentsReview,
      total;
  factory ReviewCounts.fromJson(JsonReader r) => ReviewCounts(
        uncategorized: r.optInt('uncategorized') ?? 0,
        smsReview: r.optInt('sms_review') ?? 0,
        linksNeedReview: r.optInt('links_need_review') ?? 0,
        installmentsReview: r.optInt('installments_review') ?? 0,
        total: r.optInt('total') ?? 0,
      );
}

/// How current one account is: its sources, when each last delivered, and
/// the bank's last stated balance (verified) against the ledger (estimated).
class AccountFreshness {
  const AccountFreshness(
      {required this.accountId,
      required this.sources,
      required this.state,
      required this.balanceKind,
      this.lastActivityOn,
      this.lastImportAt,
      this.lastSmsAt,
      this.lastApplePayAt,
      this.verifiedBalance,
      this.verifiedAt,
      this.verifiedBy,
      this.difference,
      this.creditLimit});
  final String accountId, state, balanceKind;
  final List<String> sources;
  final String? lastActivityOn,
      lastImportAt,
      lastSmsAt,
      lastApplePayAt,
      verifiedBalance,
      verifiedAt,
      verifiedBy,
      difference,
      creditLimit;
  factory AccountFreshness.fromJson(JsonReader r) => AccountFreshness(
        accountId: r.string('account_id'),
        sources: r.optStrings('sources') ?? const [],
        state: r.optString('state') ?? 'manual',
        balanceKind: r.optString('balance_kind') ?? 'estimated',
        lastActivityOn: r.optString('last_activity_on'),
        lastImportAt: r.optString('last_import_at'),
        lastSmsAt: r.optString('last_sms_at'),
        lastApplePayAt: r.optString('last_apple_pay_at'),
        verifiedBalance: r.optString('verified_balance'),
        verifiedAt: r.optString('verified_at'),
        verifiedBy: r.optString('verified_by'),
        difference: r.optString('difference'),
        creditLimit: r.optString('credit_limit'),
      );

  /// The newest automated capture: (`sms` | `apple_wallet` | `statement`, time).
  (String, String)? get lastCapture {
    final seen = [
      if (lastSmsAt != null) ('sms', lastSmsAt!),
      if (lastApplePayAt != null) ('apple_wallet', lastApplePayAt!),
      if (lastImportAt != null) ('statement', lastImportAt!),
    ]..sort((a, b) => b.$2.compareTo(a.$2));
    return seen.firstOrNull;
  }
}

/// The dashboard part of `GET /finance/summary`. An older server leaves it
/// out: every list is then empty and [hasDashboard] is false.
class FinanceDashboard {
  const FinanceDashboard(
      {this.detail = 'full',
      this.excluded = const [],
      this.positions = const [],
      this.upcomingPayments = const [],
      this.paymentsDue = const [],
      this.incomeBreakdown = const [],
      this.salary,
      this.review = const ReviewCounts(),
      this.freshness = const [],
      this.hasDashboard = false});
  final String detail;
  final List<ExcludedFlow> excluded;
  final List<PositionPart> positions;
  final List<UpcomingPayment> upcomingPayments;
  final List<CurrencyAmount> paymentsDue;
  final List<IncomeLine> incomeBreakdown;
  final SalaryStatus? salary;
  final ReviewCounts review;
  final List<AccountFreshness> freshness;
  final bool hasDashboard;

  factory FinanceDashboard.fromJson(JsonReader r) {
    final salary = r.optObject('salary');
    final review = r.optObject('review');
    return FinanceDashboard(
      detail: r.optString('detail') ?? 'full',
      excluded: r.optObjects('excluded', ExcludedFlow.fromJson),
      positions: r.optObjects('positions', PositionPart.fromJson),
      upcomingPayments:
          r.optObjects('upcoming_payments', UpcomingPayment.fromJson),
      paymentsDue: r.optObjects('payments_due', CurrencyAmount.fromJson),
      incomeBreakdown: r.optObjects('income_breakdown', IncomeLine.fromJson),
      salary: salary == null ? null : SalaryStatus.fromJson(salary),
      review:
          review == null ? const ReviewCounts() : ReviewCounts.fromJson(review),
      freshness: r.optObjects('freshness', AccountFreshness.fromJson),
      hasDashboard: r['positions'] != null,
    );
  }

  AccountFreshness? freshnessFor(String accountId) =>
      freshness.where((f) => f.accountId == accountId).firstOrNull;

  PositionPart? positionFor(String currency) =>
      positions.where((p) => p.currency == currency).firstOrNull;
}

/// A zero in the currency's own minor digits ("0.00", "0.000", "0").
String zeroIn(String currency) =>
    const {'KWD', 'BHD', 'OMR', 'JOD', 'TND'}.contains(currency)
        ? '0.000'
        : const {'JPY', 'KRW'}.contains(currency)
            ? '0'
            : '0.00';

String amountIn(List<CurrencyAmount> rows, String currency) =>
    rows.where((r) => r.currency == currency).firstOrNull?.amount ??
    zeroIn(currency);

/// What the overview shows for one currency: the twin of the web's
/// `dashboardTotals` (apps/web/src/lib/financeDashboard.ts).
class DashboardTotals {
  const DashboardTotals({
    required this.currency,
    required this.balance,
    required this.cash,
    required this.cardOwed,
    required this.cardAvailable,
    required this.income,
    required this.spend,
    required this.net,
    required this.paymentsDue,
    required this.upcomingCount,
    required this.excluded,
    required this.reviewTotal,
    required this.staleAccounts,
    required this.salaryReceived,
  });
  final String currency, balance, cash, cardOwed, income, spend, net;
  final String paymentsDue;
  final String? cardAvailable, salaryReceived;
  final int upcomingCount, reviewTotal, staleAccounts;

  /// Kind to (money in, money out, count), as the server counted them.
  final Map<String, (String, String, int)> excluded;
}

DashboardTotals dashboardTotals(FinanceSummary s, String currency) {
  final d = s.dashboard;
  final position = d.positionFor(currency);
  return DashboardTotals(
    currency: currency,
    balance: amountIn(s.balances, currency),
    cash: position?.cash ?? zeroIn(currency),
    cardOwed: position?.cardOwed ?? zeroIn(currency),
    cardAvailable: position?.cardAvailable,
    income: amountIn(s.income, currency),
    spend: amountIn(s.spend, currency),
    net: amountIn(s.net, currency),
    paymentsDue: amountIn(d.paymentsDue, currency),
    upcomingCount:
        d.upcomingPayments.where((p) => p.currency == currency).length,
    excluded: {
      for (final e in d.excluded)
        if (e.currency == currency) e.kind: (e.moneyIn, e.moneyOut, e.count)
    },
    reviewTotal: d.review.total,
    staleAccounts: d.freshness.where((f) => f.state == 'stale').length,
    salaryReceived: d.salary != null && d.salary!.currency == currency
        ? d.salary!.received
        : null,
  );
}

// -- the register and a transaction's detail --------------------------------

/// The register's filters (docs/finance.md#register-and-account-overview).
class RegisterFilters {
  const RegisterFilters(
      {this.query = '',
      this.accountId,
      this.category,
      this.type,
      this.status,
      this.direction,
      this.from,
      this.to});
  final String query;
  final String? accountId, category, type, status, direction, from, to;

  bool get narrowed =>
      query.isNotEmpty ||
      accountId != null ||
      category != null ||
      type != null ||
      status != null ||
      direction != null ||
      from != null ||
      to != null;

  Map<String, String> toQuery() => {
        if (query.isNotEmpty) 'q': query,
        if (accountId != null) 'account_id': accountId!,
        if (category != null) 'category': category!,
        if (type != null) 'type': type!,
        if (status != null) 'status': status!,
        if (direction != null) 'direction': direction!,
        if (from != null) 'from': from!,
        if (to != null) 'to': to!,
      };

  RegisterFilters copyWith(
          {String? query,
          String? Function()? accountId,
          String? Function()? category,
          String? Function()? type,
          String? Function()? status,
          String? Function()? direction,
          String? Function()? from,
          String? Function()? to}) =>
      RegisterFilters(
        query: query ?? this.query,
        accountId: accountId == null ? this.accountId : accountId(),
        category: category == null ? this.category : category(),
        type: type == null ? this.type : type(),
        status: status == null ? this.status : status(),
        direction: direction == null ? this.direction : direction(),
        from: from == null ? this.from : from(),
        to: to == null ? this.to : to(),
      );
}

/// One register row: the transaction with where it came from and its state.
class RegisterRow {
  const RegisterRow(
      {required this.id,
      required this.accountId,
      required this.postedOn,
      required this.amount,
      required this.currency,
      required this.description,
      required this.category,
      required this.categorySource,
      required this.origin,
      required this.sources,
      required this.status,
      required this.reconciled,
      this.notes,
      this.transactionType,
      this.canonicalId,
      this.linksNeedReview = 0});
  final String id, accountId, postedOn, amount, currency, description;
  final String category, categorySource, origin, status;
  final List<String> sources;
  final bool reconciled;
  final String? notes, transactionType, canonicalId;
  final int linksNeedReview;

  bool get moneyIn => !amount.startsWith('-');

  factory RegisterRow.fromJson(JsonReader r) => RegisterRow(
        id: r.string('id'),
        accountId: r.string('account_id'),
        postedOn: r.string('posted_on'),
        amount: r.string('amount'),
        currency: r.string('currency'),
        description: r.string('description'),
        category: r.string('category'),
        categorySource: r.optString('category_source') ?? 'none',
        origin: r.optString('origin') ?? 'manual',
        sources: r.optStrings('sources') ?? const [],
        status: r.optString('status') ?? 'recorded',
        reconciled: r.optBool('reconciled') ?? false,
        notes: r.optString('notes'),
        transactionType: r.optString('transaction_type'),
        canonicalId: r.optString('canonical_id'),
        linksNeedReview: r.optInt('links_need_review') ?? 0,
      );
}

class RegisterPage {
  const RegisterPage(this.items, this.total);
  final List<RegisterRow> items;
  final int total;
  factory RegisterPage.fromJson(JsonReader r) => RegisterPage(
      r.optObjects('items', RegisterRow.fromJson), r.optInt('total') ?? 0);
}

/// A record the payment is linked to, with the evidence behind the link:
/// `pending` (waits for review), `provisional` (an SMS, Apple Pay or typed
/// record only) or `verified` (a statement, or counted cash).
class LinkedRecord {
  const LinkedRecord(
      {required this.id,
      required this.dimension,
      required this.role,
      required this.status,
      required this.evidence,
      this.targetName,
      this.amount,
      this.currency});
  final String id, dimension, role, status, evidence;
  final String? targetName, amount, currency;
  factory LinkedRecord.fromJson(JsonReader r) => LinkedRecord(
        id: r.string('id'),
        dimension: r.string('dimension'),
        role: r.string('role'),
        status: r.string('status'),
        evidence: r.string('evidence'),
        targetName: r.optString('target_name'),
        amount: r.optString('amount'),
        currency: r.optString('currency'),
      );
}

/// A refund of this purchase, the purchase a refund gives back, or a fee.
class RelatedRow {
  const RelatedRow(this.id, this.relation, this.postedOn, this.amount,
      this.currency, this.description);
  final String id, relation, postedOn, amount, currency, description;
  factory RelatedRow.fromJson(JsonReader r) => RelatedRow(
      r.string('id'),
      r.string('relation'),
      r.string('posted_on'),
      r.string('amount'),
      r.string('currency'),
      r.string('description'));
}

/// One source message (SMS or Apple Pay) behind a transaction.
class SourceMessage {
  const SourceMessage(this.kind, this.receivedAt, this.text);
  final String kind, receivedAt;
  final String? text;
  factory SourceMessage.fromJson(JsonReader r) => SourceMessage(
      r.string('kind'), r.string('received_at'), r.optString('text'));
}

/// `GET /finance/register/{id}`: a transaction with its evidence.
class RegisterDetail {
  const RegisterDetail(
      {required this.row,
      required this.accountName,
      required this.categoryBy,
      required this.suggestedCategory,
      this.merchant,
      this.reference,
      this.sources = const [],
      this.links = const [],
      this.related = const [],
      this.splitCount = 0,
      this.receipts = 0});
  final RegisterRow row;
  final String accountName, categoryBy, suggestedCategory;
  final String? merchant, reference;
  final List<SourceMessage> sources;
  final List<LinkedRecord> links;
  final List<RelatedRow> related;
  final int splitCount, receipts;

  static const _by = {
    'manual': 'owner',
    'owner_rule': 'learned',
    'rule': 'rule',
    'match': 'match',
    'system': 'system',
  };

  factory RegisterDetail.fromJson(JsonReader r) {
    final row = RegisterRow.fromJson(r.object('row'));
    return RegisterDetail(
      row: row,
      accountName: r.optString('account_name') ?? '',
      categoryBy:
          r.optString('category_by') ?? _by[row.categorySource] ?? 'none',
      suggestedCategory: r.optString('suggested_category') ?? row.category,
      merchant: r.optString('merchant'),
      reference: r.optString('reference'),
      sources: r.optObjects('sources', SourceMessage.fromJson),
      links: r.optObjects('links', LinkedRecord.fromJson),
      related: r.optObjects('related', RelatedRow.fromJson),
      splitCount: r.optInt('split_count') ?? 0,
      receipts: r.object('row').optInt('receipts') ?? 0,
    );
  }
}
