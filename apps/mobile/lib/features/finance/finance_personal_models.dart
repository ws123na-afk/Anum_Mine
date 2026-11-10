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

/// One loan with a person (`FinancePersonLoan`): repaid once on its due
/// date, or monthly from `first_due_on` (`installments`), interest free
/// unless `interest_rate` (percent a year) is set.
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
    this.remind = true,
    this.remindDaysBefore = 3,
    this.closedOn,
    this.interestRate,
    this.installments,
    this.firstDueOn,
    this.interest = '0',
    this.nextDueOn,
    this.nextDueAmount = '0',
    this.overdueAmount = '0',
    this.handed = '0',
  });

  final String id, person, amount, currency, lentOn, repaid, outstanding;

  /// What the linked transactions show was handed over.
  final String handed;
  final LoanDirection direction;
  final LoanStatus status;
  final String? dueOn, notes, closedOn, interestRate, firstDueOn, nextDueOn;
  final int? daysUntilDue, installments;
  final bool remind;
  final int remindDaysBefore;

  /// Scheduled interest in total, the next repayment not covered yet and
  /// what was due before today and is not repaid, all in [currency].
  final String interest, nextDueAmount, overdueAmount;

  bool get settled =>
      status == LoanStatus.repaid || status == LoanStatus.closed;

  /// Something was due before today and is not repaid.
  bool get hasOverdue => (double.tryParse(overdueAmount) ?? 0) > 0;

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
        remind: r.optBool('remind') ?? true,
        remindDaysBefore: r.optInt('remind_days_before') ?? 3,
        closedOn: r.optString('closed_on'),
        interestRate: _optDecimal(r, 'interest_rate'),
        installments: r.optInt('installments'),
        firstDueOn: r.optString('first_due_on'),
        interest: _optDecimal(r, 'interest') ?? '0',
        nextDueOn: r.optString('next_due_on'),
        nextDueAmount: _optDecimal(r, 'next_due_amount') ?? '0',
        overdueAmount: _optDecimal(r, 'overdue_amount') ?? '0',
        handed: _optDecimal(r, 'handed') ?? '0',
      );
}

/// `repaid`, `part_repaid`, `overdue` or `due`.
enum InstallmentState { repaid, partRepaid, overdue, due }

/// One repayment of a loan's schedule (`FinanceLoanInstallment`).
class LoanInstallment {
  const LoanInstallment({
    required this.number,
    required this.dueOn,
    required this.principal,
    required this.interest,
    required this.amount,
    required this.state,
  });
  final int number;
  final String dueOn, principal, interest, amount;
  final InstallmentState state;

  factory LoanInstallment.fromJson(JsonReader r) => LoanInstallment(
        number: r.optInt('number') ?? 0,
        dueOn: r.string('due_on'),
        principal: _decimal(r, 'principal'),
        interest: _decimal(r, 'interest'),
        amount: _decimal(r, 'amount'),
        state: switch (r.optString('state')) {
          'repaid' => InstallmentState.repaid,
          'part_repaid' => InstallmentState.partRepaid,
          'overdue' => InstallmentState.overdue,
          _ => InstallmentState.due,
        },
      );
}

/// `GET /people-loans/{id}` (`FinancePersonLoanDetail`): the loan with its
/// schedule, repaid money applied in date order.
class PersonLoanDetail {
  const PersonLoanDetail(
      {required this.loan, required this.schedule, this.movements = const []});
  final PersonLoan loan;
  final List<LoanInstallment> schedule;

  /// The transactions linked to the loan: the money handed over and the
  /// repayments.
  final List<LoanMovement> movements;

  factory PersonLoanDetail.fromJson(JsonReader r) => PersonLoanDetail(
        loan: PersonLoan.fromJson(r),
        schedule: r.optObjects('schedule', LoanInstallment.fromJson),
        movements: r.optObjects('movements', LoanMovement.fromJson),
      );
}

/// One transaction linked to a loan (`FinanceMovement`): `payment` is money
/// out, `deposit` money in; a link that is not `active` needs review.
class LoanMovement {
  const LoanMovement({
    required this.linkId,
    required this.role,
    required this.status,
    this.amount,
    this.currency,
    this.postedOn,
    this.description,
  });
  final String linkId, role, status;
  final String? amount, currency, postedOn, description;
  bool get active => status == 'active';

  factory LoanMovement.fromJson(JsonReader r) {
    final link = r.object('link');
    return LoanMovement(
      linkId: link.string('id'),
      role: link.optString('role') ?? '',
      status: link.optString('status') ?? 'active',
      amount: _optDecimal(link, 'amount'),
      currency: link.optString('currency'),
      postedOn: r.optString('posted_on'),
      description: r.optString('description'),
    );
  }
}

/// What is owed in one currency over every open loan (`FinanceOwed`), with
/// the overdue part of each side. Never added across currencies.
class OwedTotal {
  const OwedTotal(
      {required this.currency,
      required this.theyOweMe,
      required this.iOwe,
      this.theyOweMeOverdue = '0',
      this.iOweOverdue = '0'});
  final String currency, theyOweMe, iOwe, theyOweMeOverdue, iOweOverdue;

