import '../../data/json_read.dart';

/// The fixed asset register, the business's loans and the ledger accounts a
/// manual entry posts to, as the phone reads them
/// (docs/business-books.md#fixed-asset-register, #loan-schedules,
/// #the-journal). Money stays a decimal string next to its currency.

String? _dec(JsonReader r, String key) => switch (r.json[key]) {
      null => null,
      final String text => text,
      final num number => number.toString(),
      _ => r.optString(key),
    };

String _decOr(JsonReader r, String key) => _dec(r, key) ?? '0';

/// One asset (`asset_view`), with its schedule and impairments on the
/// detail.
class BookAsset {
  const BookAsset({
    required this.id,
    required this.name,
    required this.category,
    required this.sourceKind,
    required this.sourceLive,
    required this.acquiredOn,
    required this.currency,
    required this.cost,
    required this.residualValue,
    required this.method,
    required this.depreciationStart,
    required this.status,
    required this.accumulated,
    required this.impairment,
    required this.netBookValue,
    required this.version,
    required this.pendingImpairments,
    this.usefulLifeMonths,
    this.ratePct,
    this.disposedOn,
    this.disposalKind,
    this.proceeds,
    this.gainOrLoss,
    this.schedule = const [],
    this.impairments = const [],
    this.canEdit = false,
    this.me,
  });
  final String id, name, category, sourceKind, acquiredOn, currency, cost;
  final String residualValue, method, depreciationStart, status;
  final String accumulated, impairment, netBookValue;
  final bool sourceLive, canEdit;
  final int version, pendingImpairments;
  final int? usefulLifeMonths;
  final String? ratePct, disposedOn, disposalKind, proceeds, gainOrLoss, me;
  final List<AssetMonth> schedule;
  final List<AssetImpairment> impairments;

  bool get disposed => status == 'disposed';

  factory BookAsset.fromJson(JsonReader r) => BookAsset(
        id: r.string('id'),
        name: r.string('name'),
        category: r.optString('category') ?? '',
        sourceKind: r.optString('source_kind') ?? 'bill',
        sourceLive: r.optBool('source_live') ?? true,
        acquiredOn: r.string('acquired_on'),
        currency: r.string('currency'),
        cost: _decOr(r, 'cost'),
        usefulLifeMonths: r.optInt('useful_life_months'),
        residualValue: _decOr(r, 'residual_value'),
        method: r.optString('method') ?? 'straight_line',
        ratePct: _dec(r, 'rate_pct'),
        depreciationStart: r.optString('depreciation_start') ?? '',
        status: r.optString('status') ?? 'active',
        disposedOn: r.optString('disposed_on'),
        disposalKind: r.optString('disposal_kind'),
        proceeds: _dec(r, 'proceeds'),
        accumulated: _decOr(r, 'accumulated_depreciation'),
        impairment: _decOr(r, 'impairment'),
        netBookValue: _decOr(r, 'net_book_value'),
        gainOrLoss: _dec(r, 'gain_or_loss'),
        version: r.optInt('version') ?? 0,
        pendingImpairments: r.optInt('pending_impairments') ?? 0,
        schedule: r.optObjects('schedule', AssetMonth.fromJson),
        impairments: r.optObjects('impairments', AssetImpairment.fromJson),
        canEdit: r.optBool('can_edit') ?? false,
        me: r.optString('me'),
      );
}

class AssetMonth {
  const AssetMonth(this.month, this.amount, this.accumulated, this.state);
  final String month, amount, accumulated, state;
  factory AssetMonth.fromJson(JsonReader r) => AssetMonth(
      r.string('month'),
      _decOr(r, 'amount'),
      _decOr(r, 'accumulated'),
      r.optString('state') ?? '');
}

class AssetImpairment {
  const AssetImpairment(
      {required this.id,
      required this.entryDate,
      required this.amount,
      required this.reason,
      required this.status,
      required this.requestedBy,
      required this.version});
  final String id, entryDate, amount, reason, status, requestedBy;
  final int version;
  factory AssetImpairment.fromJson(JsonReader r) => AssetImpairment(
        id: r.string('id'),
        entryDate: r.string('entry_date'),
        amount: _decOr(r, 'amount'),
        reason: r.optString('reason') ?? '',
        status: r.optString('status') ?? 'pending',
        requestedBy: r.optString('requested_by') ?? '',
        version: r.optInt('version') ?? 0,
      );
}

/// The register's page: assets and the roll-forward per currency.
class AssetRegister {
  const AssetRegister(
      {required this.items,
      required this.total,
      required this.from,
      required this.to,
      required this.rollForward,
      required this.canEdit,
      required this.pending});
  final List<BookAsset> items;
  final int total, pending;
  final String from, to;
  final List<Map<String, String>> rollForward;
  final bool canEdit;

