import '../../data/json_read.dart';

/// Personal money on the phone (docs/finance.md#lent-and-borrowed,
/// #fixed-commitments, #planning-and-advice): what the phone reads from
/// `/api/v1/finance/people-loans`, `/fixed-commitments` and
/// `/planner/payoff`, mirroring `packages/contracts/src/financePersonal.ts`.
/// Money stays a decimal string next to its currency; nothing is added
/// across currencies.

/// A decimal the API sends as a string (`"1234.50"`); a number is accepted
/// too and kept as its text.
String _decimal(JsonReader r, String key) {
  final value = r.json[key];
  return switch (value) {
    final String text => text,
    final num number => number.toString(),
    _ => r.string(key),
  };
}

String? _optDecimal(JsonReader r, String key) {
  final value = r.json[key];
  return switch (value) {
    null => null,
    final String text => text,
    final num number => number.toString(),
    _ => r.optString(key),
  };
}

/// `lent`: I lent, they owe me. `borrowed`: I borrowed, I owe them.
enum LoanDirection { lent, borrowed }

/// `open`, `due_soon` (7 days or less), `overdue`, `repaid` or `closed`.
enum LoanStatus { open, dueSoon, overdue, repaid, closed }

LoanStatus _loanStatus(String value) => switch (value) {
      'due_soon' => LoanStatus.dueSoon,
      'overdue' => LoanStatus.overdue,
      'repaid' => LoanStatus.repaid,
      'closed' => LoanStatus.closed,
      _ => LoanStatus.open,
    };

/// One loan with a person (`FinancePersonLoan`).
class PersonLoan {
  const PersonLoan({
    required this.id,
    required this.direction,
    required this.person,
    required this.amount,
    required this.currency,
    required this.lentOn,
    required this.repaid,
    required this.outstanding,
    required this.status,
    this.dueOn,
    this.daysUntilDue,
    this.notes,
  });

  final String id, person, amount, currency, lentOn, repaid, outstanding;
  final LoanDirection direction;
  final LoanStatus status;
  final String? dueOn, notes;
  final int? daysUntilDue;

  bool get settled =>
      status == LoanStatus.repaid || status == LoanStatus.closed;

  factory PersonLoan.fromJson(JsonReader r) => PersonLoan(
        id: r.string('id'),
        direction: r.string('direction') == 'borrowed'
            ? LoanDirection.borrowed
            : LoanDirection.lent,
        person: r.string('person'),
        amount: _decimal(r, 'amount'),
        currency: r.string('currency'),
        lentOn: r.string('lent_on'),
        dueOn: r.optString('due_on'),
        notes: r.optString('notes'),
        repaid: _decimal(r, 'repaid'),
        outstanding: _decimal(r, 'outstanding'),
        status: _loanStatus(r.string('status')),
        daysUntilDue: r.optInt('days_until_due'),
      );
}

/// What is owed in one currency over every open loan (`FinanceOwed`).
class OwedTotal {
  const OwedTotal(
      {required this.currency, required this.theyOweMe, required this.iOwe});
  final String currency, theyOweMe, iOwe;

  factory OwedTotal.fromJson(JsonReader r) => OwedTotal(
        currency: r.string('currency'),
        theyOweMe: _decimal(r, 'they_owe_me'),
        iOwe: _decimal(r, 'i_owe'),
      );
}

/// `GET /people-loans` (`FinancePersonLoanPage`).
class PersonLoanPage {
  const PersonLoanPage({
    required this.items,
    required this.total,
    required this.owed,
    required this.overdue,
  });
  final List<PersonLoan> items;
  final int total, overdue;
  final List<OwedTotal> owed;

  factory PersonLoanPage.fromJson(JsonReader r) => PersonLoanPage(
        items: r.optObjects('items', PersonLoan.fromJson),
        total: r.optInt('total') ?? 0,
        owed: r.optObjects('owed', OwedTotal.fromJson),
        overdue: r.optInt('overdue') ?? 0,
      );
}

