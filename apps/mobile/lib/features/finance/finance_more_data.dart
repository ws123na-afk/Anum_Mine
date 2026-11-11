import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../records_kit/records_export.dart';
import '../records_kit/records_kit.dart';
import 'finance_money_models.dart';
import 'finance_personal_models.dart';

/// The Finance pages the phone gained for parity with the web
/// (docs/finance.md): reconciliation sessions and the exceptions across
/// accounts, bank loans with their schedule and accepted batches, one
/// payment split across loans with people, foreign-currency balances, and
/// trips and projects. What the phone reads and sends, mirroring
/// `packages/contracts/src/financeReconciliation.ts`, `financePersonal.ts`
/// and the bank loan routes. Money stays a decimal string next to its
/// currency; nothing is added across currencies.

/// A decimal the API sends as a string; a number is kept as its text.
String? dec(JsonReader r, String key) => switch (r.json[key]) {
      null => null,
      final String text => text,
      final num number => number.toString(),
      _ => r.optString(key),
    };

String decOr(JsonReader r, String key, [String fallback = '0']) =>
    dec(r, key) ?? fallback;

// -- reconciliation -------------------------------------------------------------

/// One exception of a session (`FinanceReconException`).
class ReconIssue {
  const ReconIssue({
    required this.id,
    required this.kind,
    required this.severity,
    this.postedOn,
    this.amount,
    this.description,
    this.reason,
    this.month,
    this.transactionId,
  });
  final String id, kind, severity;
  final String? postedOn, amount, description, reason, month, transactionId;

  factory ReconIssue.fromJson(JsonReader r) => ReconIssue(
        id: r.string('id'),
        kind: r.string('kind'),
        severity: r.optString('severity') ?? 'info',
        postedOn: r.optString('posted_on'),
        amount: dec(r, 'amount'),
        description: r.optString('description'),
        reason: r.optString('reason'),
        month: r.optString('month'),
        transactionId: r.optString('transaction_id'),
      );
}

/// One open exception across accounts (`FinanceReconQueueItem`).
class ReconQueueItem {
  const ReconQueueItem({
    required this.accountId,
    required this.accountName,
    required this.currency,
    required this.issue,
    this.reconciliationId,
    this.periodStart,
    this.periodEnd,
  });
  final String accountId, accountName, currency;
  final String? reconciliationId, periodStart, periodEnd;
  final ReconIssue issue;

  factory ReconQueueItem.fromJson(JsonReader r) => ReconQueueItem(
        accountId: r.string('account_id'),
        accountName: r.optString('account_name') ?? '',
        currency: r.optString('currency') ?? '',
        reconciliationId: r.optString('reconciliation_id'),
        periodStart: r.optString('period_start'),
        periodEnd: r.optString('period_end'),
        issue: ReconIssue.fromJson(r.object('exception')),
      );
}

/// One session as the list shows it (`FinanceReconSummary`).
class ReconRow {
  const ReconRow({
    required this.id,
    required this.accountId,
    required this.accountName,
    required this.accountKind,
    required this.currency,
    required this.periodStart,
    required this.periodEnd,
    required this.status,
    required this.state,
    required this.changed,
    required this.canComplete,
    required this.unexplained,
    required this.statementClosing,
    required this.bookClosing,
    required this.exceptions,
    required this.blocking,
    required this.version,
    this.completedAt,
  });
  final String id, accountId, accountName, accountKind, currency;
  final String periodStart, periodEnd, status, state;
  final bool changed, canComplete;
  final String unexplained, statementClosing, bookClosing;
  final int exceptions, blocking, version;
  final String? completedAt;

  bool get open => status == 'open';

  /// `changed`, `completed`, `ready` (balanced) or `unresolved`, as the web
  /// labels a session.
  String get label => changed
      ? 'changed'
      : status == 'completed'
          ? 'completed'
          : state == 'balanced'
              ? 'ready'
              : 'unresolved';