  factory AssetRegister.fromJson(JsonReader r) {
    final period = r.optObject('period');
    return AssetRegister(
      items: r.optObjects('items', BookAsset.fromJson),
      total: r.optInt('total') ?? 0,
      from: period?.optString('from') ?? '',
      to: period?.optString('to') ?? '',
      rollForward: [
        for (final row in r.optObjects('roll_forward', (j) => j))
          {
            for (final e in row.json.entries)
              if (e.value != null) e.key: '${e.value}'
          }
      ],
      canEdit: r.optBool('can_edit') ?? false,
      pending: r.optInt('pending_impairments') ?? 0,
    );
  }
}

/// One loan the business received (`loan_view`, with `loan_detail`).
class BookLoan {
  const BookLoan({
    required this.id,
    required this.lender,
    required this.principal,
    required this.currency,
    required this.annualRate,
    required this.startDate,
    required this.termMonths,
    required this.frequency,
    required this.method,
    required this.earlyRepayment,
    required this.status,
    required this.outstanding,
    required this.interestAccrued,
    required this.periods,
    required this.repaymentCount,
    this.payment,
    this.maturity,
    this.repaid,
    this.nextDue,
    this.nextPayment,
    this.voidReason,
    this.actual = const [],
    this.repayments = const [],
    this.canEdit = false,
  });
  final String id, lender, principal, currency, annualRate, startDate;
  final String frequency, method, earlyRepayment, status;
  final String outstanding, interestAccrued;
  final int termMonths, periods, repaymentCount;
  final String? payment, maturity, repaid, nextDue, nextPayment, voidReason;
  final List<LoanActualRow> actual;
  final List<LoanRepayment> repayments;
  final bool canEdit;

  bool get voided => status == 'void';

  factory BookLoan.fromJson(JsonReader r) => BookLoan(
        id: r.string('id'),
        lender: r.string('lender'),
        principal: _decOr(r, 'principal'),
        currency: r.string('currency'),
        annualRate: _decOr(r, 'annual_rate'),
        startDate: r.string('start_date'),
        termMonths: r.optInt('term_months') ?? 0,
        frequency: r.optString('frequency') ?? 'monthly',
        method: r.optString('method') ?? 'annuity',
        earlyRepayment: r.optString('early_repayment') ?? 'reduce_term',
        status: r.optString('status') ?? 'active',
        payment: _dec(r, 'payment'),
        periods: r.optInt('periods') ?? 0,
        maturity: r.optString('maturity'),
        outstanding: _decOr(r, 'outstanding'),
        interestAccrued: _decOr(r, 'interest_accrued'),
        repaid: _dec(r, 'repaid'),
        nextDue: r.optString('next_due'),
        nextPayment: _dec(r, 'next_payment'),
        repaymentCount: r.optInt('repayments') ?? 0,
        voidReason: r.optString('void_reason'),
        actual: r.optObjects('actual', LoanActualRow.fromJson),
        repayments: r.optObjects('repayment_rows', LoanRepayment.fromJson),
        canEdit: r.optBool('can_edit') ?? false,
      );
}

class LoanActualRow {
  const LoanActualRow(
      {required this.number,
      required this.due,
      required this.payment,
      required this.interest,
      required this.principal,
      required this.paid,
      required this.state});
  final int number;
  final String due, payment, interest, principal, paid, state;
  factory LoanActualRow.fromJson(JsonReader r) => LoanActualRow(
        number: r.integer('number'),
        due: r.string('due'),
        payment: _decOr(r, 'payment'),
        interest: _decOr(r, 'interest'),
        principal: _decOr(r, 'principal'),
        paid: _decOr(r, 'paid'),
        state: r.optString('state') ?? 'upcoming',
      );
}

class LoanRepayment {
  const LoanRepayment(
      {required this.id,
      required this.paidOn,
      required this.amount,
      required this.interest,
      required this.principal,
      required this.currency,
      required this.status});
  final String id, paidOn, amount, interest, principal, currency, status;
  bool get active => status == 'active';
  factory LoanRepayment.fromJson(JsonReader r) => LoanRepayment(
        id: r.string('id'),
        paidOn: r.string('paid_on'),
        amount: _decOr(r, 'amount'),
        interest: _decOr(r, 'interest'),
        principal: _decOr(r, 'principal'),
        currency: r.optString('currency') ?? '',
        status: r.optString('status') ?? 'active',
      );
}

/// A ledger account a manual entry posts to.
class LedgerAccount {
  const LedgerAccount(this.id, this.code, this.name);
  final String id, code, name;
  String get label => '$code $name';
  factory LedgerAccount.fromJson(JsonReader r) => LedgerAccount(
      r.string('id'), r.optString('code') ?? '', r.optString('name') ?? '');
}