/// A new loan (`FinancePersonLoanInput`).
class PersonLoanInput {
  const PersonLoanInput({
    required this.direction,
    required this.person,
    required this.amount,
    required this.lentOn,
    this.currency,
    this.dueOn,
    this.notes,
  });
  final LoanDirection direction;
  final String person, amount, lentOn;
  final String? currency, dueOn, notes;

  Map<String, Object?> toJson() => {
        'direction': direction.name,
        'person': person,
        'amount': amount,
        if (currency != null && currency!.isNotEmpty)
          'currency': currency!.toUpperCase(),
        'lent_on': lentOn,
        if (dueOn != null && dueOn!.isNotEmpty) 'due_on': dueOn,
        if (notes != null && notes!.isNotEmpty) 'notes': notes,
      };
}

/// One of the person's recorded transactions a repayment can be linked to
/// (`GET /payment-candidates?for=person_loan`).
class LoanCandidate {
  const LoanCandidate({
    required this.canonicalId,
    required this.postedOn,
    required this.merchant,
    required this.amount,
    required this.currency,
    required this.remaining,
  });
  final String canonicalId, postedOn, merchant, amount, currency, remaining;

  factory LoanCandidate.fromJson(JsonReader r) => LoanCandidate(
        canonicalId: r.string('canonical_id'),
        postedOn: r.string('posted_on'),
        merchant: r.string('merchant'),
        amount: _decimal(r, 'amount'),
        currency: r.string('currency'),
        remaining: _decimal(r, 'remaining'),
      );
}

/// One fixed monthly cost (`FinanceCommitment`).
class Commitment {
  const Commitment({
    required this.kind,
    required this.name,
    required this.currency,
    required this.monthly,
    required this.source,
    this.note,
  });

  /// rent, subscription, bill, installment, loan_minimum, card_minimum or
  /// remittance.
  final String kind;
  final String name, currency, monthly, source;
  final String? note;

  factory Commitment.fromJson(JsonReader r) => Commitment(
        kind: r.string('kind'),
        name: r.string('name'),
        currency: r.string('currency'),
        monthly: _decimal(r, 'monthly'),
        source: r.string('source'),
        note: r.optString('note'),
      );
}

/// A per-currency total, never added across currencies.
class MoneyTotal {
  const MoneyTotal(this.currency, this.amount);
  final String currency, amount;
  factory MoneyTotal.fromJson(JsonReader r) =>
      MoneyTotal(r.string('currency'), _decimal(r, 'amount'));
}

/// Fixed costs against income in one currency (`FinanceIncomeShare`).
class IncomeShare {
  const IncomeShare(
      {required this.currency, this.monthly, this.source, this.percent});
  final String currency;

  /// The one income figure (salary profile, else the records), or null
  /// without income.
  final String? monthly;

  /// salary_profile or records.
  final String? source;

  /// Fixed costs as a share of income, rounded; null without income.
  final int? percent;

  factory IncomeShare.fromJson(JsonReader r) => IncomeShare(
        currency: r.string('currency'),
        monthly: _optDecimal(r, 'monthly'),
        source: r.optString('source'),
        percent: r.optInt('share_percent'),
      );
}

/// `GET /fixed-commitments` (`FinanceCommitments`).
class FixedCommitments {
  const FixedCommitments({
    required this.month,
    required this.items,
    required this.notCounted,
    required this.totals,
    this.income = const [],
  });
  final String month;
  final List<Commitment> items, notCounted;
  final List<MoneyTotal> totals;

  /// Per currency with fixed costs; older servers leave it out.
  final List<IncomeShare> income;

  IncomeShare? incomeFor(String currency) =>
      income.where((i) => i.currency == currency).firstOrNull;

  bool get isEmpty => items.isEmpty;