  factory ReconRow.fromJson(JsonReader r) => ReconRow(
        id: r.string('id'),
        accountId: r.string('account_id'),
        accountName: r.optString('account_name') ?? '',
        accountKind: r.optString('account_kind') ?? 'bank',
        currency: r.string('currency'),
        periodStart: r.string('period_start'),
        periodEnd: r.string('period_end'),
        status: r.string('status'),
        state: r.optString('state') ?? 'unresolved',
        changed: r.optBool('changed') ?? false,
        canComplete: r.optBool('can_complete') ?? false,
        unexplained: decOr(r, 'unexplained'),
        statementClosing: decOr(r, 'statement_closing'),
        bookClosing: decOr(r, 'book_closing'),
        exceptions: r.optInt('exceptions') ?? 0,
        blocking: r.optInt('blocking') ?? 0,
        version: r.optInt('version') ?? 0,
        completedAt: r.optString('completed_at'),
      );
}

/// The equation of a session (`FinanceReconFigures`).
class ReconFigures {
  const ReconFigures(this.values);
  final Map<String, String?> values;
  static const keys = [
    'book_opening',
    'deposits',
    'payments',
    'book_closing',
    'pending_total',
    'adjusted_book_closing',
    'statement_opening',
    'statement_closing',
    'unexplained',
  ];
  String? operator [](String key) => values[key];

  factory ReconFigures.fromJson(JsonReader r) =>
      ReconFigures({for (final key in keys) key: dec(r, key)});
}

/// A card statement's figures (`FinanceReconCard`).
class ReconCardFigures {
  const ReconCardFigures(
      {this.amountDue,
      this.minimumDue,
      this.dueOn,
      required this.owedInLedger,
      this.dueDifference,
      required this.paidSince,
      this.minimumCovered});
  final String? amountDue, minimumDue, dueOn, dueDifference;
  final String owedInLedger, paidSince;
  final bool? minimumCovered;

  factory ReconCardFigures.fromJson(JsonReader r) => ReconCardFigures(
        amountDue: dec(r, 'amount_due'),
        minimumDue: dec(r, 'minimum_due'),
        dueOn: r.optString('due_on'),
        owedInLedger: decOr(r, 'owed_in_ledger'),
        dueDifference: dec(r, 'due_difference'),
        paidSince: decOr(r, 'paid_since'),
        minimumCovered: r.optBool('minimum_covered'),
      );
}

/// One session in full (`FinanceReconDetail`): the figures, the card check,
/// a page of its exceptions and whether it ever was completed.
class ReconDetail {
  const ReconDetail({
    required this.row,
    required this.figures,
    required this.blockers,
    required this.issues,
    required this.total,
    required this.offset,
    required this.hasHistory,
    this.card,
  });
  final ReconRow row;
  final ReconFigures figures;
  final ReconCardFigures? card;
  final List<String> blockers;
  final List<ReconIssue> issues;
  final int total, offset;

  /// Completed at least once: it can no longer be deleted.
  final bool hasHistory;

  bool get owedKind => row.accountKind == 'card' || row.accountKind == 'loan';

  factory ReconDetail.fromJson(JsonReader r) => ReconDetail(
        row: ReconRow.fromJson(r),
        figures: ReconFigures.fromJson(r.object('figures')),
        card: r.optObject('card') == null
            ? null
            : ReconCardFigures.fromJson(r.object('card')),
        blockers: r.optStrings('blockers') ?? const [],
        issues: r.optObjects('items', ReconIssue.fromJson),
        total: r.optInt('total') ?? 0,
        offset: r.optInt('offset') ?? 0,
        hasHistory: (r.optList('history') ?? const []).isNotEmpty,
      );
}

/// A card or loan statement prints what is owed as a positive figure; the
/// ledger keeps it negative (the web's `ledgerClosing`).
String ledgerFigure(String accountKind, String typed) {
  final value = typed.trim();
  if ((accountKind == 'card' || accountKind == 'loan') &&
      value.isNotEmpty &&
      !value.startsWith('-') &&
      double.tryParse(value) != 0) {
    return '-$value';
  }
  return value;
}

/// The stored ledger figure as the owner types it (the web's
/// `typedFromLedger`).
String typedFigure(String accountKind, String? stored) {
  if (stored == null) return '';
  if ((accountKind == 'card' || accountKind == 'loan') &&
      double.tryParse(stored) != 0) {
    return stored.startsWith('-') ? stored.substring(1) : '-$stored';
  }
  return stored;
}

