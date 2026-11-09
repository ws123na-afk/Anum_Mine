import '../../data/json_read.dart';

/// What the phone reads for the review side of Finance (docs/finance.md):
/// transaction types, the last statement import, the suggestion queue and
/// reconciliation status. It mirrors `packages/contracts/src/finance.ts`,
/// `financeRules.ts` and `financeReconciliation.ts`. Money stays a decimal
/// string next to its currency; nothing is added across currencies.

/// A decimal the API sends as a string; a number is kept as its text.
String? _decimal(JsonReader r, String key) => switch (r.json[key]) {
      null => null,
      final String text => text,
      final num number => number.toString(),
      _ => r.optString(key),
    };

/// The transaction types an owner can file a row as
/// (docs/finance.md#transaction-types). `uncategorized` is what the rules
/// give a row they do not recognise, never a choice.
const fileableTypes = <String>[
  'expense',
  'income',
  'internal_transfer',
  'card_payment',
  'loan_received',
  'loan_principal',
  'interest',
  'refund',
  'cashback',
  'bank_fee',
  'investment_contribution',
  'investment_withdrawal',
  'owner_contribution',
  'owner_drawing',
  'reimbursement',
  'receivable_collection',
  'payable_settlement',
  'fx_exchange',
  'tax',
  'reversal',
];

/// The categories an owner can file a row under, in the web's order.
const fileableCategories = <String>[
  'groceries',
  'dining',
  'food',
  'room_rent',
  'transport',
  'fuel',
  'utilities',
  'telecom',
  'housing',
  'shopping',
  'health',
  'education',
  'entertainment',
  'subscriptions',
  'travel',
  'insurance',
  'fees',
  'interest',
  'cash',
  'transfer',
  'income',
  'cashback',
  'refunds',
  'other',
];

/// The newest statement import (`FinanceImport`), counts only.
class ImportRecord {
  const ImportRecord({
    required this.id,
    required this.accountId,
    required this.filename,
    required this.createdAt,
    required this.imported,
    required this.held,
    required this.attached,
    required this.notInLedger,
    this.status,
  });
  final String id, accountId, filename, createdAt;

  /// Rows added, rows held for review, rows attached to a payment already
  /// recorded (SMS, Apple Pay, typed) and statement rows the ledger does not
  /// hold (held or left out).
  final int imported, held, attached, notInLedger;

  /// The statement's balance check: tied, differs or unknown (null when the
  /// import had none).
  final String? status;

  factory ImportRecord.fromJson(JsonReader r) => ImportRecord(
        id: r.string('id'),
        accountId: r.string('account_id'),
        filename: r.string('filename'),
        createdAt: r.string('created_at'),
        imported: r.optInt('imported_count') ?? 0,
        held: r.optInt('held_for_review') ?? 0,
        attached: r.optInt('reconciled') ?? 0,
        notInLedger: r.optInt('not_in_ledger') ?? 0,
        status: r.optObject('reconciliation')?.optString('status'),
      );
}

/// One account's connection health (`FinanceReconHealth`).
class ReconHealth {
  const ReconHealth({
    required this.accountId,
    required this.accountName,
    required this.kind,
    required this.currency,
    required this.missingMonths,
    required this.nextStep,
    this.reconciledThrough,
    this.lastStatementEnd,
    this.openId,
    this.changedId,
    this.nextMonth,
  });
  final String accountId, accountName, kind, currency, nextStep;
  final List<String> missingMonths;
  final String? reconciledThrough, lastStatementEnd, openId, changedId;
  final String? nextMonth;

  /// Bank and card accounts are reconciled; cash, loans and investments not.
  bool get reconcilable => kind == 'bank' || kind == 'card';

  factory ReconHealth.fromJson(JsonReader r) => ReconHealth(
        accountId: r.string('account_id'),
        accountName: r.string('account_name'),
        kind: r.string('kind'),
        currency: r.string('currency'),
        missingMonths: r.optStrings('missing_months') ?? const [],
        nextStep: r.optString('next_step') ?? 'none',
        reconciledThrough: r.optString('reconciled_through'),
        lastStatementEnd: r.optString('last_statement_end'),
        openId: r.optString('open_reconciliation_id'),
        changedId: r.optString('changed_reconciliation_id'),
        nextMonth: r.optString('next_month'),
      );
}

/// One reconciliation session as the list shows it (`FinanceReconSummary`).
class ReconSession {
  const ReconSession({
    required this.id,
    required this.accountId,
    required this.currency,
    required this.periodStart,
    required this.periodEnd,
    required this.status,
    required this.unexplained,
    required this.blocking,
    required this.exceptions,
    required this.changed,
    required this.canComplete,
  });
  final String id, accountId, currency, periodStart, periodEnd, status;

  /// Statement closing less the adjusted book closing: 0 to complete.
  final String unexplained;
  final int blocking, exceptions;
  final bool changed, canComplete;

