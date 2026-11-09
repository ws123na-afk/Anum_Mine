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

/// `GET /fixed-commitments` (`FinanceCommitments`).
class FixedCommitments {
  const FixedCommitments({
    required this.month,
    required this.items,
    required this.notCounted,
    required this.totals,
  });
  final String month;
  final List<Commitment> items, notCounted;
  final List<MoneyTotal> totals;

  bool get isEmpty => items.isEmpty;

  factory FixedCommitments.fromJson(JsonReader r) => FixedCommitments(
        month: r.optString('month') ?? '',
        items: r.optObjects('items', Commitment.fromJson),
        notCounted: r.optObjects('not_counted', Commitment.fromJson),
        totals: r.optObjects('totals', MoneyTotal.fromJson),
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