/// The orders the exceptions list accepts (`sort` on the API).
const reconSorts = [
  'severity',
  'account',
  'period',
  'kind',
  'posted',
  'amount'
];

// -- bank loans -----------------------------------------------------------------

/// A loan's terms (`LoanTerms`).
class BankLoanTerms {
  const BankLoanTerms({
    required this.startOn,
    required this.amount,
    required this.rateType,
    required this.repayment,
    required this.status,
    this.lender,
    this.rate,
    this.termMonths,
    this.installment,
    this.paymentDay,
    this.accountId,
    this.closedOn,
  });
  final String startOn, amount, rateType, repayment, status;
  final String? lender, rate, installment, accountId, closedOn;
  final int? termMonths, paymentDay;

  factory BankLoanTerms.fromJson(JsonReader r) => BankLoanTerms(
        lender: r.optString('lender'),
        startOn: r.string('start_on'),
        amount: decOr(r, 'amount'),
        rateType: r.optString('rate_type') ?? 'reducing',
        rate: dec(r, 'rate'),
        termMonths: r.optInt('term_months'),
        installment: dec(r, 'installment'),
        paymentDay: r.optInt('payment_day'),
        repayment: r.optString('repayment') ?? 'manual',
        accountId: r.optString('account_id'),
        status: r.optString('status') ?? 'open',
        closedOn: r.optString('closed_on'),
      );
}

/// What a loan has paid, owes and has due (`LoanTotals`).
class BankLoanTotals {
  const BankLoanTotals({
    required this.status,
    required this.basis,
    required this.installment,
    required this.splitKnown,
    required this.neverEnds,
    required this.totalPayable,
    required this.paid,
    required this.balance,
    required this.dueCount,
    required this.paidCount,
    required this.monthsAhead,
    required this.nextAmount,
    required this.found,
    required this.missing,
    required this.ambiguous,
    this.totalInterest,
    this.interestPaid,
    this.payoffOn,
    this.nextDueOn,
  });
  final String status, basis, installment, totalPayable, paid, balance;
  final String nextAmount;
  final bool splitKnown, neverEnds;
  final int dueCount, paidCount, monthsAhead, found, missing, ambiguous;
  final String? totalInterest, interestPaid, payoffOn, nextDueOn;

  factory BankLoanTotals.fromJson(JsonReader r) => BankLoanTotals(
        status: r.optString('status') ?? 'open',
        basis: r.optString('basis') ?? 'plain',
        installment: decOr(r, 'installment'),
        splitKnown: r.optBool('split_known') ?? false,
        neverEnds: r.optBool('never_ends') ?? false,
        totalPayable: decOr(r, 'total_payable'),
        totalInterest: dec(r, 'total_interest'),
        paid: decOr(r, 'paid'),
        interestPaid: dec(r, 'interest_paid'),
        balance: decOr(r, 'balance'),
        payoffOn: r.optString('payoff_on'),
        dueCount: r.optInt('due_count') ?? 0,
        paidCount: r.optInt('paid_count') ?? 0,
        monthsAhead: r.optInt('months_ahead') ?? 0,
        nextDueOn: r.optString('next_due_on'),
        nextAmount: decOr(r, 'next_amount'),
        found: r.optInt('found') ?? 0,
        missing: r.optInt('missing') ?? 0,
        ambiguous: r.optInt('ambiguous') ?? 0,
      );
}

/// A debit that fits an instalment more than once over (`LoanCandidate`).
class BankLoanFit {
  const BankLoanFit(
      {required this.canonicalId,
      required this.postedOn,
      required this.amount,
      required this.currency,
      required this.merchant});
  final String canonicalId, postedOn, amount, currency, merchant;

  factory BankLoanFit.fromJson(JsonReader r) => BankLoanFit(
        canonicalId: r.string('canonical_id'),
        postedOn: r.string('posted_on'),
        amount: decOr(r, 'amount'),
        currency: r.optString('currency') ?? '',
        merchant: r.optString('merchant') ?? '',
      );
}