  factory OwedTotal.fromJson(JsonReader r) => OwedTotal(
        currency: r.string('currency'),
        theyOweMe: _decimal(r, 'they_owe_me'),
        iOwe: _decimal(r, 'i_owe'),
        theyOweMeOverdue: _optDecimal(r, 'they_owe_me_overdue') ?? '0',
        iOweOverdue: _optDecimal(r, 'i_owe_overdue') ?? '0',
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

/// A new loan, or the full body that replaces one (`FinancePersonLoanInput`).
/// Only what was given is sent; an edit passes every field it keeps (the
/// API replaces the loan with the body, so a field left out is cleared).
class PersonLoanInput {
  const PersonLoanInput({
    required this.direction,
    required this.person,
    required this.amount,
    required this.lentOn,
    this.currency,
    this.dueOn,
    this.notes,
    this.remind,
    this.remindDaysBefore,
    this.closed,
    this.interestRate,
    this.installments,
    this.firstDueOn,
  });
  final LoanDirection direction;
  final String person, amount, lentOn;
  final String? currency, dueOn, notes, interestRate, firstDueOn;
  final bool? remind, closed;
  final int? remindDaysBefore, installments;

  Map<String, Object?> toJson() {
    final scheduled = installments != null;
    return {
      'direction': direction.name,
      'person': person,
      'amount': amount,
      if (currency != null && currency!.isNotEmpty)
        'currency': currency!.toUpperCase(),
      'lent_on': lentOn,
      // A schedule decides the due date: the last repayment.
      if (!scheduled && dueOn != null && dueOn!.isNotEmpty) 'due_on': dueOn,
      if (notes != null && notes!.isNotEmpty) 'notes': notes,
      if (remind != null) 'remind': remind,
      if (remindDaysBefore != null) 'remind_days_before': remindDaysBefore,
      if (closed != null) 'closed': closed,
      if (interestRate != null && interestRate!.isNotEmpty)
        'interest_rate': interestRate,
      if (scheduled) 'installments': installments,
      if (scheduled && firstDueOn != null && firstDueOn!.isNotEmpty)
        'first_due_on': firstDueOn,
    };
  }
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

/// The state of one expected one-off income: still `expected`, `late` (its
/// date passed and it is not marked received, so the forecast stops
/// counting it) or `received`.
enum IncomeState { expected, late, received }

/// One-off income the person expects on a date (`FinanceExpectedIncome`,
/// docs/finance.md#forecast): counted by the forecast until received.
class ExpectedIncome {
  const ExpectedIncome({
    required this.id,
    required this.label,
    required this.amount,
    required this.currency,
    required this.expectedOn,
    required this.state,
    this.receivedOn,
    this.notes,
  });
  final String id, label, amount, currency, expectedOn;
  final String? receivedOn, notes;
  final IncomeState state;

  factory ExpectedIncome.fromJson(JsonReader r) => ExpectedIncome(
        id: r.string('id'),
        label: r.string('label'),
        amount: _decimal(r, 'amount'),
        currency: r.string('currency'),
        expectedOn: r.string('expected_on'),
        receivedOn: r.optString('received_on'),
        notes: r.optString('notes'),
        state: switch (r.optString('state')) {
          'late' => IncomeState.late,
          'received' => IncomeState.received,
          _ => IncomeState.expected,
        },
      );

  /// The same income as a full body, received on [on] (or as it was).
  ExpectedIncomeInput toInput({String? receivedOn}) => ExpectedIncomeInput(
        label: label,
        amount: amount,
        currency: currency,
        expectedOn: expectedOn,
        receivedOn: receivedOn ?? this.receivedOn,
        notes: notes,
      );
}

/// `GET /expected-income` (`FinanceExpectedIncomePage`).
class ExpectedIncomePage {
  const ExpectedIncomePage({required this.items, required this.total});
  final List<ExpectedIncome> items;
  final int total;

  factory ExpectedIncomePage.fromJson(JsonReader r) => ExpectedIncomePage(
        items: r.optObjects('items', ExpectedIncome.fromJson),
        total: r.optInt('total') ?? 0,
      );
}

/// The body of `POST /expected-income` and `PATCH /expected-income/{id}`
/// (the PATCH replaces the record with the full body).
class ExpectedIncomeInput {
  const ExpectedIncomeInput({
    required this.label,
    required this.amount,
    required this.expectedOn,
    this.currency,
    this.receivedOn,
    this.notes,
  });
  final String label, amount, expectedOn;
  final String? currency, receivedOn, notes;

  Map<String, Object?> toJson() => {
        'label': label,
        'amount': amount,
        if (currency != null && currency!.isNotEmpty)
          'currency': currency!.toUpperCase(),
        'expected_on': expectedOn,
        'received_on': receivedOn,
        'notes': (notes == null || notes!.isEmpty) ? null : notes,
      };
}