  factory FixedCommitments.fromJson(JsonReader r) => FixedCommitments(
        month: r.optString('month') ?? '',
        items: r.optObjects('items', Commitment.fromJson),
        notCounted: r.optObjects('not_counted', Commitment.fromJson),
        totals: r.optObjects('totals', MoneyTotal.fromJson),
        income: r.optObjects('income', IncomeShare.fromJson),
      );
}

/// One payoff order (`FinancePayoffOrder`): months to debt-free (null when
/// the payment never covers the interest), total interest and the order.
class PayoffOrder {
  const PayoffOrder(
      {required this.months, required this.totalInterest, required this.order});
  final int? months;
  final String totalInterest;
  final List<String> order;

  factory PayoffOrder.fromJson(JsonReader r) => PayoffOrder(
        months: r.optInt('months'),
        totalInterest: _decimal(r, 'total_interest'),
        order: r.optStrings('order') ?? const [],
      );
}

/// Avalanche against snowball for one currency (`FinancePayoff`).
class Payoff {
  const Payoff({
    required this.currency,
    required this.payment,
    required this.belowMinimums,
    required this.avalanche,
    required this.snowball,
    required this.interestSaved,
    required this.debtNames,
    required this.missingRates,
    this.fromPeople = const [],
    this.cheaper,
    this.firstWinMonths,
  });
  final String currency, payment, interestSaved;
  final bool belowMinimums;
  final PayoffOrder avalanche, snowball;

  /// avalanche, snowball, same, or null when it cannot be told.
  final String? cheaper;
  final int? firstWinMonths;

  /// Debt id to name, to show the payoff order by name.
  final Map<String, String> debtNames;

  /// Debts with no rate entered: counted at 0 percent, never guessed.
  final List<String> missingRates;

  /// Names of the debts that are money borrowed from a person (Lent and
  /// borrowed, no interest), counted in the order like any debt.
  final List<String> fromPeople;

  factory Payoff.fromJson(JsonReader r) => Payoff(
        currency: r.string('currency'),
        payment: _decimal(r, 'payment'),
        belowMinimums: r.optBool('below_minimums') ?? false,
        avalanche: PayoffOrder.fromJson(r.object('avalanche')),
        snowball: PayoffOrder.fromJson(r.object('snowball')),
        cheaper: r.optString('cheaper'),
        interestSaved: _optDecimal(r, 'interest_saved') ?? '0',
        firstWinMonths: r.optInt('first_win_months'),
        debtNames: {
          for (final d in r.optObjects(
              'debts', (d) => (d.string('id'), d.string('name'))))
            d.$1: d.$2
        },
        missingRates: r.optStrings('missing_rates') ?? const [],
        fromPeople: [
          for (final d in r.optObjects(
              'debts', (d) => (d.string('name'), d.optString('source'))))
            if (d.$2 == 'person_loan') d.$1
        ],
      );

  /// The order by debt name (an id with no known name stays as it is).
  List<String> names(PayoffOrder o) =>
      [for (final id in o.order) debtNames[id] ?? id];
}

/// `GET /planner/payoff` (`FinancePayoffPage`).
class PayoffPage {
  const PayoffPage({required this.currencies, required this.note});
  final List<Payoff> currencies;
  final String note;

  factory PayoffPage.fromJson(JsonReader r) => PayoffPage(
        currencies: r.optObjects('currencies', Payoff.fromJson),
        note: r.optString('note') ?? '',
      );
}

/// The salary profile, the parts the phone shows (`FinanceSalaryProfile`).
class SalaryProfile {
  const SalaryProfile({
    required this.employer,
    required this.currency,
    required this.expectedNet,
    required this.nextPayOn,
    required this.payDay,
  });
  final String employer, currency, expectedNet, nextPayOn;
  final int payDay;

  factory SalaryProfile.fromJson(JsonReader r) => SalaryProfile(
        employer: r.string('employer'),
        currency: r.string('currency'),
        expectedNet: _decimal(r, 'expected_net'),
        nextPayOn: r.string('next_pay_on'),
        payDay: r.optInt('pay_day') ?? 1,
      );
}