/// One instalment (`LoanRow`): `paid`, `part_paid`, `missed`, `upcoming`,
/// `ambiguous` or `settled`.
class BankLoanRow {
  const BankLoanRow({
    required this.number,
    required this.dueOn,
    required this.payment,
    required this.balanceAfter,
    required this.status,
    required this.paid,
    required this.fits,
    this.principal,
    this.interest,
  });
  final int number;
  final String dueOn, payment, balanceAfter, status, paid;
  final String? principal, interest;
  final List<BankLoanFit> fits;

  factory BankLoanRow.fromJson(JsonReader r) => BankLoanRow(
        number: r.integer('number'),
        dueOn: r.string('due_on'),
        payment: decOr(r, 'payment'),
        principal: dec(r, 'principal'),
        interest: dec(r, 'interest'),
        balanceAfter: decOr(r, 'balance_after'),
        status: r.optString('status') ?? 'upcoming',
        paid: decOr(r, 'paid'),
        fits: r.optObjects('candidates', BankLoanFit.fromJson),
      );
}

/// Payments accepted at once from a "payments found" suggestion.
class BankLoanBatch {
  const BankLoanBatch(
      {required this.id,
      required this.status,
      required this.count,
      required this.amount,
      this.currency,
      this.createdAt,
      this.undoneAt});
  final String id, status, amount;
  final int count;
  final String? currency, createdAt, undoneAt;
  bool get undone => status != 'active' || undoneAt != null;

  factory BankLoanBatch.fromJson(JsonReader r) => BankLoanBatch(
        id: r.string('id'),
        status: r.optString('status') ?? 'active',
        count: r.optInt('count') ?? 0,
        amount: decOr(r, 'amount'),
        currency: r.optString('currency'),
        createdAt: r.optString('created_at'),
        undoneAt: r.optString('undone_at'),
      );
}

/// A loan in the list (`LoanSummary`).
class BankLoanSummary {
  const BankLoanSummary(
      {required this.debtId,
      required this.name,
      required this.currency,
      required this.repayment,
      required this.totals,
      this.lender});
  final String debtId, name, currency, repayment;
  final String? lender;
  final BankLoanTotals totals;

  factory BankLoanSummary.fromJson(JsonReader r) => BankLoanSummary(
        debtId: r.string('debt_id'),
        name: r.string('name'),
        lender: r.optString('lender'),
        currency: r.string('currency'),
        repayment: r.optString('repayment') ?? 'manual',
        totals: BankLoanTotals.fromJson(r.object('totals')),
      );
}

/// A loan in full (`LoanView`).
class BankLoan {
  const BankLoan(
      {required this.debtId,
      required this.name,
      required this.currency,
      required this.terms,
      required this.totals,
      required this.rows,
      required this.batches});
  final String debtId, name, currency;
  final BankLoanTerms terms;
  final BankLoanTotals totals;
  final List<BankLoanRow> rows;
  final List<BankLoanBatch> batches;

  factory BankLoan.fromJson(JsonReader r) => BankLoan(
        debtId: r.string('debt_id'),
        name: r.string('name'),
        currency: r.string('currency'),
        terms: BankLoanTerms.fromJson(r.object('terms')),
        totals: BankLoanTotals.fromJson(r.object('totals')),
        rows: r.optObjects('rows', BankLoanRow.fromJson),
        batches: r.optObjects('batches', BankLoanBatch.fromJson),
      );
}

/// A debt as the "Add loan terms" picker shows it.
class DebtChoice {
  const DebtChoice(this.id, this.name, this.currency, this.kind);
  final String id, name, currency, kind;
  factory DebtChoice.fromJson(JsonReader r) => DebtChoice(r.string('id'),
      r.string('name'), r.string('currency'), r.optString('kind') ?? '');
}

/// A bank or cash account the instalments may be debited from.
class AccountChoice {
  const AccountChoice(this.id, this.name, this.kind, this.currency);
  final String id, name, kind, currency;
  factory AccountChoice.fromJson(JsonReader r) => AccountChoice(
      r.string('id'), r.string('name'), r.string('kind'), r.string('currency'));
}