  factory ReconSession.fromJson(JsonReader r) => ReconSession(
        id: r.string('id'),
        accountId: r.string('account_id'),
        currency: r.string('currency'),
        periodStart: r.string('period_start'),
        periodEnd: r.string('period_end'),
        status: r.string('status'),
        unexplained: _decimal(r, 'unexplained') ?? '0',
        blocking: r.optInt('blocking') ?? 0,
        exceptions: r.optInt('exceptions') ?? 0,
        changed: r.optBool('changed') ?? false,
        canComplete: r.optBool('can_complete') ?? false,
      );
}

/// Connection health with the open sessions, per account.
class ReconStatus {
  const ReconStatus({required this.accounts, required this.open});
  final List<ReconHealth> accounts;
  final List<ReconSession> open;

  /// Bank and card accounts only: the others have nothing to reconcile.
  List<ReconHealth> get reconcilable =>
      accounts.where((a) => a.reconcilable).toList();

  ReconSession? openFor(ReconHealth h) =>
      open.where((s) => s.id == h.openId).firstOrNull ??
      open.where((s) => s.accountId == h.accountId).firstOrNull;
}

/// The money a suggestion would move or count (`effect`).
class SuggestionEffect {
  const SuggestionEffect(this.amount, this.currency, this.direction);
  final String amount, currency, direction;

  /// The amount with its sign: money out is shown negative.
  String get signed =>
      direction == 'out' && !amount.startsWith('-') ? '-$amount' : amount;

  factory SuggestionEffect.fromJson(JsonReader r) => SuggestionEffect(
      _decimal(r, 'amount') ?? '0',
      r.string('currency'),
      r.optString('direction') ?? 'none');
}

/// One item of the suggestion queue (`FinanceSuggestion`).
class Suggestion {
  const Suggestion({
    required this.key,
    required this.kind,
    required this.why,
    required this.params,
    required this.effects,
    required this.actionKind,
    required this.payloadHash,
    this.confidence,
    this.href,
    this.occurredOn,
    this.hasLink = false,
    this.transactionIds = const [],
  });
  final String key, kind, why, actionKind, payloadHash;

  /// Accept makes a link (or a business allocation) through the links API.
  final bool hasLink;

  /// The rows the proposal is about (the purchase of an FX fee item).
  final List<String> transactionIds;
  final Map<String, String> params;
  final List<SuggestionEffect> effects;
  final String? confidence, href, occurredOn;

  /// What Accept does here: change rows (`apply`), only record that it was
  /// seen (`acknowledge`), or nothing on the phone (`none`: resolved on its
  /// own page on the web).
  String get acceptMode => switch (actionKind) {
        'classify' || 'confirm_refund' => 'apply',
        'link' when hasLink => 'link',
        'acknowledge' => 'acknowledge',
        _ => 'none',
      };

  /// The words of an attribution, payment or FX fee item, one per linked
  /// module (docs/finance.md#attribution-suggestions), or null.
  String? get variant {
    if (kind == 'attribution') {
      final dimension = params['dimension'] ?? 'business';
      return dimension == 'vehicle' && params['record'] == 'none'
          ? 'vehicle_record'
          : dimension;
    }
    if (kind == 'payment_match') return actionKind == 'link' ? 'link' : null;
    if (kind == 'fx_fee') {
      return params['source'] == 'fee_row' ? 'link' : 'split';
    }
    return null;
  }

  /// The confidence as a whole percentage, or null (0 is "not recognised",
  /// not a confidence worth showing).
  int? get percent {
    final n = double.tryParse(confidence ?? '');
    return n == null || n <= 0 ? null : (n * 100).round();
  }

  factory Suggestion.fromJson(JsonReader r) => Suggestion(
        key: r.string('key'),
        kind: r.string('kind'),
        why: r.optString('why') ?? '',
        params: {
          for (final e in (r.optMap('params') ?? const {}).entries)
            if (e.value != null) e.key: '${e.value}'
        },
        effects: r.optObjects('effect', SuggestionEffect.fromJson),
        actionKind: r.optObject('action')?.optString('kind') ?? 'open',
        hasLink: r.optObject('action')?.optObject('link') != null,
        transactionIds:
            r.optObject('action')?.optStrings('transaction_ids') ?? const [],
        payloadHash: r.string('payload_hash'),
        confidence: _decimal(r, 'confidence'),
        href: r.optString('href'),
        occurredOn: r.optString('occurred_on'),
      );
}

/// A page of the queue (`FinanceSuggestionQueue`).
class SuggestionQueue {
  const SuggestionQueue(
      {required this.items, required this.total, required this.unavailable});
  final List<Suggestion> items;
  final int total;

  /// Checks that could not run this time; the rest are shown.
  final List<String> unavailable;

  factory SuggestionQueue.fromJson(JsonReader r) => SuggestionQueue(
        items: r.optObjects('items', Suggestion.fromJson),
        total: r.optInt('total') ?? 0,
        unavailable: r.optStrings('unavailable') ?? const [],
      );
}

/// The outcome of an accept: how many rows changed, when it changed any.
class SuggestionDecision {
  const SuggestionDecision(this.status, this.changed);
  final String status;
  final int? changed;

  factory SuggestionDecision.fromJson(JsonReader r) => SuggestionDecision(
      r.optString('status') ?? 'accepted',
      r.optObject('batch')?.optInt('changed'));
}