/// The salary found in the records for a month, waiting or decided.
class SalaryFound {
  const SalaryFound(
      {required this.transactionId,
      required this.postedOn,
      required this.amount,
      required this.description});
  final String transactionId, postedOn, amount, description;

  factory SalaryFound.fromJson(JsonReader r) => SalaryFound(
        transactionId: r.string('transaction_id'),
        postedOn: r.string('posted_on'),
        amount: _decimal(r, 'amount'),
        description: r.optString('description') ?? '',
      );
}

/// One month of the salary check (`FinanceSalaryMonth`).
class SalaryMonth {
  const SalaryMonth({
    required this.month,
    required this.currency,
    required this.payOn,
    required this.status,
    required this.expectedNet,
    required this.received,
    required this.difference,
    this.basis,
    this.found,
  });
  final String month, currency, payOn, expectedNet, received, difference;

  /// received, late, partial, missing or upcoming.
  final String status;

  /// confirmed, payslip, suggested (waiting for a confirm or reject) or null.
  final String? basis;
  final SalaryFound? found;

  /// A salary was found in the records and waits for the person's word.
  bool get waiting => basis == 'suggested' && found != null;

  factory SalaryMonth.fromJson(JsonReader r) {
    final found = r.optObject('found');
    return SalaryMonth(
      month: r.string('month'),
      currency: r.string('currency'),
      payOn: r.string('pay_on'),
      status: r.string('status'),
      expectedNet: _decimal(r.object('expected'), 'net'),
      received: _decimal(r, 'received'),
      difference: _decimal(r, 'difference'),
      basis: r.optString('basis'),
      found: found == null ? null : SalaryFound.fromJson(found),
    );
  }
}

/// `GET /salary/months` (`FinanceSalaryMonthsPage`), newest month first.
class SalaryMonths {
  const SalaryMonths(
      {required this.months, required this.missing, required this.waiting});
  final List<SalaryMonth> months;
  final int missing, waiting;

  factory SalaryMonths.fromJson(JsonReader r) => SalaryMonths(
        months: r.optObjects('months', SalaryMonth.fromJson),
        missing: r.optInt('missing') ?? 0,
        waiting: r.optInt('waiting') ?? 0,
      );
}

/// The salary card: the profile (null when none is set) and recent months.
class SalaryView {
  const SalaryView({this.profile, this.months});
  final SalaryProfile? profile;
  final SalaryMonths? months;

  /// This month (the newest), or null.
  SalaryMonth? get current => months?.months.firstOrNull;

  /// Months whose salary waits for a confirm or reject, newest first.
  List<SalaryMonth> get waiting =>
      months?.months.where((m) => m.waiting).toList() ?? const [];
}

/// The planner's answer to a question, written by the Assistant's finance
/// skill from the person's own records and, when web search is on, live
/// sources with their address and the date read.
class PlannerAnswer {
  const PlannerAnswer({required this.text, this.conversationId});
  final String text;

  /// The Assistant conversation that keeps the question and the answer.
  final String? conversationId;

  /// The live sources named in the answer (https only), in order, once each.
  List<Uri> get sources => plannerSources(text);
}

/// The https addresses in [text], in order, once each. Trailing punctuation
/// that ends a sentence is not part of the address.
List<Uri> plannerSources(String text) {
  final found = <Uri>[];
  final seen = <String>{};
  for (final match in RegExp(r'https://[^\s<>()\[\]"]+').allMatches(text)) {
    final raw = match.group(0)!.replaceFirst(RegExp(r'[.,;:!?]+$'), '');
    final uri = Uri.tryParse(raw);
    if (uri == null || uri.host.isEmpty || !seen.add(raw)) continue;
    found.add(uri);
  }
  return found;
}

/// The planner could not answer: [message] says what failed.
class PlannerFailure implements Exception {
  const PlannerFailure(this.message, {this.code});
  final String message;
  final String? code;

  @override
  String toString() => message;
}