// -- foreign-currency balances ----------------------------------------------------

/// One account in another currency (`RevaluationRow`).
class FxBalance {
  const FxBalance({
    required this.accountId,
    required this.name,
    required this.currency,
    required this.baseCurrency,
    required this.balance,
    required this.status,
    required this.missingRateDates,
    this.value,
    this.rate,
    this.rateAsOf,
    this.rateSource,
    this.cost,
    this.unrealised,
  });
  final String accountId, name, currency, baseCurrency, balance, status;
  final String? value, rate, rateAsOf, rateSource, cost, unrealised;
  final List<String> missingRateDates;

  factory FxBalance.fromJson(JsonReader r) => FxBalance(
        accountId: r.string('account_id'),
        name: r.string('name'),
        currency: r.string('currency'),
        baseCurrency: r.string('base_currency'),
        balance: decOr(r, 'balance'),
        status: r.optString('status') ?? 'ok',
        value: dec(r, 'value'),
        rate: dec(r, 'rate'),
        rateAsOf: r.optString('rate_as_of'),
        rateSource: r.optString('rate_source'),
        cost: dec(r, 'cost'),
        unrealised: dec(r, 'unrealised'),
        missingRateDates: r.optStrings('missing_rate_dates') ?? const [],
      );
}

class FxBalances {
  const FxBalances(
      {required this.baseCurrency,
      required this.asOf,
      required this.rateMaxAgeDays,
      required this.items,
      required this.incomplete,
      required this.truncated,
      required this.note,
      this.unrealisedTotal});
  final String baseCurrency, asOf, note;
  final int rateMaxAgeDays, incomplete;
  final bool truncated;
  final String? unrealisedTotal;
  final List<FxBalance> items;

  factory FxBalances.fromJson(JsonReader r) => FxBalances(
        baseCurrency: r.string('base_currency'),
        asOf: r.string('as_of'),
        rateMaxAgeDays: r.optInt('rate_max_age_days') ?? 7,
        items: r.optObjects('items', FxBalance.fromJson),
        unrealisedTotal: dec(r, 'unrealised_total'),
        incomplete: r.optInt('incomplete') ?? 0,
        truncated: r.optBool('truncated') ?? false,
        note: r.optString('note') ?? '',
      );
}

// -- trips and projects -----------------------------------------------------------

/// A personal trip or project (`TagView`, with `movements` on the detail).
class FinanceTag {
  const FinanceTag({
    required this.id,
    required this.kind,
    required this.name,
    required this.archived,
    required this.totals,
    required this.links,
    this.startsOn,
    this.endsOn,
    this.keywords,
    this.notes,
    this.movements = const [],
  });
  final String id, kind, name;
  final bool archived;
  final Map<String, String> totals;
  final int links;
  final String? startsOn, endsOn, keywords, notes;
  final List<LoanMovement> movements;

  factory FinanceTag.fromJson(JsonReader r) => FinanceTag(
        id: r.string('id'),
        kind: r.string('kind'),
        name: r.string('name'),
        archived: r.optBool('archived') ?? false,
        totals: {
          for (final entry in (r.optMap('totals') ?? const {}).entries)
            entry.key: '${entry.value}',
        },
        links: r.optInt('links') ?? 0,
        startsOn: r.optString('starts_on'),
        endsOn: r.optString('ends_on'),
        keywords: r.optString('keywords'),
        notes: r.optString('notes'),
        movements: r.optObjects('movements', LoanMovement.fromJson),
      );

  Map<String, Object?> body({
    String? name,
    String? kind,
    String? startsOn,
    String? endsOn,
    String? keywords,
    String? notes,
    bool? archived,
  }) =>
      {
        'kind': kind ?? this.kind,
        'name': name ?? this.name,
        'starts_on': startsOn ?? this.startsOn,
        'ends_on': endsOn ?? this.endsOn,
        'keywords': keywords ?? this.keywords,
        'notes': notes ?? this.notes,
        'archived': archived ?? this.archived,
      };
}

// -- the repository ---------------------------------------------------------------

/// Every route these pages use, through the app's API client.
class FinanceMoreRepository {
  FinanceMoreRepository(this.api);
  final AnumApiClient api;
  static const base = '/api/v1/finance';
  static const pageSize = 20;

  String _id(String value) => Uri.encodeComponent(value);

  Future<JsonReader> _get(String path) async =>
      JsonReader.of(await api.request('GET', '$base$path'));

  // reconciliation

  Future<RecordsChunk<ReconRow>> reconSessions(
      {String state = '', String? token}) async {
    final offset = int.tryParse(token ?? '0') ?? 0;
    final page = await _get('/reconciliations${recordsQuery({
          'state': state,
          'limit': pageSize,
          'offset': offset,
        })}');
    return RecordsChunk.offsetPage(page, ReconRow.fromJson, offset: offset);
  }

  Future<ReconDetail> recon(String id, {int offset = 0}) async =>
      ReconDetail.fromJson(
          await _get('/reconciliations/${_id(id)}${recordsQuery({
            'limit': 50,
            'offset': offset,
          })}'));

  Future<ReconDetail> updateRecon(String id, Map<String, Object?> body) async =>
      ReconDetail.fromJson(JsonReader.of(await api
          .request('PATCH', '$base/reconciliations/${_id(id)}', body: body)));

  Future<void> deleteRecon(String id) =>
      api.request('DELETE', '$base/reconciliations/${_id(id)}');

  Future<RecordsChunk<ReconQueueItem>> reconExceptions(
      {String sort = '', bool desc = false, String? token}) async {
    final offset = int.tryParse(token ?? '0') ?? 0;
    final page = await _get('/reconciliations/exceptions${recordsQuery({
          'sort': sort,
          'desc': desc ? 'true' : null,
          'limit': pageSize,
          'offset': offset,
        })}');
    return RecordsChunk.offsetPage(page, ReconQueueItem.fromJson,
        offset: offset);
  }

  Future<String> reconExceptionsCsv({String sort = '', bool desc = false}) =>
      fetchCsv(
          api,
          '$base/reconciliations/exceptions/export${recordsQuery({
                'sort': sort,
                'desc': desc ? 'true' : null,
              })}');

  // bank loans

  Future<List<BankLoanSummary>> bankLoans() async =>
      (await _get('/loans')).optObjects('items', BankLoanSummary.fromJson);

  Future<BankLoan> bankLoan(String debtId) async =>
      BankLoan.fromJson(await _get('/debts/${_id(debtId)}/loan'));

  Future<BankLoan> saveLoanTerms(
          String debtId, Map<String, Object?> body) async =>
      BankLoan.fromJson(JsonReader.of(await api
          .request('PUT', '$base/debts/${_id(debtId)}/loan', body: body)));

  Future<void> removeLoanTerms(String debtId) =>
      api.request('DELETE', '$base/debts/${_id(debtId)}/loan');

  Future<void> undoLoanBatch(String batchId) =>
      api.request('POST', '$base/loan-batches/${_id(batchId)}/undo',
          body: const {});

  /// Picks the debit that paid an ambiguous instalment.
  Future<void> pickLoanPayment(String debtId, String canonicalId,
          {required String idempotencyKey}) =>
      api.request('POST', '$base/debts/${_id(debtId)}/payments',
          body: {'canonical_id': canonicalId},
          headers: {'Idempotency-Key': idempotencyKey});

  Future<List<DebtChoice>> debts() async =>
      JsonReader.of(await api.request('GET', '$base/debts'))
          .optObjects('data', DebtChoice.fromJson);

  Future<List<AccountChoice>> accounts() async =>
      JsonReader.of(await api.request('GET', '$base/accounts'))
          .optObjects('data', AccountChoice.fromJson);

  // one payment split across loans with people

  /// The person's own transactions a loan payment could be (money in for
  /// money lent, money out for money borrowed).
  Future<List<LoanCandidate>> splitCandidates(
          {required String direction,
          required String currency,
          String query = ''}) async =>
      (await _get('/payment-candidates${recordsQuery({
            'for': 'person_loan',
            'direction': direction,
            'currency': currency,
            'q': query.trim(),
            'limit': 30,
          })}'))
          .optObjects('items', LoanCandidate.fromJson);

  /// Every loan with [person] (the split offers the open ones in the same
  /// currency and direction).
  Future<List<PersonLoan>> loansWith(String person) async =>
      (await _get('/people-loans${recordsQuery({
            'q': person,
            'limit': 50,
          })}'))
          .optObjects('items', PersonLoan.fromJson);

  Future<void> splitLoans(
          {required String canonicalId,
          required String kind,
          required Map<String, String> parts,
          required String idempotencyKey}) =>
      api.request('POST', '$base/people-loans/split', body: {
        'canonical_id': canonicalId,
        'kind': kind,
        'parts': [
          for (final part in parts.entries)
            {'loan_id': part.key, 'amount': part.value}
        ],
      }, headers: {
        'Idempotency-Key': idempotencyKey
      });

  // foreign-currency balances

  Future<FxBalances> fxBalances() async =>
      FxBalances.fromJson(await _get('/fx-revaluation'));

  // trips and projects

  Future<RecordsChunk<FinanceTag>> tags(
      {String kind = '',
      String query = '',
      bool archived = false,
      String? token}) async {
    final offset = int.tryParse(token ?? '0') ?? 0;
    final page = await _get('/tags${recordsQuery({
          'kind': kind,
          'q': query,
          'archived': archived ? 'true' : null,
          'limit': pageSize,
          'offset': offset,
        })}');
    return RecordsChunk.offsetPage(page, FinanceTag.fromJson, offset: offset);
  }

  Future<FinanceTag> tag(String id) async =>
      FinanceTag.fromJson(await _get('/tags/${_id(id)}'));

  Future<FinanceTag> saveTag(String? id, Map<String, Object?> body) async =>
      FinanceTag.fromJson(JsonReader.of(id == null
          ? await api.request('POST', '$base/tags', body: body)
          : await api.request('PATCH', '$base/tags/${_id(id)}', body: body)));

  Future<void> deleteTag(String id) =>
      api.request('DELETE', '$base/tags/${_id(id)}');

  Future<List<LoanCandidate>> tagCandidates(
          {required String kind, String query = ''}) async =>
      (await _get('/payment-candidates${recordsQuery({
            'for': kind,
            'q': query.trim(),
            'limit': 30,
          })}'))
          .optObjects('items', LoanCandidate.fromJson);

  Future<void> tagPayment(String tagId, String canonicalId,
          {String? amount, required String idempotencyKey}) =>
      api.request('POST', '$base/tags/${_id(tagId)}/links', body: {
        'canonical_id': canonicalId,
        if (amount != null) 'amount': amount,
      }, headers: {
        'Idempotency-Key': idempotencyKey
      });

  Future<void> untagPayment(String tagId, String linkId) =>
      api.request('DELETE', '$base/tags/${_id(tagId)}/links/${_id(linkId)}');

  // the Money advisor

  Future<AdvisorOverviewData> advisor() async =>
      AdvisorOverviewData.fromJson(await _get('/advisor'));

  Future<RecordsChunk<AdvisorNoteRow>> advisorNotes({String? token}) async {
    final offset = int.tryParse(token ?? '0') ?? 0;
    final page = await _get('/advisor/notes${recordsQuery({
          'limit': pageSize,
          'offset': offset,
        })}');
    return RecordsChunk.offsetPage(page, AdvisorNoteRow.fromJson,
        offset: offset);
  }

  Future<AdvisorNoteData> advisorNote(String id) async =>
      AdvisorNoteData.fromJson(await _get('/advisor/notes/${_id(id)}'));

  /// Works this week's note out again; sends no notice.
  Future<AdvisorNoteData> generateAdvice() async =>
      AdvisorNoteData.fromJson(JsonReader.of(
          await api.request('POST', '$base/advisor/generate', body: const {})));

  Future<AdvisorSettingsData> saveAdvisorSettings(
          {required bool enabled, required int weekday}) async =>
      AdvisorSettingsData.fromJson(JsonReader.of(await api.request(
          'PUT', '$base/advisor/settings',
          body: {'enabled': enabled, 'weekday': weekday})));

  // the Plan

  Future<AffordAnswer> afford(Map<String, Object?> body) async =>
      AffordAnswer.fromJson(JsonReader.of(
          await api.request('POST', '$base/planner/afford', body: body)));

  Future<({String currency, List<CompareOption> options})> compare(
      Map<String, Object?> body) async {
    final r = JsonReader.of(
        await api.request('POST', '$base/planner/compare', body: body));
    return (
      currency: r.string('currency'),
      options: r.optObjects('options', CompareOption.fromJson),
    );
  }

  // payslips

  Future<RecordsChunk<Payslip>> payslips(
      {String query = '', String? token}) async {
    final offset = int.tryParse(token ?? '0') ?? 0;
    final page = await _get('/payslips${recordsQuery({
          'q': query,
          'limit': pageSize,
          'offset': offset,
        })}');
    return RecordsChunk.offsetPage(page, Payslip.fromJson, offset: offset);
  }

  Future<Payslip> payslip(String id) async =>
      Payslip.fromJson(await _get('/payslips/${_id(id)}'));

  Future<Payslip> savePayslip(String? id, Map<String, Object?> body) async =>
      Payslip.fromJson(JsonReader.of(id == null
          ? await api.request('POST', '$base/payslips', body: body)
          : await api.request('PATCH', '$base/payslips/${_id(id)}',
              body: body)));

  Future<void> deletePayslip(String id) =>
      api.request('DELETE', '$base/payslips/${_id(id)}');

  // transfers home

  Future<({RecordsChunk<Remittance> chunk, List<RemitProvider> providers})>
      remittances({String query = '', String? token}) async {
    final offset = int.tryParse(token ?? '0') ?? 0;
    final page = await _get('/remittances${recordsQuery({
          'q': query,
          'limit': pageSize,
          'offset': offset,
        })}');
    return (
      chunk: RecordsChunk.offsetPage(page, Remittance.fromJson, offset: offset),
      providers: page.optObjects('providers', RemitProvider.fromJson),
    );
  }

  Future<Remittance> remittance(String id) async =>
      Remittance.fromJson(await _get('/remittances/${_id(id)}'));

  Future<Remittance> saveRemittance(
          String? id, Map<String, Object?> body) async =>
      Remittance.fromJson(JsonReader.of(id == null
          ? await api.request('POST', '$base/remittances', body: body)
          : await api.request('PATCH', '$base/remittances/${_id(id)}',
              body: body)));

  Future<void> deleteRemittance(String id) =>
      api.request('DELETE', '$base/remittances/${_id(id)}');

  /// The person's own transactions a payslip (salary credit, money in) or
  /// a transfer home (money out) could be linked to.
  Future<List<LoanCandidate>> recordCandidates(String kind,
          {String? around, String? currency, String query = ''}) async =>
      (await _get('/payment-candidates${recordsQuery({
            'for': kind,
            'around': around,
            'currency': currency,
            'q': query.trim(),
            'limit': 30,
          })}'))
          .optObjects('items', LoanCandidate.fromJson);

  /// Links a transaction to a payslip or a transfer home ([kind] is
  /// `payslips` or `remittances`).
  Future<void> linkRecord(String kind, String id, String canonicalId,
          {required String idempotencyKey}) =>
      api.request('POST', '$base/$kind/${_id(id)}/links',
          body: {'canonical_id': canonicalId},
          headers: {'Idempotency-Key': idempotencyKey});

  Future<void> unlinkRecord(String kind, String id, String linkId) =>
      api.request('DELETE', '$base/$kind/${_id(id)}/links/${_id(linkId)}');

  // the salary profile

  /// The profile, or null until one is added.
  Future<SalaryProfileData?> salaryProfile() async {
    final r = await _get('/salary/profile');
    final profile = r.optObject('profile');
    return profile == null ? null : SalaryProfileData.fromJson(profile);
  }

  Future<void> saveSalaryProfile(Map<String, Object?> body) =>
      api.request('PUT', '$base/salary/profile', body: body);

  Future<void> deleteSalaryProfile() =>
      api.request('DELETE', '$base/salary/profile');
}
